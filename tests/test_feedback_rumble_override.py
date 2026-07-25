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
    def make_ready_esp32_bridge(self):
        bridge = ESP32Bridge("TEST")
        bridge.running = True
        bridge.connected_channel = 0
        bridge._ready_channel = 0
        bridge._connection_generation = 1
        bridge._rumble_worker_running = True
        bridge._rumble_accepting = True
        bridge.serial = type("Serial", (), {"is_open": True})()
        return bridge

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
        bridge = self.make_ready_esp32_bridge()
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

    @patch("esp32_bridge.time.sleep")
    def test_esp32_old_feedback_cannot_cross_generation(self, _sleep):
        bridge = self.make_ready_esp32_bridge()
        commands = []

        def send(command):
            commands.append(command)
            bridge._connection_generation = 2
            return True

        bridge.send = send

        self.assertFalse(bridge._play_fixed_feedback(
            CONNECTION_FEEDBACK_PATTERN,
            CONNECTION_LF_FREQUENCY,
            CONNECTION_HF_FREQUENCY,
        ))
        self.assertEqual(len(commands), 1)

    @patch("esp32_bridge.threading.Thread")
    def test_esp32_failed_connection_cue_does_not_run_ready_callback(
        self,
        thread_factory,
    ):
        bridge = self.make_ready_esp32_bridge()
        callbacks = []
        bridge.calibration_mode = True
        bridge.ready_callback = lambda: callbacks.append("ready")
        bridge._play_fixed_feedback = lambda *_args: False

        self.assertTrue(bridge.connection_rumble(expected_generation=1))
        thread_factory.assert_called_once()

        thread_factory.call_args.kwargs["target"]()

        self.assertEqual(callbacks, [])

    @patch("wired_controller.time.sleep")
    def test_wired_old_feedback_cannot_cross_generation(self, _sleep):
        controller = WiredController()
        controller.running = True
        controller.connected = True
        controller._rumble_accepting = True

        class Device:
            def __init__(self):
                self.reports = []

            def write(inner_self, report):
                inner_self.reports.append(bytes(report))
                controller._connection_generation += 1
                return len(report)

        device = Device()
        controller._device = device

        self.assertFalse(controller._play_fixed_feedback(
            CONNECTION_FEEDBACK_PATTERN,
            CONNECTION_LF_FREQUENCY,
            CONNECTION_HF_FREQUENCY,
        ))
        self.assertEqual(len(device.reports), 1)

    @patch("wired_controller.time.sleep")
    def test_only_latest_wired_feedback_reservation_runs(self, _sleep):
        controller = WiredController()
        controller.running = True
        controller.connected = True
        controller._rumble_accepting = True
        device = _HidDevice(controller)
        controller._device = device
        older = controller._reserve_feedback()
        latest = controller._reserve_feedback()

        self.assertIsNotNone(older)
        self.assertIsNotNone(latest)
        self.assertFalse(controller._play_fixed_feedback(
            CONNECTION_FEEDBACK_PATTERN,
            CONNECTION_LF_FREQUENCY,
            CONNECTION_HF_FREQUENCY,
            *older,
        ))
        self.assertEqual(device.reports, [])
        self.assertTrue(controller._play_fixed_feedback(
            CONNECTION_FEEDBACK_PATTERN,
            CONNECTION_LF_FREQUENCY,
            CONNECTION_HF_FREQUENCY,
            *latest,
        ))


class BluetoothFeedbackOverrideTests(unittest.IsolatedAsyncioTestCase):
    def make_ready_bluetooth_controller(self):
        controller = BluetoothController()
        controller.running = True
        controller.connected = True
        controller._application_ready = True
        controller._rumble_accepting = True
        controller._connection_generation = 1
        controller._loop = asyncio.get_running_loop()
        controller._feedback_lock = asyncio.Lock()
        controller.client = type("Client", (), {"is_connected": True})()
        return controller

    async def wait_for_feedback_task(self, controller):
        for _ in range(3):
            await asyncio.sleep(0)
            if controller._feedback_task is None:
                return

    async def test_native_ble_fixed_cue_cannot_be_overwritten_by_audio(self):
        controller = self.make_ready_bluetooth_controller()
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

    async def test_native_ble_failed_cue_does_not_run_completed_callback(self):
        controller = self.make_ready_bluetooth_controller()
        callbacks = []

        async def fail_feedback(*_args):
            return False

        controller._play_fixed_feedback_async = fail_feedback

        self.assertTrue(controller._start_fixed_feedback(
            CONNECTION_FEEDBACK_PATTERN,
            CONNECTION_LF_FREQUENCY,
            CONNECTION_HF_FREQUENCY,
            completed_callback=lambda: callbacks.append("completed"),
        ))
        await self.wait_for_feedback_task(controller)

        self.assertEqual(callbacks, [])

    async def test_native_ble_stale_cue_does_not_run_completed_callback(self):
        controller = self.make_ready_bluetooth_controller()
        callbacks = []

        async def finish_on_new_generation(*_args):
            controller._connection_generation = 2
            return True

        controller._play_fixed_feedback_async = finish_on_new_generation

        self.assertTrue(controller._start_fixed_feedback(
            CONNECTION_FEEDBACK_PATTERN,
            CONNECTION_LF_FREQUENCY,
            CONNECTION_HF_FREQUENCY,
            completed_callback=lambda: callbacks.append("completed"),
        ))
        await self.wait_for_feedback_task(controller)

        self.assertEqual(callbacks, [])


if __name__ == "__main__":
    unittest.main()
