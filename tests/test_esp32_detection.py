import unittest
from types import SimpleNamespace
from unittest.mock import patch

from esp32_detection import (
    _port_priority,
    find_esp32_port,
)


class _UnavailableSerial:
    def __init__(self, *_args, **_kwargs):
        raise OSError("temporarily unavailable")


class ESP32DetectionTests(unittest.TestCase):
    def test_standalone_usb_personality_has_native_priority(self):
        port = SimpleNamespace(device="COM8", vid=0xCAFE, pid=0x4020)
        self.assertEqual(_port_priority(port)[0], 0)

    def test_known_standalone_cdc_port_is_safe_fallback(self):
        port = SimpleNamespace(device="COM8", vid=0xCAFE, pid=0x4021)
        with patch("esp32_detection.serial.Serial", _UnavailableSerial):
            self.assertEqual(find_esp32_port(port_infos=[port]), "COM8")

    def test_unrelated_unresponsive_port_is_not_accepted(self):
        port = SimpleNamespace(device="COM9", vid=0x1234, pid=0x5678)
        with patch("esp32_detection.serial.Serial", _UnavailableSerial):
            self.assertIsNone(find_esp32_port(port_infos=[port]))
