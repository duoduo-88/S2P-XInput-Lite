import sys
import threading
import unittest
from pathlib import Path
from unittest import mock


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from bluetooth_controller import BluetoothController
from esp32_bridge import ESP32Bridge
from wired_controller import WiredController


class _FakeFuture:
    def __init__(self):
        self.callback = None

    def add_done_callback(self, callback):
        self.callback = callback

    @staticmethod
    def result():
        return None


class _FakeLoop:
    @staticmethod
    def is_running():
        return True


class _FakeHidDevice:
    def __init__(self):
        self.reports = []

    def write(self, report):
        self.reports.append(bytes(report))
        return len(report)


class BatteryLedTransportTests(unittest.TestCase):
    def test_esp32_command_contains_requested_led_mask(self):
        bridge = ESP32Bridge.__new__(ESP32Bridge)
        bridge._state_lock = threading.RLock()
        bridge.connected_channel = 2
        commands = []
        bridge.send = lambda command: commands.append(command) or True

        self.assertTrue(bridge.set_player_led_mask(0x0F))

        payload = bytes.fromhex(commands[0].split()[-1])
        self.assertEqual(payload[:8], bytes.fromhex("09 91 01 07 00 08 00 00"))
        self.assertEqual(payload[8:], bytes.fromhex("0f 00 00 00 00 00 00 00"))

    def test_bluetooth_schedules_led_command_once_per_mask(self):
        controller = BluetoothController()
        controller.connected = True
        controller._application_ready = True
        controller._connection_generation = 4
        controller._loop = _FakeLoop()

        async def fake_write(*_args, **_kwargs):
            return b""

        controller._write_command = mock.Mock(side_effect=fake_write)
        scheduled = _FakeFuture()
        with mock.patch(
            "bluetooth_controller.asyncio.run_coroutine_threadsafe",
            return_value=scheduled,
        ) as submit:
            self.assertFalse(controller.set_player_led_mask(0x07))
            self.assertFalse(controller.set_player_led_mask(0x07))
            self.assertFalse(controller.set_player_led_mask(0x0F))
            scheduled.callback(scheduled)
            self.assertTrue(controller.set_player_led_mask(0x07))

        controller._write_command.assert_called_once_with(
            0x09,
            0x07,
            bytes.fromhex("07 00 00 00 00 00 00 00"),
            generation=4,
        )
        submit.assert_called_once()
        submit.call_args.args[0].close()

    def test_wired_report_contains_requested_led_mask(self):
        controller = WiredController.__new__(WiredController)
        controller._hid_write_lock = threading.Lock()
        controller._state_lock = threading.RLock()
        controller._device_lock = threading.Lock()
        controller.connected = True
        controller._device = _FakeHidDevice()

        self.assertTrue(controller.set_player_led_mask(0x03))

        report = controller._device.reports[0]
        self.assertEqual(report[0], 0x02)
        self.assertEqual(report[1:9], bytes.fromhex("09 91 00 07 00 08 00 00"))
        self.assertEqual(report[9], 0x03)


if __name__ == "__main__":
    unittest.main()
