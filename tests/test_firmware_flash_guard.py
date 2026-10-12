"""Fail-closed, read-only flash preflight before ESP32-S3 write_flash."""
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from firmware_flash_guard import FlashProbeError, probe_esp32_flash_size


class FirmwareFlashGuardTests(unittest.TestCase):
    def test_detects_16mb_flash_without_writing(self):
        commands = []

        def runner(command, **kwargs):
            commands.append(command)
            self.assertEqual(kwargs["timeout"], 20)
            return SimpleNamespace(
                returncode=0,
                stdout="Chip is ESP32-S3\nDetected flash size: 16MB\n",
                stderr="",
            )

        size = probe_esp32_flash_size("esptool.exe", "COM4", runner=runner)
        self.assertEqual(size, 16 * 1024 * 1024)
        self.assertEqual(commands[0][-1], "flash_id")
        self.assertNotIn("write_flash", commands[0])

    def test_reports_8mb_flash_for_caller_to_reject(self):
        size = probe_esp32_flash_size(
            "esptool.exe", "COM4",
            runner=lambda *_a, **_kw: SimpleNamespace(
                returncode=0, stdout="Detected flash size: 8MB", stderr="",
            ),
        )
        self.assertEqual(size, 8 * 1024 * 1024)

    def test_unknown_size_and_failed_esptool_fail_closed(self):
        for returncode, stdout in ((0, "Unknown flash"), (2, "Detected flash size: 16MB")):
            with self.subTest(returncode=returncode):
                with self.assertRaises(FlashProbeError):
                    probe_esp32_flash_size(
                        "esptool.exe", "COM4",
                        runner=lambda *_a, **_kw: SimpleNamespace(
                            returncode=returncode, stdout=stdout, stderr="error",
                        ),
                    )
