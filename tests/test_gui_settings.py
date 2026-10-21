import configparser
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from gui_settings import (
    apply_gui_settings_to_config,
    canonical_settings_snapshot,
    config_settings_bundle,
)
from switch2_input import SWITCH_BUTTONS


class FakeGui:
    def __init__(self, config):
        self.gyro_activation_button_options = list(SWITCH_BUTTONS)
        self.gyro_stabilization_button_options = list(SWITCH_BUTTONS)
        self.button_vars = {
            name: None for name, _value in config.items("buttons")
        }
        directions = (
            "up", "up_right", "right", "down_right",
            "down", "down_left", "left", "up_left",
        )
        self.stick_direction_vars = {
            "LEFT": {name: None for name in directions},
            "RIGHT": {name: None for name in directions},
        }


def load_default():
    config = configparser.ConfigParser()
    if not config.read(
        ROOT / "src" / "profiles" / "System Default.ini",
        encoding="utf-8",
    ):
        raise AssertionError("System Default.ini was not readable")
    return config


class GuiSettingsTests(unittest.TestCase):
    def test_equivalent_numeric_spelling_has_same_snapshot(self):
        original = load_default()
        equivalent = load_default()
        equivalent.set(
            "stick_curve_left",
            "deadzone",
            f"{original.getfloat('stick_curve_left', 'deadzone'):.4f}",
        )
        gui = FakeGui(original)

        first = canonical_settings_snapshot(
            config_settings_bundle(gui, original)
        )
        second = canonical_settings_snapshot(
            config_settings_bundle(gui, equivalent)
        )

        self.assertEqual(first, second)

    def test_apply_preserves_unknown_and_calibration_sections(self):
        config = load_default()
        config.add_section("extension")
        config.set("extension", "custom", "keep")
        config.add_section("sticks.TESTPAD")
        config.set("sticks.TESTPAD", "left_center", "1, 2")
        gui = FakeGui(config)
        bundle = config_settings_bundle(gui, config)

        apply_gui_settings_to_config(config, bundle)

        self.assertEqual(config.get("extension", "custom"), "keep")
        self.assertEqual(
            config.get("sticks.TESTPAD", "left_center"), "1, 2"
        )


if __name__ == "__main__":
    unittest.main()
