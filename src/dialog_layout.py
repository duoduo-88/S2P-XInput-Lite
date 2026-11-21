"""Shared Tk window positioning."""

import ctypes
import functools
import logging
import os
import sys
import tkinter as tk
from contextlib import contextmanager
from ctypes import wintypes
from tkinter import colorchooser, filedialog, font as tkfont, messagebox

from gamepad_aim_display import fullscreen_monitor_bounds

MESSAGEBOX_FUNCTIONS = (
    "showinfo", "showwarning", "showerror",
    "askquestion", "askokcancel", "askretrycancel",
    "askyesno", "askyesnocancel",
)
FILEDIALOG_FUNCTIONS = (
    "askopenfilename", "askopenfilenames", "asksaveasfilename", "askdirectory",
)
_WH_CALLWNDPROCRET = 12
_WM_SHOWWINDOW = 0x0018
_GA_ROOT = 2
_SWP_NOSIZE_NOZORDER_NOACTIVATE = 0x0001 | 0x0004 | 0x0010
_MONITOR_DEFAULTTONEAREST = 2
# Win32 class of the top-level window used by MessageBox, the file dialog
# and the color picker.
_DIALOG_CLASS = "#32770"
_center_hook_active = False


def centered_position(window_size, anchor_rect, work_area):
    """Center a window over anchor_rect, kept inside the monitor work area."""
    width, height = window_size
    anchor_x, anchor_y, anchor_width, anchor_height = anchor_rect
    left, top, right, bottom = work_area
    x = anchor_x + (anchor_width - width) // 2
    y = anchor_y + (anchor_height - height) // 2
    x = max(left, min(x, right - width))
    y = max(top, min(y, bottom - height))
    return x, y


class _MonitorInfo(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", wintypes.RECT),
        ("rcWork", wintypes.RECT),
        ("dwFlags", wintypes.DWORD),
    )


class _CallWndProcRet(ctypes.Structure):
    _fields_ = (
        ("lResult", ctypes.c_ssize_t),
        ("lParam", wintypes.LPARAM),
        ("wParam", wintypes.WPARAM),
        ("message", wintypes.UINT),
        ("hwnd", wintypes.HWND),
    )


class _CenterDialogHook:
    """Move the next system dialog shown on this thread over the anchor.

    The move happens on WM_SHOWWINDOW, just before the dialog becomes
    visible: by then the file dialog has restored its remembered size, which
    it does after activation and would otherwise undo an earlier move.
    """

    def __init__(self, anchor_rect, anchor_hwnd):
        # Private DLL handles keep these prototypes from clobbering other
        # modules that configure the shared ctypes.windll.user32 functions.
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32 = user32
        self._anchor_rect = anchor_rect
        self._anchor_hwnd = anchor_hwnd
        self._moved = False
        hook_proc = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
        )
        user32.SetWindowsHookExW.argtypes = (
            ctypes.c_int, hook_proc, wintypes.HINSTANCE, wintypes.DWORD,
        )
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.CallNextHookEx.argtypes = (
            ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM,
        )
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        user32.UnhookWindowsHookEx.argtypes = (ctypes.c_void_p,)
        user32.UnhookWindowsHookEx.restype = wintypes.BOOL
        user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
        user32.GetClassNameW.restype = ctypes.c_int
        user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user32.GetAncestor.restype = wintypes.HWND
        user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = (
            wintypes.HWND, wintypes.HWND,
            ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
        )
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
        user32.MonitorFromWindow.restype = ctypes.c_void_p
        user32.GetMonitorInfoW.argtypes = (ctypes.c_void_p, ctypes.POINTER(_MonitorInfo))
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        # Keep the callback object alive for as long as the hook is installed.
        self._callback = hook_proc(self._on_message)
        self._hook = user32.SetWindowsHookExW(
            _WH_CALLWNDPROCRET, self._callback, None, kernel32.GetCurrentThreadId(),
        )
        if not self._hook:
            raise ctypes.WinError(ctypes.get_last_error())

    def _on_message(self, code, wparam, lparam):
        if code >= 0 and not self._moved:
            try:
                message = ctypes.cast(lparam, ctypes.POINTER(_CallWndProcRet)).contents
                if message.message == _WM_SHOWWINDOW and message.wParam:
                    self._move_if_dialog(message.hwnd)
            except Exception:  # A hook must never raise into Windows.
                logging.getLogger(__name__).debug("Could not center dialog", exc_info=True)
        return self._user32.CallNextHookEx(None, code, wparam, lparam)

    def _move_if_dialog(self, hwnd):
        user32 = self._user32
        name = ctypes.create_unicode_buffer(32)
        if not user32.GetClassNameW(hwnd, name, len(name)) or name.value != _DIALOG_CLASS:
            return
        # Child dialogs inside the file dialog also use #32770.
        if user32.GetAncestor(hwnd, _GA_ROOT) != hwnd:
            return
        self._moved = True
        rect = wintypes.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return
        info = _MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        monitor = user32.MonitorFromWindow(self._anchor_hwnd, _MONITOR_DEFAULTTONEAREST)
        if not monitor or not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return
        work = info.rcWork
        x, y = centered_position(
            (rect.right - rect.left, rect.bottom - rect.top),
            self._anchor_rect,
            (work.left, work.top, work.right, work.bottom),
        )
        user32.SetWindowPos(hwnd, None, x, y, 0, 0, _SWP_NOSIZE_NOZORDER_NOACTIVATE)

    def close(self):
        hook, self._hook = self._hook, None
        if hook:
            self._user32.UnhookWindowsHookEx(hook)


