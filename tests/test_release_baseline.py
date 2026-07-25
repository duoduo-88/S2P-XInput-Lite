import hashlib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FIRMWARE_ROOT = (
    ROOT
    / "esp32s3"
    / "source"
    / "esp32s3_usb_bridge_bluedroid"
)


class ReleaseBaselineTests(unittest.TestCase):
    def test_desktop_release_version_is_061(self):
        source = (ROOT / "src" / "version.py").read_text(encoding="utf-8")
        self.assertRegex(source, r'VERSION\s*=\s*"0\.6\.1"')

    def test_bundled_firmware_is_stable_0140(self):
        main_source = (
            FIRMWARE_ROOT / "main" / "main.c"
        ).read_text(encoding="utf-8")
        cmake_source = (
            FIRMWARE_ROOT / "CMakeLists.txt"
        ).read_text(encoding="utf-8")
        self.assertRegex(
            main_source,
            r'APP_FIRMWARE_VERSION\s+"0\.14\.0"',
        )
        self.assertNotIn("0.14.0-dev", main_source)
        self.assertRegex(
            cmake_source,
            r'set\(PROJECT_VER\s+"0\.14\.0"\)',
        )

    def test_bundled_firmware_hashes_match_v060(self):
        expected = {
            "bootloader.bin": (
                "9f1be89eecd1c24a562c0c570894f6f625405041508e55a2b1aa3875b74d237a"
            ),
            "esp32s3_bluedroid_bridge.bin": (
                "d8503d233c4305f613645fc3b277f97a1b412c0c93ac50e87644f82fbb4b3be4"
            ),
        }
        firmware_dir = ROOT / "esp32s3" / "firmware"
        for name, digest in expected.items():
            with self.subTest(name=name):
                actual = hashlib.sha256(
                    (firmware_dir / name).read_bytes()
                ).hexdigest()
                self.assertEqual(actual, digest)


if __name__ == "__main__":
    unittest.main()
