"""Regression tests for the v0.8.2 race and startup review findings."""
import configparser
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import serial  # noqa: E402

import main as connector  # noqa: E402
from config_utils import DEFAULT_CONFIG_PATH, load_stick_calibration, store_stick_calibration  # noqa: E402
from esp32_bridge import ESP32Bridge  # noqa: E402
from xinput_controller import CalibrationResultPendingError  # noqa: E402


class _VanishingSerialBridge(ESP32Bridge):
    """Model close() clearing the port after the first read of self.serial."""

    def __init__(self, port):
        super().__init__("test")
        self._port = port
        self._serial_reads = 0

    @property
    def serial(self):
        self._serial_reads += 1
        return self._port if self._serial_reads == 1 else None

    @serial.setter
    def serial(self, value):
        self._port = value


class BridgeRumbleQueueTests(unittest.TestCase):
    def test_rumble_queue_reads_the_serial_port_once(self):
        bridge = _VanishingSerialBridge(Mock(is_open=True))
        bridge._serial_reads = 0
        bridge.running = True
        bridge.connected_channel = 1
        bridge._rumble_worker_running = True
        bridge._rumble_accepting = True

        self.assertTrue(bridge.send_pro_rumble_latest(160, 10, 320, 10))
        self.assertEqual(bridge._serial_reads, 1)


def _connector_mocks(mocks, *, config, mode="esp32", controller=None):
    values = {
        "ControllerCommandInbox": Mock(),
        "ControllerStatusPublisher": Mock(),
        "load_config": Mock(return_value=config),
        "load_stick_calibration": Mock(return_value=None),
        "reconcile_active_hidhide": Mock(return_value={}),
        "find_wired_controller": Mock(return_value=None),
        "find_esp32_port": Mock(return_value="COM5" if mode == "esp32" else None),
        "XInputController": Mock(), "SharedTestTelemetry": Mock(),
        "AudioHaptics": Mock(), "InputDispatcher": Mock(),
        "WiredController": Mock(), "BluetoothController": Mock(),
        "watch_parent_process_exit": Mock(), "print": Mock(),
    }
    for name, mock in values.items():
        mocks.enter_context(patch.object(connector, name, mock))

    class Bridge:
        """A real class keeps main's isinstance(controller, ESP32Bridge) valid."""

    mocks.enter_context(patch.object(Bridge, "__new__", return_value=controller))
    mocks.enter_context(patch.object(connector, "ESP32Bridge", Bridge))
    mocks.enter_context(patch.object(connector.signal, "signal"))
    return values


class ConnectorStartupErrorTests(unittest.TestCase):
    def setUp(self):
        self.config = configparser.ConfigParser()
        self.config.read_dict({"gui": {}, "serial": {}})

    def test_bridge_lost_during_startup_still_shows_the_error_prompt(self):
        bridge = Mock()

        def fail_handshake():
            # The heartbeat thread reports the failure before open() raises.
            bridge.bridge_disconnected_callback()
            raise serial.SerialException("ESP32 disconnected during startup")

        bridge.open.side_effect = fail_handshake
        with ExitStack() as mocks:
            mocks.enter_context(patch.dict(os.environ, {"S2P_GUI_PID": ""}))
            values = _connector_mocks(mocks, config=self.config, controller=bridge)
            prompt = mocks.enter_context(patch.object(connector, "input"))
            connector._run_connector()

        prompt.assert_called_once()
        printed = " ".join(str(call.args[0]) for call in values["print"].call_args_list if call.args)
        self.assertTrue(
            "disconnected during startup" in printed or "啟動期間中斷連線" in printed, printed,
        )
        self.assertNotIn("in use by another program", printed)

    def test_shutdown_request_during_open_still_skips_the_prompt(self):
        bridge = Mock()
        handler = {}

        def capture(signum, callback):
            handler["callback"] = callback

        def interrupted_open():
            handler["callback"]()
            raise serial.SerialException("ESP32 host session startup cancelled")

        bridge.open.side_effect = interrupted_open
        with ExitStack() as mocks:
            mocks.enter_context(patch.dict(os.environ, {"S2P_GUI_PID": ""}))
            _connector_mocks(mocks, config=self.config, controller=bridge)
            connector.signal.signal.side_effect = capture
            prompt = mocks.enter_context(patch.object(connector, "input"))
            connector._run_connector()

        prompt.assert_not_called()


