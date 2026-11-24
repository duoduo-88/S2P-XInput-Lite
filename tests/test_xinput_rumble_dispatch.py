import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from xinput_controller import (
    HF_FOLLOW_RANGE,
    LF_FOLLOW_RANGE,
    XInputController,
    _blend_frequency,
    _followed_frequency,
)


class _AdvancingCondition:
    def __init__(self, clock, on_wait=None):
        self.clock = clock
        self.on_wait = on_wait
        self.timeouts = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def wait(self, timeout=None):
        self.timeouts.append(timeout)
        if timeout is not None:
            self.clock[0] += timeout
        if self.on_wait is not None:
            self.on_wait()
        return True


class XInputRumbleDispatchTests(unittest.TestCase):
    @staticmethod
    def make_controller():
        controller = XInputController.__new__(XInputController)
        controller._rumble_running = True
        controller._rumble_sequence = 7
        controller._rumble_timer_period = False
        return controller

    def _mix_output(self, ratio, game, audio):
        controller = self.make_controller()
        controller._rumble_condition = threading.Condition()
        controller.audio_haptics_mode = "MIX"
        controller.audio_haptics_mix_ratio = ratio
        controller.max_amplitude = 1000
        controller.lf_frequency = 215
        controller.hf_frequency = 320
        controller._game_rumble = game
        controller._audio_rumble = audio
        queued = []
        controller._queue_rumble = (
            lambda lf, hf, **_kwargs: queued.append((lf, hf))
        )
        controller._publish_rumble_mix(priority=True)
        return queued[-1]

    def assertMix(self, ratio, game, audio, expected):
        # _soft_mix truncates to int, so allow one amplitude step.
        actual = self._mix_output(ratio, game, audio)
        for got, want in zip(actual, expected):
            self.assertLessEqual(abs(got - want), 1, (ratio, actual, expected))

    def test_mix_keeps_full_game_rumble_until_half_ratio(self):
        for ratio in (0.0, 0.2, 0.5):
            self.assertMix(ratio, (600, 400), (0, 0), (600, 400))
        self.assertMix(0.75, (600, 400), (0, 0), (300, 200))
        self.assertMix(1.0, (600, 400), (0, 0), (0, 0))

    def test_mix_audio_share_follows_ratio(self):
        self.assertMix(0.0, (0, 0), (500, 500), (0, 0))
        self.assertMix(0.2, (0, 0), (500, 500), (100, 100))
        self.assertMix(1.0, (0, 0), (500, 500), (500, 500))

    def test_followed_frequency_is_off_at_zero_and_octave_scaled(self):
        self.assertEqual(_followed_frequency(215, 1.0, 0.0, LF_FOLLOW_RANGE), 215)
        self.assertEqual(_followed_frequency(215, 0.0, 1.0, LF_FOLLOW_RANGE), 215)
        self.assertEqual(
            _followed_frequency(215, 1.0, 1.0, LF_FOLLOW_RANGE),
            round(215 * 2 ** 0.25),
        )
        self.assertEqual(
            _followed_frequency(215, -1.0, 1.0, LF_FOLLOW_RANGE),
            round(215 * 2 ** -0.25),
        )
        self.assertEqual(
            _followed_frequency(215, 1.0, 0.5, LF_FOLLOW_RANGE),
            round(215 * 2 ** 0.125),
        )

    def test_followed_frequency_stays_in_the_safe_range(self):
        self.assertEqual(_followed_frequency(320, 1.0, 1.0, HF_FOLLOW_RANGE), 350)
        self.assertEqual(_followed_frequency(170, -1.0, 1.0, LF_FOLLOW_RANGE), 160)
        # A user base outside the range is kept, never pulled into it.
        self.assertEqual(_followed_frequency(400, 1.0, 1.0, HF_FOLLOW_RANGE), 400)
        self.assertEqual(_followed_frequency(400, -1.0, 1.0, HF_FOLLOW_RANGE), 336)

    def test_blend_frequency_follows_the_stronger_source(self):
        self.assertEqual(_blend_frequency(215, 255, 0, 0), 215)
        self.assertEqual(_blend_frequency(215, 255, 500, 0), 215)
        self.assertEqual(_blend_frequency(215, 255, 0, 500), 255)
        middle = _blend_frequency(215, 255, 300, 300)
        self.assertGreater(middle, 215)
        self.assertLess(middle, 255)

    def _audio_controller(self, mode, follow):
        controller = self.make_controller()
        controller._rumble_condition = threading.Condition()
        controller.audio_haptics_mode = mode
        controller.audio_haptics_mix_ratio = 0.5
        controller.audio_freq_follow = follow
        controller.max_amplitude = 1000
        controller.lf_curve = controller.hf_curve = 1.0
        controller.lf_strength = controller.hf_strength = 1.0
        controller.lf_to_hf_compensation = 0.0
        controller.hf_to_lf_compensation = 0.0
        controller.lf_frequency = 215
        controller.hf_frequency = 320
        controller._game_rumble = (0, 0)
        controller._audio_rumble = (0, 0)
        controller._audio_frequency = (215, 320)
        queued = []
        controller._queue_rumble = (
            lambda lf, hf, **kwargs: queued.append(
                (lf, hf, kwargs.get("frequencies"))
            )
        )
        return controller, queued

    def test_audio_mode_sends_followed_frequencies(self):
        controller, queued = self._audio_controller("AUDIO", 1.0)
        controller.set_audio_rumble(0.5, 0.5, -1.0, 1.0)
        self.assertEqual(queued[-1][2], (round(215 * 2 ** -0.25), 350))

    def test_follow_off_keeps_configured_frequencies(self):
        controller, queued = self._audio_controller("AUDIO", 0.0)
        controller.set_audio_rumble(0.5, 0.5, -1.0, 1.0)
        self.assertEqual(queued[-1][2], (215, 320))

    def test_frequency_change_alone_is_published(self):
        controller, queued = self._audio_controller("AUDIO", 1.0)
        controller.set_audio_rumble(0.5, 0.5, 0.0, 0.0)
        controller.set_audio_rumble(0.5, 0.5, 1.0, 0.0)
        self.assertEqual(len(queued), 2)
        self.assertNotEqual(queued[0][2], queued[1][2])

    def test_mix_frequency_is_led_by_game_when_audio_is_silent(self):
        controller, queued = self._audio_controller("MIX", 1.0)
        controller._audio_frequency = (255, 350)
        controller._game_rumble = (600, 600)
        controller._publish_rumble_mix(priority=True)
        self.assertEqual(queued[-1][2], (215, 320))

    def test_active_refresh_uses_positive_one_ms_slices_and_deadline(self):
        controller = self.make_controller()
        clock = [10.0]
        condition = _AdvancingCondition(clock)
        controller._rumble_condition = condition

        with patch(
            "xinput_controller.time.perf_counter",
            side_effect=lambda: clock[0],
        ):
            controller._wait_for_rumble_refresh(7, output_active=True)

        self.assertGreater(len(condition.timeouts), 1)
        self.assertTrue(all(
            timeout is not None and 0.0 < timeout <= 0.001
            for timeout in condition.timeouts
        ))
        self.assertAlmostEqual(clock[0], 10.015, places=9)

    def test_dispatch_loop_finally_restores_active_timer_period(self):
        controller = self.make_controller()
        timer_period = object()
        controller._rumble_timer_period = timer_period

        def fail():
            raise RuntimeError("injected dispatch failure")

        controller._rumble_dispatch_loop_impl = fail
        with (
            patch(
                "xinput_controller._end_windows_timer_period",
            ) as end_timer,
            self.assertRaisesRegex(RuntimeError, "injected"),
        ):
            controller._rumble_dispatch_loop()

        end_timer.assert_called_once_with(timer_period)
        self.assertIs(controller._rumble_timer_period, False)

    def test_timer_period_is_held_across_one_active_window(self):
        controller = self.make_controller()
        timer_period = object()
        with (
            patch(
                "xinput_controller._begin_windows_timer_period",
                return_value=timer_period,
            ) as begin_timer,
            patch(
                "xinput_controller._end_windows_timer_period",
            ) as end_timer,
        ):
            controller._begin_rumble_timer_window()
            controller._begin_rumble_timer_window()
            controller._end_rumble_timer_window()
            controller._end_rumble_timer_window()

        begin_timer.assert_called_once_with()
        end_timer.assert_called_once_with(timer_period)
        self.assertIs(controller._rumble_timer_period, False)

    def test_new_sequence_ends_active_wait_after_notification_slice(self):
        controller = self.make_controller()
        clock = [20.0]

        def publish_new_state():
            controller._rumble_sequence += 1

        condition = _AdvancingCondition(clock, on_wait=publish_new_state)
        controller._rumble_condition = condition

        with patch(
            "xinput_controller.time.perf_counter",
            side_effect=lambda: clock[0],
        ):
            controller._wait_for_rumble_refresh(7, output_active=True)

        self.assertEqual(condition.timeouts, [0.001])
        self.assertAlmostEqual(clock[0], 20.001, places=9)

    def test_inactive_wait_remains_notification_driven(self):
        controller = self.make_controller()
        condition = _AdvancingCondition([30.0])
        controller._rumble_condition = condition

        with patch(
            "xinput_controller._begin_windows_timer_period",
        ) as begin_timer:
            controller._wait_for_rumble_refresh(7, output_active=False)

        self.assertEqual(condition.timeouts, [None])
        begin_timer.assert_not_called()

    def test_pending_priority_survives_latest_nonpriority_payload_until_claim(self):
        controller = self.make_controller()
        controller._rumble_condition = threading.Condition()
        controller._rumble_sender_supports_priority = True
        controller._rumble_state = None
        controller._rumble_force_zero = False
        controller._rumble_priority = False
        controller._rumble_sequence = 0
        controller._rumble_peak_output = (0, 0)
        controller.lf_frequency = 90
        controller.hf_frequency = 180
        controller.final_tail_decay_ms = 100.0
        controller.final_tail_strength = 0.0
        controller.max_amplitude = 1000
        sent = []

        controller._queue_rumble(
            0, 0, force_zero=True, priority=True
        )
        controller._queue_rumble(
            700, 500, force_zero=False, priority=False
        )

        self.assertEqual(controller._rumble_state, (90, 700, 180, 500))
        self.assertFalse(controller._rumble_force_zero)
        self.assertTrue(controller._rumble_priority)

        def sender(*state, **metadata):
            sent.append((state, metadata, controller._rumble_priority))
            with controller._rumble_condition:
                controller._rumble_running = False
                controller._rumble_condition.notify_all()

        controller._rumble_sender = sender
        timer_period = object()
        with (
            patch(
                "xinput_controller._begin_windows_timer_period",
                return_value=timer_period,
            ) as begin_timer,
            patch(
                "xinput_controller._end_windows_timer_period",
            ) as end_timer,
        ):
            controller._rumble_dispatch_loop()

        self.assertEqual(sent[0][0], (90, 700, 180, 500))
        self.assertEqual(
            sent[0][1],
            {"priority": True, "force_zero": False},
        )
        self.assertFalse(sent[0][2])
        self.assertFalse(controller._rumble_priority)
        begin_timer.assert_called_once_with()
        end_timer.assert_called_once_with(timer_period)

    def test_dispatch_loop_uses_perf_counter_for_tail_delta(self):
        controller = self.make_controller()
        controller._rumble_condition = threading.Condition()
        controller._rumble_sender_supports_priority = False
        controller._rumble_state = (90, 120, 180, 80)
        controller._rumble_force_zero = False
        controller._rumble_priority = False
        controller.final_tail_decay_ms = 100.0
        controller.final_tail_strength = 0.0
        controller.max_amplitude = 1000
        sent = []

        def sender(*state):
            sent.append(state)
            with controller._rumble_condition:
                controller._rumble_running = False
                controller._rumble_condition.notify_all()

        controller._rumble_sender = sender
        clock = iter((40.0, 40.004, 40.005))
        with (
            patch(
                "xinput_controller.time.perf_counter",
                side_effect=lambda: next(clock),
            ),
            patch(
                "xinput_controller.time.monotonic",
                side_effect=AssertionError("coarse clock used"),
            ),
        ):
            controller._rumble_dispatch_loop()

        self.assertEqual(sent, [(90, 120, 180, 80)])


if __name__ == "__main__":
    unittest.main()
