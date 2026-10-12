"""Truncated wired reports must not manufacture controller input."""
import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from switch2_input import parse_input_report
from wired_controller import (
    REPORT_ID_COMMON,
    REPORT_ID_PRO2,
    translate_usb_report,
)


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

    def test_complete_common_report_keeps_sticks_and_imu_data(self):
        body = bytearray(60)
        body[10:16] = bytes((0x34, 0x12, 0x56, 0x78, 0x09, 0xAB))
        body[48:54] = bytes((1, 0, 2, 0, 3, 0))
        body[54:60] = bytes((4, 0, 5, 0, 6, 0))

        state = parse_input_report(translate_usb_report(
            bytes([REPORT_ID_COMMON]) + body
        ))

        self.assertEqual(state.left_stick, (0x234, 0x561))
        self.assertEqual(state.right_stick, (0x978, 0xAB0))
        self.assertEqual(state.accelerometer, (1, 2, 3))
        self.assertEqual(state.gyroscope, (4, 5, 6))

    def test_complete_pro2_report_still_translates(self):
        report = bytes((REPORT_ID_PRO2, 7, 0x0C, 0x01, 0x02, 0x04,
                        0x11, 0x22, 0x33, 0x44, 0x55, 0x66))

        translated = translate_usb_report(report)
        state = parse_input_report(translated)

        self.assertEqual(len(translated), 64)
        self.assertEqual(state.left_stick, (0x211, 0x332))
        self.assertEqual(state.right_stick, (0x544, 0x665))
