"""Temporary fullscreen display modes for the Windows aim viewport."""

import atexit
import ctypes
import sys
from ctypes import wintypes
from itertools import count
from math import prod
from threading import RLock
from tkinter import TclError


_DWORD = ctypes.c_uint32
_WORD = ctypes.c_uint16
_CDS_TEST = 0x00000002
_CDS_FULLSCREEN = 0x00000004
_ENUM_CURRENT_SETTINGS = 0xFFFFFFFF
_DM_POSITION = 0x00000020
_DM_INTERLACED = 0x00000002
_MONITOR_DEFAULTTONEAREST = 2
_GA_ROOT = 2
_ERROR_ACCESS_DENIED = 5
_MODE_LOCK = RLock()
_ACTIVE_MODES = {}


class _PointL(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _DevMode(ctypes.Structure):
    # The display members of the two DEVMODEW unions occupy their full storage.
    _fields_ = [
        ("dmDeviceName", ctypes.c_wchar * 32),
        ("dmSpecVersion", _WORD),
        ("dmDriverVersion", _WORD),
        ("dmSize", _WORD),
        ("dmDriverExtra", _WORD),
        ("dmFields", _DWORD),
        ("dmPosition", _PointL),
        ("dmDisplayOrientation", _DWORD),
        ("dmDisplayFixedOutput", _DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", ctypes.c_wchar * 32),
        ("dmLogPixels", _WORD),
        ("dmBitsPerPel", _DWORD),
        ("dmPelsWidth", _DWORD),
        ("dmPelsHeight", _DWORD),
        ("dmDisplayFlags", _DWORD),
        ("dmDisplayFrequency", _DWORD),
        ("dmICMMethod", _DWORD),
        ("dmICMIntent", _DWORD),
        ("dmMediaType", _DWORD),
        ("dmDitherType", _DWORD),
        ("dmReserved1", _DWORD),
        ("dmReserved2", _DWORD),
        ("dmPanningWidth", _DWORD),
        ("dmPanningHeight", _DWORD),
    ]

    @property
    def size(self):
        return self.dmPelsWidth, self.dmPelsHeight


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", _DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", _DWORD),
        ("szDevice", ctypes.c_wchar * 32),
    ]


class _WindowsDisplay:
    def __init__(self):
        if sys.platform != "win32":
            raise OSError("Temporary display modes require Windows")
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._is_window = user32.IsWindow
        self._is_window.argtypes = (wintypes.HWND,)
        self._is_window.restype = wintypes.BOOL
        self._get_root = user32.GetAncestor
        self._get_root.argtypes = (wintypes.HWND, _DWORD)
        self._get_root.restype = wintypes.HWND
        self._monitor_from_window = user32.MonitorFromWindow
        self._monitor_from_window.argtypes = (wintypes.HWND, _DWORD)
        self._monitor_from_window.restype = wintypes.HMONITOR
        self._get_monitor_info = user32.GetMonitorInfoW
        self._get_monitor_info.argtypes = (wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo))
        self._get_monitor_info.restype = wintypes.BOOL
        self._read = user32.EnumDisplaySettingsExW
        self._read.argtypes = (wintypes.LPCWSTR, _DWORD, ctypes.POINTER(_DevMode), _DWORD)
        self._read.restype = wintypes.BOOL
        self._change = user32.ChangeDisplaySettingsExW
        self._change.argtypes = (
            wintypes.LPCWSTR,
            ctypes.POINTER(_DevMode),
            wintypes.HWND,
            _DWORD,
            wintypes.LPVOID,
        )
        self._change.restype = ctypes.c_int32

    def monitor(self, hwnd):
        if not self._is_window(hwnd):
            raise OSError("Display mode selection requires a valid owner window")
        # Tk's widget HWND may be inside a native top-level wrapper.
        owner = self._get_root(hwnd, _GA_ROOT) or hwnd
        monitor = self._monitor_from_window(owner, _MONITOR_DEFAULTTONEAREST)
        return monitor, *self.monitor_info(monitor)

    def monitor_info(self, monitor):
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        if (
            not monitor
            or not self._get_monitor_info(monitor, ctypes.byref(info))
            or not info.szDevice
        ):
            raise OSError("Cannot read the selected monitor's bounds")
        rect = info.rcMonitor
        return info.szDevice, (rect.left, rect.top, rect.right, rect.bottom)

    def read_mode(self, device, index):
        mode = _DevMode()
        mode.dmSize = ctypes.sizeof(mode)
        # No RAWMODE or ROTATEDMODE. The driver filters for this monitor and orientation.
        if not self._read(device, index, ctypes.byref(mode), 0):
            return None
        mode.dmDriverExtra = 0
        return mode

    def change(self, device, mode, flags):
        ctypes.set_last_error(0)
        result = self._change(device, ctypes.byref(mode), None, flags, None)
        if result and ctypes.get_last_error() == _ERROR_ACCESS_DENIED:
            raise ctypes.WinError(_ERROR_ACCESS_DENIED)
        return result


def _current_mode(api, device):
    current = api.read_mode(device, _ENUM_CURRENT_SETTINGS)
    if current is None or not all(current.size) or not current.dmBitsPerPel:
        raise OSError(f"Cannot read the current display mode for {device}")
    return current


