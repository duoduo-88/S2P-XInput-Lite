from pathlib import Path
import sys
from types import SimpleNamespace
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gamepad_test_window import GamepadTestWindow


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class FakeWindow:
    """Records scheduled callbacks without running a Tk event loop."""

    def __init__(self):
        self.next_id = 0
        self.cancelled = set()

    def after(self, _delay, _callback):
        self.next_id += 1
        return f"job{self.next_id}"

    def after_cancel(self, job):
        self.cancelled.add(job)


class RumbleTemplateOverlapTests(unittest.TestCase):
    def make_tester(self, repeat=False, overlap=False):
        tester = object.__new__(GamepadTestWindow)
        tester.gui = SimpleNamespace(tr=lambda text: text)
        tester.window = FakeWindow()
        tester.repeat_rumble_var = Value(repeat)
        tester.overlap_rumble_var = Value(overlap)
        tester.rumble_repeat_hz_var = Value(1.0)
        tester.rumble_strength_var = Value(100.0)
        tester.template_lf_enabled_var = Value(True)
        tester.template_hf_enabled_var = Value(True)
        tester._rumble_jobs = {}
        tester._repeat_rumble_jobs = {}
        tester._rumble_layers = {}
        tester._rumble_layer_templates = {}
        tester._active_rumble_templates = {}
        tester._next_rumble_layer_id = 0
        tester._manual_rumble = (0.0, 0.0)
        tester.outputs = []
        tester._set_rumble = lambda left, right, **_kwargs: (
            tester.outputs.append((left, right)) or True
        )
        tester._refresh_rumble_template_styles = lambda: None
        return tester

    @staticmethod
    def playing(tester):
        return sorted(tester._rumble_layer_templates.values())

    def test_without_overlap_a_new_template_replaces_the_current_one(self):
        tester = self.make_tester()
        tester.play_rumble("impact")
        first_jobs = list(tester._rumble_jobs[1])
        tester.play_rumble("engine")
        self.assertEqual(self.playing(tester), ["engine"])
        # The replaced template's pending steps can no longer fire.
        self.assertTrue(set(first_jobs) <= tester.window.cancelled)

    def test_overlap_stacks_templates(self):
        tester = self.make_tester(overlap=True)
        tester.play_rumble("impact")
        tester.play_rumble("engine")
        self.assertEqual(self.playing(tester), ["engine", "impact"])

    def test_repeat_loops_one_template_until_clicked_again(self):
        tester = self.make_tester(repeat=True)
        tester.play_rumble("impact")
        self.assertEqual(self.playing(tester), ["impact"])
        self.assertIn(1, tester._repeat_rumble_jobs)
        tester.play_rumble("engine")
        self.assertEqual(self.playing(tester), ["engine"])
        self.assertEqual(set(tester._active_rumble_templates), {"engine"})
        tester.play_rumble("engine")
        self.assertEqual(self.playing(tester), [])

    def test_repeat_with_overlap_toggles_each_template_independently(self):
        tester = self.make_tester(repeat=True, overlap=True)
        tester.play_rumble("impact")
        tester.play_rumble("engine")
        self.assertEqual(self.playing(tester), ["engine", "impact"])
        tester.play_rumble("impact")
        self.assertEqual(self.playing(tester), ["engine"])

    def test_turning_overlap_off_keeps_only_the_newest_template(self):
        tester = self.make_tester(repeat=True, overlap=True)
        for name in ("impact", "engine", "wave"):
            tester.play_rumble(name)
        tester.overlap_rumble_var.set(False)
        tester._on_overlap_rumble_changed()
        self.assertEqual(self.playing(tester), ["wave"])

    def test_replacing_templates_keeps_manual_output_and_aim_layer(self):
        tester = self.make_tester()
        tester._manual_rumble = (0.2, 0.1)
        tester._rumble_layers["aim"] = (0.0, 0.3)
        tester.play_rumble("impact")
        tester.play_rumble("engine")
        self.assertIn("aim", tester._rumble_layers)
        self.assertEqual(tester._manual_rumble, (0.2, 0.1))


class RumbleTemplateSetTests(unittest.TestCase):
    KEPT = (
        "lf", "hf", "alternate", "ramp", "wave",
        "impact", "double_tap", "heartbeat", "footsteps", "machine_gun",
        "engine", "terrain", "rotor", "alert", "countdown",
    )

    def test_templates_fill_a_five_column_grid(self):
        self.assertEqual(len(self.KEPT) % 5, 0)
        for name in self.KEPT:
            pattern = GamepadTestWindow._rumble_pattern(None, name)
            self.assertIsNotNone(pattern, name)
            self.assertEqual(pattern[-1][1:], (0.0, 0.0), name)

    def test_near_duplicate_templates_are_gone(self):
        for name in ("rapid_fire", "low_rumble", "burst", "shotgun", "turbo"):
            self.assertIsNone(GamepadTestWindow._rumble_pattern(None, name))


if __name__ == "__main__":
    unittest.main()
