"""Windows high-resolution one-shot wakeups for an active aim viewport."""

import ctypes
from ctypes import wintypes
import threading


_HIGH_RESOLUTION = 2
_TIMER_ACCESS = 0x00100002  # SYNCHRONIZE | TIMER_MODIFY_STATE
_INFINITE = 0xFFFFFFFF


def _api(dll, name, result, *arguments):
    function = getattr(dll, name)
    function.restype = result
    function.argtypes = arguments
    return function


class NativeAimTimer:
    """Wake the native message pump without executing Tcl from another callback."""

    def __init__(self, hwnd):
        self._hwnd = hwnd
        self._thread = self._timer = self._stop = None
        self._closed = False
        self._configure_api()
        try:
            self._timer = self._create_timer(None, None, _HIGH_RESOLUTION, _TIMER_ACCESS)
            self._stop = self._create_event(None, True, False, None)
            if not self._timer or not self._stop:
                raise ctypes.WinError(ctypes.get_last_error())
            worker = threading.Thread(target=self._wait, name="AimDeadline", daemon=True)
            worker.start()
            self._thread = worker
        except BaseException:
            self.close()
            raise

    def _configure_api(self):
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        user = ctypes.WinDLL("user32", use_last_error=True)
        handle, pointer, boolean, dword = wintypes.HANDLE, wintypes.LPVOID, wintypes.BOOL, wintypes.DWORD
        hwnd, uint, word, long_word = wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM
        self._create_timer = _api(kernel, "CreateWaitableTimerExW", handle, pointer, wintypes.LPCWSTR, dword, dword)
        self._create_event = _api(kernel, "CreateEventW", handle, pointer, boolean, boolean, wintypes.LPCWSTR)
        self._set_timer = _api(kernel, "SetWaitableTimer", boolean, handle, ctypes.POINTER(ctypes.c_longlong),
                               wintypes.LONG, pointer, pointer, boolean)
        self._cancel_timer = _api(kernel, "CancelWaitableTimer", boolean, handle)
        self._set_event = _api(kernel, "SetEvent", boolean, handle)
        self._close_handle = _api(kernel, "CloseHandle", boolean, handle)
        self._wait_handles = _api(kernel, "WaitForMultipleObjects", dword, dword, ctypes.POINTER(handle), boolean, dword)
        self._post = _api(user, "PostMessageW", boolean, hwnd, uint, word, long_word)

    def arm(self, seconds):
        due = ctypes.c_longlong(-max(500, round(seconds * 10_000_000)))
        if not self._set_timer(self._timer, ctypes.byref(due), 0, None, None, False):
            raise ctypes.WinError(ctypes.get_last_error())

    def cancel(self):
        self._cancel_timer(self._timer)

    def _wait(self):
        handles = (wintypes.HANDLE * 2)(self._stop, self._timer)
        while self._wait_handles(2, handles, False, _INFINITE) == 1:
            # WM_NULL wakes Tk's Windows message loop. Tk's own timer handler
            # then dispatches the callback with its normal interpreter state.
            self._post(self._hwnd, 0, 0, 0)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._stop:
            self._set_event(self._stop)
        if self._thread is not None:
            self._thread.join()
        for handle in (self._timer, self._stop):
            if handle:
                self._close_handle(handle)
