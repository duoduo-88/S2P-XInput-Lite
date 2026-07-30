import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import hidhide_manager


class HidHideApplicationTests(unittest.TestCase):
    def test_inspection_requires_every_managed_application(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python_path = root / "python.exe"
            probe_path = root / "raw_hid_probe.exe"
            cli_path = root / "HidHideCLI.exe"

            def run_cli(_cli, *arguments):
                command = arguments[0]
                if command == "--cloak-state":
                    return "--cloak-on"
                if command == "--app-list":
                    return f'--app-reg "{python_path}"'
                if command == "--dev-list":
                    return '--dev-hide "HID\\\\VID_057E&PID_2069&MI_00\\\\PAD"'
                if command == "--dev-gaming":
                    return (
                        '[{"devices":[{"present":true,'
                        '"deviceInstancePath":'
                        '"HID\\\\\\\\VID_057E&PID_2069&MI_00\\\\\\\\PAD",'
                        '"symbolicLink":"hid-pad"}]}]'
                    )
                raise AssertionError(arguments)

            with (
                patch.object(
                    hidhide_manager, "locate_hidhide_cli",
                    return_value=cli_path,
                ),
                patch.object(hidhide_manager, "_run_cli", side_effect=run_cli),
                patch.object(
                    hidhide_manager, "_preferred_hid_symbolic_link",
                    return_value=None,
                ),
                patch.object(
                    hidhide_manager, "_physical_nintendo_devices",
                    return_value=["HID\\VID_057E&PID_2069&MI_00\\PAD"],
                ),
            ):
                status = hidhide_manager.inspect_hidhide(
                    (python_path, probe_path)
                )

            self.assertFalse(status["app_registered"])
            self.assertEqual(
                status["missing_application_paths"],
                [str(probe_path.resolve())],
            )
            self.assertEqual(status["state"], "setup")

    def test_configuration_registers_only_missing_application(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python_path = root / "python.exe"
            probe_path = root / "raw_hid_probe.exe"
            cli_path = root / "HidHideCLI.exe"
            before = {
                "state": "setup",
                "installed": True,
                "cli_path": cli_path,
                "error": None,
                "physical_devices": ["HID\\PAD"],
                "hidden_devices": ["HID\\PAD"],
                "missing_application_paths": [str(probe_path)],
                "cloak_active": True,
            }
            after = dict(
                before,
                state="ready",
                app_registered=True,
                missing_application_paths=[],
            )

            with (
                patch.object(
                    hidhide_manager, "inspect_hidhide",
                    side_effect=(before, after),
                ),
                patch.object(hidhide_manager, "_run_cli") as run_cli,
            ):
                result = hidhide_manager.configure_hidhide(
                    (python_path, probe_path)
                )

            self.assertEqual(result["state"], "ready")
            run_cli.assert_called_once_with(
                cli_path, "--app-reg", str(probe_path)
            )

    def test_removal_unregisters_only_present_managed_applications(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            python_path = root / "python.exe"
            probe_path = root / "raw_hid_probe.exe"
            cli_path = root / "HidHideCLI.exe"
            before = {
                "state": "setup",
                "installed": True,
                "cli_path": cli_path,
                "error": None,
                "physical_devices": ["HID\\PAD"],
                "hidden_devices": ["HID\\PAD", "HID\\OTHER"],
                "missing_application_paths": [str(probe_path)],
                "cloak_active": True,
            }
            after = dict(before, state="setup")

            with (
                patch.object(
                    hidhide_manager, "inspect_hidhide",
                    side_effect=(before, after),
                ),
                patch.object(hidhide_manager, "_run_cli") as run_cli,
            ):
                hidhide_manager.remove_hidhide_configuration(
                    (python_path, probe_path)
                )

            run_cli.assert_called_once_with(
                cli_path,
                "--dev-unhide", "HID\\PAD",
                "--app-unreg", str(python_path.resolve()),
            )


if __name__ == "__main__":
    unittest.main()
