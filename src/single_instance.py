"""Windows single-instance guard with activation notification."""

from __future__ import annotations

import ctypes
import os
import tkinter as tk


ERROR_ALREADY_EXISTS = 183
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
WAIT_FAILED = 0xFFFFFFFF
ASFW_ANY = 0xFFFFFFFF
SW_RESTORE = 9


class SingleInstance:
    """Own one named Windows event and wake the existing owner on duplicates."""

    def __init__(self, name):
        self.name = str(name)
        self.is_primary = True
        self.error = None
        self._handle = None
        self._kernel32 = None

        if os.name != "nt":
            return

        try:
            kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel32.CreateEventW.argtypes = (
                ctypes.c_void_p,
                ctypes.c_int,
                ctypes.c_int,
                ctypes.c_wchar_p,
            )
            kernel32.CreateEventW.restype = ctypes.c_void_p
            kernel32.SetEvent.argtypes = (ctypes.c_void_p,)
            kernel32.SetEvent.restype = ctypes.c_int
            kernel32.WaitForSingleObject.argtypes = (
                ctypes.c_void_p,
                ctypes.c_uint32,
            )
            kernel32.WaitForSingleObject.restype = ctypes.c_uint32
            kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
            kernel32.CloseHandle.restype = ctypes.c_int

            ctypes.set_last_error(0)
            handle = kernel32.CreateEventW(
                None,
                False,
                False,
                f"Local\\{self.name}.Activation",
            )
            if not handle:
                raise ctypes.WinError(ctypes.get_last_error())

            self._kernel32 = kernel32
            self._handle = handle
            self.is_primary = (
                ctypes.get_last_error() != ERROR_ALREADY_EXISTS
            )
        except (AttributeError, OSError) as exc:
            # Failing open keeps the application usable on restricted systems.
            # The startup capture records this message for support diagnostics.
            self.error = exc
            self.is_primary = True
            self.close()

    def notify_existing(self):
        """Signal the primary process and grant it foreground permission."""
        if self._handle is None or self._kernel32 is None:
            return False
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
            user32.AllowSetForegroundWindow.argtypes = (ctypes.c_uint32,)
            user32.AllowSetForegroundWindow.restype = ctypes.c_int
            user32.AllowSetForegroundWindow(ASFW_ANY)
        except (AttributeError, OSError):
            pass
        return bool(self._kernel32.SetEvent(self._handle))

    def activation_requested(self):
        """Consume one pending activation request without blocking."""
        if self._handle is None or self._kernel32 is None:
            return False
        result = int(
            self._kernel32.WaitForSingleObject(self._handle, 0)
        )
        if result == WAIT_OBJECT_0:
            return True
        if result == WAIT_TIMEOUT:
            return False
        if result == WAIT_FAILED:
            raise ctypes.WinError(ctypes.get_last_error())
        return False

    def close(self):
        """Release this process's handle; safe to call more than once."""
        handle = self._handle
        kernel32 = self._kernel32
        self._handle = None
        self._kernel32 = None
        if handle is not None and kernel32 is not None:
            kernel32.CloseHandle(handle)


def activate_tk_window(window):
    """Restore and foreground an existing Tk top-level window."""
    try:
        if not window.winfo_exists():
            return False
        if window.state() in ("iconic", "withdrawn"):
            window.deiconify()
        window.update_idletasks()
        window.attributes("-topmost", True)
        window.lift()
        window.focus_force()
        if os.name == "nt":
            try:
                user32 = ctypes.WinDLL("user32", use_last_error=True)
                user32.ShowWindow.argtypes = (
                    ctypes.c_void_p,
                    ctypes.c_int,
                )
                user32.ShowWindow.restype = ctypes.c_int
                user32.SetForegroundWindow.argtypes = (ctypes.c_void_p,)
                user32.SetForegroundWindow.restype = ctypes.c_int
                hwnd = ctypes.c_void_p(int(window.winfo_id()))
                user32.ShowWindow(hwnd, SW_RESTORE)
                user32.SetForegroundWindow(hwnd)
            except (AttributeError, OSError, TypeError, ValueError):
                pass

        def clear_temporary_topmost():
            try:
                if window.winfo_exists():
                    window.attributes("-topmost", False)
            except (AttributeError, RuntimeError, tk.TclError):
                pass

        window.after(75, clear_temporary_topmost)
        return True
    except (AttributeError, RuntimeError, tk.TclError):
        return False
