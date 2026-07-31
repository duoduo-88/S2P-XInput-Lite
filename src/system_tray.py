"""Dependency-free Windows notification-area icon.

The Win32 message loop lives on its own thread.  It never calls Tk directly;
menu selections are delivered through a queue for the Tk thread to consume.
"""

from __future__ import annotations

import ctypes
import os
import queue
import threading
import uuid
from ctypes import wintypes
from pathlib import Path


WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_APP = 0x8000
WM_TRAY_CALLBACK = WM_APP + 1
WM_TRAY_SHOW = WM_APP + 2
WM_TRAY_HIDE = WM_APP + 3

NIM_ADD = 0x00000000
NIM_DELETE = 0x00000002
NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004

MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100
TPM_NONOTIFY = 0x0080

MENU_SHOW = 1001
MENU_EXIT = 1002
IDI_APPLICATION = 32512


class GUID(ctypes.Structure):
    _fields_ = (
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    )


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    )


class POINT(ctypes.Structure):
    _fields_ = (("x", wintypes.LONG), ("y", wintypes.LONG))


class MSG(ctypes.Structure):
    _fields_ = (
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", POINT),
        ("lPrivate", wintypes.DWORD),
    )


LRESULT = ctypes.c_ssize_t
HCURSOR = getattr(wintypes, "HCURSOR", wintypes.HANDLE)
HBRUSH = getattr(wintypes, "HBRUSH", wintypes.HANDLE)
WNDPROC = ctypes.WINFUNCTYPE(
    LRESULT,
    wintypes.HWND,
    wintypes.UINT,
    wintypes.WPARAM,
    wintypes.LPARAM,
)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.UINT),
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", HCURSOR),
        ("hbrBackground", HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
        ("hIconSm", wintypes.HICON),
    )


