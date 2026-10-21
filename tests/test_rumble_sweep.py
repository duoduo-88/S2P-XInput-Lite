import sys
import unittest
from pathlib import Path


TESTS_DIR = Path(__file__).resolve().parent
SRC_DIR = TESTS_DIR.parent / "src"
for path in (str(SRC_DIR), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from live_rumble_sweep import parse_values, send_rumble, stop_rumble
from rumble_protocol import CONNECTION_HF_FREQUENCY, CONNECTION_LF_FREQUENCY


class _FakeTransport:
    def __init__(self):
        self.calls = []

    def send_pro_rumble(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return True

    def send_pro_rumble_latest(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return True


class RumbleSweepTests(unittest.TestCase):
    def test_default_and_custom_frequency_lists(self):
        self.assertEqual(parse_values(None, "lf"), (190, 205, 215, 225))
        self.assertEqual(parse_values("315, 330,350", "hf"), (315, 330, 350))
        with self.assertRaises(ValueError):
            parse_values("512", "hf")

    def test_wired_stop_uses_zero_amplitudes_and_priority(self):
        transport = _FakeTransport()
        stop_rumble(transport, "wired")
        args, kwargs = transport.calls[-1]
        self.assertEqual(
            args,
            (CONNECTION_LF_FREQUENCY, 0, CONNECTION_HF_FREQUENCY, 0),
        )
        self.assertTrue(kwargs["priority"])
        self.assertTrue(kwargs["force_zero"])

    def test_esp32_active_send_uses_latest_only_interface(self):
        transport = _FakeTransport()
        self.assertTrue(send_rumble(transport, "esp32", 215, 300, 330, 0))
        args, kwargs = transport.calls[-1]
        self.assertEqual(args, (215, 300, 330, 0))
        self.assertTrue(kwargs["priority"])
        self.assertFalse(kwargs["force_zero"])


if __name__ == "__main__":
    unittest.main()
