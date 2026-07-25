import asyncio
import unittest
from unittest.mock import patch

from bluetooth_controller import BluetoothController
from esp32_bridge import ESP32Bridge
from rumble_protocol import (
    CONNECTION_FEEDBACK_PATTERN,
    CONNECTION_HF_FREQUENCY,
    CONNECTION_LF_FREQUENCY,
)
from wired_controller import WiredController, _rumble_active


class _HidDevice:
    def __init__(self, controller):
        self.controller = controller
        self.reports = []
        self.normal_results = []

    def write(self, report):
        if not self.reports:
            self.normal_results.append(
                self.controller.send_pro_rumble(90, 300, 180, 300)
            )
        self.reports.append(bytes(report))
        return len(report)


class FeedbackRumbleOverrideTests(unittest.TestCase):
    @patch("wired_controller.time.sleep")
    def test_wired_fixed_cue_cannot_be_overwritten_by_audio(self, _sleep):
        controller = WiredController()
        controller.running = True
        controller.connected = True
        controller._rumble_accepting = True
        device = _HidDevice(controller)
        controller._device = device

        self.assertTrue(
            controller._play_fixed_feedback(
                CONNECTION_FEEDBACK_PATTERN,
                CONNECTION_LF_FREQUENCY,
                CONNECTION_HF_FREQUENCY,
            )
        )

        self.assertEqual(device.normal_results, [False])
        self.assertEqual(
            sum(_rumble_active(report) for report in device.reports),
            2,
        )
        self.assertFalse(_rumble_active(device.reports[-1]))
        self.assertTrue(
            controller.send_pro_rumble(90, 300, 180, 300)
        )

    @patch("esp32_bridge.time.sleep")
    def test_esp32_fixed_cue_cannot_be_overwritten_by_audio(self, _sleep):
        bridge = ESP32Bridge("TEST")
        bridge.running = True
        bridge.connected_channel = 0
        bridge._ready_channel = 0
        bridge._connection_generation = 1
        bridge._rumble_worker_running = True
        bridge._rumble_accepting = True
        bridge.serial = type("Serial", (), {"is_open": True})()
        commands = []
        normal_results = []

        def send(command):
            if not commands:
                normal_results.append(
                    bridge.send_pro_rumble_latest(90, 300, 180, 300)
                )
            commands.append(command)
            return True

        bridge.send = send

        self.assertTrue(
            bridge._play_fixed_feedback(
                CONNECTION_FEEDBACK_PATTERN,
                CONNECTION_LF_FREQUENCY,
                CONNECTION_HF_FREQUENCY,
            )
        )

        self.assertEqual(normal_results, [False])
        self.assertEqual(len(commands), 5)
        self.assertTrue(
            bridge.send_pro_rumble_latest(90, 300, 180, 300)
        )


class BluetoothFeedbackOverrideTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_ble_fixed_cue_cannot_be_overwritten_by_audio(self):
        controller = BluetoothController()
        controller.running = True
        controller.connected = True
        controller._application_ready = True
        controller._rumble_accepting = True
        controller._connection_generation = 1
        controller._loop = asyncio.get_running_loop()
        controller._feedback_lock = asyncio.Lock()
        controller.client = type("Client", (), {"is_connected": True})()
        sent = []
        normal_results = []

        async def send_now(lf_freq, lf_amp, hf_freq, hf_amp):
            if not sent:
                normal_results.append(
                    controller.send_pro_rumble(90, 300, 180, 300)
                )
            sent.append((lf_freq, lf_amp, hf_freq, hf_amp))
            return True

        async def no_sleep(_duration):
            return None

        controller._send_pro_rumble_async = send_now
        with patch(
            "bluetooth_controller.asyncio.sleep",
            new=no_sleep,
        ):
            self.assertTrue(
                await controller._play_fixed_feedback_async(
                    CONNECTION_FEEDBACK_PATTERN,
                    CONNECTION_LF_FREQUENCY,
                    CONNECTION_HF_FREQUENCY,
                    1,
                )
            )

        self.assertEqual(normal_results, [False])
        self.assertEqual(
            [(lf_amp, hf_amp) for _, lf_amp, _, hf_amp in sent[:-1]],
            [(lf_amp, hf_amp) for lf_amp, hf_amp, _ in CONNECTION_FEEDBACK_PATTERN],
        )
        self.assertEqual(sent[-1][1::2], (0, 0))
        self.assertTrue(
            controller.send_pro_rumble(90, 300, 180, 300)
        )


if __name__ == "__main__":
    unittest.main()
