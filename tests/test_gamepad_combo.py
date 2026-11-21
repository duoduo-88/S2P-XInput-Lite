import configparser
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config_utils import load_stick_calibration
from mapping_runtime import MAP_COMBO, compile_button_mapping, compile_combo_output
from mapping_targets import (
    button_target_error,
    combo_members,
    combo_target_error,
    direction_target_error,
    format_combo_target,
    validate_button_target,
)
from readable_combobox import mapping_target_label
from switch2_input import SWITCH_BUTTONS
from xinput_controller import XB_BUTTONS, XInputController


class ComboTargetTests(unittest.TestCase):
    def test_valid_combo_is_accepted_for_buttons_and_stick_directions(self):
        for target in ("COMBO:X+Y", "combo:lb+rb+lt+rt", "COMBO:UP+A"):
            with self.subTest(target=target):
                self.assertEqual(button_target_error(target), "")
                self.assertEqual(direction_target_error(target), "")

    def test_invalid_combos_are_rejected_with_a_reason(self):
        cases = {
            "COMBO:X": "2 到 4",
            "COMBO:A+B+X+Y+LB": "2 到 4",
            "COMBO:X+X": "重複",
            "COMBO:X+L_STICK_UP": "不支援",
            "COMBO:X++Y": "空白",
            "COMBO:": "2 到 4",
        }
        for target, reason in cases.items():
            with self.subTest(target=target):
                self.assertIn(reason, combo_target_error(target))
                with self.assertRaises(ValueError):
                    validate_button_target(target)

    def test_unfinished_combo_choice_blocks_saving(self):
        self.assertEqual(button_target_error("CUSTOM_COMBO"), "手把組合尚未完成選擇")
        self.assertEqual(direction_target_error("CUSTOM_COMBO"), "手把組合尚未完成選擇")

    def test_members_use_one_canonical_order(self):
        self.assertEqual(combo_members("COMBO:Y+RT+X"), ("X", "Y", "RT"))
        self.assertEqual(format_combo_target(["y", "x", "rt"]), "COMBO:X+Y+RT")
        self.assertEqual(combo_members("COMBO:X"), ())

    def test_dropdown_label_is_compact(self):
        self.assertEqual(mapping_target_label("COMBO:X+Y"), "Pad:X+Y")
        self.assertEqual(mapping_target_label("CUSTOM_COMBO"), "自訂手把組合")


class ComboCompileTests(unittest.TestCase):
    def test_combo_compiles_to_one_button_mask_and_trigger_flags(self):
        combo = compile_combo_output("COMBO:X+Y+LT", XB_BUTTONS)

        self.assertEqual(combo.buttons, int(XB_BUTTONS["X"]) | int(XB_BUTTONS["Y"]))
        self.assertTrue(combo.left_trigger)
        self.assertFalse(combo.right_trigger)
        self.assertEqual(combo.members, ("X", "Y", "LT"))

    def test_button_mapping_marks_combo_entries(self):
        compiled = compile_button_mapping(
            {"A": "COMBO:LB+RB"}, SWITCH_BUTTONS, XB_BUTTONS, {},
        )
        _name, _mask, kind, value, _source = compiled[0]

        self.assertEqual(kind, MAP_COMBO)
        self.assertEqual(value.buttons, int(XB_BUTTONS["LB"]) | int(XB_BUTTONS["RB"]))