class SystemTrayIcon:
    """Own a native notification icon and expose menu actions as a queue."""

    def __init__(
        self,
        title,
        executable_path=None,
        show_label="Show Settings",
        exit_label="Exit",
        icon_path=None,
    ):
        self.title = str(title)
        self.executable_path = (
            Path(executable_path) if executable_path is not None else None
        )
        self.icon_path = Path(icon_path) if icon_path is not None else None
        self._show_label = str(show_label)
        self._exit_label = str(exit_label)
        self._labels_lock = threading.Lock()
        self._actions = queue.Queue()
        self._ready = threading.Event()
        self._command_complete = threading.Event()
        self._thread = None
        self._hwnd = None
        self._available = False
        self._visible = False
        self._visible_requested = False
        self.error = None
        self._user32 = None
        self._shell32 = None
        self._kernel32 = None
        self._wndproc = None
        self._class_name = None
        self._hinstance = None
        self._owned_icons = []
        self._icon = None
        self._taskbar_created_message = 0

    @property
    def available(self):
        return self._available

    @property
    def visible(self):
        return self._visible

    def start(self, timeout=2.0):
        """Start the native message loop without showing the icon yet."""
        if os.name != "nt":
            return False
        if self._thread is not None:
            return self.available
        self._thread = threading.Thread(
            target=self._thread_main,
            name="S2P-SystemTray",
            daemon=True,
        )
        self._thread.start()
        self._ready.wait(max(0.0, float(timeout)))
        return self.available

    def show(self, timeout=1.0):
        """Request that the notification icon be displayed."""
        if not self.available or not self._hwnd:
            return False
        self._visible_requested = True
        self._command_complete.clear()
        posted = self._user32.PostMessageW(
            self._hwnd, WM_TRAY_SHOW, 0, 0
        )
        if not posted:
            self._visible_requested = False
            return False
        self._command_complete.wait(max(0.0, float(timeout)))
        if not self._visible:
            self._visible_requested = False
        return self._visible

    def hide(self, timeout=1.0):
        """Request that the notification icon be removed."""
        self._visible_requested = False
        if not self.available or not self._hwnd:
            return False
        self._command_complete.clear()
        posted = self._user32.PostMessageW(
            self._hwnd, WM_TRAY_HIDE, 0, 0
        )
        if not posted:
            return False
        self._command_complete.wait(max(0.0, float(timeout)))
        return not self._visible

    def update_labels(self, show_label, exit_label):
        """Update labels used the next time the context menu opens."""
        with self._labels_lock:
            self._show_label = str(show_label)
            self._exit_label = str(exit_label)

    def get_action(self):
        """Return one pending ``show``/``exit`` action without blocking."""
        try:
            return self._actions.get_nowait()
        except queue.Empty:
            return None

    def stop(self, timeout=2.0):
        """Remove the icon and stop its native message-loop thread."""
        self._visible_requested = False
        thread = self._thread
        hwnd = self._hwnd
        if thread is None:
            return
        if hwnd and self._user32 is not None:
            self._user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        if thread is not threading.current_thread():
            thread.join(max(0.0, float(timeout)))
        if not thread.is_alive():
            self._thread = None

    def _configure_win32(self):
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._shell32 = ctypes.WinDLL("shell32", use_last_error=True)
        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self._kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)
        self._kernel32.GetModuleHandleW.restype = wintypes.HMODULE
        self._user32.RegisterClassExW.argtypes = (
            ctypes.POINTER(WNDCLASSEXW),
        )
        self._user32.RegisterClassExW.restype = wintypes.ATOM
        self._user32.UnregisterClassW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.HINSTANCE,
        )
        self._user32.UnregisterClassW.restype = wintypes.BOOL
        self._user32.CreateWindowExW.argtypes = (
            wintypes.DWORD,
            wintypes.LPCWSTR,
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            wintypes.HMENU,
            wintypes.HINSTANCE,
            ctypes.c_void_p,
        )
        self._user32.CreateWindowExW.restype = wintypes.HWND
        self._user32.DefWindowProcW.argtypes = (
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        self._user32.DefWindowProcW.restype = LRESULT
        self._user32.DestroyWindow.argtypes = (wintypes.HWND,)
        self._user32.DestroyWindow.restype = wintypes.BOOL
        self._user32.PostQuitMessage.argtypes = (ctypes.c_int,)
        self._user32.GetMessageW.argtypes = (
            ctypes.POINTER(MSG),
            wintypes.HWND,
            wintypes.UINT,
            wintypes.UINT,
        )
        self._user32.GetMessageW.restype = wintypes.BOOL
        self._user32.TranslateMessage.argtypes = (ctypes.POINTER(MSG),)
        self._user32.DispatchMessageW.argtypes = (ctypes.POINTER(MSG),)
        self._user32.DispatchMessageW.restype = LRESULT
        self._user32.PostMessageW.argtypes = (
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        self._user32.PostMessageW.restype = wintypes.BOOL
        self._user32.RegisterWindowMessageW.argtypes = (wintypes.LPCWSTR,)
        self._user32.RegisterWindowMessageW.restype = wintypes.UINT
        self._shell32.Shell_NotifyIconW.argtypes = (
            wintypes.DWORD,
            ctypes.POINTER(NOTIFYICONDATAW),
        )
        self._shell32.Shell_NotifyIconW.restype = wintypes.BOOL

    def _thread_main(self):
        try:
            self._configure_win32()
            self._hinstance = self._kernel32.GetModuleHandleW(None)
            self._class_name = f"S2PTrayWindow.{uuid.uuid4().hex}"
            self._wndproc = WNDPROC(self._window_proc)
            window_class = WNDCLASSEXW()
            window_class.cbSize = ctypes.sizeof(WNDCLASSEXW)
            window_class.lpfnWndProc = self._wndproc
            window_class.hInstance = self._hinstance
            window_class.lpszClassName = self._class_name
            if not self._user32.RegisterClassExW(
                ctypes.byref(window_class)
            ):
                raise ctypes.WinError(ctypes.get_last_error())

            self._hwnd = self._user32.CreateWindowExW(
                0,
                self._class_name,
                self.title,
                0,
                0,
                0,
                0,
                0,
                None,
                None,
                self._hinstance,
                None,
            )
            if not self._hwnd:
                raise ctypes.WinError(ctypes.get_last_error())
            self._icon = self._load_icon()
            self._taskbar_created_message = int(
                self._user32.RegisterWindowMessageW("TaskbarCreated")
            )
            self._available = True
            self._ready.set()

            message = MSG()
            while True:
                result = int(
                    self._user32.GetMessageW(
                        ctypes.byref(message), None, 0, 0
                    )
                )
                if result == 0:
                    break
                if result == -1:
                    raise ctypes.WinError(ctypes.get_last_error())
                self._user32.TranslateMessage(ctypes.byref(message))
                self._user32.DispatchMessageW(ctypes.byref(message))
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            self.error = exc
        finally:
            self._available = False
            self._ready.set()
            self._remove_icon()
            self._destroy_owned_icons()
            hwnd = self._hwnd
            self._hwnd = None
            if hwnd and self._user32 is not None:
                self._user32.DestroyWindow(hwnd)
            if (
                self._class_name
                and self._hinstance
                and self._user32 is not None
            ):
                self._user32.UnregisterClassW(
                    self._class_name, self._hinstance
                )

    def _window_proc(self, hwnd, message, wparam, lparam):
        try:
            if message == WM_TRAY_SHOW:
                self._add_icon()
                self._command_complete.set()
                return 0
            if message == WM_TRAY_HIDE:
                self._remove_icon()
                self._command_complete.set()
                return 0
            if message == WM_TRAY_CALLBACK:
                event_message = int(lparam) & 0xFFFF
                if event_message in (WM_LBUTTONUP, WM_LBUTTONDBLCLK):
                    self._actions.put("show")
                elif event_message == WM_RBUTTONUP:
                    self._show_context_menu(hwnd)
                return 0
            if (
                self._taskbar_created_message
                and message == self._taskbar_created_message
            ):
                self._visible = False
                if self._visible_requested:
                    self._add_icon()
                return 0
            if message == WM_CLOSE:
                self._remove_icon()
                self._user32.DestroyWindow(hwnd)
                return 0
            if message == WM_DESTROY:
                self._remove_icon()
                self._user32.PostQuitMessage(0)
                return 0
        except (AttributeError, OSError, TypeError, ValueError) as exc:
            self.error = exc
        return self._user32.DefWindowProcW(hwnd, message, wparam, lparam)

    def _notification_data(self):
        data = NOTIFYICONDATAW()
        data.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        data.hWnd = self._hwnd
        data.uID = 1
        data.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        data.uCallbackMessage = WM_TRAY_CALLBACK
        data.hIcon = self._icon
        data.szTip = self.title[:127]
        return data

    def _add_icon(self):
        if self._visible:
            return True
        self._visible_requested = True
        data = self._notification_data()
        self._visible = bool(
            self._shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(data))
        )
        if not self._visible:
            self.error = ctypes.WinError(ctypes.get_last_error())
        return self._visible

    def _remove_icon(self):
        self._visible_requested = False
        if not self._visible or self._shell32 is None or not self._hwnd:
            self._visible = False
            return
        data = self._notification_data()
        self._shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(data))
        self._visible = False

    def _show_context_menu(self, hwnd):
        user32 = self._user32
        user32.CreatePopupMenu.argtypes = ()
        user32.CreatePopupMenu.restype = wintypes.HMENU
        user32.AppendMenuW.argtypes = (
            wintypes.HMENU,
            wintypes.UINT,
            ctypes.c_size_t,
            wintypes.LPCWSTR,
        )
        user32.AppendMenuW.restype = wintypes.BOOL
        user32.GetCursorPos.argtypes = (ctypes.POINTER(POINT),)
        user32.GetCursorPos.restype = wintypes.BOOL
        user32.SetForegroundWindow.argtypes = (wintypes.HWND,)
        user32.SetForegroundWindow.restype = wintypes.BOOL
        user32.TrackPopupMenu.argtypes = (
            wintypes.HMENU,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.HWND,
            ctypes.c_void_p,
        )
        user32.TrackPopupMenu.restype = wintypes.UINT
        user32.DestroyMenu.argtypes = (wintypes.HMENU,)
        user32.DestroyMenu.restype = wintypes.BOOL

        menu = user32.CreatePopupMenu()
        if not menu:
            return
        try:
            with self._labels_lock:
                show_label = self._show_label
                exit_label = self._exit_label
            user32.AppendMenuW(menu, MF_STRING, MENU_SHOW, show_label)
            user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
            user32.AppendMenuW(menu, MF_STRING, MENU_EXIT, exit_label)
            point = POINT()
            if not user32.GetCursorPos(ctypes.byref(point)):
                return
            user32.SetForegroundWindow(hwnd)
            command = int(
                user32.TrackPopupMenu(
                    menu,
                    TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                    point.x,
                    point.y,
                    0,
                    hwnd,
                    None,
                )
            )
            if command == MENU_SHOW:
                self._actions.put("show")
            elif command == MENU_EXIT:
                self._actions.put("exit")
        finally:
            user32.DestroyMenu(menu)

    def _load_icon(self):
        self._user32.LoadImageW.argtypes = (
            wintypes.HINSTANCE,
            wintypes.LPCWSTR,
            wintypes.UINT,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        )
        self._user32.LoadImageW.restype = wintypes.HANDLE
        self._user32.GetSystemMetrics.argtypes = (ctypes.c_int,)
        self._user32.GetSystemMetrics.restype = ctypes.c_int
        if self.icon_path is not None and self.icon_path.is_file():
            width = max(16, int(self._user32.GetSystemMetrics(49)))
            height = max(16, int(self._user32.GetSystemMetrics(50)))
            icon = self._user32.LoadImageW(
                None,
                str(self.icon_path),
                1,  # IMAGE_ICON
                width,
                height,
                0x0010,  # LR_LOADFROMFILE
            )
            if icon:
                self._owned_icons = [icon]
                return icon

        shell32 = self._shell32
        shell32.ExtractIconExW.argtypes = (
            wintypes.LPCWSTR,
            ctypes.c_int,
            ctypes.POINTER(wintypes.HICON),
            ctypes.POINTER(wintypes.HICON),
            wintypes.UINT,
        )
        shell32.ExtractIconExW.restype = wintypes.UINT
        if self.executable_path is not None and self.executable_path.is_file():
            large = wintypes.HICON()
            small = wintypes.HICON()
            count = shell32.ExtractIconExW(
                str(self.executable_path),
                0,
                ctypes.byref(large),
                ctypes.byref(small),
                1,
            )
            if count:
                self._owned_icons = [
                    icon for icon in (large, small) if icon
                ]
                return small or large

        self._user32.LoadIconW.argtypes = (
            wintypes.HINSTANCE,
            ctypes.c_void_p,
        )
        self._user32.LoadIconW.restype = wintypes.HICON
        return self._user32.LoadIconW(
            None, ctypes.c_void_p(IDI_APPLICATION)
        )

    def _destroy_owned_icons(self):
        if self._user32 is not None:
            self._user32.DestroyIcon.argtypes = (wintypes.HICON,)
            self._user32.DestroyIcon.restype = wintypes.BOOL
            for icon in self._owned_icons:
                self._user32.DestroyIcon(icon)
        self._owned_icons = []
