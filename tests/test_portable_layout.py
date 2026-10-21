"""Relocation checks for the clean portable ZIP and older installations."""

import runpy
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from application_paths import application_data_directory, installation_directory  # noqa: E402


class PortableLayoutTests(unittest.TestCase):
    def test_both_frozen_launchers_find_support_files_after_relocation(self):
        with tempfile.TemporaryDirectory(prefix="S2P 測試 ") as temporary:
            root = Path(temporary)
            for modern in (False, True):
                data = root / "app" if modern else root
                (data / "src").mkdir(parents=True, exist_ok=True)
                expected_data = data.resolve()
                for source, executable, entry in (
                    ("launcher.py", "S2P-XInput-Lite.exe", "config_gui.py"),
                    ("gamepad_tester_launcher.py", "GamepadTester.exe", "gamepad_test_app.py"),
                ):
                    with self.subTest(modern=modern, executable=executable), patch.object(
                        sys, "frozen", True, create=True
                    ), patch.object(sys, "executable", str(root / executable)):
                        namespace = runpy.run_path(str(ROOT / "src" / source))
                        self.assertEqual(namespace["BASE_DIR"], expected_data)
                        self.assertEqual(namespace["PYTHONW"], expected_data / "runtime" / "pythonw.exe")
                        self.assertEqual(namespace.get("GUI", namespace.get("TEST_APP")), expected_data / "src" / entry)
                        self.assertEqual(namespace["CONFIG"], expected_data / "src" / "config.ini")

    def test_root_executable_is_used_by_tray_after_support_files_move(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "S2P-XInput-Lite.exe").touch()
            (root / "app").mkdir()
            self.assertEqual(installation_directory(root / "app"), root)
            self.assertEqual(installation_directory(root), root)
            self.assertEqual(application_data_directory(root), root / "app")

    def test_incomplete_app_directory_does_not_fall_back_to_legacy_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "app").mkdir()
            self.assertEqual(application_data_directory(root), root / "app")


if __name__ == "__main__":
    unittest.main()
