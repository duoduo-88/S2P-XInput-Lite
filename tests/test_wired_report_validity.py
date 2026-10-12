"""Truncated wired reports must not manufacture controller input."""
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from wired_controller import REPORT_ID_COMMON, translate_usb_report


class WiredReportValidityTests(unittest.TestCase):
    def test_truncated_full_reports_are_discarded(self):
        for body_size in (0, 8, 15, 32, 59):
            with self.subTest(body_size=body_size):
                frame = bytes([REPORT_ID_COMMON]) + bytes(body_size)
                self.assertIsNone(translate_usb_report(frame))

    def test_complete_full_reports_are_preserved(self):
        body = bytes(range(60))
        self.assertEqual(
            translate_usb_report(bytes([REPORT_ID_COMMON]) + body),
            body,
        )

    def test_unrecognized_report_is_ignored(self):
        self.assertIsNone(translate_usb_report(b"\xff" + bytes(64)))
