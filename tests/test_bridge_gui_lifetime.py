import configparser
import threading
import os
import subprocess
import sys
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import runtime_cleanup


@unittest.skipUnless(os.name == "nt", "Windows process wait API")
class BridgeGuiLifetimeTests(unittest.TestCase):
    def test_connector_stops_if_gui_exits_during_startup(self):
        import main as connector

        for mode in ("wired", "bluetooth", "esp32"):
            with self.subTest(mode=mode), ExitStack() as mocks:
                callback = None

                def watch(_pid, on_exit):
                    nonlocal callback
                    callback = on_exit

                def start_audio():
                    if callback is not None:
                        callback()

                config = configparser.ConfigParser()
                config.read_dict({"gui": {}, "serial": {}})
                mocks.enter_context(patch.dict(os.environ, {"S2P_GUI_PID": "123"}))
                mocks.enter_context(patch.object(connector.signal, "signal"))
                values = {
                    "ControllerCommandInbox": Mock(),
                    "ControllerStatusPublisher": Mock(),
                    "load_config": Mock(return_value=config),
                    "load_stick_calibration": Mock(return_value=None),
                    "reconcile_active_hidhide": Mock(return_value={}),
                    "find_wired_controller": Mock(return_value={} if mode == "wired" else None),
                    "find_esp32_port": Mock(return_value="COM5" if mode == "esp32" else None),
                    "XInputController": Mock(), "SharedTestTelemetry": Mock(),
                    "AudioHaptics": Mock(), "InputDispatcher": Mock(),
                    "WiredController": Mock(), "BluetoothController": Mock(),
                    "watch_parent_process_exit": Mock(side_effect=watch), "print": Mock(),
                }
                for name, mock in values.items():
                    mocks.enter_context(patch.object(connector, name, mock))
                if mode == "esp32":
                    class Bridge:
                        def __init__(self, *_args):
                            self.open = Mock()
                            self.close = Mock()
                            self.request_shutdown = Mock()
                            self.send_pro_rumble_latest = Mock()

                    mocks.enter_context(patch.object(connector, "ESP32Bridge", Bridge))
                connector.AudioHaptics.return_value.start.side_effect = start_audio
                if mode == "esp32":
                    bridge = Bridge()
                    bridge.open.side_effect = AssertionError("Transport opened after GUI exit")
                    bridge_factory = mocks.enter_context(patch.object(Bridge, "__new__", return_value=bridge))
                    controller = bridge
                else:
                    transport = connector.WiredController if mode == "wired" else connector.BluetoothController
                    controller = transport.return_value
                    controller.open.side_effect = AssertionError("Transport opened after GUI exit")
                mocks.enter_context(patch.object(connector, "input", side_effect=AssertionError("Waited on console input")))
                connector._run_connector()
                connector.watch_parent_process_exit.assert_called_once()
                controller.open.assert_not_called()
                controller.close.assert_called_once()
                connector.AudioHaptics.return_value.close.assert_called_once()
                if mode == "esp32":
                    bridge_factory.assert_called_once()
                    bridge.request_shutdown.assert_called_once()

    def test_gui_exit_releases_connector_waiting_at_startup_error_prompt(self):
        import main as connector

        callback = None

        def watch(_pid, on_exit):
            nonlocal callback
            callback = on_exit

        config = configparser.ConfigParser()
        config.read_dict({"serial": {"baudrate": "invalid"}})
        with (
            patch.dict(os.environ, {"S2P_GUI_PID": "123"}),
            patch.object(connector.signal, "signal"),
            patch.object(connector, "watch_parent_process_exit", side_effect=watch),
            patch.object(connector, "ControllerCommandInbox"),
            patch.object(connector, "ControllerStatusPublisher"),
            patch.object(connector, "load_config", return_value=config),
            patch.object(connector, "print"),
            patch.object(connector, "input", side_effect=AssertionError("Used blocking input")),
            patch.object(connector, "write_interactive_prompt") as prompt,
            patch.object(connector.sys, "stdin", Mock(isatty=Mock(return_value=True))),
            patch("msvcrt.kbhit", return_value=False),
            patch.object(connector.time, "sleep", side_effect=lambda _delay: callback()) as sleep,
        ):
            connector._run_connector()

        prompt.assert_called_once()
        sleep.assert_called_once_with(0.05)

    def test_gui_process_exit_requests_child_shutdown_and_closes_handle(self):
        callback = threading.Event()
        kernel = Mock()
        kernel.OpenProcess.return_value = 456
        kernel.WaitForSingleObject.return_value = 0
        with patch("runtime_cleanup.ctypes.WinDLL", return_value=kernel):
            watcher = runtime_cleanup.watch_parent_process_exit(123, callback.set)
            watcher.join(1.0)
        self.assertTrue(callback.is_set())
        kernel.OpenProcess.assert_called_once_with(0x00100000, False, 123)
        kernel.WaitForSingleObject.assert_called_once_with(456, 0xFFFFFFFF)
        kernel.CloseHandle.assert_called_once_with(456)

    def test_no_gui_pid_does_not_create_watcher(self):
        with patch("runtime_cleanup.ctypes.WinDLL") as api:
            self.assertIsNone(runtime_cleanup.watch_parent_process_exit(0, Mock()))
        api.assert_not_called()

    def test_failed_wait_does_not_claim_parent_has_exited(self):
        callback = Mock()
        kernel = Mock()
        kernel.OpenProcess.return_value = 456
        kernel.WaitForSingleObject.return_value = 0xFFFFFFFF
        with patch("runtime_cleanup.ctypes.WinDLL", return_value=kernel):
            watcher = runtime_cleanup.watch_parent_process_exit(123, callback)
            watcher.join(1.0)
        callback.assert_not_called()
        kernel.CloseHandle.assert_called_once_with(456)

    def test_parent_already_gone_before_child_opens_handle(self):
        kernel = Mock()
        kernel.OpenProcess.return_value = 0
        callback = Mock()
        with patch("runtime_cleanup.ctypes.WinDLL", return_value=kernel), \
             patch("runtime_cleanup.ctypes.get_last_error", return_value=87):
            self.assertIsNone(runtime_cleanup.watch_parent_process_exit(123, callback))
        callback.assert_called_once()

    def test_real_windows_process_termination_wakes_waiter(self):
        exited = threading.Event()
        process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            watcher = runtime_cleanup.watch_parent_process_exit(process.pid, exited.set)
            self.assertIsNotNone(watcher)
            self.assertFalse(exited.is_set())
            process.terminate()
            process.wait(timeout=3)
            self.assertTrue(exited.wait(3))
            watcher.join(1)
            self.assertFalse(watcher.is_alive())
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)


if __name__ == "__main__":
    unittest.main()
