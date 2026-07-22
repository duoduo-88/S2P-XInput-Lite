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


if __name__ == "__main__":
    unittest.main()
