from pathlib import Path
import sys
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gamepad_devices import GamepadDevice
from gamepad_test_window import GamepadTestWindow, build_device_display_mapping


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class RumbleBackend:
    def __init__(self):
        self.outputs = {}

    def set_xinput_rumble(self, slot, left, right):
        self.outputs[slot] = (left, right)
        return True


class DeviceRefreshRumbleTests(unittest.TestCase):
    def make_tester(self):
        tester = object.__new__(GamepadTestWindow)
        tester.gui = SimpleNamespace(tr=lambda text: "Bridge")
        tester.raw_hid_probe = SimpleNamespace(has_reader=False)
        tester._raw_hid_pending_start = None
        tester._device_enumeration_initialized = True
        tester._native_test_devices = ()
        tester.raw_hid_devices = {}
        tester.native_sampler = None
        tester.devices = build_device_display_mapping((
            GamepadDevice("s2p", "s2p", 0, "Bridge", True),
        ))
        tester.selected_device_var = Value(next(iter(tester.devices)))
        tester.device_combo = SimpleNamespace(configure=lambda **kwargs: None)
        tester.latest_telemetry = {"timestamp_ns": time.monotonic_ns(), "xinput_slot": 0}
        tester.window = None
        tester.backend = RumbleBackend()
        tester._active_rumble_slot = None
        tester._manual_rumble = (0.5, 0.25)
        tester._rumble_jobs = {}
        tester._repeat_rumble_jobs = {}
        tester._rumble_layers = {}
        tester._rumble_layer_templates = {}
        tester._active_rumble_templates = {}
        for method in (
            "_configure_source_controls", "_configure_native_sampler", "_sync_raw_hid_stream",
            "_set_raw_hid_controls_active", "_refresh_rumble_template_styles", "clear_measurements",
        ):
            setattr(tester, method, lambda *args, **kwargs: None)
        tester._apply_rumble_mix()
        return tester

    def test_bridge_slot_change_or_loss_stops_old_output_and_clears_mix(self):
        for slot in (1, None):
            with self.subTest(slot=slot):
                tester = self.make_tester()
                self.assertEqual(tester.backend.outputs[0], (0.5, 0.25))
                tester.latest_telemetry["xinput_slot"] = slot
                tester._refresh_devices(force=False)
                self.assertEqual(tester.backend.outputs[0], (0.0, 0.0))
                self.assertEqual(tester._manual_rumble, (0.0, 0.0))
                self.assertIsNone(tester._active_rumble_slot)
                tester._apply_rumble_mix()
                self.assertEqual(tester.backend.outputs.get(1, (0.0, 0.0)), (0.0, 0.0))

    def test_unchanged_bridge_refresh_preserves_active_test(self):
        tester = self.make_tester()
        tester._refresh_devices(force=False)
        self.assertEqual(tester.backend.outputs[0], (0.5, 0.25))
        self.assertEqual(tester._manual_rumble, (0.5, 0.25))
        self.assertEqual(tester._active_rumble_slot, 0)

    def test_disappearing_device_clears_measurements_before_using_a_fallback(self):
        tester = self.make_tester()
        fallback = GamepadDevice("xinput:1", "xinput", 1, "Other controller", True)
        tester._native_test_devices = (fallback,)
        tester.latest_telemetry = {}
        tester.clear_measurements = Mock()

        tester._refresh_devices(force=False)

        self.assertEqual(tester._selected_device().key, fallback.key)
        tester.clear_measurements.assert_called_once_with()

    def test_bridge_slot_change_clears_measurements_but_unchanged_refresh_does_not(self):
        tester = self.make_tester()
        tester.clear_measurements = Mock()

        tester.latest_telemetry["xinput_slot"] = 1
        tester._refresh_devices(force=False)
        tester._refresh_devices(force=False)

        tester.clear_measurements.assert_called_once_with()

    def test_rumble_tick_before_device_refresh_cannot_transfer_to_new_slot(self):
        tester = self.make_tester()
        tester.latest_telemetry["xinput_slot"] = 1
        tester._apply_rumble_mix()
        tester._refresh_devices(force=False)
        self.assertEqual(tester.backend.outputs[0], (0.0, 0.0))
        self.assertEqual(tester.backend.outputs.get(1, (0.0, 0.0)), (0.0, 0.0))
        self.assertEqual(tester._manual_rumble, (0.0, 0.0))
        self.assertIsNone(tester._active_rumble_slot)

    def test_template_silent_gap_does_not_transfer_next_pulse_to_new_slot(self):
        tester = self.make_tester()
        tester._manual_rumble = (0.0, 0.0)
        tester._rumble_layers[1] = (0.5, 0.25)
        tester._set_rumble_layer(1, 0.0, 0.0)
        self.assertIsNone(tester._active_rumble_slot)
        tester.latest_telemetry["xinput_slot"] = 1
        tester._set_rumble_layer(1, 0.5, 0.25)
        self.assertEqual(tester.backend.outputs[0], (0.0, 0.0))
        self.assertEqual(tester.backend.outputs.get(1, (0.0, 0.0)), (0.0, 0.0))
        tester._refresh_devices(force=False)
        self.assertEqual(tester._rumble_layers, {})


if __name__ == "__main__":
    unittest.main()
