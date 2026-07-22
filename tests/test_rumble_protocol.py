import sys
import unittest
from pathlib import Path


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from rumble_protocol import (
    AMPLITUDE_MAX,
    CONNECTION_FEEDBACK_PATTERN,
    CONNECTION_HF_FREQUENCY,
    CONNECTION_LF_FREQUENCY,
    FREQUENCY_MAX,
    PIN_FEEDBACK_PATTERN,
    PIN_HF_FREQUENCY,
    PIN_LF_FREQUENCY,
    STRESS_AMPLITUDES,
    encode_vibration_frame,
)
from wired_controller import _rumble_active


def unpack_frame(frame):
    value = int.from_bytes(frame, byteorder="little")
    return (
        value & FREQUENCY_MAX,
        (value >> 10) & AMPLITUDE_MAX,
        (value >> 20) & FREQUENCY_MAX,
        (value >> 30) & AMPLITUDE_MAX,
    )


class RumbleProtocolTests(unittest.TestCase):
    @staticmethod
    def _transport_payload(frame):
        segment = b"\x50" + frame * 3
        return b"\x00" + segment + segment

    def test_fields_preserve_their_full_protocol_ranges(self):
        self.assertEqual(
            encode_vibration_frame(511, 1023, 511, 1023),
            b"\xff\xfd\xff\xdf\xff",
        )
        self.assertEqual(
            unpack_frame(encode_vibration_frame(225, 512, 481, 1023)),
            (225, 512, 481, 1023),
        )
        self.assertEqual(
            encode_vibration_frame(225, 0, 481, 0),
            b"\xe1\x00\x10\x1e\x00",
        )

    def test_v051_feedback_frame_is_byte_identical(self):
        self.assertEqual(
            encode_vibration_frame(225, 800, 481, 800),
            bytes.fromhex("e1801c1ec8"),
        )

    def test_wired_activity_detection_uses_ten_bit_slot_boundaries(self):
        frequency_only = encode_vibration_frame(225, 0, 481, 0)
        active = encode_vibration_frame(225, 1, 481, 1)

        self.assertFalse(_rumble_active(self._transport_payload(frequency_only)))
        self.assertTrue(_rumble_active(self._transport_payload(active)))

    def test_fields_are_clamped_without_wrapping(self):
        self.assertEqual(
            unpack_frame(encode_vibration_frame(-1, 2048, 512, -20)),
            (0, 1023, 511, 0),
        )

    def test_connection_feedback_matches_v051_two_pulse_cue(self):
        self.assertEqual(CONNECTION_LF_FREQUENCY, 225)
        self.assertEqual(CONNECTION_HF_FREQUENCY, 481)
        self.assertEqual(CONNECTION_FEEDBACK_PATTERN[-1][:2], (0, 0))
        active = [
            step for step in CONNECTION_FEEDBACK_PATTERN
            if step[0] or step[1]
        ]
        self.assertTrue(all((lf_amp, hf_amp) == (800, 800) for lf_amp, hf_amp, _ in active))
        self.assertLessEqual(
            max(max(lf_amp, hf_amp) for lf_amp, hf_amp, _ in CONNECTION_FEEDBACK_PATTERN),
            800,
        )
        self.assertEqual(
            sum(
                1 for index, step in enumerate(CONNECTION_FEEDBACK_PATTERN)
                if (step[0] or step[1])
                and (
                    index == 0
                    or not any(CONNECTION_FEEDBACK_PATTERN[index - 1][:2])
                )
            ),
            2,
        )
        self.assertLessEqual(max(STRESS_AMPLITUDES), 450)

    def test_pin_feedback_matches_connection_cue(self):
        self.assertEqual(PIN_LF_FREQUENCY, 225)
        self.assertEqual(PIN_HF_FREQUENCY, 481)
        active = [step for step in PIN_FEEDBACK_PATTERN if step[0] or step[1]]
        self.assertTrue(active)
        self.assertEqual(PIN_FEEDBACK_PATTERN, CONNECTION_FEEDBACK_PATTERN)
        self.assertEqual(max(lf_amp for lf_amp, _, _ in active), 800)
        self.assertLessEqual(sum(duration for _, _, duration in active), 0.22)
        self.assertEqual(
            sum(
                1 for index, step in enumerate(PIN_FEEDBACK_PATTERN)
                if (step[0] or step[1])
                and (
                    index == 0
                    or not any(PIN_FEEDBACK_PATTERN[index - 1][:2])
                )
            ),
            2,
        )
        self.assertEqual(PIN_FEEDBACK_PATTERN[-1], (0, 0, 0.0))


if __name__ == "__main__":
    unittest.main()
