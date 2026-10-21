import unittest
from types import SimpleNamespace
from unittest.mock import patch

from esp32_detection import (
    _is_bridge_status,
    _port_priority,
    find_esp32_firmware,
    find_esp32_port,
)


class _UnavailableSerial:
    def __init__(self, *_args, **_kwargs):
        raise OSError("temporarily unavailable")


class ESP32DetectionTests(unittest.TestCase):
    def test_current_s2p_firmware_identity_is_accepted(self):
        self.assertTrue(_is_bridge_status({
            "cmd": "status",
            "product": "S2P-FW",
            "version": "1.0.0",
            "protocol": "s2p_bridge",
            "protocol_version": "1.0.0",
            "profile": "s2p_usb_bridge",
            "build": "standalone_diagnostics",
        }))

    def test_last_pre_1_0_s2p_bundle_is_upgrade_only_legacy(self):
        self.assertTrue(_is_bridge_status({
            "cmd": "status",
            "version": "0.14.3",
            "profile": "tinyusb_direct",
            "build": "cdc_bridge_2_lowlatency",
        }))

    def test_upstream_firmware_is_not_claimed_as_compatible(self):
        self.assertFalse(_is_bridge_status({
            "cmd": "status",
            "version": "0.12.4",
            "profile": "tinyusb_direct",
            "build": "cdc_bridge_2_lowlatency",
        }))

    def test_standalone_usb_personality_has_native_priority(self):
        port = SimpleNamespace(device="COM8", vid=0xCAFE, pid=0x4020)
        self.assertEqual(_port_priority(port)[0], 0)

    def test_unresponsive_candidate_never_synthesizes_identity(self):
        for vid, pid in ((0xCAFE, 0x4020), (0xCAFE, 0x4021),
                         (0xCAFE, 0x4022), (0x303A, 0x4005)):
            with self.subTest(vid=vid, pid=pid):
                port = SimpleNamespace(device="COM8", vid=vid, pid=pid)
                with patch("esp32_detection.serial.Serial", _UnavailableSerial):
                    self.assertIsNone(find_esp32_firmware(port_infos=[port]))

    def test_old_auto_usb_personality_is_an_upgrade_candidate(self):
        self.assertEqual(_port_priority(SimpleNamespace(
            device="COM8", vid=0xCAFE, pid=0x4022))[0], 0)

    def test_unrelated_unresponsive_port_is_not_accepted(self):
        port = SimpleNamespace(device="COM9", vid=0x1234, pid=0x5678)
        with patch("esp32_detection.serial.Serial", _UnavailableSerial):
            self.assertIsNone(find_esp32_port(port_infos=[port]))
