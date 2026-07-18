import configparser
import ctypes
import subprocess
import sys
from pathlib import Path


if getattr(sys, "frozen", False):
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    SCRIPT_DIR = Path(__file__).resolve().parent
    BASE_DIR = SCRIPT_DIR.parent if SCRIPT_DIR.name.lower() == "src" else SCRIPT_DIR


PYTHONW = BASE_DIR / "runtime" / "pythonw.exe"
GUI = BASE_DIR / "src" / "config_gui.py"
CONFIG = BASE_DIR / "src" / "config.ini"


def get_language():
    config = configparser.ConfigParser()
    try:
        config.read(CONFIG, encoding="utf-8")
        language = config.get("gui", "language", fallback="zh").strip().lower()
        return "en" if language == "en" else "zh"
    except (OSError, configparser.Error):
        return "zh"


def show_startup_error(detail):
    if get_language() == "en":
        message = (
            "The settings UI could not start.\n\n"
            "Check that the release package is complete, then try again.\n\n"
            f"Details: {detail}"
        )
        title = "S2P-XInput-Lite Startup Error"
    else:
        message = (
            "設定介面無法啟動。\n\n"
            "請確認發佈包檔案完整後再試一次。\n\n"
            f"詳細資訊：{detail}"
        )
        title = "S2P-XInput-Lite 啟動錯誤"
    ctypes.windll.user32.MessageBoxW(None, message, title, 0x10)


def main():
    if not PYTHONW.is_file():
        show_startup_error(f"pythonw.exe not found: {PYTHONW}")
        return 1
    if not GUI.is_file():
        show_startup_error(f"config_gui.py not found: {GUI}")
        return 1

    try:
        process = subprocess.Popen(
            [str(PYTHONW), str(GUI)],
            cwd=str(BASE_DIR),
        )
    except OSError as exc:
        show_startup_error(str(exc))
        return 1

    # pythonw normally hides import tracebacks. Watch the startup window long
    # enough to turn an early non-zero exit into a visible native dialog.
    try:
        return_code = process.wait(timeout=8.0)
    except subprocess.TimeoutExpired:
        return 0

    if return_code != 0:
        show_startup_error(f"config_gui.py exited with code {return_code}")
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
