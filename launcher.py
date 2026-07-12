import sys
import subprocess
from pathlib import Path


if getattr(sys, "frozen", False):
    # PyInstaller 打包後：取得 EXE 實際所在位置
    BASE_DIR = Path(sys.executable).resolve().parent
else:
    # 直接執行 launcher.py 時
    BASE_DIR = Path(__file__).resolve().parent


PYTHONW = BASE_DIR / "runtime" / "pythonw.exe"
GUI = BASE_DIR / "src" / "config_gui.py"


subprocess.Popen(
    [
        str(PYTHONW),
        str(GUI)
    ],
    cwd=str(BASE_DIR)
)