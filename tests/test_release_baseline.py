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
    def test_desktop_release_version_is_070(self):
        source = (ROOT / "src" / "version.py").read_text(encoding="utf-8")
        self.assertRegex(source, r'VERSION\s*=\s*"0\.7\.0"')

    def test_bundled_firmware_is_stable_0141(self):
        main_source = (
            FIRMWARE_ROOT / "main" / "main.c"
        ).read_text(encoding="utf-8")
        cmake_source = (
            FIRMWARE_ROOT / "CMakeLists.txt"
        ).read_text(encoding="utf-8")
        self.assertRegex(
            main_source,
            r'APP_FIRMWARE_VERSION\s+"0\.14\.1"',
        )
        self.assertNotIn("0.14.1-dev", main_source)
        self.assertRegex(
            cmake_source,
            r'set\(PROJECT_VER\s+"0\.14\.1"\)',
        )

    def test_bundled_firmware_hashes_match_current_release(self):
        expected = {
            "bootloader.bin": (
                "1e6b5148e11223f7e50e98549b0d220c77cf48f55d6f7397365a7efbaf3711d4"
            ),
            "partition-table.bin": (
                "7f00b6c042a89b15b0cac534f82ed988caf29278ff5700b0c511eb1b5bb7c820"
            ),
            "esp32s3_bluedroid_bridge.bin": (
                "6f3bc53bc066a90fa513b51356bb499db33526531091b3d2f789a5b9d4b104d7"
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
