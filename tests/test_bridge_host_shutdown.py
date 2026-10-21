"""Exercise the desktop shutdown protocol without opening USB or BLE."""
import json
import ast
from pathlib import Path
import subprocess
import threading
import unittest
from unittest.mock import Mock, patch

import config_gui
from esp32_bridge import ESP32Bridge


class BridgeHostShutdownTests(unittest.TestCase):
    def make_bridge(self):
        bridge = ESP32Bridge("test")
        bridge.running = True
        bridge.connected_channel = 2
        bridge._ready_channel = 2
        bridge._host_session = 123
        bridge._host_session_ready = True
        bridge._host_released_event = threading.Event()
        bridge._send_final_zero_rumble = Mock(return_value=True)
        bridge._release_preferred_connection_request = Mock()
        bridge.serial = Mock(is_open=True)
        return bridge

    def test_gui_allows_complete_cleanup_before_force_kill(self):
        gui = object.__new__(config_gui.ConfigGUI)
        process = gui.main_process = Mock()
        process.poll.return_value = None
        with patch("config_gui.subprocess.run") as kill:
            gui.stop_main_process()
        process.send_signal.assert_called_once()
        self.assertGreaterEqual(process.wait.call_args.kwargs["timeout"], 12)
        kill.assert_not_called()
        self.assertIsNone(gui.main_process)

    def test_gui_timeout_kills_only_after_grace_period(self):
        gui = object.__new__(config_gui.ConfigGUI)
        process = gui.main_process = Mock(pid=987)
        process.poll.return_value = None
        process.wait.side_effect = subprocess.TimeoutExpired("connector", 12)
        with patch("config_gui.subprocess.run") as kill:
            gui.stop_main_process()
        self.assertGreaterEqual(process.wait.call_args.kwargs["timeout"], 12)
        self.assertEqual(kill.call_args.args[0], ["taskkill", "/PID", "987", "/T", "/F"])

    def test_ack_for_another_session_cannot_confirm_release(self):
        bridge = self.make_bridge()
        bridge._closing = True
        bridge._handle_text(json.dumps({"cmd": "host_released", "session": 122}).encode())
        self.assertFalse(bridge._host_released_event.is_set())
        bridge._handle_text(json.dumps({"cmd": "host_released", "session": 123}).encode())
        self.assertTrue(bridge._host_released_event.is_set())

    @patch("esp32_bridge.time.sleep")
    def test_close_waits_for_all_links_not_first_disconnect_or_acceptance(self, _sleep):
        bridge = self.make_bridge()
        commands = []
        def send(command):
            commands.append(command)
            if command == "host end 123":
                bridge._handle_text(b'{"cmd":"host_session","session":123,"state":"stopping"}')
                bridge._handle_text(b'{"cmd":"disconnected","channel":2}')
            return True
        bridge.send = send
        bridge._host_released_event.wait = Mock(return_value=False)
        self.assertFalse(bridge.close(timeout=0.1))
        self.assertIn("host end 123", commands)
        bridge._host_released_event.wait.assert_called_once()
        self.assertIsNone(bridge.serial)

    @patch("esp32_bridge.time.sleep")
    def test_confirmed_release_precedes_serial_close_and_late_connect_is_ignored(self, _sleep):
        bridge = self.make_bridge()
        events = []
        bridge.serial.close.side_effect = lambda: events.append("serial_close")
        bridge._send_final_zero_rumble.side_effect = lambda **kw: events.append("zero") or True
        def send(command):
            events.append(command)
            if command == "host end 123":
                bridge._handle_text(b'{"cmd":"connected","channel":3}')
                bridge._handle_text(b'{"cmd":"host_released","session":123}')
            return True
        bridge.send = send
        self.assertTrue(bridge.close())
        self.assertLess(events.index("zero"), events.index("host end 123"))
        self.assertLess(events.index("host end 123"), events.index("serial_close"))
        self.assertIsNone(bridge.connected_channel)

    def test_unrelated_controller_disconnect_does_not_clear_selected_link(self):
        bridge = self.make_bridge()
        bridge._handle_text(b'{"cmd":"disconnected","channel":1}')
        self.assertEqual(bridge.connected_channel, 2)
        self.assertFalse(bridge._disconnect_event.is_set())

    def test_main_releases_bridge_before_potentially_blocking_audio_cleanup(self):
        source = (Path(__file__).resolve().parents[1] / "src/main.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        cleanup = next(node for node in ast.walk(tree)
                       if isinstance(node, ast.FunctionDef) and node.name == "cleanup")
        cleanup.body = [ast.Global(node.names) if isinstance(node, ast.Nonlocal) else node
                        for node in cleanup.body]
        module = ast.fix_missing_locations(ast.Module(body=[cleanup], type_ignores=[]))
        for bridge_mode in (True, False):
            events = []
            bridge = ESP32Bridge("test") if bridge_mode else Mock()
            bridge.close = lambda: events.append("transport") or True
            context = dict(cleanup_complete=False, controller=bridge, ESP32Bridge=ESP32Bridge,
                           audio_haptics=Mock(close=lambda: events.append("audio")),
                           set_input_gc_suppressed=Mock(), close_xinput_after_dispatcher=Mock(return_value=True),
                           input_dispatcher=None, xinput=Mock(), test_telemetry=None,
                           publish_status=Mock(), print=Mock(), tr=lambda zh, en: en)
            exec(compile(module, "main.cleanup", "exec"), context)
            context["cleanup"]()
            context["cleanup"]()  # idempotent
            self.assertEqual(events, ["transport", "audio"] if bridge_mode else ["audio", "transport"])

    def test_open_establishes_session_before_scanning_and_legacy_still_opens(self):
        for managed in (True, False):
            bridge = ESP32Bridge("test")
            writes = []
            port = Mock(is_open=True)
            port.write.side_effect = lambda data: writes.append(data) or len(data)
            def started():
                if managed:
                    bridge._handle_text(json.dumps({"cmd": "host_session", "session": bridge._host_session,
                                                   "state": "ready"}).encode())
                else:
                    bridge._handle_text(b'{"cmd":"status","ble_channels":0}')
            thread = Mock()
            thread.start.side_effect = started
            with patch("esp32_bridge.serial.Serial", return_value=port), \
                 patch("esp32_bridge.threading.Thread", return_value=thread):
                bridge.open()
            self.assertTrue(writes[0].startswith(b"host begin "))
            self.assertEqual(bridge._host_session_ready, managed)
            self.assertTrue(bridge._host_session_event.is_set())

    def test_heartbeat_uses_existing_worker_and_cannot_rearm_during_close(self):
        for ready, closing in ((False, False), (True, False), (True, True)):
            bridge = self.make_bridge()
            bridge._closing = closing
            bridge._host_session_ready = ready
            bridge._status_event.wait = Mock(return_value=True)
            commands = []
            def send(command):
                commands.append(command)
                if command == "status lite":
                    bridge.running = False
                return True
            bridge.send = send
            bridge._heartbeat_loop()
            expected = [] if closing else ["host ping 123" if ready else "host begin 123"]
            self.assertEqual(commands, expected + ["status lite"])

    def test_gui_signal_failure_still_terminates_child(self):
        gui = object.__new__(config_gui.ConfigGUI)
        process = gui.main_process = Mock(pid=987)
        process.poll.return_value = None
        process.send_signal.side_effect = OSError("console vanished")
        with patch("config_gui.subprocess.run") as kill:
            gui.stop_main_process()
        kill.assert_called_once()

    def test_exit_cancels_handshake_without_waiting_for_crashed_owner(self):
        bridge = ESP32Bridge("test")
        bridge.request_shutdown()
        self.assertTrue(bridge._host_session_event.is_set())
        with patch("esp32_bridge.serial.Serial") as port:
            with self.assertRaisesRegex(Exception, "startup cancelled"):
                bridge.open()
        port.assert_not_called()

    def test_handshake_budget_covers_previous_lease_and_multiple_links(self):
        bridge = ESP32Bridge("test")
        bridge._host_session_event.wait = Mock(return_value=True)
        with patch("esp32_bridge.serial.Serial"), patch("esp32_bridge.threading.Thread"):
            bridge.open()
        self.assertGreaterEqual(bridge._host_session_event.wait.call_args.kwargs["timeout"], 12.0)


if __name__ == "__main__":
    unittest.main()
