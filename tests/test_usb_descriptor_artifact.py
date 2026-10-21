"""Validate USB interfaces read from the compiled firmware, not C source text."""

import importlib.util
import hashlib
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "inspect_usb_descriptors", ROOT / "scripts" / "inspect_usb_descriptors.py"
)
inspector = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(inspector)

# USB 2.0 CDC ACM + HID, hand-specified interface/endpoint descriptors.
HID_CONFIG = bytes.fromhex(
    "09 02 64 00 03 01 00 a0 32 "
    "08 0b 00 02 02 02 00 00 "
    "09 04 00 00 01 02 02 00 00 "
    "05 24 00 20 01 05 24 01 00 01 04 24 02 02 05 24 06 00 01 "
    "07 05 81 03 08 00 10 "
    "09 04 01 00 02 0a 00 00 00 "
    "07 05 02 02 40 00 00 07 05 82 02 40 00 00 "
    "09 04 02 00 01 03 00 00 00 "
    "09 21 11 01 00 01 22 01 00 "
    "07 05 83 03 20 00 01"
)


class USBDescriptorArtifactTests(unittest.TestCase):
    def test_bundled_evidence_matches_firmware_and_decodes_both_modes(self):
        bundle = ROOT / "esp32s3" / "firmware"
        evidence = json.loads((bundle / "usb-descriptors.json").read_text(encoding="utf-8"))
        self.assertEqual(evidence["application_sha256"], hashlib.sha256(
            (bundle / "esp32s3_bluedroid_bridge.bin").read_bytes()
        ).hexdigest())
        self.assertEqual(set(evidence["personalities"]), {"bridge", "hid"})
        for mode, fields in evidence["personalities"].items():
            with self.subTest(mode=mode):
                result = inspector.validate_personality(
                    mode, bytes.fromhex(fields["device_hex"]),
                    bytes.fromhex(fields["configuration_hex"]),
                    bytes.fromhex(fields.get("report_hex", "")),
                )
                self.assertEqual(result["vid_pid"],
                                 "303A:4001" if mode == "bridge" else "303A:4005")

    def test_cdc_hid_has_three_interfaces_and_cdc_iad(self):
        result = inspector.decode_configuration(HID_CONFIG)
        self.assertEqual(result["classes"], [2, 10, 3])
        self.assertEqual(result["iads"], [[0, 2, 2]])
        self.assertEqual(result["endpoints"], [0x81, 0x02, 0x82, 0x83])
        self.assertEqual(result["hid_report_length"], 1)

    def test_wrong_total_length_is_rejected(self):
        with self.assertRaises(ValueError):
            inspector.decode_configuration(HID_CONFIG[:-1])

    def test_duplicate_endpoint_is_rejected(self):
        duplicate = bytearray(HID_CONFIG)
        duplicate[-5] = 0x82
        with self.assertRaises(ValueError):
            inspector.decode_configuration(bytes(duplicate))

    def test_vendor_interface_is_rejected_by_personality_validation(self):
        vendor = bytearray(HID_CONFIG)
        vendor[80] = 0xff
        with self.assertRaises(ValueError):
            inspector.validate_personality("hid", bytes.fromhex(
                "12 01 00 02 ef 02 01 40 3a 30 05 40 00 01 01 02 03 01"
            ), bytes(vendor), b"\xc0")

    def test_device_pid_must_match_actual_interfaces(self):
        with self.assertRaises(ValueError):
            inspector.validate_personality("hid", bytes.fromhex(
                "12 01 00 02 ef 02 01 40 3a 30 01 40 00 01 01 02 03 01"
            ), HID_CONFIG, b"\xc0")

    def test_equal_size_report_without_gamepad_controls_is_rejected(self):
        configuration = bytearray(HID_CONFIG)
        configuration[91:93] = b"\x06\x00"
        with self.assertRaises(ValueError):
            inspector.validate_personality("hid", bytes.fromhex(
                "12 01 00 02 ef 02 01 40 3a 30 05 40 00 01 01 02 03 01"
            ), bytes(configuration), bytes.fromhex("75 08 95 0d 81 02"))


if __name__ == "__main__":
    unittest.main()
