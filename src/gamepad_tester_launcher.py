"""Small windowed launcher for opening Gamepad Tester directly."""

from __future__ import annotations

import configparser
import ctypes
import os
import subprocess
import sys
import traceback
from pathlib import Path

from support_log import (
    STARTUP_LOG_PATH_ENV,
    open_startup_log,
    startup_log_paths,
    write_startup_field,
)


if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    SCRIPT_DIR = Path(__file__).resolve().parent
    BASE_DIR = (
        SCRIPT_DIR.parent
        if SCRIPT_DIR.name.lower() == "src"
        else SCRIPT_DIR
    )


PYTHONW = BASE_DIR / "runtime" / "pythonw.exe"
TEST_APP = BASE_DIR / "src" / "gamepad_test_app.py"
CONFIG = BASE_DIR / "src" / "config.ini"


def get_language():
    config = configparser.ConfigParser()
    try:
        config.read(CONFIG, encoding="utf-8")
        language = config.get(
            "gui", "language", fallback="zh"
        ).strip().lower()
        return "en" if language == "en" else "zh"
    except (OSError, configparser.Error):
        return "zh"


def _log_environment():
    environment = os.environ.copy()
    current, _previous = startup_log_paths(environment)
    environment[STARTUP_LOG_PATH_ENV] = str(
        current.with_name("gamepad-tester-startup-latest.txt")
    )
    return environment


def show_startup_error(detail, log_path=None):
    if get_language() == "en":
        message = (
            "Gamepad Tester could not start.\n\n"
            f"Details: {detail}"
        )
        title = "Gamepad Tester Startup Error"
        if log_path is not None:
            message += (
                f"\n\nA support log was saved to:\n{log_path}"
                "\n\nOpen the log folder now?"
            )
    else:
        message = (
            "手把測試程式無法啟動。\n\n"
            f"詳細資訊：{detail}"
        )
        title = "手把測試程式啟動錯誤"
        if log_path is not None:
            message += (
                f"\n\n支援 Log 已儲存至：\n{log_path}"
                "\n\n是否立即開啟 Log 資料夾？"
            )
    style = 0x10 | (0x04 if log_path is not None else 0x00)
    result = ctypes.windll.user32.MessageBoxW(
        None, message, title, style
    )
    if log_path is not None and result == 6:
        try:
            os.startfile(str(Path(log_path).parent))
        except (AttributeError, OSError):
            pass


def main():
    environment = _log_environment()
    try:
        log_stream, log_path = open_startup_log(
            BASE_DIR,
            PYTHONW,
            TEST_APP,
            environment,
        )
    except OSError as exc:
        show_startup_error(f"could not create startup log: {exc}")
        return 1

    try:
        if not PYTHONW.is_file():
            detail = f"pythonw.exe not found: {PYTHONW}"
            write_startup_field(log_stream, "RESULT", "PYTHON_NOT_FOUND")
            write_startup_field(log_stream, "ERROR", detail)
            show_startup_error(detail, log_path)
            return 1
        if not TEST_APP.is_file():
            detail = f"gamepad_test_app.py not found: {TEST_APP}"
            write_startup_field(
                log_stream, "RESULT", "TEST_APP_NOT_FOUND"
            )
            write_startup_field(log_stream, "ERROR", detail)
            show_startup_error(detail, log_path)
            return 1

        environment[STARTUP_LOG_PATH_ENV] = str(log_path)
        environment["PYTHONFAULTHANDLER"] = "1"
        environment["PYTHONUNBUFFERED"] = "1"
        write_startup_field(
            log_stream, "PHASE", "starting_gamepad_tester"
        )
        try:
            process = subprocess.Popen(
                [str(PYTHONW), str(TEST_APP)],
                cwd=str(BASE_DIR),
                stdout=log_stream,
                stderr=subprocess.STDOUT,
                env=environment,
            )
        except OSError as exc:
            write_startup_field(
                log_stream, "RESULT", "TEST_APP_SPAWN_FAILED"
            )
            write_startup_field(log_stream, "ERROR", exc)
            show_startup_error(str(exc), log_path)
            return 1

        write_startup_field(log_stream, "TEST_APP_PID", process.pid)
        try:
            return_code = process.wait(timeout=8.0)
        except subprocess.TimeoutExpired:
            write_startup_field(
                log_stream,
                "RESULT",
                "GAMEPAD_TESTER_RUNNING_AFTER_STARTUP_WINDOW",
            )
            return 0

        write_startup_field(
            log_stream, "RESULT", "GAMEPAD_TESTER_EXITED_EARLY"
        )
        write_startup_field(
            log_stream,
            "EXIT_CODE",
            (
                f"0x{return_code & 0xFFFFFFFF:08X}"
                if return_code < 0 or return_code > 255
                else return_code
            ),
        )
        if return_code != 0:
            show_startup_error(
                f"gamepad_test_app.py exited with code {return_code}",
                log_path,
            )
        return return_code
    except Exception as exc:
        log_stream.write("\n[LAUNCHER_EXCEPTION]\n")
        traceback.print_exc(file=log_stream)
        write_startup_field(log_stream, "RESULT", "LAUNCHER_EXCEPTION")
        show_startup_error(str(exc), log_path)
        return 1
    finally:
        log_stream.close()


if __name__ == "__main__":
    raise SystemExit(main())
