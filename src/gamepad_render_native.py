"""Load the optional Windows rendering kernels from the installation."""

import ctypes
from functools import lru_cache
from pathlib import Path
import sys


@lru_cache(maxsize=1)
def load_render_library():
    if sys.platform == "win32":
        path = Path(__file__).resolve().with_name("gamepad_render_native.dll")
        try:
            return ctypes.CDLL(str(path))
        except OSError:
            pass
    return None
