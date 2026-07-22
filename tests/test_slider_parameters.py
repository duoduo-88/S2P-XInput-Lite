import sys
import unittest
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config_gui import ConfigGUI, normalize_slider_input, scrub_numeric_value
from gui_sections.audio_haptics import (
    AUDIO_ATTACK_VALUES,
    AUDIO_RELEASE_VALUES,
    AUDIO_TAIL_DECAY_VALUES,
)


class SliderParameterTests(unittest.TestCase):
    def test_disabled_parameter_is_registered_for_later_context_menu(self):
        class FakeVariable:
            def __str__(self):
                return "setting_var"

        class FakeWidget:
            def cget(self, option):
                return {
                    "state": "disabled",
                    "textvariable": "",
                    "variable": "setting_var",
                }[option]

        variable = FakeVariable()
        gui = ConfigGUI.__new__(ConfigGUI)
        gui.profile_combo = None
        gui.parameter_default_registry = {
            "setting_var": (variable, "system"),
        }
        gui.parameter_saved_registry = {
            "setting_var": (variable, "saved"),
        }
        gui.audio_haptics_parameter_default_binding = lambda _name: None
        gui.gyro_parameter_default_binding = lambda _name: None

        self.assertEqual(
            gui.parameter_binding_for_widget(FakeWidget()),
            (variable, "saved", "system"),
        )

    def test_typed_value_is_clamped_and_snapped_to_step(self):
        self.assertAlmostEqual(
            normalize_slider_input(0.237, 0.0, 1.0, step=0.05),
            0.25,
        )
        self.assertEqual(normalize_slider_input(-5, 0, 10, step=1), 0)
        self.assertEqual(normalize_slider_input(15, 0, 10, step=1), 10)

    def test_typed_value_uses_nearest_nonlinear_slider_value(self):
        values = (5, 10, 20, 35)
        self.assertEqual(
            normalize_slider_input(12, 5, 35, allowed_values=values),
            10,
        )
        self.assertEqual(
            normalize_slider_input(18, 5, 35, allowed_values=values),
            20,
        )
        self.assertEqual(
            normalize_slider_input(35, 5, 35, allowed_values=values),
            35,
        )

    def test_typed_value_rejects_non_finite_numbers(self):
        for value in (math.nan, math.inf, -math.inf, "nan", "inf"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    normalize_slider_input(value, 0, 10, step=1)

    def test_audio_time_sliders_are_ordered_and_cover_schema_range(self):
        for values in (
            AUDIO_ATTACK_VALUES,
            AUDIO_RELEASE_VALUES,
            AUDIO_TAIL_DECAY_VALUES,
        ):
            self.assertEqual(tuple(sorted(set(values))), values)

        self.assertEqual(AUDIO_ATTACK_VALUES[0], 1)
        self.assertEqual(AUDIO_ATTACK_VALUES[-1], 500)
        self.assertEqual(AUDIO_RELEASE_VALUES[:4], (5, 10, 20, 35))
        self.assertEqual(AUDIO_RELEASE_VALUES[-1], 2000)
        self.assertEqual(AUDIO_TAIL_DECAY_VALUES[0], 50)
        self.assertEqual(AUDIO_TAIL_DECAY_VALUES[-1], 2000)

    def test_scrub_value_moves_by_linear_steps_and_clamps(self):
        self.assertAlmostEqual(
            scrub_numeric_value(0.50, 2, 0.0, 1.0, step=0.05),
            0.60,
        )
        self.assertEqual(
            scrub_numeric_value(8, 5, 0, 10, step=1),
            10,
        )
        self.assertEqual(
            scrub_numeric_value(2, -5, 0, 10, step=1),
            0,
        )

    def test_scrub_value_walks_nonlinear_allowed_values(self):
        values = (5, 10, 20, 35)
        self.assertEqual(
            scrub_numeric_value(10, 2, 5, 35, allowed_values=values),
            35,
        )
        self.assertEqual(
            scrub_numeric_value(20, -1, 5, 35, allowed_values=values),
            10,
        )
        self.assertEqual(
            scrub_numeric_value(35, 4, 5, 35, allowed_values=values),
            35,
        )

    def test_scrub_non_finite_start_falls_back_to_minimum(self):
        self.assertEqual(
            scrub_numeric_value(math.nan, 2, 0, 10, step=1),
            2,
        )


if __name__ == "__main__":
    unittest.main()