def _compatible_modes(api, device, current):
    for index in count():
        mode = api.read_mode(device, index)
        if mode is None:
            return
        if (
            all(mode.size)
            and mode.dmBitsPerPel == current.dmBitsPerPel
            and mode.dmDisplayOrientation == current.dmDisplayOrientation
        ):
            yield mode


def fullscreen_monitor_bounds(hwnd: int) -> tuple[int, int, int, int]:
    """Return the owner monitor's bounds in the window's DPI coordinate space."""
    with _MODE_LOCK:
        _, _, bounds = _WindowsDisplay().monitor(hwnd)
        return bounds


def window_refresh_rate(window):
    """Return the current mode of the monitor nearest the Tk window."""
    try:
        api = _WindowsDisplay()
        _, device, _ = api.monitor(window.winfo_id())
        rate = _current_mode(api, device).dmDisplayFrequency
        if rate > 1:
            return float(rate)
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError, TclError):
        pass
    return 60.0


def available_resolutions(hwnd: int) -> tuple[str, ...]:
    """List usable resolutions without changing the owner monitor's mode."""
    with _MODE_LOCK:
        api = _WindowsDisplay()
        _, device, _ = api.monitor(hwnd)
        current = _current_mode(api, device)
        candidates = {}
        for mode in _compatible_modes(api, device, current):
            if mode.size != current.size:
                candidates.setdefault(mode.size, []).append(mode)
        sizes = {current.size}
        try:
            for size, modes in candidates.items():
                if _tested_mode(api, device, modes, current) is not None:
                    sizes.add(size)
        except OSError as error:
            if getattr(error, "winerror", None) != _ERROR_ACCESS_DENIED:
                raise
        return tuple(f"{width}x{height}" for width, height in sorted(sizes, reverse=True))


def highest_resolution(resolutions):
    return max(resolutions, key=lambda size: prod(map(int, size.split("x"))), default="")


class TemporaryDisplayMode:
    """Own a temporary mode until the exact prior DEVMODE is restored."""

    def __init__(self, api, device, original, bounds):
        self._api = api
        self._device = device
        self._original = _DevMode.from_buffer_copy(original)
        self.bounds = bounds
        self._restore_pending = False

    def close(self):
        """Restore the prior mode. A failed restore stays registered for exit."""
        with _MODE_LOCK:
            if _ACTIVE_MODES.get(self._device) is not self:
                return
            self._restore_pending = True
            result = self._api.change(self._device, self._original, 0)
            if result:
                raise OSError(f"Cannot restore the display mode for {self._device} ({result})")
            self._unregister()

    def _unregister(self):
        del _ACTIVE_MODES[self._device]
        atexit.unregister(self.close)


def _mode_priority(mode, current):
    refresh = mode.dmDisplayFrequency
    return (
        bool(mode.dmDisplayFlags & _DM_INTERLACED),
        abs(refresh - current.dmDisplayFrequency),
        -refresh,
    )


def _tested_mode(api, device, modes, current):
    for mode in sorted(modes, key=lambda mode: _mode_priority(mode, current)):
        # A driver's enumerated mode does not describe the desktop layout.
        mode = _DevMode.from_buffer_copy(mode)
        mode.dmFields &= ~_DM_POSITION
        if api.change(device, mode, _CDS_TEST) == 0:
            return mode
    return None


def activate_fullscreen_mode(hwnd: int, size: tuple[int, int]) -> TemporaryDisplayMode | None:
    """Test and apply a requested mode on the nearest monitor, without saving it."""
    if (
        not isinstance(size, tuple)
        or len(size) != 2
        or any(type(n) is not int or n < 1 for n in size)
    ):
        raise OSError("Display resolution requires positive integer dimensions")
    with _MODE_LOCK:
        api = _WindowsDisplay()
        monitor, device, bounds = api.monitor(hwnd)
        previous = _ACTIVE_MODES.get(device)
        if previous is not None:
            if not previous._restore_pending:
                raise OSError(f"A temporary display mode is already active on {device}")
            previous.close()
        original = _current_mode(api, device)
        if size == original.size:
            return None

        width, height = size
        candidates = (mode for mode in _compatible_modes(api, device, original) if mode.size == size)
        mode = _tested_mode(api, device, candidates, original)
        if mode is None:
            raise OSError(f"{width}x{height} is not available on {device}")

        session = TemporaryDisplayMode(api, device, original, bounds)
        atexit.register(session.close)
        _ACTIVE_MODES[device] = session
        try:
            try:
                result = api.change(device, mode, _CDS_FULLSCREEN)
            except PermissionError:
                # The native call reported failure before raising access denied.
                session._unregister()
                raise
            if result:
                session._unregister()
                raise OSError(f"Cannot apply {width}x{height} on {device} ({result})")
            active = _current_mode(api, device)
            if (
                active.size != size
                or active.dmBitsPerPel != original.dmBitsPerPel
                or active.dmDisplayOrientation != original.dmDisplayOrientation
            ):
                raise OSError(f"The display driver returned an unexpected mode for {device}")
            active_device, session.bounds = api.monitor_info(monitor)
            if active_device != device:
                raise OSError(f"The monitor changed while applying the display mode for {device}")
        except BaseException:
            session.close()
            raise
        return session
