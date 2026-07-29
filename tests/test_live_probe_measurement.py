import contextlib
import io
import struct
import sys
import threading
import unittest
from collections import OrderedDict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import live_input_probe
import live_transport_probe
from input_dispatcher import InputDispatcher


PROBE_MODULES = (live_input_probe, live_transport_probe)


def report(report_time, buttons, marker):
    payload = bytearray(64)
    struct.pack_into("<II", payload, 0, report_time, buttons)
    payload[20] = marker
    return bytes(payload)


class LiveProbeSubmissionTests(unittest.TestCase):
    def test_submission_entry_retains_identity_and_latest_timestamp(self):
        for module in PROBE_MODULES:
            with self.subTest(module=module.__name__):
                entries = OrderedDict()
                payload = report(1, 0, 1)

                module._remember_submission(entries, payload, 100)
                self.assertIs(entries[id(payload)][0], payload)
                module._remember_submission(entries, payload, 200)

                self.assertEqual(module._take_submission(entries, payload), 200)
                self.assertEqual(entries, {})

    def test_submission_limit_evicts_oldest_identity(self):
        for module in PROBE_MODULES:
            with self.subTest(module=module.__name__):
                entries = OrderedDict()
                payloads = [report(index, 0, index) for index in range(3)]
                for index, payload in enumerate(payloads):
                    module._remember_submission(
                        entries, payload, 100 + index, limit=2
                    )

                self.assertIsNone(
                    module._take_submission(entries, payloads[0])
                )
                self.assertEqual(
                    module._take_submission(entries, payloads[1]), 101
                )
                self.assertEqual(
                    module._take_submission(entries, payloads[2]), 102
                )

    def test_identity_mismatch_never_returns_an_unrelated_timestamp(self):
        for module in PROBE_MODULES:
            with self.subTest(module=module.__name__):
                entries = OrderedDict()
                payload = report(1, 0, 1)
                unrelated = report(2, 0, 2)
                entries[id(payload)] = (unrelated, 123)

                self.assertIsNone(module._take_submission(entries, payload))

    def test_batch_timestamps_survive_cross_worker_dispatch(self):
        for module in PROBE_MODULES:
            with self.subTest(module=module.__name__):
                entries = OrderedDict()
                lock = threading.Lock()
                received = []
                completed = threading.Event()
                payloads = (
                    report(1, 0, 1),
                    report(2, 1, 2),
                    report(3, 1, 3),
                    report(4, 0, 4),
                )

                def callback(payload):
                    with lock:
                        received.append((
                            payload[20],
                            module._take_submission(entries, payload),
                        ))
                        if len(received) == 3:
                            completed.set()

                dispatcher = InputDispatcher(
                    callback, inline_fast_path=False
                )
                try:
                    with lock:
                        for timestamp, payload in enumerate(
                            payloads, start=100
                        ):
                            module._remember_submission(
                                entries, payload, timestamp
                            )
                    dispatcher.submit_batch(payloads)
                    self.assertTrue(completed.wait(1.0))
                    self.assertEqual(
                        received,
                        [(1, 100), (2, 101), (4, 103)],
                    )
                finally:
                    dispatcher.stop()


class LiveTransportCliTests(unittest.TestCase):
    def test_inline_defaults_to_production_and_old_flag_still_works(self):
        parser = live_transport_probe._build_argument_parser()

        self.assertTrue(parser.parse_args(["--mode", "wired"]).inline)
        self.assertTrue(
            parser.parse_args(["--mode", "wired", "--inline"]).inline
        )
        self.assertFalse(
            parser.parse_args(["--mode", "wired", "--no-inline"]).inline
        )

    def test_inline_flags_remain_mutually_exclusive(self):
        parser = live_transport_probe._build_argument_parser()
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                parser.parse_args([
                    "--mode", "wired", "--inline", "--no-inline",
                ])


if __name__ == "__main__":
    unittest.main()
