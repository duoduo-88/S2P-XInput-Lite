import struct
import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from switch2_input import (
    battery_led_mask,
    battery_level,
    estimate_battery_percent,
    parse_input_report,
)


class BatteryParsingTests(unittest.TestCase):
    def test_preserves_raw_charge_status_and_current(self):
        payload = bytearray(64)
        struct.pack_into("<H", payload, 31, 3641)
        payload[33] = 0xA5
        struct.pack_into("<H", payload, 34, 0x1234)

        state = parse_input_report(payload)

        self.assertEqual(state.battery_voltage, 3.641)
        self.assertEqual(state.battery_percent, 95)
        self.assertEqual(state.charge_status_raw, 0xA5)
        self.assertEqual(state.battery_current_raw, 0x1234)
        self.assertTrue(state.charging)

    def test_short_report_leaves_raw_power_fields_unknown(self):
        state = parse_input_report(bytes(16))

        self.assertIsNone(state.charge_status_raw)
        self.assertIsNone(state.battery_current_raw)
        self.assertFalse(state.charging)

    def test_measured_voltage_endpoints_and_midpoint(self):
        self.assertEqual(estimate_battery_percent(2589), 0)
        self.assertEqual(estimate_battery_percent(3000), 5)
        self.assertEqual(estimate_battery_percent(3100), 5)
        self.assertEqual(estimate_battery_percent(3150), 10)
        self.assertEqual(estimate_battery_percent(3200), 20)
        self.assertEqual(estimate_battery_percent(3250), 35)
        self.assertEqual(estimate_battery_percent(3400), 55)
        self.assertEqual(estimate_battery_percent(3475), 70)
        self.assertEqual(estimate_battery_percent(3687), 100)
        self.assertEqual(estimate_battery_percent(3700), 100)

    def test_battery_led_bar_uses_four_cumulative_levels(self):
        self.assertIsNone(battery_level(None))
        self.assertEqual(battery_level(0), 1)
        self.assertEqual(battery_level(25), 1)
        self.assertEqual(battery_level(30), 2)
        self.assertEqual(battery_level(50), 2)
        self.assertEqual(battery_level(55), 3)
        self.assertEqual(battery_level(75), 3)
        self.assertEqual(battery_level(80), 4)
        self.assertEqual(battery_level(100), 4)
        self.assertIsNone(battery_led_mask(None))
        self.assertEqual(battery_led_mask(0), 0x01)
        self.assertEqual(battery_led_mask(25), 0x01)
        self.assertEqual(battery_led_mask(30), 0x03)
        self.assertEqual(battery_led_mask(50), 0x03)
        self.assertEqual(battery_led_mask(55), 0x07)
        self.assertEqual(battery_led_mask(75), 0x07)
        self.assertEqual(battery_led_mask(80), 0x0F)
        self.assertEqual(battery_led_mask(100), 0x0F)


if __name__ == "__main__":
    unittest.main()
