import sys
import threading
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from xinput_controller import XInputController


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
