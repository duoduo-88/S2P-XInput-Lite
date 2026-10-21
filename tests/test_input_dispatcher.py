import threading
import time
import unittest
import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC_DIR))

from input_dispatcher import InputDispatcher


def report(buttons=0, marker=0):
    payload = bytearray(16)
    payload[4:8] = int(buttons).to_bytes(4, "little")
    payload[8] = marker & 0xFF
    return bytes(payload)


class InputDispatcherTests(unittest.TestCase):
    def test_input_rate_counts_identical_and_batched_reports(self):
        dispatcher = InputDispatcher(lambda _payload: None)
        try:
            with dispatcher._lock:
                dispatcher._record_input_rate_locked(1, 10.0)
                for index in range(1, 67):
                    dispatcher._record_input_rate_locked(
                        2, 10.0 + index / 132.0
                    )

            self.assertAlmostEqual(dispatcher.input_rate_hz, 264.0, places=6)
        finally:
            dispatcher.stop()

    def test_reset_clears_input_rate_window(self):
        dispatcher = InputDispatcher(lambda _payload: None)
        try:
            with dispatcher._lock:
                dispatcher._record_input_rate_locked(1, 10.0)
                dispatcher._record_input_rate_locked(66, 10.5)
            self.assertEqual(dispatcher.input_rate_hz, 132.0)

            self.assertTrue(dispatcher.reset())
            self.assertIsNone(dispatcher.input_rate_hz)
            self.assertIsNone(dispatcher._input_rate_window_started)
            self.assertEqual(dispatcher._input_rate_count, 0)
        finally:
            dispatcher.stop()

    def test_button_edges_are_preserved_and_callback_is_serial(self):
        received = []
        active = 0
        max_active = 0
        lock = threading.Lock()

        def callback(payload):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.001)
            received.append(int.from_bytes(payload[4:8], "little"))
            with lock:
                active -= 1

        dispatcher = InputDispatcher(callback, inline_fast_path=False)
        try:
            dispatcher.submit_batch([
                report(0, 1),
                report(1, 2),
                report(1, 3),
                report(0, 4),
            ])
            deadline = time.time() + 1.0
            while time.time() < deadline and len(received) < 3:
                time.sleep(0.005)
            self.assertEqual(max_active, 1)
            self.assertIn(1, received)
            self.assertEqual(received[-1], 0)
        finally:
            dispatcher.stop()

    def test_analog_backlog_coalesces_to_latest_snapshot(self):
        received = []
        gate = threading.Event()

        def callback(payload):
            received.append(payload[8])
            if payload[8] == 1:
                gate.wait(0.1)

        dispatcher = InputDispatcher(callback, inline_fast_path=False)
        try:
            dispatcher.submit(report(0, 1))
            time.sleep(0.01)
            for marker in range(2, 20):
                dispatcher.submit(report(0, marker))
            gate.set()
            deadline = time.time() + 1.0
            while time.time() < deadline and (not received or received[-1] != 19):
                time.sleep(0.005)
            self.assertEqual(received[-1], 19)
            self.assertLessEqual(len(received), 3)
        finally:
            gate.set()
            dispatcher.stop()

    def test_stop_uses_one_wall_clock_deadline(self):
        entered = threading.Event()
        release = threading.Event()

        def callback(_payload):
            entered.set()
            release.wait(1.0)

        dispatcher = InputDispatcher(callback, inline_fast_path=False)
        dispatcher.submit(report(0, 1))
        self.assertTrue(entered.wait(1.0))

        started = time.perf_counter()
        stopped = dispatcher.stop(timeout=0.05)
        elapsed = time.perf_counter() - started

        self.assertFalse(stopped)
        self.assertLess(elapsed, 0.15)
        release.set()
        dispatcher._thread.join(1.0)
        self.assertFalse(dispatcher._thread.is_alive())


if __name__ == "__main__":
    unittest.main()
