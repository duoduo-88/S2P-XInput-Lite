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
    def test_desktop_release_version_is_071(self):
        source = (ROOT / "src" / "version.py").read_text(encoding="utf-8")
        self.assertRegex(source, r'VERSION\s*=\s*"0\.7\.1"')

    def test_runtime_build_check_does_not_require_installed_vigembus(self):
        source = (ROOT / "scripts" / "build_runtime.ps1").read_text(
            encoding="utf-8"
        )
        import_check = source.split("$importCheck = (", 1)[1].split(
            "& $runtimePython -c $importCheck", 1
        )[0]
        self.assertNotIn("import serial, vgamepad", import_check)
        self.assertIn("util.find_spec('vgamepad')", import_check)
        self.assertIn("ctypes.CDLL(str(client))", import_check)

    def test_firmware_ci_initializes_esp_idf_environment(self):
        source = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('. "$IDF_PATH/export.sh"', source)
        self.assertIn("idf.py build", source)
        self.assertIn('"esptool.py", "--chip", "esp32s3", "image_info"', source)
        self.assertIn('for marker in (b"S2P-FW", b"1.0.1")', source)

    def test_bundled_firmware_is_s2p_101(self):
        main_source = (
            FIRMWARE_ROOT / "main" / "main.c"
        ).read_text(encoding="utf-8")
        cmake_source = (
            FIRMWARE_ROOT / "CMakeLists.txt"
        ).read_text(encoding="utf-8")
        self.assertRegex(
            main_source,
            r'APP_FIRMWARE_PRODUCT\s+"S2P-FW"',
        )
        self.assertRegex(
            main_source,
            r'APP_FIRMWARE_VERSION\s+"1\.0\.1"',
        )
        self.assertRegex(
            main_source,
            r'APP_PROTOCOL_NAME\s+"s2p_bridge"',
        )
        self.assertRegex(
            main_source,
            r'APP_PROTOCOL_VERSION\s+"1\.0\.0"',
        )
        self.assertNotIn("1.0.1-dev", main_source)
        self.assertRegex(
            cmake_source,
            r'set\(PROJECT_VER\s+"1\.0\.1"\)',
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
                "c83609e11df3c207d359439c421dc79ebf54e31bb4d2368c71b78bc7c19640c0"
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
