from dataclasses import replace
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gamepad_devices import GamepadDevice, GamepadState, NativeGamepadSampler
from gamepad_test_window import GamepadTestWindow


def state(packet, buttons=(), rt=0):
    return GamepadState(packet, 0, buttons, (0, 0), (0.4, -0.2), 0, rt)


class AimInputSamplerTests(unittest.TestCase):
    def setUp(self):
        self.device = GamepadDevice("xinput:0", "xinput", 0, "Controller", True)
        self.backend = SimpleNamespace(read_state=Mock())
        self.time = 10.0
        self.sampler = NativeGamepadSampler(self.backend, clock=lambda: self.time)
        self.sampler.set_device(self.device)

    def poll(self, *states):
        for sample in states:
            self.backend.read_state.return_value = sample
            self.sampler._poll_once()
            self.time += 0.001

    def test_aim_drains_distinct_timed_transitions_independently_of_monitor(self):
        idle = state(1)
        first_press = state(2, ("START",), 1)
        first_release = state(3)
        second_press = state(4, (), 1)
        second_release = state(5)
        self.poll(idle)
        self.sampler.set_aim_capture(True)
        self.poll(first_press, first_press, first_release, second_press, second_release)

        self.assertEqual(len(self.sampler.read_snapshot()[1]), 5)
        latest, generation, samples = self.sampler.read_aim_snapshot(self.device)
        self.assertEqual(latest, second_release)
        self.assertEqual([value for _, value in samples], [first_press, first_release, second_press, second_release])
        self.assertAlmostEqual(samples[0][0], 10.001)
        self.assertAlmostEqual(samples[1][0], 10.003)
        self.assertEqual(self.sampler.read_aim_snapshot(self.device), (latest, generation, ()))

        self.sampler.set_aim_capture(False)
        self.poll(state(6, ("B",)))
        self.assertEqual(self.sampler.read_aim_snapshot(self.device)[2], ())

    def test_reconnect_clears_queued_edges_even_when_packet_number_repeats(self):
        self.sampler.set_aim_capture(True)
        self.poll(state(7))
        _, generation, _ = self.sampler.read_aim_snapshot(self.device)
        self.poll(state(8, ("B",)), None, state(8))

        latest, new_generation, samples = self.sampler.read_aim_snapshot(self.device)
        self.assertNotEqual(generation, new_generation)
        self.assertEqual(latest, state(8))
        self.assertEqual([value for _, value in samples], [latest])

    def test_sampler_waits_for_device_instead_of_polling_an_empty_slot(self):
        self.sampler.set_device(None)
        wake = Mock()
        wake.wait.side_effect = lambda *_args: self.sampler._stop.set()
        self.sampler._wake = wake

        self.sampler._run()

        wake.wait.assert_called_once_with()
        self.backend.read_state.assert_not_called()


