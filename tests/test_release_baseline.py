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
    def test_desktop_release_version_is_076(self):
        source = (ROOT / "src" / "version.py").read_text(encoding="utf-8")
        self.assertRegex(source, r'VERSION\s*=\s*"0\.7\.6"')

    def test_release_manifest_requires_exact_payload_coverage(self):
        source = (ROOT / "scripts" / "verify_release.ps1").read_text(
            encoding="utf-8"
        )
        self.assertIn('"src\\update_manager.py"', source)
        self.assertIn("$manifestEntries.ContainsKey", source)
        self.assertIn("$actualPayload.Contains", source)
        self.assertIn("$manifestEntries.Count -ne $actualPayload.Count", source)
        self.assertIn("Unsafe SHA256SUMS path", source)
        self.assertIn("& $runtimePython -B -c $importCheck", source)
        self.assertIn('"LAUNCHER_BUILD.json"', source)
        self.assertIn('"image\\icon.ico"', source)
        self.assertIn("Packaged launcher version does not match", source)
        self.assertIn("Packaged launcher hash does not match", source)
        self.assertIn("third_party\\sources\\esptool-v4.11.0-source.zip", source)
        self.assertIn("audited official v4.11.0 binary", source)
        self.assertIn("audited v4.11.0 tag archive", source)

        package_source = (
            ROOT / "scripts" / "package_release.ps1"
        ).read_text(encoding="utf-8")
        build_source = (
            ROOT / "scripts" / "build_launchers.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("Launcher build version does not match", package_source)
        self.assertIn('"third_party"', package_source)
        self.assertIn('".ico"', package_source)
        self.assertIn("source_version = $version", build_source)

        launcher_source = (
            ROOT / "native" / "build_launcher.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn('"image\\icon.ico"', launcher_source)

        runtime_source = (
            ROOT / "scripts" / "build_runtime.ps1"
        ).read_text(encoding="utf-8")
        self.assertIn("pyserial-3.5\\LICENSE.txt", runtime_source)
        self.assertIn("pywinrt-3.2.1\\LICENSE", runtime_source)
        self.assertIn('Filter "winrt_*-3.2.1.dist-info"', runtime_source)

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
        runner = (ROOT / "tests" / "run_tests.py").read_text(encoding="utf-8")
        path_setup = runner.index("sys.path.insert(0, str(TESTS_DIR))")
        stub_import = runner.index("from vgamepad_test_stub import")
        self.assertLess(path_setup, stub_import)

    def test_firmware_ci_initializes_esp_idf_environment(self):
        source = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
            encoding="utf-8"
        )
        self.assertIn('. "$IDF_PATH/export.sh"', source)
        self.assertIn("idf.py build", source)
        self.assertGreaterEqual(source.count('. "$IDF_PATH/export.sh"'), 2)
        self.assertIn(
            'sys.executable, "-m", "esptool", "--chip", "esp32s3"',
            source,
        )
        self.assertIn('for marker in (b"S2P-FW", b"1.0.2")', source)

    def test_bundled_firmware_is_s2p_102(self):
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
            r'APP_FIRMWARE_VERSION\s+"1\.0\.2"',
        )
        self.assertRegex(
            main_source,
            r'APP_PROTOCOL_NAME\s+"s2p_bridge"',
        )
        self.assertRegex(
            main_source,
            r'APP_PROTOCOL_VERSION\s+"1\.0\.0"',
        )
        self.assertNotIn("1.0.2-dev", main_source)
        self.assertRegex(
            cmake_source,
            r'set\(PROJECT_VER\s+"1\.0\.2"\)',
        )

    def test_bundled_firmware_hashes_match_current_release(self):
        expected = {
            "bootloader.bin": (
                "6c8d58e019ba2b3c3b5d1fa5e736971f46e296df85e578ad2cb9a40e37696ff5"
            ),
            "partition-table.bin": (
                "7f00b6c042a89b15b0cac534f82ed988caf29278ff5700b0c511eb1b5bb7c820"
            ),
            "esp32s3_bluedroid_bridge.bin": (
                "4b6a8ce62d7aa909cedb56f8a685a291ee113183ee362821b71825469bf00557"
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
