"""Update trail photo pixels through the active Windows Tk 8.6 runtime."""

from __future__ import annotations

import _tkinter
import ctypes
import sys
from functools import lru_cache
from pathlib import Path
from threading import current_thread
from tkinter import PhotoImage, TclError


_PHOTO_COMPOSITE_SET = 1
_INT_MAX = 2**31 - 1


class _PhotoBlock(ctypes.Structure):
    _fields_ = [
        ("pixels", ctypes.c_void_p),
        ("width", ctypes.c_int),
        ("height", ctypes.c_int),
        ("pitch", ctypes.c_int),
        ("pixel_size", ctypes.c_int),
        ("offset", ctypes.c_int * 4),
    ]


@lru_cache(maxsize=1)
def _load_photo_api():
    if sys.platform != "win32" or _tkinter.TK_VERSION != "8.6":
        return None
    try:
        kernel32 = ctypes.WinDLL("kernel32", winmode=0x00000800)  # LOAD_LIBRARY_SEARCH_SYSTEM32
        get_module_handle = kernel32.GetModuleHandleW
        get_module_handle.argtypes = [ctypes.c_wchar_p]
        get_module_handle.restype = ctypes.c_void_p
        dlls = Path(_tkinter.__file__).resolve().parent
        # CPython installs Tk beside _tkinter. The bundled runtime puts Tk beside python.exe.
        handles = {
            get_module_handle(str(path / "tk86t.dll")) for path in (dlls, dlls.parent)
        } - {None}
        if len(handles) != 1:
            return None

        # Borrow the already loaded module and hold the GIL while Tk reads the buffer.
        tk = ctypes.PyDLL("tk86t.dll", handle=handles.pop())
        tk.Tk_FindPhoto.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        tk.Tk_FindPhoto.restype = ctypes.c_void_p
        tk.Tk_PhotoPutBlock.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_PhotoBlock),
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        ]
        tk.Tk_PhotoPutBlock.restype = ctypes.c_int
        return tk
    except (AttributeError, OSError, RuntimeError):
        return None


class TrailPhotoUploader:
    """Copy RGBA rectangles into Tk from scanlines with a one-byte row prefix."""

    def __init__(self):
        self._thread = current_thread()
        self._tk = None
        self._interp = None
        self._api = _load_photo_api()

    def upload(
        self, photo: PhotoImage, scanlines: bytearray, source_stride: int,
        left: int, top: int, width: int, height: int, *,
        dest_x: int | None = None, dest_y: int | None = None,
    ) -> bool:
        """Return False when the caller should use the portable photo update."""
        if self._api is None or current_thread() is not self._thread:
            return False
        if not isinstance(photo, PhotoImage) or not isinstance(photo.tk, _tkinter.TkappType):
            return False

        dest_x = left if dest_x is None else dest_x
        dest_y = top if dest_y is None else dest_y
        offset = 1 + top * source_stride + left * 4
        if (min(left, top, dest_x, dest_y) < 0 or min(width, height) <= 0
                or not 1 + (left + width) * 4 <= source_stride <= _INT_MAX
                or max(dest_x + width, dest_y + height) > _INT_MAX):
            return False

        try:
            if self._tk is not photo.tk:
                # eval checks Tk's owning thread before we use its interpreter pointer.
                photo.tk.eval("package present Tk 8.6")
                self._interp = photo.tk.interpaddr()
                self._tk = photo.tk
            handle = self._api.Tk_FindPhoto(self._interp, str(photo).encode("ascii"))
            if not handle:
                return False
            # Validate the last byte as we pin the buffer for the complete native read.
            last_byte_delta = (height - 1) * source_stride + width * 4 - 1
            tail = ctypes.c_ubyte.from_buffer(scanlines, offset + last_byte_delta)
            pixels = ctypes.addressof(tail) - last_byte_delta
            block = _PhotoBlock(pixels, width, height, source_stride, 4, (0, 1, 2, 3))
            return self._api.Tk_PhotoPutBlock(
                self._interp, handle, ctypes.byref(block), dest_x, dest_y,
                width, height, _PHOTO_COMPOSITE_SET,
            ) == 0
        except (BufferError, OSError, RuntimeError, TclError, TypeError, ValueError):
            return False
