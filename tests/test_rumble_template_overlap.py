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
        self.delays = []

    def after(self, delay, _callback):
        self.next_id += 1
        self.delays.append(delay)
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

    def scheduled(self, name, rate, repeat):
        tester = self.make_tester(repeat=repeat)
        tester.rumble_repeat_hz_var.set(rate)
        tester.play_rumble(name)
        return tester, tester.window.delays

    def test_rate_repeats_an_effect_without_changing_its_timing(self):
        native = [
            delay for delay, _, _ in
            GamepadTestWindow._rumble_pattern(None, "heartbeat")
        ]
        # 1.2 Hz: same thumps, shorter rest, restart at 833 ms (72 bpm).
        tester, delays = self.scheduled("heartbeat", 1.2, repeat=True)
        self.assertEqual(delays[:-1], [d for d in native if d < 833])
        self.assertEqual(delays[-1], 833)
        # 0.5 Hz: same thumps, then a two-second cycle (30 bpm).
        tester, delays = self.scheduled("heartbeat", 0.5, repeat=True)
        self.assertEqual(delays[:-1], native)
        self.assertEqual(delays[-1], 2000)

    def test_faster_rate_restarts_an_effect_early(self):
        tester, delays = self.scheduled("machine_gun", 2.0, repeat=True)
        self.assertTrue(all(delay < 500 for delay in delays[:-1]))
        self.assertEqual(delays[-1], 500)
        # Shots keep their 100 ms spacing.
        self.assertIn(100, delays)
        self.assertIn(400, delays)

    def test_single_play_ignores_rate_for_effects(self):
        native = [
            delay for delay, _, _ in
            GamepadTestWindow._rumble_pattern(None, "double_tap")
        ]
        for rate in (0.5, 1.0, 5.0):
            tester, delays = self.scheduled("double_tap", rate, repeat=False)
            self.assertEqual(delays[:-1], native, rate)
            self.assertEqual(delays[-1], 1001, rate)

    def test_band_check_shapes_still_follow_rate_as_tempo(self):
        tester, delays = self.scheduled("wave", 2.0, repeat=True)
        native = [
            delay for delay, _, _ in GamepadTestWindow._rumble_pattern(None, "wave")
        ]
        self.assertEqual(delays[:-1], [round(d * 500 / 900) for d in native])
        self.assertEqual(delays[-1], 500)

    def test_restarting_cancels_the_previous_cycle(self):
        tester = self.make_tester(repeat=True)
        tester.rumble_repeat_hz_var.set(2.0)
        tester.play_rumble("machine_gun")
        first_cycle = list(tester._rumble_jobs[1])
        tester._repeat_rumble_pattern("machine_gun", 1)
        self.assertTrue(set(first_cycle) <= tester.window.cancelled)
        self.assertNotEqual(tester._rumble_jobs[1], first_cycle)

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

    @staticmethod
    def pulses(pattern):
        """Return (on, off, peak) for each non-silent run of a pattern."""
        found = []
        start = None
        peak = 0.0
        for delay, left, right in pattern:
            level = max(left, right)
            if level > 0.0 and start is None:
                start, peak = delay, level
            elif level > 0.0:
                peak = max(peak, level)
            elif start is not None:
                found.append((start, delay, peak))
                start = None
        return found

    def test_short_effects_keep_their_own_timing_at_one_hz(self):
        # Each effect lasts one second at its own timing.
        for name in (
            "impact", "double_tap", "alert", "heartbeat", "machine_gun",
            "footsteps", "engine", "rotor", "countdown",
        ):
            pattern = GamepadTestWindow._rumble_pattern(None, name)
            self.assertEqual(max(delay for delay, _, _ in pattern), 1000, name)

    def test_double_tap_matches_the_pin_cue(self):
        # rumble_protocol.PIN_FEEDBACK_PATTERN: 80 ms on, 100 ms off, 80 ms on.
        taps = self.pulses(GamepadTestWindow._rumble_pattern(None, "double_tap"))
        self.assertEqual([(on, off) for on, off, _ in taps], [(0, 80), (180, 260)])
        pattern = GamepadTestWindow._rumble_pattern(None, "double_tap")
        self.assertEqual(
            {(left, right) for _, left, right in pattern if left or right},
            {(1.0, 1.0)},
        )

    def test_footsteps_are_two_short_heel_strikes_per_second(self):
        steps = self.pulses(GamepadTestWindow._rumble_pattern(None, "footsteps"))
        self.assertEqual([on for on, _, _ in steps], [0, 500])
        self.assertTrue(all(off - on <= 90 for on, off, _ in steps))
        self.assertNotEqual(steps[0][2], steps[1][2])

    def test_engine_idles_with_fast_lf_pulsation(self):
        pattern = GamepadTestWindow._rumble_pattern(None, "engine")
        body = [step for step in pattern if step[0] < 1000]
        self.assertEqual(len(self.pulses(pattern)), 1)  # continuous
        self.assertEqual({b[0] - a[0] for a, b in zip(body, body[1:])}, {40})
        levels = [left for _, left, _ in body]
        self.assertTrue(all(a != b for a, b in zip(levels, levels[1:])))
        self.assertTrue(all(left > right for _, left, right in body))

    def test_rotor_thumps_five_times_per_second_over_a_hum(self):
        pattern = GamepadTestWindow._rumble_pattern(None, "rotor")
        thumps = [delay for delay, left, _ in pattern if left >= 0.7]
        self.assertEqual(thumps, [0, 200, 400, 600, 800])
        hum = [left for delay, left, _ in pattern if 0 < delay < 1000 and left < 0.2]
        self.assertTrue(hum and all(level > 0.0 for level in hum))

    def test_terrain_grain_fills_the_cycle_at_its_own_size(self):
        pattern = GamepadTestWindow._rumble_pattern(None, "terrain")
        delays = [delay for delay, _, _ in pattern]
        self.assertGreaterEqual(max(delays), 950)
        self.assertEqual({b - a for a, b in zip(delays, delays[1:])}, {65})

    def test_machine_gun_fires_short_separated_shots(self):
        pattern = GamepadTestWindow._rumble_pattern(None, "machine_gun")
        shots = self.pulses(pattern)
        self.assertEqual(len(shots), 10)
        for on, off, peak in shots:
            self.assertLessEqual(off - on, 60)
            self.assertGreaterEqual(peak, 0.8)
        self.assertEqual({b[0] - a[0] for a, b in zip(shots, shots[1:])}, {100})
        # Every shot carries a recoil kick, not only a high-frequency buzz.
        firsts = [step for step in pattern if step[0] % 100 == 0 and step[1] > 0]
        self.assertTrue(all(left >= 0.5 for _, left, _ in firsts))
        # Between shots the motors are fully released.
        self.assertTrue(all(
            (left, right) == (0.0, 0.0)
            for delay, left, right in pattern if delay % 100 == 60
        ))

    def test_countdown_is_three_ticks_then_a_strong_go(self):
        pattern = GamepadTestWindow._rumble_pattern(None, "countdown")
        pulses = self.pulses(pattern)
        self.assertEqual(len(pulses), 4)
        ticks, go = pulses[:3], pulses[3]
        # Three identical short ticks at an even pace ...
        self.assertEqual({off - on for on, off, _ in ticks}, {60})
        self.assertEqual({peak for _, _, peak in ticks}, {0.70})
        self.assertEqual({b[0] - a[0] for a, b in zip(ticks, ticks[1:])}, {200})
        # ... then a longer, stronger GO that also carries LF.
        self.assertGreater(go[1] - go[0], 200)
        self.assertGreater(go[2], 0.70)
        self.assertTrue(any(
            left > 0.5 for delay, left, _ in pattern if delay >= go[0]
        ))
        self.assertTrue(all(
            left == 0.0 for delay, left, _ in pattern if delay < go[0]
        ))

    def test_heartbeat_is_lub_dub_then_a_long_rest(self):
        pattern = GamepadTestWindow._rumble_pattern(None, "heartbeat")
        cycle = max(delay for delay, _, _ in pattern)
        beats = self.pulses(pattern)
        self.assertEqual(len(beats), 2)
        (first_on, first_off, first_peak), (second_on, second_off, second_peak) = beats
        # Two short thumps, the first stronger, close together ...
        self.assertLessEqual(first_off - first_on, cycle * 0.15)
        self.assertLessEqual(second_off - second_on, cycle * 0.15)
        self.assertGreater(first_peak, second_peak)
        self.assertLess(second_on - first_off, cycle * 0.2)
        # ... then a rest longer than the lub-dub itself.
        self.assertGreater(cycle - second_off, second_off)
        # LF carries the thump.
        self.assertTrue(all(left >= right for _, left, right in pattern))

    def test_near_duplicate_templates_are_gone(self):
        for name in ("rapid_fire", "low_rumble", "burst", "shotgun", "turbo"):
            self.assertIsNone(GamepadTestWindow._rumble_pattern(None, name))


if __name__ == "__main__":
    unittest.main()
