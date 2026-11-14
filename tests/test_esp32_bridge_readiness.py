import json
import threading
import unittest
from unittest.mock import patch

from esp32_bridge import ESP32Bridge, SERIAL_RECEIVE_BUFFER_LIMIT


class ESP32BridgeReadinessTests(unittest.TestCase):
    def test_delayed_scan_worker_cannot_restart_after_another_connection_cycle(self):
        bridge = ESP32Bridge("COM5")
        bridge.running = True
        sent = []
        bridge.send = lambda command: sent.append(command) or True

        with patch("esp32_bridge.threading.Thread") as thread:
            bridge._restart_scan(delay=0)
            stale_worker = thread.call_args.kwargs["target"]
            bridge._connection_generation += 1
            bridge._restart_scan(delay=0)
            current_worker = thread.call_args.kwargs["target"]

        stale_worker()
        self.assertEqual(sent, [])
        current_worker()
        self.assertEqual(sent, ["scan on"])

    def test_missing_connection_result_releases_attempt_when_status_still_responds(self):
        bridge = ESP32Bridge("COM5")
        bridge.running = True
        bridge.esp32_mac_value = 0x112233445566
        commands = []
        bridge.send = lambda command: commands.append(command) or True
        bridge._restart_scan = lambda: commands.append("restart")
        scan = b'{"cmd":"scan_result","mac":"AA:BB:CC:DD:EE:FF"}'
        status = b'{"cmd":"status","ble_channels":0}'

        with (
            patch.object(bridge, "_parse_reconnect_mac", return_value=0),
            patch("esp32_bridge.time.monotonic", return_value=100) as clock,
        ):
            bridge._handle_text(scan)
            self.assertTrue(bridge._connecting)
            clock.return_value = 111
            bridge._handle_text(status)
            self.assertTrue(bridge._connecting)
            clock.return_value = 120
            bridge._handle_text(b'{"cmd":"status","ble_channels":4}')
            self.assertTrue(bridge._connecting)
            bridge._handle_text(status)
            bridge._handle_text(status)
            self.assertFalse(bridge._connecting)
            self.assertFalse(bridge._pending_pair)
            self.assertIsNone(bridge.controller_id)
            self.assertEqual(commands, ["scan off", "conn 0 AA:BB:CC:DD:EE:FF", "link status", "restart"])
            bridge._handle_text(scan)
            self.assertTrue(bridge._connecting)
            self.assertEqual(commands[-2:], ["scan off", "conn 0 AA:BB:CC:DD:EE:FF"])

    def test_missing_connected_notice_recovers_only_the_selected_ready_link(self):
        bridge = ESP32Bridge("COM5")
        bridge.running = True
        bridge.esp32_mac_value = 0x112233445566
        commands = []
        bridge.send = lambda command: commands.append(command) or True
        scan = b'{"cmd":"scan_result","mac":"AA:BB:CC:DD:EE:FF"}'
        status = b'{"cmd":"status","ble_channels":12}'
        links = [
            [2, "11:22:33:44:55:66", 1, 7.5, -50, 0],
            [3, "aa:bb:cc:dd:ee:ff", 0, 7.5, -50, 0],
        ]

        def report_links():
            bridge._handle_text(json.dumps({"cmd": "link_status", "ok": 1, "links": links}).encode())

        with (
            patch.object(bridge, "_parse_reconnect_mac", return_value=0),
            patch("esp32_bridge.time.monotonic", return_value=100) as clock,
            patch("esp32_bridge.threading.Thread") as thread,
        ):
            bridge._handle_text(scan)
            bridge._handle_text(status)
            self.assertNotIn("link status", commands)
            clock.return_value = 120
            bridge._handle_text(status)
            bridge._handle_text(status)
            self.assertEqual(commands.count("link status"), 1)
            report_links()
            self.assertTrue(bridge._connecting)
            thread.assert_not_called()
            clock.return_value = 122
            bridge._handle_text(status)
            self.assertEqual(commands.count("link status"), 2)
            links[1][2] = 1
            report_links()
            bridge._handle_text(b'{"cmd":"connected","channel":3,"mac":"AA:BB:CC:DD:EE:FF"}')
            self.assertFalse(bridge._connecting)
            self.assertEqual(bridge.connected_channel, 3)
            self.assertFalse(bridge.is_ready)
            thread.assert_called_once()
            self.assertEqual(thread.call_args.kwargs["args"], (3, bridge._connection_generation, True))

    def test_unrelated_ready_link_does_not_block_retry_after_timeout(self):
        bridge = ESP32Bridge("COM5")
        bridge.running = True
        bridge._connecting = True
        bridge.controller_id = "AA:BB:CC:DD:EE:FF"
        bridge._connect_result_deadline = 115
        commands = []
        bridge.send = lambda command: commands.append(command) or True
        bridge._restart_scan = lambda: commands.append("restart")

        with patch("esp32_bridge.time.monotonic", return_value=120):
            bridge._handle_text(b'{"cmd":"status","ble_channels":4}')
            bridge._handle_text(b'{"cmd":"link_status","ok":1,"links":[[2,"11:22:33:44:55:66",1]]}')
        self.assertFalse(bridge._connecting)
        self.assertIsNone(bridge.connected_channel)
        self.assertEqual(commands, ["link status", "restart"])

    def test_status_without_a_valid_channel_mask_does_not_drop_ready_link(self):
        bridge = ESP32Bridge("COM5")
        bridge.running = True
        bridge.connected_channel = bridge._ready_channel = 2
        bridge._restart_scan = lambda: None
        bridge._channel_missing_count = 2
        for status in (b'{"cmd":"status"}', b'{"cmd":"status","ble_channels":null}',
                       b'{"cmd":"status","ble_channels":false}'):
            bridge._handle_text(status)
            self.assertTrue(bridge.is_ready)
            self.assertEqual(bridge._channel_missing_count, 2)

    def test_idle_disconnect_does_not_send_a_command_after_link_changes(self):
        bridge = self.make_bridge()
        bridge._ready_channel = 2
        bridge._rumble_accepting = True
        bridge._send_final_zero_rumble = lambda **_kwargs: True
        sent = []
        bridge.send = lambda command: sent.append(command) or False

        def reconnect(_delay):
            bridge._connection_generation += 1
            bridge._rumble_accepting = True

        with patch("esp32_bridge.time.sleep", side_effect=reconnect):
            self.assertFalse(bridge.disconnect_for_idle())
        self.assertEqual(sent, [])
        self.assertTrue(bridge.is_ready)
        self.assertTrue(bridge._rumble_accepting)

    def test_idle_disconnect_does_not_confirm_after_the_bridge_is_lost(self):
        bridge = self.make_bridge()
        bridge._ready_channel = 2
        bridge._send_final_zero_rumble = lambda **_kwargs: True

        def send(_command):
            bridge.running = False
            bridge.connected_channel = bridge._ready_channel = None
            bridge._connection_generation += 1
            bridge._disconnect_event.set()
            return True

        bridge.send = send
        with patch("esp32_bridge.time.sleep"):
            self.assertFalse(bridge.disconnect_for_idle())

    def test_duplicate_disconnect_does_not_cancel_the_next_attempt(self):
        bridge = self.make_bridge()
        bridge.connected_channel = None
        bridge._connecting = True
        bridge._handle_text(b'{"cmd":"disconnected"}')
        self.assertTrue(bridge._connecting)
        self.assertEqual(bridge._connection_generation, 7)
        self.assertFalse(bridge._disconnect_event.is_set())

    def test_old_rumble_submission_does_not_replace_new_connection_output(self):
        bridge = ESP32Bridge("COM5")
        bridge.running = True
        bridge.serial = type("Serial", (), {"is_open": True})()
        bridge.connected_channel = bridge._ready_channel = 2
        bridge._connection_generation = 7
        bridge._rumble_accepting = bridge._rumble_worker_running = True
        old_submission = threading.Event()
        resume = threading.Event()

        def timestamp():
            if threading.current_thread() is submitter:
                old_submission.set()
                if not resume.wait(2):
                    raise AssertionError("Timed out waiting to resume rumble submission")
            return 1.0

        results = []
        submitter = threading.Thread(
            target=lambda: results.append(bridge.send_pro_rumble_latest(80, 500, 160, 500)),
        )
        with patch("esp32_bridge.time.perf_counter", side_effect=timestamp):
            submitter.start()
            try:
                self.assertTrue(old_submission.wait(1))
                with bridge._state_lock:
                    bridge._connection_generation += 1
                self.assertTrue(bridge.send_pro_rumble_latest(80, 700, 160, 700))
                pending = bridge._rumble_pending
            finally:
                resume.set()
                submitter.join(2)
        self.assertEqual(results, [False])
        self.assertIs(bridge._rumble_pending, pending)

    def test_unrelated_or_delayed_connection_failure_keeps_ready_link(self):
        bridge = self.make_bridge()
        bridge._ready_channel = 2
        bridge._rumble_accepting = True
        restarts = []
        bridge._restart_scan = lambda: restarts.append(True)

        for mac in ("11:22:33:44:55:66", bridge.controller_id):
            bridge._handle_text(json.dumps({"cmd": "connect_fail", "mac": mac}).encode())
            self.assertTrue(bridge.is_ready)
            self.assertEqual(bridge._connection_generation, 7)
            self.assertTrue(bridge._rumble_accepting)
            self.assertFalse(bridge._disconnect_event.is_set())
        self.assertEqual(restarts, [])

    def test_connection_events_only_affect_the_selected_attempt(self):
        bridge = self.make_bridge()
        bridge.connected_channel = None
        bridge._connecting = True
        bridge._pending_pair = True
        restarts = []
        bridge._restart_scan = lambda: restarts.append(True)

        with patch("esp32_bridge.threading.Thread") as thread:
            bridge._handle_text(b'{"cmd":"connected","channel":3,"mac":"11:22:33:44:55:66"}')
        thread.assert_not_called()
        bridge._handle_text(b'{"cmd":"connect_fail","mac":"11:22:33:44:55:66"}')
        self.assertIsNone(bridge.connected_channel)
        self.assertTrue(bridge._connecting)
        self.assertEqual(restarts, [])

        bridge._handle_text(b'{"cmd":"connect_fail","mac":"aa:bb:cc:dd:ee:ff"}')
        self.assertFalse(bridge._connecting)
        self.assertFalse(bridge._pending_pair)
        self.assertIsNone(bridge.controller_id)
        self.assertEqual(restarts, [True])

    def test_scan_ignores_unclassified_advertisements_and_recovers_from_failed_write(self):
        bridge = ESP32Bridge("COM5")
        bridge.running = True
        commands = []
        bridge.send = lambda command: commands.append(command) or not command.startswith("conn ")
        bridge._restart_scan = lambda: commands.append("restart")
        scan = json.dumps({"cmd": "scan_result", "mac": "AA:BB:CC:DD:EE:FF", "data": ""}).encode()
        bridge._handle_text(scan)
        self.assertEqual(commands, [])
        with patch.object(bridge, "_parse_reconnect_mac", return_value=0):
            bridge._handle_text(scan)
            self.assertEqual(commands, [])
            bridge.esp32_mac_value = 0x112233445566
            with patch("esp32_bridge.print") as output:
                bridge._handle_text(scan)
        self.assertEqual(commands, ["scan off", "conn 0 AA:BB:CC:DD:EE:FF", "restart"])
        self.assertFalse(bridge._connecting)
        self.assertFalse(bridge._pending_pair)
        self.assertIsNone(bridge.controller_id)
        self.assertEqual(bridge.pairing_diagnostics["reconnect_mac"], "000000000000")
        self.assertNotIn("000000000000", str(output.call_args_list))

    def test_foreign_host_notice_does_not_block_a_later_sync_advertisement(self):
        bridge = ESP32Bridge("COM5")
        bridge.running = True
        bridge.esp32_mac_value = 0x112233445566
        commands = []
        bridge.send = lambda command: commands.append(command) or True
        scan = json.dumps({"cmd": "scan_result", "mac": "AA:BB:CC:DD:EE:FF"}).encode()
        with (patch.object(bridge, "_parse_reconnect_mac", side_effect=(0x223344556677, 0x223344556677, 0)),
              patch("esp32_bridge.print") as output):
            bridge._handle_text(scan)
            bridge._handle_text(scan)
            self.assertEqual(commands, [])
            bridge._handle_text(scan)
        self.assertEqual(commands, ["scan off", "conn 0 AA:BB:CC:DD:EE:FF"])
        self.assertTrue(bridge._pending_pair)
        self.assertEqual(output.call_count, 3)

    def make_bridge(self):
        bridge = ESP32Bridge.__new__(ESP32Bridge)
        bridge.running = True
        bridge._closing = False
        bridge._state_lock = threading.RLock()
        bridge.connected_channel = 2
        bridge._ready_channel = None
        bridge._connection_generation = 7
        bridge._connecting = False
        bridge.controller_id = "AA:BB:CC:DD:EE:FF"
        bridge._rumble_condition = threading.Condition()
        bridge._rumble_accepting = False
        bridge._rumble_pending = None
        bridge._command_response_lock = threading.Lock()
        bridge._command_wait_lock = threading.Lock()
        bridge._command_response_event = threading.Event()
        bridge._command_response = None
        bridge._disconnect_event = threading.Event()
        bridge._last_input_time = 0.0
        bridge.input_callback = None
        bridge.connected_callback = None
        bridge.send = lambda _command: True
        bridge._restart_scan = lambda: None
        bridge.connection_rumble = lambda **_kwargs: None
        return bridge

    @patch("esp32_bridge.threading.Thread")
    def test_ble_connected_event_does_not_report_ready_immediately(
        self,
        thread_factory,
    ):
        bridge = self.make_bridge()
        bridge.connected_channel = None
        bridge._connecting = True
        events = []
        bridge._channel_missing_count = 0
        bridge._status_grace_until = 0.0
        bridge._pending_pair = False
        bridge.connected_callback = lambda: events.append("connected")

        notice = b'{"cmd":"connected","channel":2,"mac":"aa:bb:cc:dd:ee:ff"}\n'
        bridge._handle_text(notice)
        bridge._handle_text(notice)

        self.assertEqual(events, [])
        self.assertEqual(bridge.connected_channel, 2)
        self.assertEqual(bridge._connection_generation, 8)
        self.assertIsNone(bridge._ready_channel)
        self.assertFalse(bridge._rumble_accepting)
        thread_factory.assert_called_once()
        self.assertEqual(
            thread_factory.call_args.kwargs["target"],
            bridge._prepare_connected_controller,
        )

    @patch("esp32_bridge.time.sleep")
    def test_ready_callback_runs_only_after_initialization(self, _sleep):
        bridge = self.make_bridge()
        events = []
        bridge.initialize_controller_features = lambda _channel, _generation: (
            events.append("initialize") or True
        )
        bridge.connected_callback = lambda: events.append(
            ("connected", bridge.is_ready)
        )
        bridge.connection_rumble = (
            lambda **_kwargs: events.append("rumble")
        )

        bridge._prepare_connected_controller(2, 7, False)

        self.assertEqual(
            events,
            ["initialize", ("connected", False), "rumble"],
        )
        self.assertEqual(bridge._ready_channel, 2)
        self.assertTrue(bridge._rumble_accepting)

    def test_initialization_failure_disconnects_without_ready_callback(self):
        bridge = self.make_bridge()
        events = []
        bridge.initialize_controller_features = (
            lambda _channel, _generation: False
        )
        bridge.connected_callback = lambda: events.append("connected")
        bridge.send = lambda command: events.append(command) or True
        bridge._restart_scan = lambda: events.append("restart_scan")

        bridge._prepare_connected_controller(2, 7, False)

        self.assertNotIn("connected", events)
        self.assertEqual(events, ["disc 2", "restart_scan"])
        self.assertIsNone(bridge.connected_channel)
        self.assertIsNone(bridge._ready_channel)
        self.assertFalse(bridge._rumble_accepting)

    @patch("esp32_bridge.time.sleep")
    def test_stale_generation_never_reports_ready(self, _sleep):
        bridge = self.make_bridge()
        events = []
        bridge.initialize_controller_features = (
            lambda _channel, _generation: True
        )
        bridge.connected_callback = lambda: events.append("connected")

        bridge._prepare_connected_controller(2, 6, False)

        self.assertEqual(events, [])
        self.assertIsNone(bridge._ready_channel)

    def test_stale_pairing_preparation_sends_no_pairing_command(self):
        bridge = self.make_bridge()
        events = []
        bridge.pair_controller_to_esp32 = (
            lambda _channel, _generation: events.append("pair") or True
        )

        bridge._prepare_connected_controller(2, 6, True)

        self.assertEqual(events, [])

    def test_disconnect_immediately_after_final_check_blocks_callback(self):
        bridge = self.make_bridge()
        events = []
        bridge.initialize_controller_features = (
            lambda _channel, _generation: True
        )
        bridge.connected_callback = lambda: events.append("connected")
        original_check = bridge._connection_is_current
        check_count = 0

        def disconnect_after_final_check(channel, generation):
            nonlocal check_count
            check_count += 1
            current = original_check(channel, generation)
            if check_count == 3 and current:
                with bridge._state_lock:
                    bridge._connection_generation += 1
            return current

        bridge._connection_is_current = disconnect_after_final_check

        bridge._prepare_connected_controller(2, 7, False)

        self.assertEqual(events, [])
        self.assertFalse(bridge.is_ready)

    def test_command_ack_is_accepted_before_ready(self):
        bridge = self.make_bridge()
        response = b"\x15\x91\x01\x04"

        result = bridge._handle_binary(b"\x83" + response)

        self.assertIsNone(result)
        self.assertEqual(bridge._command_response, (7, response))
        self.assertTrue(bridge._command_response_event.is_set())

    def test_ordinary_input_is_blocked_before_ready(self):
        bridge = self.make_bridge()
        received = []
        bridge.input_callback = received.append

        result = bridge._handle_binary(b"\x03input")

        self.assertIsNone(result)
        self.assertEqual(received, [])
        self.assertEqual(bridge._last_input_time, 0.0)

    def test_ordinary_input_is_dispatched_after_ready(self):
        bridge = self.make_bridge()
        received = []
        bridge._ready_channel = 2
        bridge.input_callback = received.append

        result = bridge._handle_binary(b"\x03input")

        self.assertEqual(result, b"input")
        self.assertEqual(received, [b"input"])
        self.assertGreater(bridge._last_input_time, 0.0)

    def test_input_before_disconnect_in_same_cdc_read_is_discarded(self):
        bridge = self.make_bridge()
        bridge._ready_channel = 2
        bridge._channel_missing_count = 0
        bridge._disconnect_event = threading.Event()
        bridge.disconnected_callback = None
        bridge._restart_scan = lambda: None
        received = []
        bridge.input_callback = received.append
        data = b"\x03input"
        stream = (
            b"\xaa\x55"
            + bytes([len(data)])
            + data
            + b'{"cmd":"disconnected"}\n'
        )

        class SerialChunk:
            def __init__(self):
                self.remaining = bytearray(stream)
                self.reads = 0

            @property
            def in_waiting(self):
                return len(self.remaining)

            def read(self, size):
                self.reads += 1
                if not self.remaining:
                    bridge.running = False
                    return b""
                result = bytes(self.remaining[:size])
                del self.remaining[:size]
                return result

        bridge.serial = SerialChunk()

        bridge._read_loop()

        self.assertEqual(received, [])


class ESP32BridgeReceiveBufferTests(unittest.TestCase):
    def test_corrupt_receive_buffer_is_bounded_and_resynchronized(self):
        tail = b"\xaa\x55\x03abc"
        buf = bytearray(
            b"x" * (SERIAL_RECEIVE_BUFFER_LIMIT + 32) + tail
        )

        ESP32Bridge._limit_receive_buffer(buf)

        self.assertEqual(buf, tail)
        self.assertLessEqual(len(buf), SERIAL_RECEIVE_BUFFER_LIMIT)

    def test_partial_packet_header_is_preserved(self):
        buf = bytearray(
            b"x" * (SERIAL_RECEIVE_BUFFER_LIMIT + 1) + b"\xaa"
        )

        ESP32Bridge._limit_receive_buffer(buf)

        self.assertEqual(buf, b"\xaa")

    def test_unframed_corrupt_data_is_discarded(self):
        buf = bytearray(b"x" * (SERIAL_RECEIVE_BUFFER_LIMIT + 1))

        ESP32Bridge._limit_receive_buffer(buf)

        self.assertEqual(buf, b"")


if __name__ == "__main__":
    unittest.main()