def _anchor_geometry(anchor):
    """Return (rect, hwnd) for a visible Tk window, or None."""
    window = anchor.winfo_toplevel()
    if not window.winfo_viewable() or window.state() not in ("normal", "zoomed"):
        return None
    window.update_idletasks()
    try:
        hwnd = int(window.wm_frame(), 16)
    except (tk.TclError, ValueError):
        hwnd = window.winfo_id()
    rect = (window.winfo_rootx(), window.winfo_rooty(), window.winfo_width(), window.winfo_height())
    return rect, hwnd


@contextmanager
def centered_message_box(anchor, fallback=None):
    """Open a system dialog centered over anchor instead of where Windows puts it.

    Windows centers MessageBox on the screen and the color picker at the
    owner's top-left even when Tk passes an owner window. When anchor is
    hidden (for example a withdrawn dialog that still has keyboard focus),
    the dialog is centered over fallback instead.
    """
    global _center_hook_active
    hook = None
    if sys.platform == "win32" and not _center_hook_active:
        for candidate in (anchor, fallback):
            if candidate is None:
                continue
            try:
                geometry = _anchor_geometry(candidate)
                if geometry is not None:
                    hook = _CenterDialogHook(*geometry)
                    break
            except (tk.TclError, OSError, AttributeError, ValueError):
                hook = None
    if hook is not None:
        _center_hook_active = True
    try:
        yield
    finally:
        if hook is not None:
            _center_hook_active = False
            hook.close()


def _wrap_centered(module, name, default_anchor):
    original = getattr(module, name)
    if getattr(original, "_centered_over_parent", False):
        return

    @functools.wraps(original)
    def centered(*args, _original=original, **kwargs):
        try:
            fallback = default_anchor()
        except tk.TclError:
            fallback = None
        with centered_message_box(kwargs.get("parent"), fallback):
            return _original(*args, **kwargs)

    centered._centered_over_parent = True
    setattr(module, name, centered)


def install_centered_dialogs(default_anchor, *, messageboxes=True):
    """Center tkinter's system dialogs over their parent, or default_anchor().

    Covers message boxes (unless messageboxes=False, for callers that already
    center them in their own wrapper), file dialogs and the color picker.
    """
    targets = [(filedialog, name) for name in FILEDIALOG_FUNCTIONS]
    targets.append((colorchooser, "askcolor"))
    if messageboxes:
        targets.extend((messagebox, name) for name in MESSAGEBOX_FUNCTIONS)
    for module, name in targets:
        _wrap_centered(module, name, default_anchor)


def process_owns_foreground():
    """Return True when the active Windows window belongs to this process."""
    if sys.platform != "win32":
        return True
    try:
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.GetWindowThreadProcessId.argtypes = (
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
        )
        user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return False
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return int(pid.value) == os.getpid()
    except (AttributeError, OSError):
        return False


def _focus_is_inside(window):
    try:
        focused = window.focus_get()
    except (tk.TclError, KeyError):
        # focus_get() raises KeyError for widgets Tk created internally.
        return False
    return focused is not None and focused.winfo_toplevel() is window