class RuntimeStickFallbackTests(unittest.TestCase):
    def test_damaged_default_profile_falls_back_to_neutral_sticks(self):
        config = configparser.ConfigParser()
        config.read(DEFAULT_CONFIG_PATH, encoding="utf-8")
        store_stick_calibration(config, load_stick_calibration(config), "AABBCCDDEEFF")
        with tempfile.TemporaryDirectory() as tmp:
            broken = Path(tmp) / "System Default.ini"
            broken.write_text("[sticks]\nleft_center = broken\n", encoding="utf-8")
            with patch.object(connector, "DEFAULT_CONFIG_PATH", broken), \
                 patch.object(connector, "print") as printed:
                calibration = connector.load_runtime_stick_calibration(config, "001122334455")

        self.assertEqual(calibration, connector.neutral_stick_calibration())
        self.assertEqual(calibration, load_stick_calibration(config))
        printed.assert_called_once()


class ReloadAfterCalibrationTests(unittest.TestCase):
    def run_reload(self, reconfigure_effects):
        with tempfile.TemporaryDirectory() as tmp:
            config_path = Path(tmp) / "config.ini"
            config = configparser.ConfigParser()
            config.read(DEFAULT_CONFIG_PATH, encoding="utf-8")
            with config_path.open("w", encoding="utf-8") as stream:
                config.write(stream)

            controller = Mock(controller_id=None, is_ready=True)
            controller.open.side_effect = lambda: controller.connected_callback()
            xinput = Mock(mapping_layers=[])
            xinput.get_rumble_diagnostics.return_value = {}
            xinput.get_gyro_initialization_status.return_value = {
                "complete": True, "ready": True, "source": "saved",
            }
            for sensor in ("gyro", "magnetometer", "accelerometer"):
                getattr(xinput, f"get_{sensor}_calibration_status").return_value = {
                    "state": "idle", "message": "", "progress": 0, "spans": (0, 0, 0),
                }
                getattr(xinput, f"consume_{sensor}_calibration_result").return_value = None
            dispatcher = Mock()
            dispatcher.run_exclusive.side_effect = lambda callback, timeout: callback() or True
            publisher = Mock()
            request = SimpleNamespace(
                command="reload_settings", request_id="reload-1", connection_id=None,
                compensation_signature=None, clear_legacy=Mock(), finish=Mock(),
            )
            commands = iter([request])
            inbox = Mock(next=Mock(side_effect=lambda: next(commands, None)))
            connected = []

            def reconfigure(*_args, **_kwargs):
                if not connected:
                    connected.append(True)  # on_connected() reload
                    return None
                effect = next(reconfigure_effects)
                if effect is not None:
                    raise effect
                return None

            xinput.reconfigure.side_effect = reconfigure
            sleeps = iter(range(8))

            def sleep(_delay):
                if next(sleeps, None) is None:
                    raise KeyboardInterrupt

            with patch.multiple(
                connector,
                CONFIG_PATH=config_path,
                config_file_lock=nullcontext,
                find_wired_controller=Mock(return_value={}),
                WiredController=Mock(return_value=controller),
                XInputController=Mock(return_value=xinput),
                InputDispatcher=Mock(return_value=dispatcher),
                AudioHaptics=Mock(),
                SharedTestTelemetry=Mock(),
                ControllerCommandInbox=Mock(return_value=inbox),
                ControllerStatusPublisher=Mock(return_value=publisher),
                reconcile_active_hidhide=Mock(return_value={}),
                watch_parent_process_exit=Mock(),
                print=Mock(),
                input=Mock(side_effect=AssertionError("Unexpected startup prompt")),
            ), patch.object(connector.signal, "signal"), \
               patch.object(connector.time, "sleep", side_effect=sleep):
                connector._run_connector()

        states = [
            update.kwargs["settings_reload_state"]
            for update in publisher.publish.call_args_list
            if update.kwargs.get("settings_reload_request_id") == "reload-1"
        ]
        return states, request

    def test_reload_waits_for_a_finished_calibration_result_to_be_saved(self):
        pending = CalibrationResultPendingError("calibration result is waiting to be saved")
        states, request = self.run_reload(iter([pending, None]))

        self.assertEqual(states[-1], "success")
        self.assertNotIn("failed", states)
        request.finish.assert_called_once()

    def test_reload_still_fails_when_the_result_never_clears(self):
        pending = CalibrationResultPendingError("calibration result is waiting to be saved")
        states, request = self.run_reload(iter([pending] * (connector.RELOAD_RETRY_LIMIT + 1)))

        self.assertEqual(states[-1], "failed")
        self.assertNotIn("success", states)
        request.finish.assert_called_once()


if __name__ == "__main__":
    unittest.main()
