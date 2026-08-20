import configparser
import tempfile
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
import sys
sys.path.insert(0, str(SRC_DIR))

from config_utils import load_config, read_profile, PROFILE_DIR
from settings_schema import (
    SettingValidationError,
    normalize_config_in_place,
    normalize_section_values,
    read_section_settings,
    validate_config_sections,
    write_section_settings,
)


class SettingsSchemaTests(unittest.TestCase):
    def test_system_default_and_all_profiles_are_valid(self):
        default_config = configparser.ConfigParser()
        self.assertTrue(default_config.read(SRC_DIR / "profiles" / "System Default.ini", encoding="utf-8"))
        validated = validate_config_sections(default_config, strict=True)
        self.assertIn("gyro_mapping", validated)
        self.assertEqual(validated["rumble"]["max_amplitude"], 500)

        for path in sorted(PROFILE_DIR.glob("*.ini")):
            if path.stem == "System Default":
                continue
            profile = read_profile(path.stem)
            validate_config_sections(profile, strict=True)

    def test_strict_deadzone_overlap_is_rejected(self):
        with self.assertRaises(SettingValidationError):
            normalize_section_values(
                "stick_curve_left",
                {"deadzone": "0.80", "outer_deadzone": "0.20"},
                strict=True,
            )
        with self.assertRaises(SettingValidationError):
            normalize_section_values(
                "stick_curve_left",
                {"deadzone": "1.00", "outer_deadzone": "0.00"},
                strict=True,
            )

    def test_runtime_deadzone_overlap_is_repaired(self):
        values = normalize_section_values(
            "stick_curve_left",
            {"deadzone": "0.80", "outer_deadzone": "0.80"},
            strict=False,
        )
        self.assertAlmostEqual(values["deadzone"], 0.80)
        self.assertAlmostEqual(values["outer_deadzone"], 0.19)
        self.assertLess(values["deadzone"] + values["outer_deadzone"], 1.0)

    def test_gyro_alias_and_cross_field_rules(self):
        values = normalize_section_values(
            "gyro_mapping",
            {"response_curve": "dynamic"},
            strict=True,
        )
        self.assertEqual(values["response_curve"], "LATE")

        with self.assertRaises(SettingValidationError):
            normalize_section_values(
                "gyro_mapping",
                {"target": "MOUSE", "motion_mode": "TILT"},
                strict=True,
            )
        repaired = normalize_section_values(
            "gyro_mapping",
            {"target": "MOUSE", "motion_mode": "TILT"},
            strict=False,
        )
        self.assertEqual(repaired["motion_mode"], "CENTER")

    def test_stick_direction_hysteresis_is_shared(self):
        with self.assertRaises(SettingValidationError):
            normalize_section_values(
                "stick_direction_left",
                {"trigger_threshold": "0.60", "release_threshold": "0.59"},
                strict=True,
            )
        repaired = normalize_section_values(
            "stick_direction_left",
            {"trigger_threshold": "0.60", "release_threshold": "0.59"},
            strict=False,
        )
        self.assertAlmostEqual(repaired["release_threshold"], 0.57)

    def test_normalize_config_preserves_unknown_sections_and_options(self):
        config = configparser.ConfigParser()
        config["stick_curve_left"] = {
            "deadzone": "0.80",
            "outer_deadzone": "0.80",
            "custom_extension": "keep-me",
        }
        config["custom_plugin"] = {"value": "42"}
        changed = normalize_config_in_place(config)
        self.assertTrue(changed)
        self.assertEqual(config["stick_curve_left"]["custom_extension"], "keep-me")
        self.assertEqual(config["custom_plugin"]["value"], "42")
        parsed = read_section_settings(config, "stick_curve_left")
        self.assertLess(parsed["deadzone"] + parsed["outer_deadzone"], 1.0)

    def test_load_config_repairs_invalid_values_without_losing_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_text(
                "[stick_curve_left]\n"
                "deadzone = 0.90\n"
                "outer_deadzone = 0.90\n"
                "custom_extension = keep\n\n"
                "[gyro_mapping]\n"
                "target = MOUSE\n"
                "motion_mode = TILT\n\n"
                "[custom_plugin]\n"
                "value = 99\n",
                encoding="utf-8",
            )
            config = load_config(path)
            self.assertEqual(config.get("custom_plugin", "value"), "99")
            self.assertEqual(config.get("stick_curve_left", "custom_extension"), "keep")
            self.assertEqual(config.get("gyro_mapping", "motion_mode"), "CENTER")
            self.assertEqual(
                config.getfloat("audio_haptics", "lf_hf_balance"), 0.0
            )
            self.assertEqual(
                config.getfloat("audio_haptics", "band_6_gain"), 0.15
            )
            self.assertLess(
                config.getfloat("stick_curve_left", "deadzone")
                + config.getfloat("stick_curve_left", "outer_deadzone"),
                1.0,
            )

    def test_audio_routing_balance_and_sixth_band_ranges(self):
        values = normalize_section_values(
            "audio_haptics",
            {"lf_hf_balance": "-1.00", "band_6_gain": "2.00"},
            strict=True,
        )
        self.assertEqual(values["lf_hf_balance"], -1.0)
        self.assertEqual(values["band_6_gain"], 2.0)
        with self.assertRaises(SettingValidationError):
            normalize_section_values(
                "audio_haptics",
                {"lf_hf_balance": "1.05"},
                strict=True,
            )

    def test_schema_write_round_trip(self):
        config = configparser.ConfigParser()
        values = normalize_section_values(
            "rumble",
            {
                "lf_strength": 0.8,
                "hf_strength": 0.7,
                "lf_curve": 1.2,
                "hf_curve": 0.9,
                "lf_to_hf_compensation": 0.2,
                "hf_to_lf_compensation": 0.1,
                "lf_frequency": 210,
                "hf_frequency": 325,
                "max_amplitude": 800,
            },
            strict=True,
        )
        write_section_settings(config, "rumble", values)
        self.assertEqual(read_section_settings(config, "rumble"), values)
        self.assertEqual(config.get("rumble", "lf_strength"), "0.80")
        self.assertEqual(config.get("rumble", "max_amplitude"), "800")

    def test_rumble_frequency_fields_accept_full_9_bit_range(self):
        values = normalize_section_values(
            "rumble",
            {"lf_frequency": 511, "hf_frequency": 511},
            strict=True,
        )
        self.assertEqual(values["lf_frequency"], 511)
        self.assertEqual(values["hf_frequency"], 511)
        with self.assertRaises(SettingValidationError):
            normalize_section_values(
                "rumble",
                {"lf_frequency": 512},
                strict=True,
            )


if __name__ == "__main__":
    unittest.main()