def focus_new_window(window, owns_foreground=process_owns_foreground):
    """Give a newly shown window the keyboard focus inside this application.

    ``focus_set()`` only moves Tk's focus. Windows can keep the previous
    window active, so typing and Enter still went to the window behind the
    new dialog. The focus is forced only while S2P is already the active
    application, so a window opened in the background never steals the
    foreground from another program.
    """
    try:
        if not window.winfo_exists() or not window.winfo_viewable():
            return False
        if window.wm_overrideredirect() or getattr(window, "_s2p_keep_focus", False):
            return False
        grab = window.grab_current()
        if grab is not None and grab.winfo_toplevel() is not window:
            return False  # another modal dialog owns the input
        if not owns_foreground() or _focus_is_inside(window):
            return False
        window.lift()
        window.focus_force()
        return True
    except (tk.TclError, AttributeError):
        return False


def install_focus_new_windows(root, owns_foreground=process_owns_foreground):
    """Focus every top-level window the first time it is shown."""

    def on_map(event):
        window = event.widget
        if not isinstance(window, tk.Toplevel) or getattr(window, "_s2p_focus_on_map_done", False):
            return
        window._s2p_focus_on_map_done = True
        # Let the opening code finish (focus_set on a field, grab_set) first.
        try:
            window.after_idle(lambda: focus_new_window(window, owns_foreground))
        except tk.TclError:
            pass

    root.bind_class("Toplevel", "<Map>", on_map, add="+")
    return on_map


def startup_banner_geometry(root, width, height):
    """Center the banner on the display associated with the root window."""
    try:
        left, top, right, bottom = fullscreen_monitor_bounds(root.winfo_id())
    except OSError:
        left, top, right, bottom = 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()
    x = left + max(0, (right - left - width) // 2)
    y = top + max(0, (bottom - top - height) // 2)
    return f"{width}x{height}+{x}+{y}"


class StartupBannerText:
    """The banner's version and loading line, steady while its dots animate.

    Segoe UI has no CJK glyphs, so Windows filled them from whichever CJK font
    the computer had, and the banner looked different on each PC. Microsoft
    JhengHei UI ships with Windows and covers both the Latin and Chinese text.
    The font is sized in pixels when the banner opens, so a later Tk scaling
    change cannot resize it, and the left edge stays fixed for the widest
    text so the line does not shift as the dots change.
    """

    FAMILY = "Microsoft JhengHei UI"
    POINT_SIZE = 9
    MARGIN_X = 12
    MARGIN_Y = 8

    def __init__(self, canvas, width, height, text_for_dots, *, dots=3):
        self.canvas = canvas
        self._text_for_dots = text_for_dots
        pixels = round(self.POINT_SIZE * float(canvas.tk.call("tk", "scaling")))
        self.font = tkfont.Font(root=canvas, family=self.FAMILY, size=-max(1, pixels))
        widest = max(self.font.measure(text_for_dots(count)) for count in range(1, 4))
        self.item = canvas.create_text(
            width - self.MARGIN_X - widest, height - self.MARGIN_Y,
            anchor="sw", text=text_for_dots(dots), font=self.font, fill="#4a4a4a",
        )

    def show(self, dots):
        self.canvas.itemconfigure(self.item, text=self._text_for_dots(dots))


def center_child_dialog(dialog, owner, *, bounds=None):
    """Open a child centered over its owner's client area."""
    dialog.update_idletasks()
    min_width, min_height = dialog.minsize()
    width = max(min_width, dialog.winfo_reqwidth())
    height = max(min_height, dialog.winfo_reqheight())
    owner_width = owner.winfo_width()
    owner_height = owner.winfo_height()
    x = owner.winfo_x() + (owner_width - width) // 2
    y = owner.winfo_y() + (owner_height - height) // 2
    dialog.geometry(f"+{x}+{y}")
    dialog.deiconify()
    dialog.update_idletasks()
    # A fullscreen owner has no frame; the dialog still has a title bar.
    dx = owner.winfo_rootx() - dialog.winfo_rootx() + (owner_width - dialog.winfo_width()) // 2
    dy = owner.winfo_rooty() - dialog.winfo_rooty() + (owner_height - dialog.winfo_height()) // 2
    frame_x, frame_y = dialog.winfo_x(), dialog.winfo_y()
    x, y = frame_x + dx, frame_y + dy
    if bounds is not None:
        left, top, right, bottom = bounds
        border = max(0, dialog.winfo_rootx() - frame_x)
        title = max(0, dialog.winfo_rooty() - frame_y)
        x = max(left, min(x, right - dialog.winfo_width() - 2 * border))
        y = max(top, min(y, bottom - dialog.winfo_height() - title - border))
    if (x, y) != (frame_x, frame_y):
        dialog.geometry(f"+{x}+{y}")
