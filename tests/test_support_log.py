import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import launcher
import gamepad_tester_launcher
from support_log import (
    STARTUP_LOG_PATH_ENV,
    format_support_log,
    open_startup_log,
    startup_log_paths,
)


class SupportLogTests(unittest.TestCase):
    def test_startup_log_is_rotated_without_losing_the_previous_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {"LOCALAPPDATA": directory}
            first, first_path = open_startup_log(
                ROOT,
                ROOT / "runtime" / "pythonw.exe",
                SRC / "config_gui.py",
                environment,
            )
            first.write("RESULT=FIRST_LAUNCH_FAILED\n")
            first.close()

            second, second_path = open_startup_log(
                ROOT,
                ROOT / "runtime" / "pythonw.exe",
                SRC / "config_gui.py",
                environment,
            )
            second.close()

            self.assertEqual(first_path, second_path)
            previous = second_path.with_name("startup-previous.txt")
            self.assertIn(
                "RESULT=FIRST_LAUNCH_FAILED",
                previous.read_text(encoding="utf-8"),
            )

    def test_support_export_combines_startup_and_controller_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "startup-latest.txt"
            previous = Path(directory) / "startup-previous.txt"
            current.write_text("RESULT=CURRENT_OK\n", encoding="utf-8")
            previous.write_text(
                "RESULT=PREVIOUS_FAILURE\nTraceback: injected\n",
                encoding="utf-8",
            )
            environment = {STARTUP_LOG_PATH_ENV: str(current)}

            output = format_support_log(
                "S2P_XINPUT_LITE_DIAGNOSTIC_LOG\nVERDICT=OK\n",
                environment,
            )

            self.assertIn("[STARTUP_LOG_CURRENT]", output)
            self.assertIn("RESULT=CURRENT_OK", output)
            self.assertIn("[STARTUP_LOG_PREVIOUS]", output)
            self.assertIn("RESULT=PREVIOUS_FAILURE", output)
            self.assertIn("[CONTROLLER_DIAGNOSTIC]", output)
            self.assertIn("S2P_XINPUT_LITE_DIAGNOSTIC_LOG", output)

    def test_support_export_works_before_controller_diagnostics_run(self):
        with tempfile.TemporaryDirectory() as directory:
            current = Path(directory) / "startup-latest.txt"
            environment = {STARTUP_LOG_PATH_ENV: str(current)}

            output = format_support_log(None, environment)

            self.assertIn("STATUS=NOT_AVAILABLE", output)
            self.assertIn("[CONTROLLER_DIAGNOSTIC]\nSTATUS=NOT_RUN", output)

    def test_launcher_redirects_early_gui_output_to_startup_log(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            pythonw = directory / "pythonw.exe"
            gui = directory / "config_gui.py"
            log_path = directory / "startup-latest.txt"
            pythonw.touch()
            gui.touch()
            log_stream = log_path.open("w", encoding="utf-8", buffering=1)
            process = Mock(pid=1234)
            process.wait.side_effect = subprocess.TimeoutExpired(
                "config_gui.py", 8.0
            )

            with (
                patch.object(launcher, "PYTHONW", pythonw),
                patch.object(launcher, "GUI", gui),
                patch.object(launcher, "BASE_DIR", directory),
                patch.object(
                    launcher,
                    "open_startup_log",
                    return_value=(log_stream, log_path),
                ),
                patch.object(
                    launcher.subprocess,
                    "Popen",
                    return_value=process,
                ) as popen,
            ):
                self.assertEqual(launcher.main(), 0)

            kwargs = popen.call_args.kwargs
            self.assertIs(kwargs["stderr"], subprocess.STDOUT)
            self.assertEqual(
                kwargs["env"][STARTUP_LOG_PATH_ENV],
                str(log_path),
            )
            self.assertEqual(kwargs["env"]["PYTHONFAULTHANDLER"], "1")
            self.assertEqual(kwargs["env"]["PYTHONUNBUFFERED"], "1")
            self.assertIn(
                "RESULT=GUI_RUNNING_AFTER_STARTUP_WINDOW",
                log_path.read_text(encoding="utf-8"),
            )

    def test_gamepad_tester_launcher_uses_its_own_startup_log(self):
        with tempfile.TemporaryDirectory() as directory:
            environment = {
                "LOCALAPPDATA": directory,
                "PATH": os.environ.get("PATH", ""),
            }

            with patch.dict(
                gamepad_tester_launcher.os.environ,
                environment,
                clear=True,
            ):
                launcher_environment = (
                    gamepad_tester_launcher._log_environment()
                )

            path = Path(
                launcher_environment[STARTUP_LOG_PATH_ENV]
            )
            self.assertEqual(
                path.name,
                "gamepad-tester-startup-latest.txt",
            )
            _current, previous = startup_log_paths(
                launcher_environment
            )
            self.assertEqual(
                previous.name,
                "gamepad-tester-startup-previous.txt",
            )

    def test_gamepad_tester_launcher_captures_early_output(self):
        with tempfile.TemporaryDirectory() as directory:
            directory = Path(directory)
            pythonw = directory / "pythonw.exe"
            test_app = directory / "gamepad_test_app.py"
            log_path = (
                directory / "gamepad-tester-startup-latest.txt"
            )
            pythonw.touch()
            test_app.touch()
            log_stream = log_path.open(
                "w", encoding="utf-8", buffering=1
            )
            process = Mock(pid=4321)
            process.wait.side_effect = subprocess.TimeoutExpired(
                "gamepad_test_app.py", 8.0
            )

            with (
                patch.object(
                    gamepad_tester_launcher, "PYTHONW", pythonw
                ),
                patch.object(
                    gamepad_tester_launcher, "TEST_APP", test_app
                ),
                patch.object(
                    gamepad_tester_launcher, "BASE_DIR", directory
                ),
                patch.object(
                    gamepad_tester_launcher,
                    "_log_environment",
                    return_value={
                        STARTUP_LOG_PATH_ENV: str(log_path)
                    },
                ),
                patch.object(
                    gamepad_tester_launcher,
                    "open_startup_log",
                    return_value=(log_stream, log_path),
                ),
                patch.object(
                    gamepad_tester_launcher.subprocess,
                    "Popen",
                    return_value=process,
                ) as popen,
            ):
                self.assertEqual(
                    gamepad_tester_launcher.main(), 0
                )

            kwargs = popen.call_args.kwargs
            self.assertIs(kwargs["stdout"], log_stream)
            self.assertIs(kwargs["stderr"], subprocess.STDOUT)
            self.assertEqual(
                kwargs["env"]["PYTHONFAULTHANDLER"], "1"
            )
            self.assertIn(
                "RESULT=GAMEPAD_TESTER_RUNNING_AFTER_STARTUP_WINDOW",
                log_path.read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