class ComboRuntimeTests(unittest.TestCase):
    @staticmethod
    def _controller(configure):
        config = configparser.ConfigParser()
        if not config.read(ROOT / "src" / "profiles" / "System Default.ini", encoding="utf-8"):
            raise AssertionError("System Default.ini was not readable")
        configure(config)
        controller = XInputController(
            config, load_stick_calibration(config), activate_runtime=False,
        )
        report = SimpleNamespace(
            wButtons=0, bLeftTrigger=0, bRightTrigger=0,
            sThumbLX=0, sThumbLY=0, sThumbRX=0, sThumbRY=0,
        )
        controller.pad = SimpleNamespace(report=report, update=lambda: None)
        return controller, report

    @staticmethod
    def _state(buttons=0, left=(2048, 2048), report_time=0):
        return SimpleNamespace(
            buttons=buttons,
            left_stick=left,
            right_stick=(2048, 2048),
            gyroscope=(0, 0, 0),
            accelerometer=(0, 0, 0),
            magnetometer=(0, 0, 0),
            report_time=report_time,
        )

    def test_button_combo_presses_and_releases_every_member_in_the_same_report(self):
        controller, report = self._controller(
            lambda config: config.set("buttons", "a", "COMBO:X+Y+RT"),
        )
        expected = int(XB_BUTTONS["X"]) | int(XB_BUTTONS["Y"])

        controller.update(self._state(buttons=SWITCH_BUTTONS["A"], report_time=8))
        self.assertEqual(report.wButtons & expected, expected)
        self.assertEqual(report.bRightTrigger, 255)

        controller.update(self._state(buttons=0, report_time=16))
        self.assertEqual(report.wButtons & expected, 0)
        self.assertEqual(report.bRightTrigger, 0)

    def test_stick_direction_combo_follows_the_direction(self):
        def configure(config):
            config.set("stick_direction_left", "mode", "4WAY")
            config.set("stick_direction_left", "up", "COMBO:LB+RB")

        controller, report = self._controller(configure)
        expected = int(XB_BUTTONS["LB"]) | int(XB_BUTTONS["RB"])

        controller.update(self._state(left=(2048, 4095), report_time=8))
        self.assertEqual(report.wButtons & expected, expected)

        controller.update(self._state(report_time=16))
        self.assertEqual(report.wButtons & expected, 0)


class StandaloneComboTests(unittest.TestCase):
    @staticmethod
    def _settings(**buttons):
        return {
            "sections": {
                "stick_curve_left": {"deadzone": 0.05},
                "stick_curve_right": {"deadzone": 0.06},
                "rumble": {"max_amplitude": 800},
                "audio_haptics": {"mode": "GAME"},
                "gyro_mapping": {"activation_mode": "OFF"},
                "stick_direction_left": {"mode": "4WAY"},
                "stick_direction_right": {"mode": "4WAY"},
            },
            "buttons": {"a": "A", **buttons},
            "direction_mappings": {"left": {"up": "NONE"}, "right": {"up": "NONE"}},
        }

    def test_valid_combos_run_on_the_esp32_and_invalid_ones_block(self):
        from standalone_profile import analyze_standalone_v2_compatibility

        self.assertFalse(analyze_standalone_v2_compatibility(self._settings(b="COMBO:X+Y")))
        settings = self._settings()
        settings["direction_mappings"]["left"]["up"] = "COMBO:LB+RB"
        self.assertFalse(analyze_standalone_v2_compatibility(settings))
        issues = analyze_standalone_v2_compatibility(self._settings(b="COMBO:X"))
        self.assertEqual([issue.severity for issue in issues], ["blocking"])

    def test_profile_reports_combo_use_in_buttons_directions_and_layers(self):
        from standalone_profile import compile_standalone_profile

        self.assertFalse(compile_standalone_profile("P", self._settings()).uses_gamepad_combos)
        self.assertTrue(
            compile_standalone_profile("P", self._settings(b="COMBO:X+Y")).uses_gamepad_combos
        )
        layer = {
            "name": "Aim", "enabled": True, "activation_buttons": ["ZL"], "mode": "HOLD",
            "buttons": {}, "stick_left": {"mode": "4WAY", "up": "COMBO:A+B"},
        }
        compiled = compile_standalone_profile("P", self._settings(), [layer])
        self.assertTrue(compiled.uses_gamepad_combos)

    def test_old_firmware_without_combo_support_is_rejected_before_staging(self):
        from unittest.mock import patch

        from standalone_profile import (
            StandaloneTransferError,
            compile_standalone_profile,
            write_standalone_profile,
        )
        from test_usb_hid_desktop import FakeSerial, identity

        compiled = compile_standalone_profile("P", self._settings(b="COMBO:X+Y"))
        port = FakeSerial(identity())
        with patch("standalone_profile.serial.Serial", return_value=port):
            with self.assertRaisesRegex(StandaloneTransferError, "手把組合鍵"):
                write_standalone_profile("COM5", 2_000_000, compiled, target_mode="standalone_hid")
        self.assertFalse(any(cmd.startswith("profile begin ") for cmd in port.commands))


if __name__ == "__main__":
    unittest.main()