class AimInputRoutingTests(unittest.TestCase):
    def make_window(self, device):
        window = object.__new__(GamepadTestWindow)
        window.backend = SimpleNamespace(read_state=Mock(return_value=state(1)))
        window.native_sampler = NativeGamepadSampler(window.backend)
        window._selected_device = lambda: device
        window.latest_telemetry = {}
        return window

    def test_refreshing_device_metadata_preserves_queued_rt_release(self):
        device = GamepadDevice("xinput:0", "xinput", 0, "Controller", True)
        window = self.make_window(device)
        window._selected_device = lambda: device
        window._set_aim_input_active(True)
        sampler = window.native_sampler
        window.backend.read_state.return_value = state(1, rt=1)
        sampler._poll_once()
        original_key, _, _, _ = window._read_aim_input()

        released = state(2)
        window.backend.read_state.return_value = released
        sampler._poll_once()
        device = replace(device, name="Renamed controller")
        window._configure_native_sampler()

        key, latest, pending, _ = window._read_aim_input()
        self.assertEqual(key, original_key)
        self.assertEqual(latest, released)
        self.assertEqual([value for _, value in pending], [released])

    def test_switching_raw_selection_discards_queued_edges_on_same_windows_device(self):
        raw = GamepadDevice("raw:1", "raw_hid", 1, "Raw 1", False)
        native = GamepadDevice("xinput:0", "xinput", 0, "Controller", True)
        window = self.make_window(raw)
        window._selected_device = lambda: raw
        window._native_device_for_raw_hid = lambda: native
        window._read_aim_raw_report_rate = lambda: None
        window._set_aim_input_active(True)
        sampler = window.native_sampler
        sampler._poll_once()
        original_key, _, _, _ = window._read_aim_input()

        pressed = state(2, rt=1)
        window.backend.read_state.return_value = pressed
        sampler._poll_once()
        raw = replace(raw, key="raw:2", index=2, name="Raw 2")
        window._configure_native_sampler()

        key, latest, pending, _ = window._read_aim_input()
        self.assertNotEqual(key, original_key)
        self.assertEqual(latest, pressed)
        self.assertEqual(pending, ())

    def test_bridge_samples_final_xinput_in_worker_only_while_aim_visible(self):
        device = GamepadDevice("s2p", "s2p", 2, "Bridge", True)
        window = self.make_window(device)
        window.latest_telemetry = {"xinput_slot": 2, "source_rate_hz": 8100}
        window._telemetry_is_fresh = lambda _telemetry: True
        window._configure_native_sampler()
        window.native_sampler._poll_once()
        window.backend.read_state.assert_not_called()

        window._set_aim_input_active(True)
        window.native_sampler._poll_once()
        key, latest, pending, report_hz = window._read_aim_input()
        self.assertEqual(key[:2], ("s2p", 2))
        self.assertEqual(latest, state(1))
        self.assertEqual([value for _, value in pending], [latest])
        self.assertEqual(report_hz, 8100)
        self.assertEqual(window.backend.read_state.call_args.args[0].kind, "xinput")
        self.assertEqual(window.backend.read_state.call_args.args[0].index, 2)
        window.backend.read_state.assert_called_once()
        window.latest_telemetry["xinput_slot"] = 3
        self.assertIsNone(window._read_aim_input())

        window._set_aim_input_active(False)
        window.native_sampler._poll_once()
        window.backend.read_state.assert_called_once()

    def test_raw_hid_uses_paired_windows_state_and_raw_report_counter(self):
        raw = GamepadDevice("raw_hid:0", "raw_hid", 0, "Raw", False, raw_hid_key="raw:0")
        native = GamepadDevice("xinput:1", "xinput", 1, "Controller", True)
        window = self.make_window(raw)
        window._native_device_for_raw_hid = lambda: native
        window._selected_raw_hid_device = lambda: SimpleNamespace(path="raw-device")
        window._raw_hid_stream_path = "raw-device"
        window.raw_hid_stream = SimpleNamespace(status=Mock(side_effect=[
            {"state": "running", "raw_reports": 100},
            {"state": "running", "raw_reports": 5100},
        ]))
        window._set_aim_input_active(True)
        window.native_sampler._poll_once()

        with patch("gamepad_test_window.time.perf_counter", side_effect=[10.0, 10.2, 10.5]):
            key, latest, _pending, rate = window._read_aim_input()
            self.assertIsNone(rate)
            self.assertIsNone(window._read_aim_input()[3])
            self.assertEqual(window._read_aim_input()[3], 10_000)
        self.assertEqual(key[:2], (raw.key, raw.index))
        self.assertEqual(latest, state(1))
        window.backend.read_state.assert_called_once_with(native)
        self.assertEqual(window.raw_hid_stream.status.call_count, 2)

    def test_native_snapshot_count_is_not_report_rate(self):
        native = GamepadDevice("xinput:1", "xinput", 1, "Controller", True)
        window = self.make_window(native)
        window._set_aim_input_active(True)
        window.native_sampler._poll_once()

        self.assertIsNone(window._read_aim_input()[3])
