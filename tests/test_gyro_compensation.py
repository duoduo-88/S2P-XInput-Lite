import tkinter as tk
import unittest
import weakref

from gyro_compensation import suggested_axis_ratio


class GyroCompensationTests(unittest.TestCase):
    def test_closed_compensation_dialogs_release_their_tk_variable_callbacks(self):
        from gamepad_aim_settings import AimSettings
        from gyro_compensation_window import (
            GyroCompensationCalculator, GyroCompensationPreview, GyroCompensationSettings,
        )

        root = self.make_root()
        for create in (
            lambda: GyroCompensationCalculator(root, str),
            lambda: GyroCompensationSettings(root, str, {"X": "1.00", "Y": "1.00"}, lambda values: None),
            lambda: GyroCompensationPreview(root, str, AimSettings(), lambda: True, None, None, None,
                                            on_speed_changed=lambda speeds: None),
        ):
            dialog = create()
            dialog.window.destroy()
            reference = weakref.ref(dialog)
            del dialog
            self.assertIsNone(reference())

    def test_suggested_ratio_uses_measured_angles_and_reports_range(self):
        self.assertEqual(suggested_axis_ratio(1, 20, 30), 0.67)
        self.assertEqual(suggested_axis_ratio(1, 15, 45), 0.33)
        for values in ((1, 0, 30), (1, 20, 0), (1, 20, float("inf"))):
            with self.subTest(values=values), self.assertRaises(ValueError):
                suggested_axis_ratio(*values)

    def test_calculator_keeps_axis_inputs_separate(self):
        from gyro_compensation_window import GyroCompensationCalculator

        root = self.make_root()
        calculator = GyroCompensationCalculator(root, str)
        calculator.inputs["X"][1].set("3")
        calculator.inputs["X"][2].set("0.48")
        self.assertEqual(calculator.result.get(), "建議 X 補償倍率 0.16")
        calculator.axis.set("Y")
        self.assertEqual(calculator.result.get(), "")
        calculator.inputs["Y"][1].set("3")
        calculator.inputs["Y"][2].set("0.15")
        self.assertIn("0.10–2.00", calculator.result.get())
        calculator.axis.set("X")
        self.assertEqual(calculator.result.get(), "建議 X 補償倍率 0.16")

    def test_calculator_rejects_an_original_multiplier_the_settings_cannot_use(self):
        from gyro_compensation_window import GyroCompensationCalculator

        calculator = GyroCompensationCalculator(self.make_root(), str)
        original, old, new = calculator.inputs["X"]
        old.set("10")
        new.set("1")
        original.set("10")
        self.assertEqual(calculator.result.get(), "補償倍率須介於 0.10 和 2.00。")
        original.set("2")
        self.assertEqual(calculator.result.get(), "建議 X 補償倍率 0.20")

    def test_settings_apply_both_axes_only_when_valid(self):
        from gyro_compensation_window import GyroCompensationSettings

        root = self.make_root()
        applied = []
        settings = GyroCompensationSettings(root, str, {"X": "1.00", "Y": "1.20"}, applied.append)
        settings.compensation["X"].set("0.16")
        settings.compensation["Y"].set("0.05")
        settings.apply_button.invoke()
        self.assertEqual(applied, [])
        settings.compensation["Y"].set("2")
        settings.apply_button.invoke()
        self.assertEqual(applied, [{"X": "0.16", "Y": "2.00"}])
        self.assertFalse(settings.window.winfo_exists())

    def test_settings_slider_reset_and_cancel(self):
        from gyro_compensation_window import GyroCompensationSettings

        applied = []
        settings = GyroCompensationSettings(self.make_root(), str, {"X": "0.25", "Y": "1.20"}, applied.append)
        settings.sliders["X"].set(0.52)
        self.assertEqual(settings.compensation["X"].get(), "0.52")
        settings.compensation["Y"].set("0.75")
        self.assertAlmostEqual(settings.sliders["Y"].get(), 0.75)
        settings.defaults_button.invoke()
        self.assertEqual([value.get() for value in settings.compensation.values()], ["1.00", "1.00"])
        self.assertEqual(settings.apply_button.cget("text"), "確定")
        self.assertEqual(settings.cancel_button.cget("text"), "取消")
        settings.cancel_button.invoke()
        self.assertFalse(settings.window.winfo_exists())
        self.assertEqual(applied, [])

    def make_root(self):
        try:
            root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.addCleanup(root.destroy)
        root.withdraw()
        return root
