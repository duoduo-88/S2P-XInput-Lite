from types import SimpleNamespace
import threading
import unittest

from idle_disconnect import (
    IdleActivityTracker,
    normalize_idle_disconnect_minutes,
    perform_idle_disconnect,
)
from input_dispatcher import InputDispatcher


def _state(buttons=0, left=(2048, 2048), right=(2048, 2048), gyro=(0, 0, 0)):
    return SimpleNamespace(
        buttons=buttons,
        left_stick=left,
        right_stick=right,
        gyroscope=gyro,
    )


class IdleDisconnectTests(unittest.TestCase):
    def test_timeout_options_are_normalized(self):
        self.assertEqual(normalize_idle_disconnect_minutes("30"), 30)
        self.assertEqual(normalize_idle_disconnect_minutes(0), 0)
        self.assertEqual(normalize_idle_disconnect_minutes(9), 15)

    def test_unchanged_reports_and_sensor_noise_do_not_keep_awake(self):
        tracker = IdleActivityTracker(5, now=0)
        tracker.observe(_state(), now=0)
        tracker.observe(
            _state(left=(2055, 2044), gyro=(12, -8, 4)), now=299
        )
        self.assertTrue(tracker.expired(now=300))

    def test_deliberate_changes_reset_timeout(self):
        tracker = IdleActivityTracker(5, now=0)
        tracker.observe(_state(), now=0)
        self.assertTrue(tracker.observe(_state(buttons=1), now=10))
        self.assertTrue(
            tracker.observe(_state(buttons=1, left=(2300, 2048)), now=20)
        )
        self.assertTrue(tracker.observe(
            _state(
                buttons=1, left=(2300, 2048), gyro=(180, 0, 0)
            ),
            now=30,
        ))
        self.assertFalse(tracker.expired(now=329))
        self.assertTrue(tracker.expired(now=330))

    def test_disabled_timeout_never_expires(self):
        tracker = IdleActivityTracker(0, now=0)
        tracker.observe(_state(), now=0)
        self.assertFalse(tracker.expired(now=100000))

    def test_failed_disconnect_does_not_publish_idle_disconnected(self):
        events = []
        controller = SimpleNamespace(disconnect_for_idle=lambda: False)
        xinput = SimpleNamespace(
            reset_output_state=lambda: events.append("reset")
        )

        result = perform_idle_disconnect(
            controller,
            lambda reset: reset() is None,
            xinput.reset_output_state,
            lambda **status: events.append(status),
        )

        self.assertFalse(result)
        self.assertEqual(events, ["reset"])

    def test_idle_disconnect_does_not_reset_outputs_while_input_is_running(self):
        entered = threading.Event()
        release = threading.Event()
        events = []

        def process_input(_payload):
            entered.set()
            release.wait(2)
            events.append("input finished")

        dispatcher = InputDispatcher(process_input, inline_fast_path=False)
        try:
            dispatcher.submit(bytes(16))
            self.assertTrue(entered.wait(1))
            controller = SimpleNamespace(disconnect_for_idle=lambda: events.append("disconnect") or True)
            reset = lambda: events.append("reset")
            publish = lambda **status: events.append(status["state"])

            self.assertFalse(perform_idle_disconnect(
                controller, lambda callback: dispatcher.run_exclusive(callback, timeout=0),
                reset, publish,
            ))
            self.assertEqual(events, [])

            release.set()
            self.assertTrue(perform_idle_disconnect(
                controller, dispatcher.run_exclusive, reset, publish,
            ))
            self.assertEqual(events, ["input finished", "reset", "disconnect", "idle_disconnected"])
        finally:
            release.set()
            dispatcher.stop()
