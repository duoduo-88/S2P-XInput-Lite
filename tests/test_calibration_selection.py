import configparser
import unittest

from config_utils import select_standalone_calibration_id


class CalibrationSelectionTests(unittest.TestCase):
    def test_connected_controller_wins_with_multiple_calibrations(self):
        config = configparser.ConfigParser()
        config.add_section("sticks.AA_BB_CC_DD_EE_FF")
        config.add_section("gyro.11_22_33_44_55_66")

        selected = select_standalone_calibration_id(
            config,
            {"controller_id": "11:22:33:44:55:66"},
        )

        self.assertEqual(selected, "112233445566")

    def test_single_calibration_is_selected_without_live_status(self):
        config = configparser.ConfigParser()
        config.add_section("sticks.AA_BB_CC_DD_EE_FF")
        config.add_section("gyro.AA_BB_CC_DD_EE_FF")

        selected = select_standalone_calibration_id(config)

        self.assertEqual(selected, "AABBCCDDEEFF")

    def test_ambiguous_calibrations_are_rejected(self):
        config = configparser.ConfigParser()
        config.add_section("sticks.AA_BB_CC_DD_EE_FF")
        config.add_section("gyro.11_22_33_44_55_66")

        with self.assertRaisesRegex(ValueError, "Multiple controller"):
            select_standalone_calibration_id(config)


if __name__ == "__main__":
    unittest.main()
