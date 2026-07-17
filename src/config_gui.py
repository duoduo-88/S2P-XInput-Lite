import configparser
import ctypes
import importlib.util
import json
import math
import os
import queue
import subprocess
import signal
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from ctypes import wintypes
from pathlib import Path
import serial.tools.list_ports
import winreg
from version import APP_TITLE
from xinput_controller import apply_stick_curve
from switch2_input import SWITCH_BUTTONS
from config_utils import (
    CONFIG_PATH,
    atomic_write_config,
    parse_output_shape_steps,
)
from localization import translate_text
from console_i18n import localized_print as print


_GUI_LANGUAGE = "zh"
_MESSAGEBOX_FUNCTIONS = {}


def _parse_gyro_button_setting(raw_value, valid_buttons, fallback=()):
    """Parse a stable, de-duplicated controller-button list."""
    valid = set(valid_buttons)
    result = []
    for item in str(raw_value or "").split(","):
        name = item.strip().upper()
        if name and name != "NONE" and name in valid and name not in result:
            result.append(name)
    if result:
        return result
    return [name for name in fallback if name in valid]


class _SuppressedKeyboardCapture:
    """Capture global key transitions without activating Windows hotkeys."""

    WH_KEYBOARD_LL = 13
    WM_KEYDOWN = 0x0100
    WM_KEYUP = 0x0101
    WM_SYSKEYDOWN = 0x0104
    WM_SYSKEYUP = 0x0105
    WM_QUIT = 0x0012

    class KBDLLHOOKSTRUCT(ctypes.Structure):
        _fields_ = (
            ("vkCode", wintypes.DWORD),
            ("scanCode", wintypes.DWORD),
            ("flags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ctypes.c_size_t),
        )

    def __init__(self, output_queue):
        self.output_queue = output_queue
        self._ready = threading.Event()
        self._running = False
        self._thread = None
        self._thread_id = 0
        self._hook = None
        self._callback = None

    def start(self):
        self._thread = threading.Thread(
            target=self._run,
            daemon=True,
            name="KeyboardMappingCapture",
        )
        self._thread.start()
        self._ready.wait(timeout=1.0)
        return self._running

    def stop(self):
        if self._thread_id:
            ctypes.windll.user32.PostThreadMessageW(
                self._thread_id, self.WM_QUIT, 0, 0
            )
        thread = self._thread
        if (
            thread is not None
            and thread.is_alive()
            and threading.current_thread() is not thread
        ):
            thread.join(timeout=1.0)
        self._thread = None

    def _run(self):
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hook_proc_type = ctypes.WINFUNCTYPE(
            ctypes.c_ssize_t,
            ctypes.c_int,
            wintypes.WPARAM,
            wintypes.LPARAM,
        )
        user32.SetWindowsHookExW.restype = ctypes.c_void_p
        user32.SetWindowsHookExW.argtypes = (
            ctypes.c_int,
            hook_proc_type,
            wintypes.HINSTANCE,
            wintypes.DWORD,
        )
        user32.UnhookWindowsHookEx.argtypes = (ctypes.c_void_p,)
        user32.CallNextHookEx.restype = ctypes.c_ssize_t
        kernel32.GetModuleHandleW.restype = ctypes.c_void_p
        kernel32.GetModuleHandleW.argtypes = (wintypes.LPCWSTR,)

        def low_level_keyboard_proc(code, message, data_pointer):
            if code >= 0 and message in (
                self.WM_KEYDOWN,
                self.WM_KEYUP,
                self.WM_SYSKEYDOWN,
                self.WM_SYSKEYUP,
            ):
                data = ctypes.cast(
                    data_pointer,
                    ctypes.POINTER(self.KBDLLHOOKSTRUCT),
                ).contents
                self.output_queue.put((
                    message in (self.WM_KEYDOWN, self.WM_SYSKEYDOWN),
                    int(data.vkCode),
                ))
                # Do not pass the event to Explorer: Win+D, Win+E, etc. are
                # capture values here, not commands to execute immediately.
                return 1
            return user32.CallNextHookEx(None, code, message, data_pointer)

        self._callback = hook_proc_type(low_level_keyboard_proc)
        self._thread_id = int(kernel32.GetCurrentThreadId())
        self._hook = user32.SetWindowsHookExW(
            self.WH_KEYBOARD_LL,
            self._callback,
            kernel32.GetModuleHandleW(None),
            0,
        )
        self._running = bool(self._hook)
        self._ready.set()
        if not self._running:
            return
        try:
            message = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                user32.TranslateMessage(ctypes.byref(message))
                user32.DispatchMessageW(ctypes.byref(message))
        finally:
            if self._hook:
                user32.UnhookWindowsHookEx(self._hook)
            self._hook = None
            self._callback = None
            self._running = False
            self._thread_id = 0


def _capture_name_from_vk(vk_code):
    """Return the keyboard-mapping token for a Windows virtual-key code."""
    if 0x30 <= vk_code <= 0x39 or 0x41 <= vk_code <= 0x5A:
        return chr(vk_code)
    if 0x70 <= vk_code <= 0x7B:
        return f"F{vk_code - 0x6F}"
    return {
        0x08: "BACKSPACE", 0x09: "TAB", 0x0D: "ENTER",
        0x10: "SHIFT", 0x11: "CTRL", 0x12: "ALT",
        0x1B: "ESC", 0x20: "SPACE", 0x21: "PAGEUP",
        0x22: "PAGEDOWN", 0x23: "END", 0x24: "HOME",
        0x25: "LEFT", 0x26: "UP", 0x27: "RIGHT", 0x28: "DOWN",
        0x2D: "INSERT", 0x2E: "DELETE",
        0x5B: "WIN", 0x5C: "WIN",
        0xA0: "SHIFT", 0xA1: "SHIFT",
        0xA2: "CTRL", 0xA3: "CTRL",
        0xA4: "ALT", 0xA5: "ALT",
        0xAD: "VOLUME_MUTE", 0xAE: "VOLUME_DOWN", 0xAF: "VOLUME_UP",
        0xB0: "MEDIA_NEXT_TRACK", 0xB1: "MEDIA_PREV_TRACK",
        0xB2: "MEDIA_STOP", 0xB3: "MEDIA_PLAY_PAUSE",
    }.get(int(vk_code))


def _install_localized_messageboxes():
    """讓既有對話框自動套用目前語言，不必重複兩套流程。"""
    for name in (
        "showinfo", "showwarning", "showerror",
        "askquestion", "askokcancel", "askretrycancel",
        "askyesno", "askyesnocancel",
    ):
        original = getattr(messagebox, name)
        _MESSAGEBOX_FUNCTIONS[name] = original

        def localized(title, message, *args, _original=original, **kwargs):
            return _original(
                translate_text(title, _GUI_LANGUAGE),
                translate_text(message, _GUI_LANGUAGE),
                *args,
                **kwargs
            )

        setattr(messagebox, name, localized)


_install_localized_messageboxes()

# 優先使用 Per-Monitor V2，讓 4K 與多螢幕環境取得正確 DPI。
# 舊版 Windows 不支援時再逐級退回。
try:
    if not ctypes.windll.user32.SetProcessDpiAwarenessContext(
        ctypes.c_void_p(-4)
    ):
        raise OSError("Per-Monitor V2 DPI awareness unavailable")
except Exception:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def configure_tk_dpi(root):
    """依目前視窗所在螢幕的實際 DPI 設定 Tk，回傳 UI 比例。"""
    root.update_idletasks()

    dpi = 0.0
    try:
        get_dpi_for_window = ctypes.windll.user32.GetDpiForWindow
        get_dpi_for_window.argtypes = [ctypes.c_void_p]
        get_dpi_for_window.restype = ctypes.c_uint
        dpi = float(
            get_dpi_for_window(
                ctypes.c_void_p(root.winfo_id())
            )
        )
    except Exception:
        pass

    if dpi <= 0.0:
        try:
            dpi = float(root.winfo_fpixels("1i"))
        except Exception:
            dpi = 96.0

    dpi = max(72.0, min(288.0, dpi))
    root.tk.call("tk", "scaling", dpi / 72.0)
    return dpi / 96.0

COMMAND_PATH = Path(__file__).with_name(
    "controller_command.txt"
)
STATUS_PATH = Path(__file__).with_name("controller_status.json")

PYTHON_EXE = Path(sys.executable)

if PYTHON_EXE.name.lower() == "pythonw.exe":
    PYTHON_EXE = PYTHON_EXE.with_name(
        "python.exe"
    )

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ESPTOOL_PATH = (
    PROJECT_ROOT
    / "esp32s3"
    / "tools"
    / "esptool.exe"
)

FIRMWARE_DIR = (
    PROJECT_ROOT
    / "esp32s3"
    / "firmware"
)

BOOTLOADER_PATH = (
    FIRMWARE_DIR
    / "bootloader.bin"
)

PARTITION_PATH = (
    FIRMWARE_DIR
    / "partition-table.bin"
)

APP_FIRMWARE_PATH = (
    FIRMWARE_DIR
    / "esp32s3_bluedroid_bridge.bin"
)

def estimate_shape_circularity_error(shape_steps):
    """Estimate a 32-sector RMS circularity error."""
    try:
        steps = max(0, min(10, int(round(float(shape_steps)))))
    except (TypeError, ValueError, tk.TclError):
        steps = 0
    blend = steps / 10.0
    squared_errors = []
    for index in range(32):
        angle = index * math.pi / 16.0
        square_scale = 1.0 / max(
            abs(math.cos(angle)), abs(math.sin(angle)), 1e-9
        )
        radial_error = blend * (square_scale - 1.0)
        squared_errors.append(radial_error * radial_error)
    return 100.0 * math.sqrt(sum(squared_errors) / len(squared_errors))


def format_shape_circularity_error(shape_steps):
    return f"{estimate_shape_circularity_error(shape_steps):.1f}%"


def draw_output_shape_preview(canvas, shape_steps):
    """在提示框中畫出目前圓形到方形的輸出邊界。"""
    try:
        steps = max(0, min(10, int(round(float(shape_steps)))))
    except (TypeError, ValueError, tk.TclError):
        steps = 0
    blend = steps / 10.0
    width = int(float(canvas.cget("width")))
    height = int(float(canvas.cget("height")))
    center_x = width / 2.0
    center_y = 52.0
    radius = 39.0

    canvas.delete("all")
    canvas.create_rectangle(
        center_x - radius,
        center_y - radius,
        center_x + radius,
        center_y + radius,
        outline="#b8b8b8",
        dash=(3, 2),
    )
    canvas.create_oval(
        center_x - radius,
        center_y - radius,
        center_x + radius,
        center_y + radius,
        outline="#b8b8b8",
    )
    canvas.create_line(
        center_x - radius, center_y, center_x + radius, center_y,
        fill="#d0d0d0",
    )
    canvas.create_line(
        center_x, center_y - radius, center_x, center_y + radius,
        fill="#d0d0d0",
    )

    points = []
    for angle_index in range(121):
        angle = math.tau * angle_index / 120.0
        circle_x = math.cos(angle)
        circle_y = math.sin(angle)
        square_scale = 1.0 / max(abs(circle_x), abs(circle_y), 1e-9)
        output_x = circle_x * (1.0 - blend) + circle_x * square_scale * blend
        output_y = circle_y * (1.0 - blend) + circle_y * square_scale * blend
        points.extend((
            center_x + radius * output_x,
            center_y - radius * output_y,
        ))
    canvas.create_line(*points, fill="#2878c8", width=2)

    diagonal = (1.0 - blend) / math.sqrt(2.0) + blend
    point_x = center_x + radius * diagonal
    point_y = center_y - radius * diagonal
    canvas.create_line(
        center_x, center_y, point_x, point_y,
        fill="#e28a2b", dash=(3, 2),
    )
    canvas.create_oval(
        point_x - 3, point_y - 3, point_x + 3, point_y + 3,
        fill="#e28a2b", outline="",
    )
    canvas.create_text(
        center_x,
        104,
        text=(
            f"{steps}/10   Error≈"
            f"{estimate_shape_circularity_error(steps):.1f}%   "
            f"X=Y≈{diagonal:.3f}"
        ),
        fill="#333333",
    )


class ToolTip:
    """滑鼠移到元件上時顯示說明。"""

    def __init__(self, widget, text, translator=None, illustration=None):
        self.widget = widget
        self.text = text
        self.translator = translator or (lambda value: value)
        self.illustration = illustration
        self.window = None

        self.widget.bind("<Enter>", self.show)
        self.widget.bind("<Leave>", self.hide)

    def show(self, event=None):
        if self.window is not None:
            return

        self.window = tk.Toplevel(
            self.widget
        )

        self.window.wm_overrideredirect(
            True
        )

        content_parent = self.window
        if self.illustration is not None:
            content_parent = tk.Frame(
                self.window,
                relief="solid",
                borderwidth=1,
            )
            content_parent.pack()

        label = tk.Label(
            content_parent,
            text=self.translator(self.text),
            justify="left",
            relief="solid" if self.illustration is None else "flat",
            borderwidth=1 if self.illustration is None else 0,
            padx=8,
            pady=6,
            wraplength=320,
        )

        label.pack()
        if self.illustration is not None:
            canvas = tk.Canvas(
                content_parent,
                width=300,
                height=116,
                bg=label.cget("background"),
                highlightthickness=0,
            )
            canvas.pack(padx=8, pady=(0, 7))
            self.illustration(canvas)

        # 先計算泡泡視窗的實際大小
        self.window.update_idletasks()

        tooltip_width = (
            self.window.winfo_reqwidth()
        )

        tooltip_height = (
            self.window.winfo_reqheight()
        )

        # 預設顯示在問號右下方
        x = (
            self.widget.winfo_rootx()
            + 25
        )

        y = (
            self.widget.winfo_rooty()
            + 25
        )

        # 取得螢幕大小
        screen_width = (
            self.widget.winfo_screenwidth()
        )

        screen_height = (
            self.widget.winfo_screenheight()
        )

        # 右側超出螢幕時，
        # 改到問號左側
        if (
            x + tooltip_width
            > screen_width
        ):
            x = (
                self.widget.winfo_rootx()
                - tooltip_width
                - 5
            )

        # 下方超出螢幕時，
        # 改到問號上方
        if (
            y + tooltip_height
            > screen_height
        ):
            y = (
                self.widget.winfo_rooty()
                - tooltip_height
                - 5
            )

        # 最後保證不會超出螢幕左側或上側
        x = max(
            0,
            x
        )

        y = max(
            0,
            y
        )

        self.window.wm_geometry(
            f"+{x}+{y}"
        )

    def hide(self, event=None):
        if self.window is not None:
            self.window.destroy()
            self.window = None

def is_vigembus_installed():
    """檢查 Windows 是否已安裝 ViGEmBus。"""

    registry_paths = [
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Services\ViGEmBus"
        ),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Nefarius Software Solutions e.U.\ViGEm Bus Driver"
        ),
    ]

    for root, path in registry_paths:
        try:
            with winreg.OpenKey(
                root,
                path,
                0,
                winreg.KEY_READ
            ):
                return True

        except OSError:
            continue

    return False


class StickCurveEditor(ttk.Frame):
    """支援線性與單調平滑顯示的 5 點搖桿曲線編輯器。"""

    def __init__(
        self,
        parent,
        curve_vars,
        deadzone_var,
        outer_deadzone_var,
        deadzone_compress_var,
        outer_deadzone_compress_var,
        interpolation_var,
        label_color="#EA0000",
        width=280,
        height=220,
        zoom_command=None,
        ui_scale=1.0
    ):

        self.deadzone_var = deadzone_var
        self.outer_deadzone_var = (
            outer_deadzone_var
        )

        self.deadzone_compress_var = (
            deadzone_compress_var
        )

        self.outer_deadzone_compress_var = (
            outer_deadzone_compress_var
        )
        self.interpolation_var = interpolation_var

        super().__init__(parent)

        self.curve_vars = curve_vars
        self.label_color = label_color
        self.canvas_width = width
        self.canvas_height = height
        self.zoom_command = zoom_command
        self.zoomed = False
        self.ui_scale = max(0.75, float(ui_scale))

        # 圖表四周留白，避免點貼在邊界
        self.margin_left = round(48 * self.ui_scale)
        self.margin_right = round(20 * self.ui_scale)
        self.margin_top = round(10 * self.ui_scale)
        self.margin_bottom = round(25 * self.ui_scale)

        self.point_radius = max(6, round(6 * self.ui_scale))
        self.dragging_index = None

        self.canvas = tk.Canvas(
            self,
            width=width,
            height=height,
            background="#FFFFFF",
            highlightthickness=1,
            highlightbackground="#A0A0A0"
        )

        self.canvas.pack()

        self.canvas.bind(
            "<Button-1>",
            self.on_mouse_down
        )

        self.canvas.bind(
            "<Double-Button-1>",
            self.on_mouse_double_click
        )

        self.canvas.bind(
            "<B1-Motion>",
            self.on_mouse_drag
        )

        self.canvas.bind(
            "<ButtonRelease-1>",
            self.on_mouse_up
        )

        self.draw()


    def toggle_zoom(self):
        """切換整個搖桿設定區域的放大 / 還原模式。"""

        if self.zoom_command is not None:
            self.zoom_command()


    def set_zoomed(self, zoomed):
        """同步曲線編輯器的放大狀態。"""

        self.zoomed = zoomed


    def set_canvas_size(self, width, height):
        """調整曲線 Canvas 尺寸並重新繪製。"""

        self.canvas_width = width
        self.canvas_height = height

        self.canvas.configure(
            width=width,
            height=height
        )

        self.draw()

    def get_curve_x_range(self):
        """取得曲線目前實際顯示的 X 軸範圍。"""

        try:
            deadzone = max(
                0.0,
                min(
                    1.0,
                    float(
                        self.deadzone_var.get()
                    )
                )
            )

            outer_deadzone = max(
                0.0,
                min(
                    1.0,
                    float(
                        self.outer_deadzone_var.get()
                    )
                )
            )

        except (
            ValueError,
            TypeError
        ):
            deadzone = 0.0
            outer_deadzone = 0.0

        curve_start = 0.0
        curve_end = 1.0

        if self.deadzone_compress_var.get():
            curve_start = deadzone

        if self.outer_deadzone_compress_var.get():
            curve_end = (
                1.0
                - outer_deadzone
            )

        # 避免死區設定異常，
        # 導致曲線有效範圍為 0 或反轉。
        #
        # 發生異常時暫時取消壓縮，
        # 回到完整 0.0 ～ 1.0 範圍。
        if curve_end <= curve_start:
            curve_start = 0.0
            curve_end = 1.0

        return (
            curve_start,
            curve_end
        )


    def curve_x_to_display_x(
        self,
        x_value
    ):
        """將曲線內部 X 座標映射到實際顯示位置。"""

        curve_start, curve_end = (
            self.get_curve_x_range()
        )

        return (
            curve_start
            + x_value
            * (
                curve_end
                - curve_start
            )
        )


    def display_x_to_curve_x(
        self,
        display_x
    ):
        """將實際顯示位置還原成曲線內部 X 座標。"""

        curve_start, curve_end = (
            self.get_curve_x_range()
        )

        curve_width = (
            curve_end
            - curve_start
        )

        if curve_width <= 0.0:
            return 0.0

        x_value = (
            (
                display_x
                - curve_start
            )
            / curve_width
        )

        return max(
            0.0,
            min(
                1.0,
                x_value
            )
        )


    def value_to_canvas(self, x_value, y_value):
        """將 0.0～1.0 的曲線數值轉換成 Canvas 座標。"""

        graph_width = (
            self.canvas_width
            - self.margin_left
            - self.margin_right
        )

        graph_height = (
            self.canvas_height
            - self.margin_top
            - self.margin_bottom
        )

        x = (
            self.margin_left
            + x_value * graph_width
        )

        y = (
            self.margin_top
            + (1.0 - y_value) * graph_height
        )

        return x, y


    def canvas_to_value(self, y):
        """將滑鼠 Y 座標轉換成 0.0～1.0 的輸出值。"""

        graph_height = (
            self.canvas_height
            - self.margin_top
            - self.margin_bottom
        )

        value = 1.0 - (
            (y - self.margin_top)
            / graph_height
        )

        return max(
            0.0,
            min(1.0, value)
        )

    def canvas_to_x_value(self, x):
        """將滑鼠 X 座標轉換成 0.0～1.0 的輸入值。"""

        graph_width = (
            self.canvas_width
            - self.margin_left
            - self.margin_right
        )

        value = (
            (x - self.margin_left)
            / graph_width
        )

        return max(
            0.0,
            min(1.0, value)
        )


    def draw(self):
        """重新繪製格線、曲線與控制點。"""

        self.canvas.delete("all")

        x_values = [
            0.00,
            0.25,
            0.50,
            0.75,
            1.00
        ]

        # =========================
        # 顯示中心死區與外圍死區
        # =========================

        try:
            deadzone = max(
                0.0,
                min(
                    1.0,
                    float(
                        self.deadzone_var.get()
                    )
                )
            )

            outer_deadzone = max(
                0.0,
                min(
                    1.0,
                    float(
                        self.outer_deadzone_var.get()
                    )
                )
            )

        except (ValueError, TypeError):
            deadzone = 0.0
            outer_deadzone = 0.0

        graph_left = self.margin_left
        graph_right = (
            self.canvas_width
            - self.margin_right
        )
        graph_top = self.margin_top
        graph_bottom = (
            self.canvas_height
            - self.margin_bottom
        )

        # 中心死區結束位置
        deadzone_x, _ = self.value_to_canvas(
            deadzone,
            0.0
        )

        # 外圍死區開始位置
        outer_start = max(
            0.0,
            1.0 - outer_deadzone
        )

        outer_x, _ = self.value_to_canvas(
            outer_start,
            0.0
        )

        # 左側：中心死區
        if deadzone > 0.0:
            self.canvas.create_rectangle(
                graph_left,
                graph_top,
                deadzone_x,
                graph_bottom,
                fill="#d9d9d9",
                outline=""
            )

        # 右側：外圍死區
        if outer_deadzone > 0.0:
            self.canvas.create_rectangle(
                outer_x,
                graph_top,
                graph_right,
                graph_bottom,
                fill="#d9d9d9",
                outline=""
            )

        # 畫 25% 間隔的格線
        for value in x_values:
            x, _ = self.value_to_canvas(
                value,
                0.0
            )

            _, y = self.value_to_canvas(
                0.0,
                value
            )

            self.canvas.create_line(
                x,
                self.margin_top,
                x,
                self.canvas_height
                - self.margin_bottom,
                fill="#d0d0d0"
            )

            self.canvas.create_line(
                self.margin_left,
                y,
                self.canvas_width
                - self.margin_right,
                y,
                fill="#d0d0d0"
            )


        # X 軸標示
        for index, value in enumerate(x_values):
            x, _ = self.value_to_canvas(
                value,
                0.0
            )

            self.canvas.create_text(
                x,
                self.canvas_height - 15,
                text=f"{int(value * 100)}%"
            )

        # Y 軸標示
        for value in x_values:
            _, y = self.value_to_canvas(
                0.0,
                value
            )

            self.canvas.create_text(
                self.margin_left - 12,
                y,
                text=f"{int(value * 100)}%",
                anchor="e"
            )

        # 取得目前 5 個控制點
        points = []

        for index in range(5):
            x_value = self.curve_vars[
                index
            ]["x"].get()

            y_value = self.curve_vars[
                index
            ]["y"].get()

            display_x_value = (
                self.curve_x_to_display_x(
                    x_value
                )
            )

            x, y = self.value_to_canvas(
                display_x_value,
                y_value
            )

            points.append(
                (
                    x,
                    y,
                    x_value,
                    y_value
                )
            )

        # =========================
        # 繪製實際完整曲線
        # =========================
        #
        # 固定起點 (0, 0)
        #     ↓
        # 5 個可調整控制點
        #     ↓
        # 固定終點 (1, 1)
        #
        # 固定起點與終點只用來畫線，
        # 不會變成可拖曳控制點。

        curve_start, curve_end = (
            self.get_curve_x_range()
        )

        start_x, start_y = self.value_to_canvas(
            curve_start,
            0.0
        )

        end_x, end_y = self.value_to_canvas(
            curve_end,
            1.0
        )

        curve_points = [
            (point[2], point[3]) for point in points
        ]
        if self.interpolation_var.get().strip().upper() == "SMOOTH":
            line_coordinates = []
            for sample in range(121):
                curve_x = sample / 120.0
                curve_y = apply_stick_curve(
                    curve_x, curve_points, "SMOOTH"
                )
                display_x = self.curve_x_to_display_x(curve_x)
                canvas_x, canvas_y = self.value_to_canvas(
                    display_x, curve_y
                )
                line_coordinates.extend((canvas_x, canvas_y))
            self.canvas.create_line(
                *line_coordinates,
                width=2,
                smooth=False,
            )
        else:
            line_coordinates = [start_x, start_y]
            for point in points:
                line_coordinates.extend((point[0], point[1]))
            line_coordinates.extend((end_x, end_y))
            self.canvas.create_line(*line_coordinates, width=2)

        # 畫 5 個控制點與目前 XY 座標
        for index, (
            x,
            y,
            x_value,
            y_value
        ) in enumerate(points):
            radius = self.point_radius

            self.canvas.create_oval(
                x - radius,
                y - radius,
                x + radius,
                y + radius,
                fill="white",
                outline="black",
                width=2,
                tags=(
                    "curve_point",
                    f"point_{index}"
                )
            )

            # 在控制點上方顯示目前座標
            label_y = y - round(16 * self.ui_scale)

            # 避免最上方的文字超出 Canvas
            if label_y < round(10 * self.ui_scale):
                label_y = y + round(18 * self.ui_scale)

            self.canvas.create_text(
                x,
                label_y,
                text=(
                    f"({x_value:.2f}, "
                    f"{y_value:.2f})"
                ),
                fill=self.label_color,
                font=("", 8, "bold")
            )


    def find_nearest_point(self, mouse_x, mouse_y):
        """尋找滑鼠附近的控制點。"""

        nearest_index = None
        nearest_distance = round(15 * self.ui_scale)

        for index in range(5):
            x_value = self.curve_vars[
                index
            ]["x"].get()

            y_value = self.curve_vars[
                index
            ]["y"].get()

            display_x_value = (
                self.curve_x_to_display_x(
                    x_value
                )
            )

            x, y = self.value_to_canvas(
                display_x_value,
                y_value
            )

            distance = (
                (mouse_x - x) ** 2
                + (mouse_y - y) ** 2
            ) ** 0.5

            if distance < nearest_distance:
                nearest_distance = distance
                nearest_index = index

        return nearest_index


    def on_mouse_down(self, event):
        self.dragging_index = (
            self.find_nearest_point(
                event.x,
                event.y
            )
        )

    def on_mouse_double_click(
        self,
        event
    ):
        """雙擊單一控制點，恢復該點的預設位置。"""

        index = self.find_nearest_point(
            event.x,
            event.y
        )

        if index is None:
            return

        default_value = (
            index * 0.25
        )

        self.curve_vars[
            index
        ][
            "x"
        ].set(
            default_value
        )

        self.curve_vars[
            index
        ][
            "y"
        ].set(
            default_value
        )

        self.dragging_index = None

        self.draw()


    def on_mouse_drag(self, event):
        if self.dragging_index is None:
            return

        index = self.dragging_index

        # 將滑鼠位置轉換成實際顯示座標
        display_x_value = (
            self.canvas_to_x_value(
                event.x
            )
        )

        # 將壓縮後的顯示座標，
        # 還原成曲線內部的 0.0 ～ 1.0 座標
        x_value = (
            self.display_x_to_curve_x(
                display_x_value
            )
        )

        y_value = self.canvas_to_value(
            event.y
        )

        # 控制點之間保留最小距離，
        # 避免互相穿過或重疊
        minimum_gap = 0.01

        # 根據控制點位置決定可移動的 X 範圍
        if index == 0:
            min_x = 0.0
            max_x = (
                self.curve_vars[1]["x"].get()
                - minimum_gap
            )

        elif index == 4:
            min_x = (
                self.curve_vars[3]["x"].get()
                + minimum_gap
            )
            max_x = 1.0

        else:
            min_x = (
                self.curve_vars[
                    index - 1
                ]["x"].get()
                + minimum_gap
            )

            max_x = (
                self.curve_vars[
                    index + 1
                ]["x"].get()
                - minimum_gap
            )

        x_value = max(
            min_x,
            min(
                max_x,
                x_value
            )
        )

        # 更新目前控制點的 X、Y
        self.curve_vars[
            index
        ]["x"].set(
            round(x_value, 3)
        )

        self.curve_vars[
            index
        ]["y"].set(
            round(y_value, 3)
        )

        self.draw()


    def on_mouse_up(self, event):
        self.dragging_index = None


class ConfigGUI:
    def __init__(self, root):
        self.root = root
        self.ui_scale = configure_tk_dpi(root)
        
        self.vigembus_installed = (
            is_vigembus_installed()
        )        
        
        # 記錄由 GUI 啟動的子程序
        self.main_process = None
        self.calibration_process = None
        self.flash_process = None

        # 按下視窗右上角 X 時，統一關閉所有程序
        self.root.protocol(
            "WM_DELETE_WINDOW",
            self.on_close
        )              
        self.root.title(
            f"{APP_TITLE} Setting UI"
        )
        self.root.resizable(False, False)

        # =========================
        # 搖桿設定放大模式尺寸
        # =========================
        #
        # 這幾個數值可以直接獨立修改：
        #
        # ZOOM_CANVAS_WIDTH / HEIGHT
        #     放大後「整個 Canvas」的寬高。
        #
        # ZOOM_MARGIN_*
        #     放大後「裡面座標圖」距離 Canvas 邊框的距離。
        #
        # 例如想讓座標圖更靠近左邊：
        #     ZOOM_MARGIN_LEFT = 55
        #
        # 想讓座標圖離右邊更遠：
        #     ZOOM_MARGIN_RIGHT = 60
        #
        self.ZOOM_CANVAS_WIDTH = round(840 * self.ui_scale)
        self.ZOOM_CANVAS_HEIGHT = round(660 * self.ui_scale)

        self.ZOOM_MARGIN_LEFT = round(50 * self.ui_scale)
        self.ZOOM_MARGIN_RIGHT = round(30 * self.ui_scale)
        self.ZOOM_MARGIN_TOP = round(20 * self.ui_scale)
        self.ZOOM_MARGIN_BOTTOM = round(35 * self.ui_scale)

        # 原始曲線尺寸與邊距
        self.NORMAL_CANVAS_WIDTH = round(280 * self.ui_scale)
        self.NORMAL_CANVAS_HEIGHT = round(220 * self.ui_scale)

        self.NORMAL_MARGIN_LEFT = round(48 * self.ui_scale)
        self.NORMAL_MARGIN_RIGHT = round(20 * self.ui_scale)
        self.NORMAL_MARGIN_TOP = round(10 * self.ui_scale)
        self.NORMAL_MARGIN_BOTTOM = round(25 * self.ui_scale)

        # 搖桿設定區域放大狀態
        self.stick_settings_zoomed = False
        self.normal_geometry = None

        self.config = configparser.ConfigParser()

        if not CONFIG_PATH.exists():
            messagebox.showerror(
                "錯誤",
                "找不到 config.ini。\n"
                "請將 config_gui.py 放在主程式相同資料夾。"
            )
            self.root.destroy()
            return

        self.config.read(
            CONFIG_PATH,
            encoding="utf-8"
        )

        self.language = self.config.get(
            "gui", "language", fallback="zh"
        ).strip().lower()
        if self.language not in ("zh", "en"):
            self.language = "zh"
        global _GUI_LANGUAGE
        _GUI_LANGUAGE = self.language

        self.create_variables()
        self.create_widgets()
        self.apply_language()
        # 主動啟動連接程序，使 ESP32/Windows BLE 模式在尚未連上
        # 手把時也能刷新到狀態列，而不是顯示上次留下的狀態。
        self.root.after(250, self.start_connection_on_launch)

    def get_serial_ports(self):
        """取得目前所有可用的 COM Port。"""

        return {
            port.device
            for port in serial.tools.list_ports.comports()
        }

    def flash_firmware(self):
        """自動偵測 ESP32-S3 刷機連接埠並刷入相容韌體。"""

        if (
            self.flash_process is not None
            and self.flash_process.poll() is None
        ):
            messagebox.showinfo(
                self.tr("韌體刷寫中"),
                self.tr("ESP32-S3 韌體仍在刷寫中，請等待完成。"),
            )
            return

        required_files = [
            ESPTOOL_PATH,
            BOOTLOADER_PATH,
            PARTITION_PATH,
            APP_FIRMWARE_PATH,
        ]

        missing_files = [
            path.name
            for path in required_files
            if not path.exists()
        ]

        if missing_files:
            messagebox.showerror(
                "缺少刷機檔案",
                "找不到以下檔案：\n\n"
                + "\n".join(missing_files)
            )
            return

        confirmed = messagebox.askyesno(
            "刷入相容韌體",
            "即將刷入相容的 ESP32-S3 韌體。\n\n"
            "程式將自動偵測 ESP32-S3 的刷機連接埠。\n\n"
            "是否繼續？"
        )

        if not confirmed:
            return

        # 關閉主連接程式，釋放正常模式的 COM Port
        self.stop_main_process()

        # 記錄目前存在的 COM Port
        self.ports_before_flash = self.get_serial_ports()

        messagebox.showinfo(
            "進入刷機模式",
            "請讓 ESP32-S3 進入刷機模式：\n\n"
            "1. 連接EPSP32-S3的OTG接口\n"
            "2. 按住 BOOT 按鈕\n"
            "3. 按一下 RESET / EN 按鈕\n"
            "4. 放開 RESET / EN\n"
            "5. 再放開 BOOT\n\n"
            "完成後不需要做其他操作。\n"
            "程式會自動偵測刷機連接埠。"
        )

        self.flash_detect_attempts = 0
        self.detect_flash_port()

    def detect_flash_port(self):
        """等待 ESP32-S3 刷機模式的新 COM Port。"""

        current_ports = self.get_serial_ports()

        new_ports = (
            current_ports
            - self.ports_before_flash
        )

        if new_ports:
            flash_port = sorted(new_ports)[0]

            messagebox.showinfo(
                "偵測到刷機連接埠",
                f"已偵測到刷機連接埠：{flash_port}\n\n"
                "即將開始刷入韌體。"
            )

            self.start_firmware_flash(
                flash_port
            )
            return

        self.flash_detect_attempts += 1

        if self.flash_detect_attempts >= 60:
            messagebox.showerror(
                "未偵測到刷機連接埠",
                "30 秒內未偵測到新的 COM Port。\n\n"
                "請確認 ESP32-S3 已正確進入刷機模式，"
                "然後重新嘗試。"
            )
            return

        self.root.after(
            500,
            self.detect_flash_port
        )

    def start_firmware_flash(self, port):
        try:
            command = [
                str(ESPTOOL_PATH),

                "--chip",
                "esp32s3",

                "--port",
                port,

                "--baud",
                "921600",

                "write_flash",

                "--flash_mode",
                "dio",

                "--flash_freq",
                "80m",

                "--flash_size",
                "16MB",

                "0x0",
                str(BOOTLOADER_PATH),

                "0x8000",
                str(PARTITION_PATH),

                "0x10000",
                str(APP_FIRMWARE_PATH),
            ]

            self.flash_process = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                creationflags=getattr(
                    subprocess,
                    "CREATE_NEW_CONSOLE",
                    0
                )
            )

            self.root.after(
                250,
                lambda: self.poll_firmware_flash(self.flash_process),
            )

        except Exception as exc:
            self.flash_process = None
            messagebox.showerror(
                "刷機失敗",
                f"無法啟動韌體刷入程序：\n{exc}"
            )

    def poll_firmware_flash(self, process):
        """由 Tk 事件循環監控 esptool，完成後顯示結果。"""
        return_code = process.poll()
        if return_code is None:
            self.root.after(
                250,
                lambda: self.poll_firmware_flash(process),
            )
            return

        self.finish_firmware_flash(process, return_code)

    def finish_firmware_flash(self, process, return_code):
        if self.flash_process is process:
            self.flash_process = None

        if return_code == 0:
            messagebox.showinfo(
                self.tr("韌體刷寫完成"),
                self.tr(
                    "ESP32-S3 韌體已刷寫完成。\n\n"
                    "請按一下 RESET / EN 按鈕，或拔除後重新插入 "
                    "ESP32-S3，再重新開啟本軟體。"
                ),
            )
        else:
            messagebox.showerror(
                self.tr("韌體刷寫失敗"),
                self.tr("esptool 刷寫失敗，結束代碼：")
                + str(return_code),
            )


    def create_variables(self):
        # =========================
        # 左右搖桿死區設定
        # =========================
        #
        # 左右搖桿設定分別儲存在：
        #
        # [stick_curve_left]
        # [stick_curve_right]
        # =========================
        # 左搖桿
        # =========================

        self.left_deadzone_var = tk.StringVar(
            value=self.config.get(
                "stick_curve_left",
                "deadzone",
                fallback="0.03"
            )
        )

        self.left_outer_deadzone_var = tk.StringVar(
            value=self.config.get(
                "stick_curve_left",
                "outer_deadzone",
                fallback="0.03"
            )
        )
        self.left_output_shape_var = tk.IntVar(
            value=parse_output_shape_steps(
                self.config.get(
                    "stick_curve_left",
                    "output_shape",
                    fallback="CIRCLE",
                )
            )
        )
        self.left_interpolation_var = tk.StringVar(
            value=self.config.get(
                "stick_curve_left", "interpolation", fallback="LINEAR"
            ).strip().upper()
        )
        if self.left_interpolation_var.get() not in ("LINEAR", "SMOOTH"):
            self.left_interpolation_var.set("LINEAR")

        self.left_deadzone_compress_var = (
            tk.BooleanVar(
                value=self.config.getboolean(
                    "stick_curve_left",
                    "deadzone_compress",
                    fallback=True
                )
            )
        )

        self.left_outer_deadzone_compress_var = (
            tk.BooleanVar(
                value=self.config.getboolean(
                    "stick_curve_left",
                    "outer_deadzone_compress",
                    fallback=True
                )
            )
        )
        # =========================
        # 右搖桿
        # =========================

        self.right_deadzone_var = tk.StringVar(
            value=self.config.get(
                "stick_curve_right",
                "deadzone",
                fallback="0.03"
            )
        )

        self.right_outer_deadzone_var = tk.StringVar(
            value=self.config.get(
                "stick_curve_right",
                "outer_deadzone",
                fallback="0.03"
            )
        )
        self.right_output_shape_var = tk.IntVar(
            value=parse_output_shape_steps(
                self.config.get(
                    "stick_curve_right",
                    "output_shape",
                    fallback="CIRCLE",
                )
            )
        )
        self.right_interpolation_var = tk.StringVar(
            value=self.config.get(
                "stick_curve_right", "interpolation", fallback="LINEAR"
            ).strip().upper()
        )
        if self.right_interpolation_var.get() not in ("LINEAR", "SMOOTH"):
            self.right_interpolation_var.set("LINEAR")

        self.right_deadzone_compress_var = (
            tk.BooleanVar(
                value=self.config.getboolean(
                    "stick_curve_right",
                    "deadzone_compress",
                    fallback=True
                )
            )
        )

        self.right_outer_deadzone_compress_var = (
            tk.BooleanVar(
                value=self.config.getboolean(
                    "stick_curve_right",
                    "outer_deadzone_compress",
                    fallback=True
                )
            )
        )
 
        # 左右搖桿的 5 點 XY 曲線
        self.left_curve_vars = [
            {
                "x": tk.DoubleVar(
                    value=self.config.getfloat(
                        "stick_curve_left",
                        f"point_{i}_x",
                        fallback=i * 0.25
                    )
                ),
                "y": tk.DoubleVar(
                    value=self.config.getfloat(
                        "stick_curve_left",
                        f"point_{i}_y",
                        fallback=self.config.getfloat(
                            "stick_curve_left",
                            f"point_{i}",
                            fallback=i * 0.25
                        )
                    )
                ),
            }
            for i in range(5)
        ]

        self.right_curve_vars = [
            {
                "x": tk.DoubleVar(
                    value=self.config.getfloat(
                        "stick_curve_right",
                        f"point_{i}_x",
                        fallback=i * 0.25
                    )
                ),
                "y": tk.DoubleVar(
                    value=self.config.getfloat(
                        "stick_curve_right",
                        f"point_{i}_y",
                        fallback=self.config.getfloat(
                            "stick_curve_right",
                            f"point_{i}",
                            fallback=i * 0.25
                        )
                    )
                ),
            }
            for i in range(5)
        ] 

        # =========================
        # 左右搖桿防抖
        # =========================

        self.left_stick_smoothing_var = tk.DoubleVar(
            value=self.config.getfloat(
                "stick_curve_left",
                "smoothing",
                fallback=0.0
            )
        )

        self.right_stick_smoothing_var = tk.DoubleVar(
            value=self.config.getfloat(
                "stick_curve_right",
                "smoothing",
                fallback=0.0
            )
        )


        # Rumble
        self.lf_strength_var = tk.StringVar(
            value=self.config.get(
                "rumble",
                "lf_strength",
                fallback="1.00"
            )
        )

        self.hf_strength_var = tk.StringVar(
            value=self.config.get(
                "rumble",
                "hf_strength",
                fallback="1.00"
            )
        )

        self.lf_curve_var = tk.StringVar(
            value=self.config.get(
                "rumble",
                "lf_curve",
                fallback="1.00"
            )
        )

        self.hf_curve_var = tk.StringVar(
            value=self.config.get(
                "rumble",
                "hf_curve",
                fallback="1.00"
            )
        )

        self.lf_to_hf_compensation_var = tk.StringVar(
            value=self.config.get(
                "rumble",
                "lf_to_hf_compensation",
                fallback="0.00"
            )
        )

        self.hf_to_lf_compensation_var = tk.StringVar(
            value=self.config.get(
                "rumble",
                "hf_to_lf_compensation",
                fallback="0.00"
            )
        )

        self.lf_frequency_var = tk.StringVar(
            value=self.config.get(
                "rumble",
                "lf_frequency",
                fallback="225"
            )
        )

        self.hf_frequency_var = tk.StringVar(
            value=self.config.get(
                "rumble",
                "hf_frequency",
                fallback="481"
            )
        )

        self.max_amplitude_var = tk.StringVar(
            value=self.config.get(
                "rumble",
                "max_amplitude",
                fallback="800"
            )
        )

        # Windows 系統音訊轉震動
        self.audio_haptics_mode_var = tk.StringVar(
            value=self.config.get(
                "audio_haptics", "mode", fallback="GAME"
            ).strip().upper()
        )
        if self.audio_haptics_mode_var.get() not in ("GAME", "AUDIO", "MIX"):
            self.audio_haptics_mode_var.set("GAME")
        self.audio_haptics_mix_ratio_var = tk.StringVar(
            value=self.config.get(
                "audio_haptics", "mix_ratio", fallback="0.35"
            )
        )
        self.audio_haptics_strength_var = tk.StringVar(
            value=self.config.get("audio_haptics", "strength", fallback="0.60")
        )
        try:
            legacy_audio_low_gain = self.config.getfloat(
                "audio_haptics", "low_gain", fallback=1.00
            )
        except (ValueError, TypeError, configparser.Error):
            legacy_audio_low_gain = 1.00
        try:
            legacy_audio_high_gain = self.config.getfloat(
                "audio_haptics", "high_gain", fallback=1.00
            )
        except (ValueError, TypeError, configparser.Error):
            legacy_audio_high_gain = 1.00
        legacy_audio_low_gain = max(0.0, min(2.0, legacy_audio_low_gain))
        legacy_audio_high_gain = max(0.0, min(2.0, legacy_audio_high_gain))
        legacy_audio_band_gains = (
            legacy_audio_low_gain,
            (2.0 * legacy_audio_low_gain + legacy_audio_high_gain) / 3.0,
            (legacy_audio_low_gain + legacy_audio_high_gain) / 2.0,
            (legacy_audio_low_gain + 2.0 * legacy_audio_high_gain) / 3.0,
            legacy_audio_high_gain,
        )
        self.audio_haptics_band_gain_fallbacks = tuple(
            round(value, 2) for value in legacy_audio_band_gains
        )
        self.audio_haptics_band_gain_vars = [
            tk.StringVar(
                value=self.config.get(
                    "audio_haptics",
                    f"band_{index}_gain",
                    fallback=f"{fallback:.2f}",
                )
            )
            for index, fallback in enumerate(
                self.audio_haptics_band_gain_fallbacks, start=1
            )
        ]
        self.audio_haptics_noise_gate_var = tk.StringVar(
            value=self.config.get("audio_haptics", "noise_gate", fallback="0.015")
        )
        self.audio_haptics_attack_var = tk.StringVar(
            value=self.config.get("audio_haptics", "attack_ms", fallback="1")
        )
        self.audio_haptics_release_var = tk.StringVar(
            value=self.config.get("audio_haptics", "release_ms", fallback="140")
        )
        self.audio_haptics_final_tail_strength_var = tk.StringVar(
            value=self.config.get(
                "audio_haptics", "final_tail_strength", fallback="0.00"
            )
        )
        self.audio_haptics_final_tail_decay_var = tk.StringVar(
            value=self.config.get(
                "audio_haptics", "final_tail_decay_ms", fallback="350"
            )
        )

        # =========================
        # 按鍵映射可選目標
        # =========================
        self.button_options = [
            "NONE",

            # Xbox 按鍵
            "A", "B", "X", "Y",
            "LB", "RB",
            "LT", "RT",
            "START", "BACK", "GUIDE",
            "L_STK", "R_STK",
            "UP", "DOWN", "LEFT", "RIGHT",

            # Xbox 左搖桿方向
            "L_STICK_UP",
            "L_STICK_DOWN",
            "L_STICK_LEFT",
            "L_STICK_RIGHT",

            # Xbox 右搖桿方向
            "R_STICK_UP",
            "R_STICK_DOWN",
            "R_STICK_LEFT",
            "R_STICK_RIGHT",

            # 自定義鍵盤
            "CUSTOM_KEYBOARD",
        ]

        # =========================
        # 搖桿方向映射
        # =========================

        # 左右搖桿各自選擇 4 向或 8 向
        self.stick_direction_mode_vars = {
            "LEFT": tk.StringVar(
                value=self.config.get(
                    "stick_direction_left",
                    "mode",
                    fallback="4WAY"
                ).upper()
            ),

            "RIGHT": tk.StringVar(
                value=self.config.get(
                    "stick_direction_right",
                    "mode",
                    fallback="4WAY"
                ).upper()
            ),
        }

        # 搖桿方向可以映射到：
        # Xbox 按鍵 / D-Pad / 鍵盤
        self.stick_direction_options = [
            "NONE",
            "A", "B", "X", "Y",
            "LB", "RB",
            "LT", "RT",
            "START", "BACK", "GUIDE",
            "L_STK", "R_STK",
            "UP", "DOWN", "LEFT", "RIGHT",
            "CUSTOM_KEYBOARD",
        ]

        # 8 個可能方向
        direction_names = [
            "UP",
            "UP_RIGHT",
            "RIGHT",
            "DOWN_RIGHT",
            "DOWN",
            "DOWN_LEFT",
            "LEFT",
            "UP_LEFT",
        ]

        # 左右搖桿各自保存方向映射
        self.stick_direction_vars = {}

        for side in (
            "LEFT",
            "RIGHT"
        ):
            section_name = (
                "stick_direction_"
                + side.lower()
            )

            self.stick_direction_vars[
                side
            ] = {}

            for direction in direction_names:
                value = self.config.get(
                    section_name,
                    direction.lower(),
                    fallback="NONE"
                ).upper()

                self.stick_direction_vars[
                    side
                ][
                    direction
                ] = tk.StringVar(
                    value=value
                )

        # 方向映射參數由左右搖桿獨立保存。舊版的共用值僅作為
        # 移植回退，使現有 config.ini 首次開啟時保持原本手感。
        legacy_trigger = self.config.get(
            "stick_direction", "trigger_threshold", fallback="0.60"
        )
        legacy_release = self.config.get(
            "stick_direction", "release_threshold", fallback="0.50"
        )
        legacy_direction_deadzone = self.config.get(
            "stick_direction", "angle_hysteresis", fallback="5.0"
        )
        legacy_mouse_speed = self.config.get(
            "stick_direction", "mouse_speed", fallback="900"
        )
        self.stick_direction_trigger_vars = {}
        self.stick_direction_release_vars = {}
        self.stick_direction_deadzone_vars = {}
        self.stick_mouse_speed_vars = {}
        for side in ("LEFT", "RIGHT"):
            section_name = f"stick_direction_{side.lower()}"
            self.stick_direction_trigger_vars[side] = tk.StringVar(
                value=self.config.get(
                    section_name, "trigger_threshold", fallback=legacy_trigger
                )
            )
            self.stick_direction_release_vars[side] = tk.StringVar(
                value=self.config.get(
                    section_name, "release_threshold", fallback=legacy_release
                )
            )
            self.stick_direction_deadzone_vars[side] = tk.StringVar(
                value=self.config.get(
                    section_name,
                    "direction_deadzone",
                    fallback=legacy_direction_deadzone,
                )
            )
            self.stick_mouse_speed_vars[side] = tk.StringVar(
                value=self.config.get(
                    section_name, "mouse_speed", fallback=legacy_mouse_speed
                )
            )

        # 陀螺儀映射
        self.gyro_activation_mode_var = tk.StringVar(
            value=self.config.get(
                "gyro_mapping", "activation_mode", fallback="OFF"
            ).strip().upper()
        )
        if self.gyro_activation_mode_var.get() not in ("OFF", "HOLD", "TOGGLE"):
            self.gyro_activation_mode_var.set("OFF")
        activation_options = [
            name for name in SWITCH_BUTTONS
            if name not in (
                "SR_R", "SL_R", "SR_L", "SL_L", "L", "ZL"
            )
        ]
        shoulder_insert_index = activation_options.index("ZR") + 1
        self.gyro_activation_button_options = (
            activation_options[:shoulder_insert_index]
            + [name for name in ("L", "ZL") if name in SWITCH_BUTTONS]
            + activation_options[shoulder_insert_index:]
        )
        legacy_activation_button = self.config.get(
            "gyro_mapping", "activation_button", fallback="ZL"
        )
        raw_activation_buttons = self.config.get(
            "gyro_mapping",
            "activation_buttons",
            fallback=legacy_activation_button,
        )
        self.gyro_activation_buttons = _parse_gyro_button_setting(
            raw_activation_buttons,
            self.gyro_activation_button_options,
            fallback=("ZL",),
        )
        activation_match = self.config.get(
            "gyro_mapping", "activation_match", fallback="ANY"
        ).strip().upper()
        if activation_match not in ("ANY", "ALL"):
            activation_match = "ANY"
        self.gyro_activation_match_var = tk.StringVar(value=activation_match)
        self.gyro_activation_buttons_summary_var = tk.StringVar(
            value=self._format_gyro_button_summary(
                self.gyro_activation_buttons
            )
        )
        self.gyro_tilt_recenter_button_options = [
            "NONE", *self.gyro_activation_button_options
        ]
        self.gyro_tilt_recenter_button_var = tk.StringVar(
            value=self.config.get(
                "gyro_mapping", "tilt_recenter_button", fallback="NONE"
            ).strip().upper()
        )
        if (
            self.gyro_tilt_recenter_button_var.get()
            not in self.gyro_tilt_recenter_button_options
        ):
            self.gyro_tilt_recenter_button_var.set("NONE")
        self.gyro_target_var = tk.StringVar(
            value=self.config.get(
                "gyro_mapping", "target", fallback="RIGHT_STICK"
            ).strip().upper()
        )
        if self.gyro_target_var.get() not in (
            "LEFT_STICK", "RIGHT_STICK", "MOUSE"
        ):
            self.gyro_target_var.set("RIGHT_STICK")
        self.gyro_motion_mode_var = tk.StringVar(
            value=self.config.get(
                "gyro_mapping", "motion_mode", fallback="CENTER"
            ).strip().upper()
        )
        if self.gyro_motion_mode_var.get() not in ("CENTER", "TILT"):
            self.gyro_motion_mode_var.set("CENTER")
        self.gyro_tilt_axis_var = tk.StringVar(
            value=self.config.get(
                "gyro_mapping", "tilt_axis", fallback="HORIZONTAL"
            ).strip().upper()
        )
        if self.gyro_tilt_axis_var.get() not in ("HORIZONTAL", "DUAL"):
            self.gyro_tilt_axis_var.set("HORIZONTAL")
        self.gyro_tilt_max_angle_var = tk.StringVar(
            value=self.config.get(
                "gyro_mapping", "tilt_max_angle", fallback="35"
            )
        )
        self.gyro_tilt_deadzone_var = tk.StringVar(
            value=self.config.get(
                "gyro_mapping", "tilt_deadzone", fallback="0.80"
            )
        )
        self.gyro_tilt_smoothing_var = tk.StringVar(
            value=self.config.get(
                "gyro_mapping", "tilt_smoothing_ms", fallback="30"
            )
        )
        self.gyro_stick_sensitivity_var = tk.StringVar(
            value=self.config.get(
                "gyro_mapping", "stick_sensitivity", fallback="1.50"
            )
        )
        response_curve = self.config.get(
            "gyro_mapping", "response_curve", fallback="LINEAR"
        ).strip().upper()
        if response_curve == "DYNAMIC":
            response_curve = "LATE"
        if response_curve not in ("LINEAR", "LATE", "EARLY"):
            response_curve = "LINEAR"
        self.gyro_response_curve_var = tk.StringVar(value=response_curve)
        self.gyro_curve_strength_var = tk.StringVar(
            value=self.config.get(
                "gyro_mapping", "curve_strength", fallback="5"
            )
        )
        self.gyro_mouse_sensitivity_var = tk.StringVar(
            value=self.config.get(
                "gyro_mapping", "mouse_sensitivity", fallback="8.00"
            )
        )
        self.gyro_deadzone_var = tk.StringVar(
            value=self.config.get("gyro_mapping", "deadzone", fallback="0.60")
        )
        self.gyro_stick_anti_deadzone_var = tk.StringVar(
            value=self.config.get(
                "gyro_mapping", "stick_anti_deadzone", fallback="0"
            )
        )
        self.gyro_smoothing_var = tk.StringVar(
            value=self.config.get(
                "gyro_mapping", "smoothing_ms", fallback="15"
            )
        )
        self.gyro_player_space_var = tk.BooleanVar(
            value=self.config.getboolean(
                "gyro_mapping", "player_space", fallback=True
            )
        )
        self.gyro_accel_suppression_var = tk.StringVar(
            value=self.config.get(
                "gyro_mapping", "accel_suppression", fallback="70"
            )
        )
        self.gyro_adaptive_deadzone_var = tk.StringVar(
            value=self.config.get(
                "gyro_mapping", "adaptive_deadzone", fallback="85"
            )
        )
        self.gyro_button_freeze_var = tk.StringVar(
            value=self.config.get(
                "gyro_mapping", "button_freeze_ms", fallback="35"
            )
        )
        self.gyro_stabilization_button_options = list(
            self.gyro_activation_button_options
        )
        legacy_stabilization_button = self.config.get(
            "gyro_mapping", "stabilization_button", fallback="NONE"
        )
        raw_stabilization_buttons = self.config.get(
            "gyro_mapping",
            "stabilization_buttons",
            fallback=legacy_stabilization_button,
        )
        self.gyro_stabilization_buttons = _parse_gyro_button_setting(
            raw_stabilization_buttons,
            self.gyro_stabilization_button_options,
        )
        self.gyro_stabilization_buttons_summary_var = tk.StringVar(
            value=self._format_gyro_button_summary(
                self.gyro_stabilization_buttons
            )
        )
        self.gyro_x_ratio_var = tk.StringVar(
            value=self.config.get("gyro_mapping", "x_ratio", fallback="1.00")
        )
        self.gyro_y_ratio_var = tk.StringVar(
            value=self.config.get("gyro_mapping", "y_ratio", fallback="1.00")
        )
        self.gyro_invert_x_var = tk.BooleanVar(
            value=self.config.getboolean(
                "gyro_mapping", "invert_x", fallback=False
            )
        )
        self.gyro_invert_y_var = tk.BooleanVar(
            value=self.config.getboolean(
                "gyro_mapping", "invert_y", fallback=False
            )
        )

        self.button_vars = {}
        if self.config.has_section("buttons"):
            for name, value in self.config.items("buttons"):
                self.button_vars[name.upper()] = tk.StringVar(
                    value=value.upper()
                )

    def create_widgets(self):
        # =========================
        # 自適應低解析度視窗容器
        # =========================
        #
        # 正常解析度：
        #     內容完整顯示，不出現捲軸。
        #
        # 低解析度（例如 720p / 768p）：
        #     主內容區限制在螢幕可用高度內，
        #     超出的內容可以垂直捲動。
        #
        # 底部功能按鈕不放進捲動區，
        # 因此「儲存設定」等按鈕永遠保持可見。
        self.root.rowconfigure(0, weight=1)
        self.root.columnconfigure(0, weight=1)

        self.scroll_host = ttk.Frame(
            self.root
        )
        self.scroll_host.grid(
            row=0,
            column=0,
            sticky="nsew"
        )

        self.scroll_host.rowconfigure(
            0,
            weight=1
        )
        self.scroll_host.columnconfigure(
            0,
            weight=1
        )

        self.scroll_canvas = tk.Canvas(
            self.scroll_host,
            highlightthickness=0,
            borderwidth=0
        )
        self.scroll_canvas.grid(
            row=0,
            column=0,
            sticky="nsew"
        )

        self.scrollbar = ttk.Scrollbar(
            self.scroll_host,
            orient="vertical",
            command=self.scroll_canvas.yview
        )

        self.scroll_canvas.configure(
            yscrollcommand=self.scrollbar.set
        )

        main = ttk.Frame(
            self.scroll_canvas,
            padding=20
        )
        self.main_frame = main

        self.main_canvas_window = (
            self.scroll_canvas.create_window(
                (0, 0),
                window=main,
                anchor="nw"
            )
        )

        main.bind(
            "<Configure>",
            self._on_main_frame_configure
        )

        self.scroll_canvas.bind(
            "<Enter>",
            self._bind_mousewheel
        )

        self.scroll_canvas.bind(
            "<Leave>",
            self._unbind_mousewheel
        )

        # 左右兩欄
        content_frame = ttk.Frame(main)
        self.content_frame = content_frame
        content_frame.grid(
            row=1,
            column=0,
            columnspan=2
        )

        left_frame = ttk.Frame(
            content_frame,
            padding=(0, 0, 6, 0)
        )
        left_frame.grid(row=0, column=0, sticky="ns")
        left_frame.columnconfigure(0, weight=1)
        left_frame.rowconfigure(1, weight=1)
        self.left_frame = left_frame

        right_frame = ttk.Frame(
            content_frame,
            padding=(6, 0, 0, 0)
        )
        right_frame.grid(row=0, column=1, sticky="n")
        self.right_frame = right_frame

        # =========================
        # 左側：搖桿
        # =========================
        stick_frame = ttk.LabelFrame(
            left_frame,
            text="搖桿設定",
            padding=(8, 4)
        )
        stick_frame.grid(
            row=0,
            column=0,
            sticky="ew",
            pady=(0, 6)
        )
        self.stick_frame = stick_frame

        # =========================
        # 搖桿線性曲線
        # =========================
        curve_notebook = ttk.Notebook(
            stick_frame
        )
        self.curve_notebook = curve_notebook

        curve_notebook.grid(
            row=0,
            column=0,
            columnspan=3,
            pady=(0, 6)
        )

        # 使用 place 疊放在群組右上角，不參與 grid 欄寬計算，
        # 避免按鈕擠壓或拉伸曲線設定中的其他元件。
        ttk.Style(self.root).configure(
            "CurveZoom.TButton",
            padding=(4, 0),
        )
        self.curve_zoom_button = ttk.Button(
            stick_frame,
            text="放大",
            width=5,
            style="CurveZoom.TButton",
            command=self.toggle_stick_settings_zoom
        )
        self.curve_zoom_button.place(
            relx=1.0,
            x=-8,
            y=0,
            anchor="ne"
        )
        self.curve_zoom_button.lift()

        # 左搖桿頁籤
        left_curve_tab = ttk.Frame(
            curve_notebook,
            padding=(6, 2)
        )

        curve_notebook.add(
            left_curve_tab,
            text="左搖桿"
        )

        self.left_curve_editor = StickCurveEditor(
            left_curve_tab,
            self.left_curve_vars,
            self.left_deadzone_var,
            self.left_outer_deadzone_var,
            self.left_deadzone_compress_var,
            self.left_outer_deadzone_compress_var,
            self.left_interpolation_var,
            label_color="#EA0000",
            width=self.NORMAL_CANVAS_WIDTH,
            height=self.NORMAL_CANVAS_HEIGHT,
            zoom_command=self.toggle_stick_settings_zoom,
            ui_scale=self.ui_scale
        )

        self.left_curve_editor.pack()

        def align_group_help_column(frame, column):
            """Pin every help button to a shared right edge in this group."""
            frame.columnconfigure(column, weight=1)
            for child in frame.winfo_children():
                try:
                    if child.cget("text") == "?":
                        child.grid_configure(sticky="e")
                except (tk.TclError, AttributeError):
                    pass

        left_smoothing_frame = ttk.Frame(
            left_curve_tab
        )
        self.left_smoothing_frame = left_smoothing_frame

        # Fill the same tab width used by the curve-range group below.
        left_smoothing_frame.pack(
            pady=(4, 0),
            fill="x"
        )
        left_smoothing_frame.columnconfigure(0, weight=1)

        left_output_group = ttk.LabelFrame(
            left_smoothing_frame,
            text="輸出與防抖",
            padding=(6, 2)
        )
        self.left_output_group = left_output_group
        left_output_group.grid(row=0, column=0, sticky="ew")
        left_output_group.columnconfigure(1, weight=1)

        ttk.Label(
            left_output_group,
            text="防抖"
        ).grid(row=1, column=0, sticky="w", padx=(2, 2), pady=2)

        def snap_left_smoothing(value):
            value = round(
                float(value),
                1
            )

            self.left_stick_smoothing_var.set(
                value
            )

        ttk.Scale(
            left_output_group,
            from_=0,
            to=3.0,
            variable=self.left_stick_smoothing_var,
            command=snap_left_smoothing,
            orient="horizontal",
            length=163
        ).grid(row=1, column=1, padx=3, pady=2, sticky="ew")


        left_smoothing_value_label = ttk.Label(
            left_output_group,
            width=5
        )

        left_smoothing_value_label.grid(
            row=1, column=2, sticky="w", pady=2
        )

        def update_left_smoothing_label(
            *args
        ):
            value = round(
                self.left_stick_smoothing_var.get(),
                1
            )

            left_smoothing_value_label.config(
                text=f"{value:.1f}"
            )

        self.left_stick_smoothing_var.trace_add(
            "write",
            update_left_smoothing_label
        )

        update_left_smoothing_label()

        # =========================
        # 左搖桿死區設定
        # =========================

        left_deadzone_frame = ttk.LabelFrame(
            left_curve_tab,
            text="曲線範圍",
            padding=(6, 2)
        )
        self.left_deadzone_frame = left_deadzone_frame

        left_deadzone_frame.pack(
            pady=(4, 0),
            fill="x"
        )

        # 中心死區
        left_center_deadzone_label_frame = (
            ttk.Frame(
                left_deadzone_frame
            )
        )

        left_center_deadzone_label_frame.grid(
            row=0,
            column=0,
            sticky="e",
            pady=2
        )

        ttk.Checkbutton(
            left_center_deadzone_label_frame,
            text="壓縮",
            width=5,
            variable=(
                self.left_deadzone_compress_var
            )
        ).pack(
            side="left",
            padx=(0, 4)
        )

        ttk.Label(
            left_center_deadzone_label_frame,
            text="中心死區",
            width=7,
            anchor="w"
        ).pack(
            side="left"
        )

        ttk.Entry(
            left_deadzone_frame,
            textvariable=self.left_deadzone_var,
            width=8
        ).grid(
            row=0,
            column=1,
            padx=(15, 0),
            pady=2
        )

        self.create_help(
            left_deadzone_frame,
            (
                "用來消除左搖桿放開後的輕微飄移。\n\n"
                "設定範圍：0.00 ～ 1.00\n"
                "0.00 = 無死區\n"
                "0.03 = 建議預設值\n\n"
                "數值越大，搖桿中心附近越不敏感。\n\n"
                "勾選「壓縮」後，曲線的 0% 起點"
                "會移到中心死區邊界，"
                "並將完整曲線重新分布到剩餘行程。\n\n"
                "曲線圖左側的灰色區域代表"
                "中心死區範圍。"
            )
        ).grid(
            row=0,
            column=2,
            padx=(8, 0)
        )

        # 外圍死區
        left_outer_deadzone_label_frame = (
            ttk.Frame(
                left_deadzone_frame
            )
        )

        left_outer_deadzone_label_frame.grid(
            row=1,
            column=0,
            sticky="e",
            pady=2
        )

        ttk.Checkbutton(
            left_outer_deadzone_label_frame,
            text="壓縮",
            width=5,
            variable=(
                self.left_outer_deadzone_compress_var
            )
        ).pack(
            side="left",
            padx=(0, 4)
        )

        ttk.Label(
            left_outer_deadzone_label_frame,
            text="外圍死區",
            width=7,
            anchor="w"
        ).pack(
            side="left"
        )

        ttk.Entry(
            left_deadzone_frame,
            textvariable=(
                self.left_outer_deadzone_var
            ),
            width=8
        ).grid(
            row=1,
            column=1,
            padx=(15, 0),
            pady=2
        )

        self.create_help(
            left_deadzone_frame,
            (
                "設定左搖桿接近外圈時，"
                "提前輸出最大值。\n\n"
                "設定範圍：0.00 ～ 1.00\n"
                "0.03 = 到達 97% 時輸出 100%\n\n"
                "勾選「壓縮」後，曲線的 100% 終點"
                "會移到外圍死區邊界，"
                "並將完整曲線重新分布到剩餘行程。\n\n"
                "曲線圖右側的灰色區域代表"
                "外圍死區範圍。"
            )
        ).grid(
            row=1,
            column=2,
            padx=(8, 0)
        )

        ttk.Label(
            left_deadzone_frame, text="平滑模式"
        ).grid(row=2, column=0, sticky="w", padx=(2, 2), pady=2)
        left_interpolation_frame = ttk.Frame(left_deadzone_frame)
        left_interpolation_frame.grid(
            row=2, column=1, pady=2
        )
        ttk.Radiobutton(
            left_interpolation_frame,
            text="線性",
            variable=self.left_interpolation_var,
            value="LINEAR",
        ).pack(side="left")
        ttk.Radiobutton(
            left_interpolation_frame,
            text="平滑",
            variable=self.left_interpolation_var,
            value="SMOOTH",
        ).pack(side="left", padx=(8, 0))
        self.create_help(
            left_deadzone_frame,
            "線性：控制點之間使用直線。\n\n"
            "平滑：使用單調三次插值，平滑通過所有控制點，"
            "並避免反向與超調。"
        ).grid(row=2, column=2, padx=(8, 0))

        ttk.Label(
            left_output_group,
            text="形狀"
        ).grid(row=0, column=0, sticky="w", padx=(2, 2), pady=2)

        def snap_left_shape(value):
            self.left_output_shape_var.set(
                max(0, min(10, int(round(float(value)))))
            )

        ttk.Scale(
            left_output_group,
            from_=0,
            to=10,
            variable=self.left_output_shape_var,
            command=snap_left_shape,
            orient="horizontal",
            length=163,
        ).grid(row=0, column=1, padx=3, pady=2, sticky="ew")
        self.left_output_shape_value_label = ttk.Label(
            left_output_group,
            width=5,
            anchor="w",
        )
        self.left_output_shape_value_label.grid(
            row=0, column=2, sticky="w", pady=2
        )

        def update_left_shape_value(*args):
            del args
            self.left_output_shape_value_label.configure(
                text=format_shape_circularity_error(
                    self.left_output_shape_var.get()
                )
            )

        self.left_output_shape_var.trace_add(
            "write", update_left_shape_value
        )
        update_left_shape_value()
        self.create_help(
            left_output_group,
            "輸出形狀會在圓形與方形之間分成 10 段。\n\n"
            "灰色圓：圓形基準；虛線方框：方形極限；"
            "藍線：目前設定的最大輸出範圍。\n\n"
            "對角方向每軸約為：\n"
            "0 = 0.707　2 = 0.766　5 = 0.854\n"
            "8 = 0.941　10 = 1.000\n\n"
            "數值越高，對角方向可輸出的範圍越大。\n\n"
            "拉桿後方百分比是預估圓周誤差；"
            "實際結果會受校正與取樣影響。",
            illustration=lambda canvas: draw_output_shape_preview(
                canvas, self.left_output_shape_var.get()
            ),
        ).grid(row=0, column=3, padx=(2, 0), pady=2, sticky="e")

        self.create_help(
            left_output_group,
            (
                "防抖\n\n"
                "根據位移曲線目前區段的放大倍率，"
                "自動增加防抖強度。\n\n"
                "設定範圍：0.0 ～ 3.0\n\n"
                "0.0：關閉防抖補償。\n"
                "1.0：標準補償，曲線放大幾倍，"
                "就按相同倍率增加防抖。\n"
                "2.0：加強補償。\n"
                "3.0：更強的補償。\n\n"
                "僅在曲線斜率大於 1:1 的區域生效。\n"
                "平滑使用實際時間計算，不會因 BLE 或 ESP32 "
                "更新頻率不同而改變手感。\n"
                "數值越高，輸出越穩定，"
                "但也可能產生較明顯的平滑感。"
            )
        ).grid(row=1, column=3, padx=(2, 0), pady=2, sticky="e")

        align_group_help_column(left_deadzone_frame, 2)

        # 右搖桿頁籤
        right_curve_tab = ttk.Frame(
            curve_notebook,
            padding=(6, 2)
        )

        curve_notebook.add(
            right_curve_tab,
            text="右搖桿"
        )

        self.right_curve_editor = StickCurveEditor(
            right_curve_tab,
            self.right_curve_vars,
            self.right_deadzone_var,
            self.right_outer_deadzone_var,
            self.right_deadzone_compress_var,
            self.right_outer_deadzone_compress_var,
            self.right_interpolation_var,
            label_color="#0066CC",
            width=self.NORMAL_CANVAS_WIDTH,
            height=self.NORMAL_CANVAS_HEIGHT,
            zoom_command=self.toggle_stick_settings_zoom,
            ui_scale=self.ui_scale
        )

        self.right_curve_editor.pack()

        right_smoothing_frame = ttk.Frame(
            right_curve_tab
        )
        self.right_smoothing_frame = right_smoothing_frame

        right_smoothing_frame.pack(
            pady=(4, 0),
            fill="x"
        )
        right_smoothing_frame.columnconfigure(0, weight=1)

        right_output_group = ttk.LabelFrame(
            right_smoothing_frame,
            text="輸出與防抖",
            padding=(6, 2)
        )
        self.right_output_group = right_output_group
        right_output_group.grid(row=0, column=0, sticky="ew")
        right_output_group.columnconfigure(1, weight=1)

        ttk.Label(
            right_output_group,
            text="防抖"
        ).grid(row=1, column=0, sticky="w", padx=(2, 2), pady=2)

        def snap_right_smoothing(value):
            value = round(
                float(value),
                1
            )

            self.right_stick_smoothing_var.set(
                value
            )

        ttk.Scale(
            right_output_group,
            from_=0,
            to=3.0,
            variable=self.right_stick_smoothing_var,
            command=snap_right_smoothing,
            orient="horizontal",
            length=163
        ).grid(row=1, column=1, padx=3, pady=2, sticky="ew")


        right_smoothing_value_label = ttk.Label(
            right_output_group,
            width=5
        )

        right_smoothing_value_label.grid(
            row=1, column=2, sticky="w", pady=2
        )

        def update_right_smoothing_label(
            *args
        ):
            value = round(
                self.right_stick_smoothing_var.get(),
                1
            )

            right_smoothing_value_label.config(
                text=f"{value:.1f}"
            )

        self.right_stick_smoothing_var.trace_add(
            "write",
            update_right_smoothing_label
        )

        update_right_smoothing_label()

        self.create_help(
            right_output_group,
            (
                "防抖\n\n"
                "根據位移曲線目前區段的放大倍率，"
                "自動增加防抖強度。\n\n"
                "設定範圍：0.0 ～ 3.0\n\n"
                "0.0：關閉防抖補償。\n"
                "1.0：標準補償，曲線放大幾倍，"
                "就按相同倍率增加防抖。\n"
                "2.0：加強補償。\n"
                "3.0：更強的補償。\n\n"
                "僅在曲線斜率大於 1:1 的區域生效。\n"
                "平滑使用實際時間計算，不會因 BLE 或 ESP32 "
                "更新頻率不同而改變手感。\n"
                "數值越高，輸出越穩定，"
                "但也可能產生較明顯的平滑感。"
            )
        ).grid(row=1, column=3, padx=(2, 0), pady=2, sticky="e")
        
        # =========================
        # 右搖桿死區設定
        # =========================

        right_deadzone_frame = ttk.LabelFrame(
            right_curve_tab,
            text="曲線範圍",
            padding=(6, 2)
        )
        self.right_deadzone_frame = right_deadzone_frame

        right_deadzone_frame.pack(
            pady=(4, 0),
            fill="x"
        )

        # 中心死區
        right_center_deadzone_label_frame = (
            ttk.Frame(
                right_deadzone_frame
            )
        )

        right_center_deadzone_label_frame.grid(
            row=0,
            column=0,
            sticky="e",
            pady=2
        )

        ttk.Checkbutton(
            right_center_deadzone_label_frame,
            text="壓縮",
            width=5,
            variable=(
                self.right_deadzone_compress_var
            )
        ).pack(
            side="left",
            padx=(0, 4)
        )

        ttk.Label(
            right_center_deadzone_label_frame,
            text="中心死區",
            width=7,
            anchor="w"
        ).pack(
            side="left"
        )

        ttk.Entry(
            right_deadzone_frame,
            textvariable=self.right_deadzone_var,
            width=8
        ).grid(
            row=0,
            column=1,
            padx=(15, 0),
            pady=2
        )

        self.create_help(
            right_deadzone_frame,
            (
                "用來消除右搖桿放開後的輕微飄移。\n\n"
                "設定範圍：0.00 ～ 1.00\n"
                "0.00 = 無死區\n"
                "0.03 = 建議預設值\n\n"
                "數值越大，搖桿中心附近越不敏感。\n\n"
                "勾選「壓縮」後，曲線的 0% 起點"
                "會移到中心死區邊界，"
                "並將完整曲線重新分布到剩餘行程。\n\n"
                "曲線圖左側的灰色區域代表"
                "中心死區範圍。"
            )
        ).grid(
            row=0,
            column=2,
            padx=(8, 0)
        )

        # 外圍死區
        right_outer_deadzone_label_frame = (
            ttk.Frame(
                right_deadzone_frame
            )
        )

        right_outer_deadzone_label_frame.grid(
            row=1,
            column=0,
            sticky="e",
            pady=2
        )

        ttk.Checkbutton(
            right_outer_deadzone_label_frame,
            text="壓縮",
            width=5,
            variable=(
                self.right_outer_deadzone_compress_var
            )
        ).pack(
            side="left",
            padx=(0, 4)
        )

        ttk.Label(
            right_outer_deadzone_label_frame,
            text="外圍死區",
            width=7,
            anchor="w"
        ).pack(
            side="left"
        )

        ttk.Entry(
            right_deadzone_frame,
            textvariable=(
                self.right_outer_deadzone_var
            ),
            width=8
        ).grid(
            row=1,
            column=1,
            padx=(15, 0),
            pady=2
        )

        self.create_help(
            right_deadzone_frame,
            (
                "設定右搖桿接近外圈時，"
                "提前輸出最大值。\n\n"
                "設定範圍：0.00 ～ 1.00\n"
                "0.03 = 到達 97% 時輸出 100%\n\n"
                "勾選「壓縮」後，曲線的 100% 終點"
                "會移到外圍死區邊界，"
                "並將完整曲線重新分布到剩餘行程。\n\n"
                "曲線圖右側的灰色區域代表"
                "外圍死區範圍。"
            )
        ).grid(
            row=1,
            column=2,
            padx=(8, 0)
        )

        ttk.Label(
            right_deadzone_frame, text="平滑模式"
        ).grid(row=2, column=0, sticky="w", padx=(2, 2), pady=2)
        right_interpolation_frame = ttk.Frame(right_deadzone_frame)
        right_interpolation_frame.grid(
            row=2, column=1, pady=2
        )
        ttk.Radiobutton(
            right_interpolation_frame,
            text="線性",
            variable=self.right_interpolation_var,
            value="LINEAR",
        ).pack(side="left")
        ttk.Radiobutton(
            right_interpolation_frame,
            text="平滑",
            variable=self.right_interpolation_var,
            value="SMOOTH",
        ).pack(side="left", padx=(8, 0))
        self.create_help(
            right_deadzone_frame,
            "線性：控制點之間使用直線。\n\n"
            "平滑：使用單調三次插值，平滑通過所有控制點，"
            "並避免反向與超調。"
        ).grid(row=2, column=2, padx=(8, 0))

        ttk.Label(
            right_output_group,
            text="形狀"
        ).grid(row=0, column=0, sticky="w", padx=(2, 2), pady=2)

        def snap_right_shape(value):
            self.right_output_shape_var.set(
                max(0, min(10, int(round(float(value)))))
            )

        ttk.Scale(
            right_output_group,
            from_=0,
            to=10,
            variable=self.right_output_shape_var,
            command=snap_right_shape,
            orient="horizontal",
            length=163,
        ).grid(row=0, column=1, padx=3, pady=2, sticky="ew")
        self.right_output_shape_value_label = ttk.Label(
            right_output_group,
            width=5,
            anchor="w",
        )
        self.right_output_shape_value_label.grid(
            row=0, column=2, sticky="w", pady=2
        )

        def update_right_shape_value(*args):
            del args
            self.right_output_shape_value_label.configure(
                text=format_shape_circularity_error(
                    self.right_output_shape_var.get()
                )
            )

        self.right_output_shape_var.trace_add(
            "write", update_right_shape_value
        )
        update_right_shape_value()
        self.create_help(
            right_output_group,
            "輸出形狀會在圓形與方形之間分成 10 段。\n\n"
            "灰色圓：圓形基準；虛線方框：方形極限；"
            "藍線：目前設定的最大輸出範圍。\n\n"
            "對角方向每軸約為：\n"
            "0 = 0.707　2 = 0.766　5 = 0.854\n"
            "8 = 0.941　10 = 1.000\n\n"
            "數值越高，對角方向可輸出的範圍越大。\n\n"
            "拉桿後方百分比是預估圓周誤差；"
            "實際結果會受校正與取樣影響。",
            illustration=lambda canvas: draw_output_shape_preview(
                canvas, self.right_output_shape_var.get()
            ),
        ).grid(row=0, column=3, padx=(2, 0), pady=2, sticky="e")

        align_group_help_column(right_deadzone_frame, 2)

        # 初始畫面將「輸出與防抖」群組放在「曲線範圍」下方。
        # 重新 pack 會把整個控制列移到頁籤的最後。
        self.set_zoom_controls_centered(False)

        # =========================
        # 左右搖桿死區即時重畫
        # =========================

        def redraw_left_deadzone_preview(
            *args
        ):
            self.left_curve_editor.draw()

        self.left_deadzone_var.trace_add(
            "write",
            redraw_left_deadzone_preview
        )

        self.left_outer_deadzone_var.trace_add(
            "write",
            redraw_left_deadzone_preview
        )

        self.left_deadzone_compress_var.trace_add(
            "write",
            redraw_left_deadzone_preview
        )

        self.left_outer_deadzone_compress_var.trace_add(
            "write",
            redraw_left_deadzone_preview
        )
        self.left_interpolation_var.trace_add(
            "write",
            redraw_left_deadzone_preview
        )

        def redraw_right_deadzone_preview(
            *args
        ):
            self.right_curve_editor.draw()

        self.right_deadzone_var.trace_add(
            "write",
            redraw_right_deadzone_preview
        )

        self.right_outer_deadzone_var.trace_add(
            "write",
            redraw_right_deadzone_preview
        )

        self.right_deadzone_compress_var.trace_add(
            "write",
            redraw_right_deadzone_preview
        )

        self.right_outer_deadzone_compress_var.trace_add(
            "write",
            redraw_right_deadzone_preview
        )
        self.right_interpolation_var.trace_add(
            "write",
            redraw_right_deadzone_preview
        )

        # =========================
        # 左側：震動
        # =========================
        rumble_frame = ttk.LabelFrame(
            left_frame,
            text="震動設定",
            padding=6
        )
        rumble_frame.grid(
            row=1,
            column=0,
            sticky="nsew"
        )
        self.rumble_frame = rumble_frame

        # 外框與搖桿設定同寬；實際欄位放在獨立容器中水平置中，
        # 因此不會因為外框拉寬而在內容右側留下不對稱空白。
        rumble_content = ttk.Frame(rumble_frame)
        rumble_content.grid(row=0, column=0, sticky="ns")
        rumble_frame.columnconfigure(0, weight=1)
        rumble_frame.rowconfigure(0, weight=1)
        for row_index in range(9):
            rumble_content.rowconfigure(
                row_index,
                weight=1,
                uniform="rumble_rows"
            )

        self.add_entry(
            rumble_content, 0, "低頻震動強度", self.lf_strength_var,
            "低頻震動通道的強度倍率。\n\n"
            "設定範圍：0.00 ～ 1.00\n"
            "0.00 = 關閉\n1.00 = 完整強度"
        )
        self.add_entry(
            rumble_content, 1, "高頻震動強度", self.hf_strength_var,
            "高頻震動通道的強度倍率。\n\n"
            "設定範圍：0.00 ～ 1.00\n"
            "0.00 = 關閉\n1.00 = 完整強度"
        )
  
        self.add_entry(
            rumble_content, 2, "低頻輸出曲線", self.lf_curve_var,
            "低頻震動的響應曲線。\n\n"
            "設定範圍：0.10 ～ 5.00\n"
            "1.00 = 線性，保持原始比例\n"
            "小於 1.00 = 增強較弱的震動\n"
            "大於 1.00 = 壓低較弱的震動"
        )

        self.add_entry(
            rumble_content, 3, "高頻輸出曲線", self.hf_curve_var,
            "高頻震動的響應曲線。\n\n"
            "設定範圍：0.10 ～ 5.00\n"
            "1.00 = 線性，保持原始比例\n"
            "小於 1.00 = 增強較弱的震動\n"
            "大於 1.00 = 壓低較弱的震動"
        )  
 
        self.add_entry(
            rumble_content, 4, "低頻補償", self.hf_to_lf_compensation_var,
            "將一部分 HF 最終振幅加入 LF 通道。\n\n"
            "設定範圍：0.00 ～ 1.00\n"
            "0.00 = 不補償\n0.50 = 加入 HF 振幅的 50%"
        )


        self.add_entry(
            rumble_content, 5, "高頻補償", self.lf_to_hf_compensation_var,
            "將一部分 LF 最終振幅加入 HF 通道。\n\n"
            "設定範圍：0.00 ～ 1.00\n"
            "0.00 = 不補償\n0.50 = 加入 LF 振幅的 50%"
        )

        self.add_entry(
            rumble_content, 6, "LF 頻率命令值", self.lf_frequency_var,
            "LF 的 HD Rumble 2 頻率命令值。\n\n"
            "設定範圍：200 ～ 225\n預設：225\n\n"
            "注意：此數值不是直接的實際 Hz。"
        )
        self.add_entry(
            rumble_content, 7, "HF 頻率命令值", self.hf_frequency_var,
            "HF 的 HD Rumble 2 頻率命令值。\n\n"
            "設定範圍：1 ～ 511\n上游預設載波：481\n\n"
            "注意：此數值不是直接的實際 Hz。"
        )
        self.add_entry(
            rumble_content, 8, "最大振幅", self.max_amplitude_var,
            "限制 LF 與 HF 的最大振幅輸出。\n\n"
            "設定範圍：0 ～ 1023\n"
            "輸入接近極限數值有可能損壞控制器馬達。\n\n"
            "本專案建議預設：800"
        )


        # =========================
        # 右側：映射設定頁籤
        # =========================
        mapping_notebook = ttk.Notebook(
            right_frame
        )

        mapping_notebook.grid(
            row=0,
            column=0,
            sticky="n"
        )

        # =========================
        # 頁籤 1：按鍵映射
        # =========================
        mapping_frame = ttk.Frame(
            mapping_notebook,
            padding=(10, 4)
        )

        mapping_notebook.add(
            mapping_frame,
            text="按鍵映射"
        )

        mapping_frame.columnconfigure(
            1,
            weight=1
        )

        ttk.Label(mapping_frame, text="Switch 2 Pro").grid(
            row=0, column=0, sticky="e", padx=(0, 8), pady=(0, 4)
        )
        ttk.Label(mapping_frame, text="XInput / Xbox").grid(
            row=0, column=1, sticky="w", pady=(0, 4)
        )

        row = 1
        for switch_name, variable in self.button_vars.items():
            ttk.Label(
                mapping_frame,
                text=f"{switch_name}  →",
                width=10,
                anchor="e",
            ).grid(
                row=row,
                column=0,
                sticky="e",
                padx=(0, 8),
                pady=4,
            )

            combo = ttk.Combobox(
                mapping_frame,
                textvariable=variable,
                values=self.button_options,
                state="readonly",
                width=24
            )

            def on_mapping_selected(
                event,
                current_variable=variable
            ):
                if (
                    current_variable.get()
                    == "CUSTOM_KEYBOARD"
                ):
                    self.open_keyboard_capture(
                        current_variable,
                        mouse_mode="buttons",
                    )

            combo.bind(
                "<<ComboboxSelected>>",
                on_mapping_selected
            )
            
            combo.grid(
                row=row,
                column=1,
                sticky="ew",
                pady=4
            )

            row += 1

        for mapping_row in range(1, row):
            mapping_frame.rowconfigure(
                mapping_row, weight=1, uniform="button_mapping_row"
            )

        self.create_help(
            mapping_frame,
            "選擇 CUSTOM_KEYBOARD 後，按下要映射的輸入。\n\n"
            "支援鍵盤單鍵、Ctrl／Shift／Alt／Win 複合鍵，"
            "以及滑鼠左鍵、右鍵與中鍵。\n"
            "例如：F12、Ctrl + S、Win + D。"
        ).place(
            relx=1.0,
            x=-55,
            y=3,
            anchor="ne",
        )

        ttk.Button(
            mapping_frame,
            text="還原",
            width=5,
            command=self.reset_button_mapping
        ).place(
            relx=1.0,
            x=-2,
            y=0,
            anchor="ne"
        )

        # =========================
        # 頁籤 2：搖桿方向映射
        # =========================
        stick_direction_frame = ttk.Frame(
            mapping_notebook,
            padding=(10, 4)
        )

        mapping_notebook.add(
            stick_direction_frame,
            text="搖桿方向映射"
        )

        # 左右兩個搖桿區塊
        left_direction_frame = ttk.LabelFrame(
            stick_direction_frame,
            text="左搖桿",
            padding=10
        )

        left_direction_frame.grid(
            row=0,
            column=0,
            pady=(0, 10),
            sticky="ew"
        )

        right_direction_frame = ttk.LabelFrame(
            stick_direction_frame,
            text="右搖桿",
            padding=10
        )

        right_direction_frame.grid(
            row=1,
            column=0,
            sticky="ew"
        )

        # 保存斜方向元件，
        # 之後切換 4 向 / 8 向時使用
        self.stick_diagonal_widgets = {
            "LEFT": [],
            "RIGHT": [],
        }
        # 保存左右搖桿的模式刷新函式
        # 讓「還原預設」也能主動刷新介面
        self.stick_direction_mode_updaters = {}
        self.stick_mode_selectors = []

        def create_stick_direction_panel(
            parent,
            side
        ):

            # 左右搖桿的第三個模式不同
            if side == "LEFT":
                mode_values = [
                    "4WAY",
                    "8WAY",
                    "映射為右搖桿",
                    "映射為滑鼠",
                ]
            else:
                mode_values = [
                    "4WAY",
                    "8WAY",
                    "映射為左搖桿",
                    "映射為滑鼠",
                ]
            # =========================
            # 保存 8 個方向的下拉選單
            # =========================
            direction_combos = []

            # =========================
            # 方向下拉框建立函式
            # =========================
            def create_direction_combo(
                direction,
                row,
                column,
                symbol,
                diagonal=False
            ):
                cell = ttk.Frame(
                    parent
                )

                cell.grid(
                    row=row,
                    column=column,
                    padx=3,
                    pady=8
                )

                ttk.Label(
                    cell,
                    text=symbol,
                    anchor="center",
                    font=(
                        "",
                        12,
                        "bold"
                    )
                ).pack()

                combo = ttk.Combobox(
                    cell,
                    textvariable=(
                        self.stick_direction_vars[
                            side
                        ][
                            direction
                        ]
                    ),
                    values=(
                        self.stick_direction_options
                    ),
                    state="readonly",
                    width=12
                )

                combo.pack()
                
                direction_combos.append(
                    combo
                )

                def on_direction_selected(
                    event,
                    current_variable=(
                        self.stick_direction_vars[
                            side
                        ][
                            direction
                        ]
                    )
                ):
                    if (
                        current_variable.get()
                        == "CUSTOM_KEYBOARD"
                    ):
                        self.open_keyboard_capture(
                            current_variable,
                            mouse_mode="wheel",
                        )

                combo.bind(
                    "<<ComboboxSelected>>",
                    on_direction_selected
                )

                if diagonal:
                    self.stick_diagonal_widgets[
                        side
                    ].append(
                        cell
                    )

                return cell

            # =========================
            # 3 × 3 方向配置
            # =========================

            # 左上
            create_direction_combo(
                "UP_LEFT",
                0,
                0,
                "↖",
                diagonal=True
            )

            # 上
            create_direction_combo(
                "UP",
                0,
                1,
                "↑"
            )

            # 右上
            create_direction_combo(
                "UP_RIGHT",
                0,
                2,
                "↗",
                diagonal=True
            )

            # 左
            create_direction_combo(
                "LEFT",
                1,
                0,
                "←"
            )

            # =========================
            # 中心模式選擇
            # =========================
            center_mode_frame = ttk.Frame(
                parent
            )

            center_mode_frame.grid(
                row=1,
                column=1
            )

            ttk.Label(
                center_mode_frame,
                text="模式"
            ).pack(
                pady=(0, 3)
            )

            # 黑色外框
            mode_combo_border = tk.Frame(
                center_mode_frame,
                bg="black",
                padx=5,
                pady=5
            )

            mode_combo_border.pack()

            # 原本的模式下拉選單
            mode_display_var = tk.StringVar(
                value=self.stick_direction_mode_vars[side].get()
            )
            mode_combo = ttk.Combobox(
                mode_combo_border,
                textvariable=mode_display_var,
                values=mode_values,
                state="readonly",
                width=8
            )

            mode_combo.pack()
            self.stick_mode_selectors.append(
                (mode_combo, mode_display_var, side, tuple(mode_values))
            )
            # 右
            create_direction_combo(
                "RIGHT",
                1,
                2,
                "→"
            )

            # 左下
            create_direction_combo(
                "DOWN_LEFT",
                2,
                0,
                "↙",
                diagonal=True
            )

            # 下
            create_direction_combo(
                "DOWN",
                2,
                1,
                "↓"
            )

            # 右下
            create_direction_combo(
                "DOWN_RIGHT",
                2,
                2,
                "↘",
                diagonal=True
            )

            # 模式專用設定區固定高度，切換模式時不改變頁面尺寸。
            for column in range(3):
                parent.columnconfigure(column, weight=1)
            settings_host = ttk.Frame(parent, height=96)
            settings_host.grid(
                row=3, column=0, columnspan=3, sticky="ew", pady=(2, 0)
            )
            settings_host.grid_propagate(False)

            direction_settings_frame = ttk.Frame(settings_host)
            mouse_settings_frame = ttk.Frame(settings_host)
            direction_settings_frame.columnconfigure(1, weight=1)
            mouse_settings_frame.columnconfigure(1, weight=1)

            preview = tk.Canvas(
                direction_settings_frame,
                width=88,
                height=88,
                bg="#ffffff",
                highlightthickness=1,
                highlightbackground="#a8a8a8",
            )
            preview.grid(row=0, column=0, padx=(2, 5))
            direction_controls = ttk.Frame(direction_settings_frame)
            direction_controls.grid(
                row=0, column=1, sticky="ew", padx=(0, 2)
            )
            direction_controls.columnconfigure(1, weight=1)

            def safe_float(variable, fallback):
                try:
                    return float(variable.get())
                except (ValueError, TypeError, tk.TclError):
                    return fallback

            value_labels = []

            def add_direction_slider(
                row, label_text, variable, minimum, maximum, command, formatter
            ):
                ttk.Label(
                    direction_controls,
                    text=label_text,
                    width=4,
                    anchor="e",
                ).grid(row=row, column=0, padx=(0, 4), pady=2)
                ttk.Scale(
                    direction_controls,
                    from_=minimum,
                    to=maximum,
                    variable=variable,
                    command=command,
                    orient="horizontal",
                    length=120,
                ).grid(
                    row=row, column=1, padx=4, pady=2, sticky="ew"
                )
                value_label = ttk.Label(
                    direction_controls, width=5, anchor="w"
                )
                value_label.grid(row=row, column=2, padx=(3, 0), pady=2)
                value_labels.append((value_label, variable, formatter))

            def snap_trigger(value):
                numeric = max(0.10, min(1.0, round(float(value) * 100.0) / 100.0))
                release_var = self.stick_direction_release_vars[side]
                release = safe_float(release_var, 0.50)
                if release > numeric - 0.03:
                    release_var.set(f"{max(0.0, numeric - 0.03):.2f}")
                self.stick_direction_trigger_vars[side].set(f"{numeric:.2f}")

            def snap_release(value):
                trigger = safe_float(
                    self.stick_direction_trigger_vars[side], 0.60
                )
                numeric = max(
                    0.0,
                    min(trigger - 0.03, round(float(value) * 100.0) / 100.0),
                )
                self.stick_direction_release_vars[side].set(f"{numeric:.2f}")

            def snap_direction_deadzone(value):
                numeric = max(0.0, min(20.0, round(float(value))))
                self.stick_direction_deadzone_vars[side].set(f"{numeric:.0f}")

            add_direction_slider(
                0,
                "死區",
                self.stick_direction_deadzone_vars[side],
                0,
                20,
                snap_direction_deadzone,
                lambda value: f"{value:.0f}°",
            )
            add_direction_slider(
                1,
                "觸發",
                self.stick_direction_trigger_vars[side],
                0.10,
                1.0,
                snap_trigger,
                lambda value: f"{value * 100:.0f}%",
            )
            add_direction_slider(
                2,
                "放開",
                self.stick_direction_release_vars[side],
                0.0,
                0.97,
                snap_release,
                lambda value: f"{value * 100:.0f}%",
            )

            ttk.Label(
                mouse_settings_frame,
                text="游標速度",
                width=8,
                anchor="e",
            ).grid(row=0, column=0, padx=(0, 4))

            def snap_mouse_speed(value):
                numeric = max(
                    100.0,
                    min(3000.0, round(float(value) / 50.0) * 50.0),
                )
                self.stick_mouse_speed_vars[side].set(f"{numeric:.0f}")

            ttk.Scale(
                mouse_settings_frame,
                from_=100,
                to=3000,
                variable=self.stick_mouse_speed_vars[side],
                command=snap_mouse_speed,
                orient="horizontal",
                length=120,
            ).grid(row=0, column=1, padx=4, sticky="ew")
            mouse_speed_value = ttk.Label(
                mouse_settings_frame, width=5, anchor="w"
            )
            mouse_speed_value.grid(row=0, column=2, padx=(3, 0))

            def draw_direction_preview(*args):
                del args
                preview.delete("all")
                size = 88.0
                center = size / 2.0
                radius = 37.0
                mode = self.stick_direction_mode_vars[side].get().strip()
                deadzone = max(
                    0.0,
                    min(
                        20.0,
                        safe_float(self.stick_direction_deadzone_vars[side], 5.0),
                    ),
                )
                boundary_step = 90.0 if mode == "4WAY" else 45.0
                first_boundary = 45.0 if mode == "4WAY" else 22.5
                boundary_count = 4 if mode == "4WAY" else 8
                shape_steps = max(
                    0,
                    min(10, int(round(self.left_output_shape_var.get()
                                      if side == "LEFT"
                                      else self.right_output_shape_var.get()))),
                )
                shape_blend = shape_steps / 10.0

                def output_edge_point(angle_degrees):
                    angle = math.radians(angle_degrees)
                    direction_x = math.cos(angle)
                    direction_y = math.sin(angle)
                    square_scale = 1.0 / max(
                        abs(direction_x), abs(direction_y), 1e-9
                    )
                    edge_scale = (
                        1.0 - shape_blend
                        + square_scale * shape_blend
                    )
                    return (
                        center + radius * direction_x * edge_scale,
                        center - radius * direction_y * edge_scale,
                    )

                # 方向死區是角度區域，不會在單位圓處終止。將扇形畫到
                # 目前圓形／方形混合後的實際外緣，和執行時判定一致。
                if deadzone > 0.0:
                    for index in range(boundary_count):
                        boundary = first_boundary + index * boundary_step
                        sector_points = [center, center]
                        for sample in range(13):
                            angle = (
                                boundary - deadzone
                                + deadzone * 2.0 * sample / 12.0
                            )
                            sector_points.extend(output_edge_point(angle))
                        preview.create_polygon(
                            *sector_points,
                            fill="#f3caca",
                            outline="",
                        )
                preview.create_line(
                    center - radius, center, center + radius, center,
                    fill="#b0b0b0"
                )
                preview.create_line(
                    center, center - radius, center, center + radius,
                    fill="#b0b0b0"
                )
                trigger = max(
                    0.0,
                    min(1.0, safe_float(self.stick_direction_trigger_vars[side], 0.60)),
                )
                release = max(
                    0.0,
                    min(trigger, safe_float(self.stick_direction_release_vars[side], 0.50)),
                )
                for value, color, dash in (
                    (trigger, "#2878c8", ()),
                    (release, "#666666", (3, 2)),
                ):
                    ring = radius * value
                    preview.create_oval(
                        center - ring,
                        center - ring,
                        center + ring,
                        center + ring,
                        outline=color,
                        width=2,
                        dash=dash,
                    )
                boundary_points = []
                for sample in range(121):
                    boundary_points.extend(
                        output_edge_point(360.0 * sample / 120.0)
                    )
                preview.create_line(
                    *boundary_points,
                    fill="#333333",
                    width=2,
                )
                preview.create_oval(
                    center - 2, center - 2, center + 2, center + 2,
                    fill="#333333", outline=""
                )

                for label, variable, formatter in value_labels:
                    label.configure(
                        text=formatter(safe_float(variable, 0.0))
                    )
                mouse_speed_value.configure(
                    text=f"{safe_float(self.stick_mouse_speed_vars[side], 900):.0f}"
                )

            for variable in (
                self.stick_direction_trigger_vars[side],
                self.stick_direction_release_vars[side],
                self.stick_direction_deadzone_vars[side],
                self.stick_mouse_speed_vars[side],
                self.left_output_shape_var
                if side == "LEFT"
                else self.right_output_shape_var,
            ):
                variable.trace_add("write", draw_direction_preview)

            # =========================
            # 4 向 / 8 向顯示切換
            # =========================
            def update_direction_mode(
                *args
            ):
                mode = (
                    self.stick_direction_mode_vars[
                        side
                    ].get()
                )
                mode_display_var.set(self.tr(mode))

                is_stick_mapping = (
                    mode
                    in (
                        "映射為右搖桿",
                        "映射為左搖桿",
                        "映射為滑鼠",
                    )
                )

                # =========================
                # 對角方向顯示規則
                # =========================
                #
                # 4WAY：
                # 隱藏四個對角方向。
                #
                # 8WAY：
                # 顯示四個對角方向。
                #
                # 映射為另一邊搖桿：
                # 顯示完整 8 個方向圖示。

                for widget in (
                    self.stick_diagonal_widgets[
                        side
                    ]
                ):
                    if (
                        mode == "8WAY"
                        or is_stick_mapping
                    ):
                        widget.grid()

                    else:
                        widget.grid_remove()

                # =========================
                # 方向按鍵下拉選單狀態
                # =========================
                for combo in direction_combos:
                    if is_stick_mapping:
                        # 保留原本位置與大小，
                        # 但變灰且不能操作。
                        combo.configure(
                            state="disabled"
                        )

                    else:
                        # 恢復正常可選狀態。
                        combo.configure(
                            state="readonly"
                        )

                direction_settings_frame.place_forget()
                mouse_settings_frame.place_forget()
                if mode in ("4WAY", "8WAY"):
                    direction_settings_frame.place(
                        x=0, rely=0.5, relwidth=1.0, anchor="w"
                    )
                elif mode == "映射為滑鼠":
                    mouse_settings_frame.place(
                        x=0, rely=0.5, relwidth=1.0, anchor="w"
                    )
                draw_direction_preview()

            # 保存這支搖桿的模式刷新函式
            self.stick_direction_mode_updaters[
                side
            ] = update_direction_mode

            def select_direction_mode(event=None):
                del event
                displayed = mode_display_var.get()
                reverse_values = {
                    self.tr(value): value for value in mode_values
                }
                self.stick_direction_mode_vars[side].set(
                    reverse_values.get(displayed, displayed)
                )
                update_direction_mode()

            mode_combo.bind(
                "<<ComboboxSelected>>",
                select_direction_mode
            )

            # 初始化顯示狀態
            update_direction_mode()

            # 疊放在區塊右上角，不參與 grid 尺寸計算。
            ttk.Button(
                parent,
                text="還原",
                width=5,
                command=(
                    lambda current_side=side:
                    self.reset_stick_direction_mapping(current_side)
                )
            ).place(
                relx=1.0,
                x=-4,
                y=2,
                anchor="ne"
            )

        # 建立左搖桿方向映射
        create_stick_direction_panel(
            left_direction_frame,
            "LEFT"
        )

        # 建立右搖桿方向映射
        create_stick_direction_panel(
            right_direction_frame,
            "RIGHT"
        )

        ttk.Label(
            stick_direction_frame,
            text=(
                "紅色扇形：方向死區，延伸至目前輸出形狀外緣；"
                "藍圈：觸發門檻；虛線圈：放開門檻。\n"
                "分界內維持原方向；滑鼠模式只調整游標速度。"
            ),
            anchor="w",
            justify="left",
            wraplength=330,
        ).grid(
            row=2,
            column=0,
            pady=(5, 0),
            sticky="w",
        )

        # =========================
        # 頁籤 3：進階震動
        # =========================
        audio_haptics_frame = ttk.Frame(mapping_notebook, padding=(12, 8))
        mapping_notebook.add(audio_haptics_frame, text="進階震動")
        audio_haptics_frame.columnconfigure(0, weight=1)

        mode_frame = ttk.LabelFrame(
            audio_haptics_frame, text="輸出來源", padding=(10, 7)
        )
        mode_frame.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        mode_frame.columnconfigure(0, weight=1)
        mode_content = ttk.Frame(mode_frame)
        self.audio_haptics_mode_content = mode_content
        mode_content.grid(row=0, column=0, sticky="ns")
        for column, (label_text, value) in enumerate((
            ("遊戲", "GAME"), ("音訊", "AUDIO"), ("混合", "MIX")
        )):
            ttk.Radiobutton(
                mode_content,
                text=label_text,
                value=value,
                variable=self.audio_haptics_mode_var,
            ).grid(row=0, column=column, padx=12, sticky="w")
        self.create_help(
            mode_content,
            "遊戲：只輸出遊戲原生震動。\n\n"
            "音訊：只將 Windows 預設輸出裝置的聲音轉成震動。\n\n"
            "混合：以柔和飽和方式合併遊戲與音訊震動，避免直接相加造成削波。"
        ).grid(row=0, column=3, padx=(8, 0))

        def add_audio_scale_row(
            parent, row_index, label_text, variable,
            minimum, maximum, step, number_format, help_text,
            length=190,
        ):
            label_widget = ttk.Label(
                parent, text=label_text, width=7, anchor="e"
            )
            label_widget.grid(row=row_index, column=0, sticky="e", pady=2)

            def snap_audio_value(value):
                numeric = max(minimum, min(maximum, float(value)))
                numeric = round(numeric / step) * step
                variable.set(format(numeric, number_format))

            scale = ttk.Scale(
                parent,
                from_=minimum,
                to=maximum,
                variable=variable,
                command=snap_audio_value,
                orient="horizontal",
                length=length,
            )
            scale.grid(row=row_index, column=1, padx=(8, 8), pady=2)
            value_label = ttk.Label(
                parent, width=6, anchor="w",
                text=format(float(variable.get()), number_format),
            )
            value_label.grid(row=row_index, column=2, sticky="w", pady=2)

            def update_value_label(*_args):
                try:
                    value_label.configure(
                        text=format(float(variable.get()), number_format)
                    )
                except (ValueError, tk.TclError):
                    pass

            variable.trace_add("write", update_value_label)
            help_widget = self.create_help(parent, help_text)
            help_widget.grid(row=row_index, column=3, sticky="w", pady=2)
            return [label_widget, scale, value_label, help_widget]

        response_frame = ttk.LabelFrame(
            audio_haptics_frame, text="音訊反應", padding=(10, 6)
        )
        self.audio_haptics_response_frame = response_frame
        response_frame.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        response_frame.columnconfigure(0, weight=1)
        response_content = ttk.Frame(response_frame)
        self.audio_haptics_response_content = response_content
        response_content.grid(row=0, column=0, sticky="ns")
        audio_rows = (
            ("混合", self.audio_haptics_mix_ratio_var, 0.0, 1.0, 0.05, ".2f",
             "只在「混合」模式生效。\n\n"
             "0.00 = 只保留遊戲原生震動\n"
             "0.35 = 建議起始值（遊戲 65%、音訊 35%）\n"
             "1.00 = 只保留音訊震動"),
            ("強度", self.audio_haptics_strength_var, 0.0, 1.0, 0.05, ".2f",
             "控制音訊轉換成震動前的總靈敏度。\n\n"
             "0.60 = 建議起始值"),
            ("噪閘", self.audio_haptics_noise_gate_var, 0.0, 0.25, 0.005, ".3f",
             "低於此音量的背景聲不會產生震動。\n\n"
             "0.015 = 建議起始值"),
            ("啟動", self.audio_haptics_attack_var, 1.0, 500.0, 1.0, ".0f",
             "聲音出現後，震動升起所需的反應時間。\n\n"
             "1 ms = 最低延遲的建議起始值\n"
             "降低：反應更快、更銳利\n"
             "提高：較平順，但可能弱化短促音效"),
            ("音訊釋放", self.audio_haptics_release_var, 5.0, 2000.0, 5.0, ".0f",
             "只平滑音訊分析結果的下降速度。\n\n"
             "140 ms = 建議起始值；與最終輸出的餘震不同。"),
        )
        self.audio_mix_ratio_widgets = []
        self.audio_haptics_response_widgets = []
        for row_index, row_data in enumerate(audio_rows):
            row_widgets = add_audio_scale_row(
                response_content, row_index, *row_data
            )
            self.audio_haptics_response_widgets.extend(row_widgets)
            if row_index == 0:
                self.audio_mix_ratio_widgets = row_widgets

        eq_frame = ttk.LabelFrame(
            audio_haptics_frame, text="五頻段調整", padding=(10, 6)
        )
        self.audio_haptics_eq_frame = eq_frame
        eq_frame.grid(row=2, column=0, sticky="ew", pady=(0, 6))
        eq_frame.columnconfigure(0, weight=1)
        eq_content = ttk.Frame(eq_frame)
        eq_content.grid(row=0, column=0, sticky="ns")
        eq_canvas = tk.Canvas(
            eq_content, width=320, height=165, background="#FFFFFF",
            highlightthickness=1, highlightbackground="#A0A0A0",
        )
        self.audio_haptics_eq_canvas = eq_canvas
        eq_canvas.grid(row=0, column=0)
        band_labels = ("Low", "L-Mid", "Mid", "H-Mid", "High")
        chart_left, chart_top = 32, 14
        chart_right, chart_bottom = 306, 128
        dragged_band = {"index": None}

        def audio_eq_enabled():
            return self.audio_haptics_mode_var.get().strip().upper() in (
                "AUDIO", "MIX"
            )

        def band_x(index):
            return chart_left + (
                (chart_right - chart_left) * index / 4.0
            )

        def draw_audio_eq(*_args):
            enabled = audio_eq_enabled()
            eq_canvas.configure(
                background="#FFFFFF" if enabled else "#F2F2F2",
                cursor="hand2" if enabled else "",
            )
            eq_canvas.delete("all")
            grid_color = "#E2E2E2" if enabled else "#D8D8D8"
            curve_color = "#1976D2" if enabled else "#9E9E9E"
            text_color = "#555555" if enabled else "#909090"
            for gain in (0.0, 0.5, 1.0, 1.5, 2.0):
                y = chart_bottom - (
                    (chart_bottom - chart_top) * gain / 2.0
                )
                eq_canvas.create_line(
                    chart_left, y, chart_right, y, fill=grid_color
                )
                eq_canvas.create_text(
                    4, y, text=f"{gain:g}", anchor="w",
                    fill=text_color, font=("Segoe UI", 8),
                )
            points = []
            gains = []
            for index, variable in enumerate(
                self.audio_haptics_band_gain_vars
            ):
                try:
                    gain = max(0.0, min(2.0, float(variable.get())))
                except (ValueError, tk.TclError):
                    gain = 1.0
                gains.append(gain)
                x = band_x(index)
                y = chart_bottom - (
                    (chart_bottom - chart_top) * gain / 2.0
                )
                points.extend((x, y))
                eq_canvas.create_line(
                    x, chart_top, x, chart_bottom, fill=grid_color
                )
            eq_canvas.create_line(
                *points, fill=curve_color, width=3, smooth=True
            )
            for index, gain in enumerate(gains):
                x = band_x(index)
                y = chart_bottom - (
                    (chart_bottom - chart_top) * gain / 2.0
                )
                eq_canvas.create_oval(
                    x - 6, y - 6, x + 6, y + 6,
                    fill=curve_color, outline="#FFFFFF", width=2,
                )
                value_y = y + 15 if y < chart_top + 20 else y - 14
                eq_canvas.create_text(
                    x, value_y, text=f"{gain:.2f}",
                    fill=text_color, font=("Segoe UI", 8),
                )
                eq_canvas.create_text(
                    x, 148, text=band_labels[index],
                    fill=text_color, font=("Segoe UI", 8),
                )

        def update_dragged_band(event):
            index = dragged_band["index"]
            if index is None or not audio_eq_enabled():
                return
            y = max(chart_top, min(chart_bottom, event.y))
            gain = 2.0 * (
                chart_bottom - y
            ) / (chart_bottom - chart_top)
            gain = round(gain / 0.05) * 0.05
            self.audio_haptics_band_gain_vars[index].set(
                f"{max(0.0, min(2.0, gain)):.2f}"
            )

        def begin_audio_eq_drag(event):
            if not audio_eq_enabled():
                return
            index = min(
                range(5), key=lambda item: abs(event.x - band_x(item))
            )
            if abs(event.x - band_x(index)) <= 28:
                dragged_band["index"] = index
                update_dragged_band(event)

        def end_audio_eq_drag(_event):
            dragged_band["index"] = None

        def reset_audio_eq_band(event):
            if not audio_eq_enabled():
                return
            nearest_index = None
            nearest_distance = None
            for index, variable in enumerate(
                self.audio_haptics_band_gain_vars
            ):
                try:
                    gain = max(0.0, min(2.0, float(variable.get())))
                except (ValueError, tk.TclError):
                    gain = 1.0
                point_y = chart_bottom - (
                    (chart_bottom - chart_top) * gain / 2.0
                )
                x_distance = abs(event.x - band_x(index))
                y_distance = abs(event.y - point_y)
                distance = x_distance + y_distance
                if (
                    x_distance <= 14
                    and y_distance <= 14
                    and (
                        nearest_distance is None
                        or distance < nearest_distance
                    )
                ):
                    nearest_index = index
                    nearest_distance = distance
            if nearest_index is not None:
                dragged_band["index"] = None
                self.audio_haptics_band_gain_vars[nearest_index].set(
                    "1.00"
                )

        eq_canvas.bind("<Button-1>", begin_audio_eq_drag)
        eq_canvas.bind("<Double-Button-1>", reset_audio_eq_band)
        eq_canvas.bind("<B1-Motion>", update_dragged_band)
        eq_canvas.bind("<ButtonRelease-1>", end_audio_eq_drag)
        for variable in self.audio_haptics_band_gain_vars:
            variable.trace_add("write", draw_audio_eq)

        eq_description = ttk.Label(
            eq_frame,
            text=(
                "拖曳曲線上的五個控制點調整低頻到高頻的音訊增益。\n"
                "雙擊控制點可單獨還原為 1.00。\n"
                "1.00 為原始增益；只影響音訊與混合模式，不影響純遊戲震動。\n"
                "音樂建議：1.10 / 1.05 / 1.00 / 0.90 / 0.80。\n"
                "遊戲音效建議：1.30 / 1.15 / 1.00 / 0.90 / 0.80。"
            ),
            justify="left",
            wraplength=320,
        )
        eq_description.grid(
            row=1, column=0, sticky="ew", pady=(5, 0)
        )

        def update_eq_description_wrap(event):
            if event.width > 80:
                eq_description.configure(
                    wraplength=max(120, event.width - 20)
                )

        eq_frame.bind(
            "<Configure>", update_eq_description_wrap, add="+"
        )
        self.audio_haptics_response_widgets.append(eq_description)
        draw_audio_eq()

        final_frame = ttk.LabelFrame(
            audio_haptics_frame, text="最終輸出", padding=(10, 6)
        )
        final_frame.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        final_frame.columnconfigure(0, weight=1)
        final_content = ttk.Frame(final_frame)
        final_content.grid(row=0, column=0, sticky="ns")
        add_audio_scale_row(
            final_content, 0, "餘震強度",
            self.audio_haptics_final_tail_strength_var,
            0.0, 1.0, 0.05, ".2f",
            "餘震是震動訊號減弱或停止時，暫時保留一部分剛才的震動，"
            "再依「衰減 ms」逐漸降到 0，讓停止感較柔和。\n\n"
            "0.00 = 關閉，立即跟隨原始訊號\n"
            "0.50 = 保留約一半的下降落差\n"
            "1.00 = 完整保留後再逐漸衰減\n\n"
            "套用於遊戲與音訊混合後的最終輸出；不會產生新的重複震動。",
        )
        add_audio_scale_row(
            final_content, 1, "衰減 ms",
            self.audio_haptics_final_tail_decay_var,
            50.0, 2000.0, 25.0, ".0f",
            "控制餘震逐漸消失的時間。\n\n"
            "只在餘震強度大於 0 時生效；建議從 350 ms 開始。",
        )

        footer = ttk.Frame(audio_haptics_frame)
        footer.grid(row=4, column=0, sticky="ew")
        footer.columnconfigure(0, weight=1)
        wasapi_available = importlib.util.find_spec("pyaudiowpatch") is not None
        dependency_text = (
            "● WASAPI 系統輸出擷取：可用"
            if wasapi_available
            else "● WASAPI 系統輸出擷取：缺少 PyAudioWPatch"
        )
        self.audio_haptics_status_label = tk.Label(
            footer,
            text=dependency_text,
            fg="#178A38" if wasapi_available else "#C62828",
        )
        self.audio_haptics_status_label.grid(row=0, column=0, sticky="w")
        self.audio_haptics_reset_button = ttk.Button(
            footer,
            text="還原進階震動預設",
            command=self.reset_audio_haptics,
        )
        self.audio_haptics_reset_button.grid(row=0, column=1, sticky="e")

        def update_audio_response_state(*_args):
            mode = self.audio_haptics_mode_var.get().strip().upper()
            enabled = mode in ("AUDIO", "MIX")
            response_frame.state(["!disabled"] if enabled else ["disabled"])
            eq_frame.state(["!disabled"] if enabled else ["disabled"])
            for widget in self.audio_haptics_response_widgets:
                try:
                    widget.state(["!disabled"] if enabled else ["disabled"])
                except (AttributeError, tk.TclError):
                    try:
                        widget.configure(
                            state="normal" if enabled else "disabled"
                        )
                    except tk.TclError:
                        pass
            visible = mode == "MIX"
            for widget in self.audio_mix_ratio_widgets:
                if visible:
                    widget.grid()
                else:
                    widget.grid_remove()
            draw_audio_eq()

        self.audio_haptics_mode_var.trace_add(
            "write", update_audio_response_state
        )
        update_audio_response_state()

        # =========================
        # 頁籤 4：陀螺儀映射
        # =========================
        gyro_frame = ttk.Frame(mapping_notebook, padding=(12, 10))
        mapping_notebook.add(gyro_frame, text="陀螺儀映射")
        gyro_frame.columnconfigure(0, weight=1)

        activation_frame = ttk.LabelFrame(
            gyro_frame, text="啟動方式", padding=(10, 8)
        )
        activation_frame.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        activation_content = ttk.Frame(activation_frame)
        activation_content.pack(anchor="center")
        ttk.Label(activation_content, text="按鍵").grid(
            row=0, column=0, padx=(0, 5)
        )
        self.gyro_activation_button_entry = ttk.Entry(
            activation_content,
            textvariable=self.gyro_activation_buttons_summary_var,
            state="readonly",
            width=7,
            justify="center",
        )
        self.gyro_activation_button_entry.grid(row=0, column=1)
        self.gyro_activation_select_button = ttk.Button(
            activation_content,
            text="…",
            width=2,
            command=lambda: self.open_gyro_button_selector("activation"),
        )
        self.gyro_activation_select_button.grid(
            row=0, column=2, padx=(2, 7)
        )
        for column, (text, value) in enumerate((
            ("關閉", "OFF"), ("按住", "HOLD"), ("切換", "TOGGLE")
        ), start=3):
            ttk.Radiobutton(
                activation_content,
                text=text,
                value=value,
                variable=self.gyro_activation_mode_var,
            ).grid(row=0, column=column, padx=2)
        self.create_help(
            activation_content,
            "關閉：不產生任何陀螺儀映射輸出。\n\n"
            "按住：選定按鍵符合任一／全部條件時啟用。\n\n"
            "切換：條件由未成立變成成立時切換一次。\n\n"
            "可選多顆按鍵；原本的 Xbox／鍵盤映射仍會保留。"
        ).grid(row=0, column=6, padx=(2, 0))

        def update_activation_button_state(*_args):
            disabled = self.gyro_activation_mode_var.get() == "OFF"
            self.gyro_activation_button_entry.configure(
                state="disabled" if disabled else "readonly"
            )
            self.gyro_activation_select_button.configure(
                state="disabled" if disabled else "normal"
            )
        self.gyro_activation_mode_var.trace_add(
            "write", update_activation_button_state
        )
        update_activation_button_state()

        target_frame = ttk.LabelFrame(
            gyro_frame, text="輸出目標", padding=(10, 8)
        )
        target_frame.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        target_content = ttk.Frame(target_frame)
        target_content.pack(anchor="center")
        for column, (text, value) in enumerate((
            ("左搖桿", "LEFT_STICK"),
            ("右搖桿", "RIGHT_STICK"),
            ("滑鼠", "MOUSE"),
        )):
            ttk.Radiobutton(
                target_content,
                text=text,
                value=value,
                variable=self.gyro_target_var,
            ).grid(row=0, column=column, padx=10)

        invert_content = ttk.Frame(target_content)
        invert_content.grid(
            row=1, column=0, columnspan=3, pady=(6, 0)
        )
        ttk.Checkbutton(
            invert_content, text="反轉 X", variable=self.gyro_invert_x_var
        ).pack(side="left", padx=8)
        ttk.Checkbutton(
            invert_content, text="反轉 Y", variable=self.gyro_invert_y_var
        ).pack(side="left", padx=8)
        self.gyro_player_space_button = ttk.Checkbutton(
            invert_content,
            text="傾斜軸補償",
            variable=self.gyro_player_space_var,
        )
        self.gyro_player_space_button.pack(side="left", padx=(8, 4))
        self.gyro_player_space_help = self.create_help(
            invert_content,
            "依手把傾斜角度修正瞄準軸向，減少左右與上下串軸。\n"
            "建議開啟；關閉後使用固定 X／Z 軸。"
        )
        self.gyro_player_space_help.pack(side="left")

        motion_mode_frame = ttk.LabelFrame(
            gyro_frame, text="控制模式", padding=(10, 8)
        )
        self.gyro_motion_mode_frame = motion_mode_frame
        motion_mode_frame.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        motion_mode_content = ttk.Frame(motion_mode_frame)
        motion_mode_content.pack(anchor="center")
        self.gyro_center_mode_button = ttk.Radiobutton(
            motion_mode_content,
            text="回中（瞄準）",
            value="CENTER",
            variable=self.gyro_motion_mode_var,
        )
        self.gyro_center_mode_button.pack(side="left", padx=10)
        self.gyro_tilt_mode_button = ttk.Radiobutton(
            motion_mode_content,
            text="傾斜（方向盤）",
            value="TILT",
            variable=self.gyro_motion_mode_var,
        )
        self.gyro_tilt_mode_button.pack(side="left", padx=10)
        self.gyro_motion_mode_help = self.create_help(
            motion_mode_content,
            "回中：依陀螺儀角速度輸出，停止轉動便自動回中，適合瞄準。\n\n"
            "傾斜：以手把相對於啟用瞬間的傾斜角度控制搖桿，適合方向盤；"
            "僅支援左右搖桿輸出。\n\n"
            "自訂重設按鍵會專門用於重設中立，不再送出原本的遊戲映射。"
        )
        self.gyro_motion_mode_help.pack(side="left", padx=(6, 0))

        tilt_axis_content = ttk.Frame(motion_mode_frame)
        tilt_axis_content.pack(anchor="center", pady=(6, 0))
        self.gyro_tilt_axis_widgets = []
        for text, value in (
            ("僅水平", "HORIZONTAL"), ("水平＋垂直", "DUAL")
        ):
            widget = ttk.Radiobutton(
                tilt_axis_content,
                text=text,
                value=value,
                variable=self.gyro_tilt_axis_var,
            )
            widget.pack(side="left", padx=8)
            self.gyro_tilt_axis_widgets.append(widget)
        tilt_recenter_content = ttk.Frame(motion_mode_frame)
        tilt_recenter_content.pack(anchor="center", pady=(6, 0))
        self.gyro_tilt_recenter_button = ttk.Button(
            tilt_recenter_content,
            text="重設中立",
            width=9,
            command=self.reset_tilt_neutral,
        )
        self.gyro_tilt_recenter_button.pack(side="left", padx=(0, 12))
        self.gyro_tilt_recenter_label = ttk.Label(
            tilt_recenter_content, text="自訂按鍵"
        )
        self.gyro_tilt_recenter_label.pack(side="left", padx=(0, 5))
        self.gyro_tilt_recenter_combo = ttk.Combobox(
            tilt_recenter_content,
            textvariable=self.gyro_tilt_recenter_button_var,
            values=self.gyro_tilt_recenter_button_options,
            state="readonly",
            width=9,
        )
        self.gyro_tilt_recenter_combo.pack(side="left")

        gyro_settings_frame = ttk.LabelFrame(
            gyro_frame, text="陀螺儀反應", padding=(10, 8)
        )
        gyro_settings_frame.grid(row=3, column=0, sticky="ew")
        gyro_settings_frame.columnconfigure(0, weight=1)
        gyro_settings_content = ttk.Frame(gyro_settings_frame)
        gyro_settings_content.grid(
            row=0, column=0, sticky="ns"
        )

        gyro_rows = (
            ("搖桿感度", self.gyro_stick_sensitivity_var, 0.1, 10.0, 0.1, ".1f",
             "回中模式：陀螺儀角速度轉成搖桿偏移的倍率。\n"
             "僅用於回中模式。", "CENTER_STICK"),
            ("滑鼠感度", self.gyro_mouse_sensitivity_var, 0.5, 30.0, 0.5, ".1f",
             "每轉動一度對應的游標像素倍率。\n建議起始值：8.0。", "MOUSE"),
            ("最大傾角", self.gyro_tilt_max_angle_var, 10.0, 60.0, 1.0, ".0f",
             "傾斜到此角度時輸出滿值。\n9 軸建議值：35 度。", "TILT"),
            ("X 比例", self.gyro_x_ratio_var, 0.5, 2.0, 0.05, ".2f",
             "水平陀螺儀感度相對倍率。1.00 = 不改變。", "ALL"),
            ("Y 比例", self.gyro_y_ratio_var, 0.5, 2.0, 0.05, ".2f",
             "垂直陀螺儀感度相對倍率。1.00 = 不改變。", "Y_AXIS"),
            ("死區", self.gyro_deadzone_var, 0.0, 5.0, 0.1, ".1f",
             "忽略小於此角速度的感測器漂移。單位：度／秒。\n"
             "自適應功能會在明確轉動時縮小死區。\n建議值：0.6。", "CENTER"),
            ("反死區", self.gyro_stick_anti_deadzone_var, 0.0, 30.0, 1.0, ".0f",
             "補償遊戲內建搖桿死區；只作用於回中搖桿輸出，不改變實體搖桿。\n"
             "0 = 中性預設；可依遊戲死區從 12% 開始調整。", "CENTER_STICK"),
            ("傾斜死區", self.gyro_tilt_deadzone_var, 0.0, 5.0, 0.1, ".1f",
             "忽略中立角度附近的小幅傾斜。單位：度。\n9 軸建議值：0.8。", "TILT"),
            ("平滑 ms", self.gyro_smoothing_var, 0.0, 100.0, 5.0, ".0f",
             "回中模式的速度自適應平滑：慢速使用設定值，快速轉動會降至約 5 ms。\n0 = 關閉；建議值：15 ms。", "CENTER"),
            ("傾斜平滑", self.gyro_tilt_smoothing_var, 0.0, 150.0, 5.0, ".0f",
             "傾斜模式的時間制低通平滑。0 = 關閉。\n"
             "9 軸建議值：30 ms。", "TILT"),
        )
        gyro_scoped_rows = []
        for row_index, (
            label, variable, minimum, maximum, step, number_format,
            help_text, target_scope
        ) in enumerate(gyro_rows):
            label_widget = ttk.Label(
                gyro_settings_content, text=label, width=8, anchor="e"
            )
            label_widget.grid(row=row_index, column=0, sticky="e", pady=2)

            def snap_gyro_value(
                value,
                current_variable=variable,
                current_minimum=minimum,
                current_maximum=maximum,
                current_step=step,
                current_format=number_format,
            ):
                numeric = max(
                    current_minimum, min(current_maximum, float(value))
                )
                numeric = round(numeric / current_step) * current_step
                current_variable.set(format(numeric, current_format))

            scale = ttk.Scale(
                gyro_settings_content,
                from_=minimum,
                to=maximum,
                variable=variable,
                command=snap_gyro_value,
                orient="horizontal",
                length=190,
            )
            scale.grid(row=row_index, column=1, padx=8, pady=2)
            value_label = ttk.Label(
                gyro_settings_content,
                width=6,
                anchor="w",
                text=format(float(variable.get()), number_format),
            )
            value_label.grid(row=row_index, column=2, sticky="w", pady=2)

            def update_gyro_value_label(
                *args,
                current_variable=variable,
                current_label=value_label,
                current_format=number_format,
            ):
                try:
                    current_label.configure(
                        text=format(float(current_variable.get()), current_format)
                    )
                except (ValueError, tk.TclError):
                    pass

            variable.trace_add("write", update_gyro_value_label)
            help_widget = self.create_help(gyro_settings_content, help_text)
            help_widget.grid(row=row_index, column=3, pady=2)
            gyro_scoped_rows.append((
                target_scope, (label_widget, scale, value_label, help_widget)
            ))

        gyro_action_row = ttk.Frame(gyro_settings_content)
        gyro_action_row.grid(
            row=len(gyro_rows), column=0, columnspan=4, pady=(10, 2)
        )
        self.gyro_calibration_button = ttk.Button(
            gyro_action_row,
            text="感測器校正",
            command=self.calibrate_gyro,
        )
        self.gyro_calibration_button.pack(side="left", padx=(0, 6))
        self.gyro_curve_button = ttk.Button(
            gyro_action_row,
            text="感度曲線",
            command=self.open_gyro_curve_window,
        )
        self.gyro_curve_button.pack(side="left", padx=(0, 6))
        stability_frame = ttk.LabelFrame(
            gyro_frame, text="穩定控制", padding=(10, 8)
        )
        stability_frame.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        stability_content = ttk.Frame(stability_frame)
        stability_content.pack(anchor="center")
        stability_rows = (
            (
                "加速抑制", self.gyro_accel_suppression_var,
                0.0, 100.0, 5.0,
                "九軸或傾斜軸補償快速轉動、震動時，降低加速度計對姿態的拉動。\n"
                "0 = 關閉；建議值：70%。", "FUSION",
            ),
            (
                "自適死區", self.gyro_adaptive_deadzone_var,
                0.0, 100.0, 5.0,
                "回中模式靜止時保留死區，明確轉動時自動縮小。\n"
                "0 = 固定死區；建議值：85%。", "CENTER",
            ),
            (
                "防晃 ms", self.gyro_button_freeze_var,
                0.0, 120.0, 5.0,
                "啟動鍵、重設中立鍵或額外按鍵改變狀態時，短暫停止輸出。\n"
                "0 = 關閉；建議值：35 ms。", "ALL",
            ),
        )
        stability_scoped_rows = []
        for row_index, (
            label, variable, minimum, maximum, step, help_text, target_scope
        ) in enumerate(stability_rows):
            label_widget = ttk.Label(
                stability_content, text=label, width=8, anchor="e"
            )
            label_widget.grid(row=row_index, column=0, sticky="e", pady=3)

            def snap_stability_value(
                value,
                current_variable=variable,
                current_minimum=minimum,
                current_maximum=maximum,
                current_step=step,
            ):
                numeric = max(
                    current_minimum, min(current_maximum, float(value))
                )
                numeric = round(numeric / current_step) * current_step
                current_variable.set(f"{numeric:.0f}")

            scale = ttk.Scale(
                stability_content,
                from_=minimum,
                to=maximum,
                variable=variable,
                command=snap_stability_value,
                orient="horizontal",
                length=190,
            )
            scale.grid(row=row_index, column=1, padx=8, pady=3)
            value_label = ttk.Label(
                stability_content, width=6, anchor="w",
                text=f"{float(variable.get()):.0f}",
            )
            value_label.grid(row=row_index, column=2, sticky="w", pady=3)

            def update_stability_value_label(
                *args,
                current_variable=variable,
                current_label=value_label,
            ):
                try:
                    current_label.configure(
                        text=f"{float(current_variable.get()):.0f}"
                    )
                except (ValueError, tk.TclError):
                    pass

            variable.trace_add("write", update_stability_value_label)
            help_widget = self.create_help(stability_content, help_text)
            help_widget.grid(row=row_index, column=3, pady=3)
            stability_scoped_rows.append((
                target_scope, (label_widget, scale, value_label, help_widget)
            ))

        stabilization_key_content = ttk.Frame(stability_content)
        stabilization_key_content.grid(
            row=len(stability_rows), column=0, columnspan=4, pady=(4, 0)
        )
        ttk.Label(stabilization_key_content, text="額外按鍵").pack(
            side="left", padx=(0, 5)
        )
        self.gyro_stabilization_button_entry = ttk.Entry(
            stabilization_key_content,
            textvariable=self.gyro_stabilization_buttons_summary_var,
            state="readonly",
            width=7,
            justify="center",
        )
        self.gyro_stabilization_button_entry.pack(side="left")
        ttk.Button(
            stabilization_key_content,
            text="…",
            width=2,
            command=lambda: self.open_gyro_button_selector("stabilization"),
        ).pack(side="left", padx=(2, 0))
        stabilization_key_help = self.create_help(
            stabilization_key_content,
            "可選多顆容易造成手把晃動的按鍵。\n"
            "任一按鍵按下或放開都會短暫停止陀螺儀，原映射仍保留。"
        )
        stabilization_key_help.pack(side="left", padx=(6, 0))
        self.gyro_reset_button = ttk.Button(
            stabilization_key_content,
            text="還原陀螺儀預設",
            command=self.reset_gyro_mapping,
        )
        self.gyro_reset_button.pack(side="left", padx=(6, 0))

        def update_gyro_target_state(*args):
            target = self.gyro_target_var.get().strip().upper()
            is_mouse = target == "MOUSE"
            if is_mouse and self.gyro_motion_mode_var.get() == "TILT":
                self.gyro_motion_mode_var.set("CENTER")
            mode_state = ["disabled"] if is_mouse else ["!disabled"]
            self.gyro_motion_mode_frame.state(mode_state)
            self.gyro_center_mode_button.state(mode_state)
            self.gyro_tilt_mode_button.state(mode_state)
            self.gyro_motion_mode_help.configure(
                state="disabled" if is_mouse else "normal"
            )
            motion_mode = self.gyro_motion_mode_var.get().strip().upper()
            curve_enabled = (
                self.gyro_activation_mode_var.get() != "OFF"
                and motion_mode == "CENTER"
                and not is_mouse
            )
            self.gyro_curve_button.state(
                ["!disabled"] if curve_enabled else ["disabled"]
            )
            player_space_enabled = motion_mode == "CENTER"
            self.gyro_player_space_button.state(
                ["!disabled"] if player_space_enabled else ["disabled"]
            )
            self.gyro_player_space_help.configure(
                state="normal" if player_space_enabled else "disabled"
            )
            tilt_enabled = motion_mode == "TILT" and not is_mouse
            for widget in self.gyro_tilt_axis_widgets:
                widget.state(["!disabled"] if tilt_enabled else ["disabled"])
            self.gyro_tilt_recenter_button.state(
                ["!disabled"] if tilt_enabled else ["disabled"]
            )
            shortcut_enabled = not is_mouse
            self.gyro_tilt_recenter_label.state(
                ["!disabled"] if shortcut_enabled else ["disabled"]
            )
            self.gyro_tilt_recenter_combo.state(
                ["!disabled", "readonly"]
                if shortcut_enabled else ["disabled"]
            )
            dual_axis = self.gyro_tilt_axis_var.get() == "DUAL"
            for scope, widgets in gyro_scoped_rows:
                visible = (
                    scope == "ALL"
                    or (scope == "MOUSE" and is_mouse)
                    or (scope == "CENTER" and motion_mode == "CENTER")
                    or (
                        scope == "CENTER_STICK"
                        and motion_mode == "CENTER"
                        and not is_mouse
                    )
                    or (scope == "TILT" and tilt_enabled)
                    or (
                        scope == "Y_AXIS"
                        and (motion_mode == "CENTER" or dual_axis)
                    )
                )
                for widget in widgets:
                    if visible:
                        widget.grid()
                    else:
                        widget.grid_remove()
            for scope, widgets in stability_scoped_rows:
                visible = (
                    scope == "ALL"
                    or (scope == "CENTER" and motion_mode == "CENTER")
                    or (scope == "TILT" and tilt_enabled)
                    or (
                        scope == "FUSION"
                        and (
                            tilt_enabled
                            or (
                                motion_mode == "CENTER"
                                and self.gyro_player_space_var.get()
                            )
                        )
                    )
                )
                for widget in widgets:
                    if visible:
                        widget.grid()
                    else:
                        widget.grid_remove()

        self.gyro_activation_mode_var.trace_add(
            "write", update_gyro_target_state
        )
        self.gyro_target_var.trace_add("write", update_gyro_target_state)
        self.gyro_motion_mode_var.trace_add("write", update_gyro_target_state)
        self.gyro_tilt_axis_var.trace_add("write", update_gyro_target_state)
        self.gyro_player_space_var.trace_add("write", update_gyro_target_state)
        update_gyro_target_state()

        # =========================
        # 狀態列
        # =========================
        status_frame = ttk.Frame(
            right_frame
        )

        status_frame.grid(
            row=1,
            column=0,
            sticky="w",
            pady=(3, 0)
        )

        # ViGEmBus 狀態
        self.driver_status_label = tk.Label(
            status_frame,
            text=""
        )

        self.driver_status_label.pack(
            anchor="w",
            padx=(0, 8)
        )

        self.update_driver_status()

        self.controller_status_label = tk.Label(
            status_frame,
            text="● 手把連線：連接程式未啟動",
            fg="#777777",
        )
        self.controller_status_label.pack(
            anchor="w",
            padx=(0, 8),
            pady=(2, 0),
        )
        self.update_controller_status()

        # =========================
        # 底部功能按鈕
        # =========================
        action_frame = ttk.Frame(
            self.root,
            padding=(20, 8, 20, 12)
        )
        self.action_frame = action_frame
        action_frame.grid(
            row=1,
            column=0,
            sticky="ew"
        )

        self.language_button = ttk.Button(
            action_frame,
            text="中 / En",
            width=6,
            command=self.toggle_language
        )
        self.language_button.pack(
            side="left",
            ipadx=3,
            ipady=3
        )

        ttk.Button(
            action_frame,
            text="還原預設",
            command=self.reset_to_defaults
        ).pack(
            side="left",
            padx=(6, 0),
            ipadx=6,
            ipady=3
        )

        ttk.Button(
            action_frame,
            text="校正搖桿",
            command=self.run_calibration
        ).pack(
            side="left",
            padx=(6, 0),
            ipadx=6,
            ipady=3
        )

        ttk.Button(
            action_frame,
            text="刷入相容韌體",
            command=self.flash_firmware
        ).pack(
            side="left",
            padx=(6, 0),
            ipadx=6,
            ipady=3
        )

        ttk.Button(
            action_frame,
            text="儲存設定",
            command=self.save_config
        ).pack(
            side="left",
            padx=(6, 0),
            ipadx=8,
            ipady=3
        )

        ttk.Button(
            action_frame,
            text="重新啟動連接程式",
            command=self.restart_main
        ).pack(
            side="left",
            padx=(6, 0),
            ipadx=6,
            ipady=3
        )

        ttk.Button(
            action_frame,
            text="Pin",
            command=self.pin_controller
        ).pack(
            side="left",
            padx=(6, 0),
            ipadx=6,
            ipady=3
        )

        # 所有元件建立完成後，
        # 再依照實際螢幕可用高度調整視窗。
        self.root.after_idle(
            self.update_adaptive_window
        )

    def _on_main_frame_configure(
        self,
        event=None
    ):
        """主內容尺寸改變時，同步更新捲動範圍。"""

        if not hasattr(
            self,
            "scroll_canvas"
        ):
            return

        self.scroll_canvas.configure(
            scrollregion=(
                self.scroll_canvas.bbox(
                    "all"
                )
            )
        )


    def _bind_mousewheel(
        self,
        event=None
    ):
        """滑鼠位於主內容區時啟用滾輪捲動。"""

        self.root.bind_all(
            "<MouseWheel>",
            self._on_mousewheel
        )


    def _unbind_mousewheel(
        self,
        event=None
    ):
        """滑鼠離開主內容區時取消全域滾輪綁定。"""

        self.root.unbind_all(
            "<MouseWheel>"
        )


    def _on_mousewheel(
        self,
        event
    ):
        """只有內容真的超出可視區時才處理滾輪。"""

        if not self.scrollbar.winfo_ismapped():
            return

        self.scroll_canvas.yview_scroll(
            int(
                -event.delta / 120
            ),
            "units"
        )


    def get_work_area(self):
        """取得目前視窗所在螢幕排除工作列後的可用範圍。"""
        try:
            class Rect(ctypes.Structure):
                _fields_ = [
                    ("left", ctypes.c_long),
                    ("top", ctypes.c_long),
                    ("right", ctypes.c_long),
                    ("bottom", ctypes.c_long),
                ]

            class MonitorInfo(ctypes.Structure):
                _fields_ = [
                    ("cbSize", ctypes.c_ulong),
                    ("rcMonitor", Rect),
                    ("rcWork", Rect),
                    ("dwFlags", ctypes.c_ulong),
                ]

            user32 = ctypes.windll.user32
            monitor_from_window = user32.MonitorFromWindow
            monitor_from_window.argtypes = [
                ctypes.c_void_p,
                ctypes.c_uint,
            ]
            monitor_from_window.restype = ctypes.c_void_p
            get_monitor_info = user32.GetMonitorInfoW
            get_monitor_info.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(MonitorInfo),
            ]
            get_monitor_info.restype = ctypes.c_int

            monitor = monitor_from_window(
                ctypes.c_void_p(self.root.winfo_id()),
                2  # MONITOR_DEFAULTTONEAREST
            )
            info = MonitorInfo()
            info.cbSize = ctypes.sizeof(MonitorInfo)
            if monitor and get_monitor_info(
                monitor,
                ctypes.byref(info)
            ):
                work = info.rcWork
                return (
                    int(work.left),
                    int(work.top),
                    int(work.right - work.left),
                    int(work.bottom - work.top),
                )
        except Exception:
            pass

        return (
            0,
            0,
            self.root.winfo_screenwidth(),
            self.root.winfo_screenheight(),
        )


    def get_zoom_canvas_size(
        self
    ):
        """依目前螢幕解析度選擇適合的曲線放大尺寸。"""

        _, _, work_width, work_height = self.get_work_area()
        logical_width = work_width / self.ui_scale
        logical_height = work_height / self.ui_scale

        # 以螢幕高度為主要判斷依據，
        # 避免 720p / 768p 的放大曲線超出可視範圍。
        #
        # 寬螢幕解析度同時受到 width 限制，
        # 防止特殊比例螢幕選到過大的版本。
        if (
            logical_height <= 768
            or logical_width <= 1366
        ):
            # 720p / 768p
            base_size = (440, 415)

        elif (
            logical_height <= 1080
            or logical_width <= 1920
        ):
            # 900p / 1080p
            base_size = (620, 580)

        elif (
            logical_height <= 1440
            or logical_width <= 2560
        ):
            # 1440p
            base_size = (800, 750)

        else:
            # 4K 與更高解析度
            base_size = (980, 920)

        return tuple(
            round(value * self.ui_scale)
            for value in base_size
        )


    def update_adaptive_window(
        self
    ):
        """依螢幕高度限制視窗，低解析度時自動啟用垂直捲動。"""

        if not hasattr(
            self,
            "scroll_canvas"
        ):
            return

        self.root.update_idletasks()

        # 主內容實際需要的尺寸
        content_width = (
            self.main_frame.winfo_reqwidth()
        )

        content_height = (
            self.main_frame.winfo_reqheight()
        )

        # 底部固定按鈕列需要的高度
        action_height = 0

        if self.action_frame.winfo_ismapped():
            action_height = (
                self.action_frame.winfo_reqheight()
            )

        work_x, work_y, work_width, work_height = (
            self.get_work_area()
        )

        # geometry() 設定的是客戶區尺寸，仍需替標題列與視窗邊框
        # 保留空間；工作列已由 get_work_area() 排除。
        window_chrome_x = max(16, round(16 * self.ui_scale))
        window_chrome_y = max(40, round(40 * self.ui_scale))

        max_window_width = max(
            640,
            work_width
            - window_chrome_x
        )

        max_window_height = max(
            480,
            work_height
            - window_chrome_y
        )

        # 主內容可使用的最大高度。
        available_content_height = max(
            300,
            max_window_height
            - action_height
        )

        need_vertical_scroll = (
            content_height
            > available_content_height
        )

        if need_vertical_scroll:
            self.scrollbar.grid(
                row=0,
                column=1,
                sticky="ns"
            )

            scrollbar_width = max(
                self.scrollbar.winfo_reqwidth(),
                16
            )

            viewport_height = (
                available_content_height
            )

        else:
            self.scrollbar.grid_remove()

            scrollbar_width = 0
            viewport_height = (
                content_height
            )

            # 不需要捲動時回到最上方。
            self.scroll_canvas.yview_moveto(
                0.0
            )

        # 目前 UI 主要問題是高度超出螢幕。
        # 寬度仍盡量保持原版面，不縮放元件。
        viewport_width = min(
            content_width,
            max_window_width
            - scrollbar_width
        )

        self.scroll_canvas.configure(
            width=viewport_width,
            height=viewport_height
        )

        # 內容寬度至少維持原本需求；
        # 若視窗更寬，則讓內容區跟著填滿。
        canvas_window_width = max(
            content_width,
            viewport_width
        )

        self.scroll_canvas.itemconfigure(
            self.main_canvas_window,
            width=canvas_window_width
        )

        self.scroll_canvas.configure(
            scrollregion=(
                self.scroll_canvas.bbox(
                    "all"
                )
            )
        )

        window_width = min(
            content_width
            + scrollbar_width,
            max_window_width
        )

        window_height = min(
            viewport_height
            + action_height,
            max_window_height
        )

        # 正常模式維持固定視窗；
        # 搖桿放大模式仍允許調整。
        if not self.stick_settings_zoomed:
            self.root.resizable(
                False,
                False
            )

        # 在目前螢幕的工作區置中。
        x = work_x + max(
            0,
            (
                work_width
                - window_width
                - window_chrome_x
            ) // 2
        )

        y = work_y + max(
            0,
            (
                work_height
                - window_height
                - window_chrome_y
            ) // 2
        )

        self.root.geometry(
            f"{window_width}x{window_height}+{x}+{y}"
        )


    def set_zoom_controls_centered(
        self,
        centered
    ):
        """放大時置中兩個設定群組；一般模式維持原本滿寬排列。"""
        groups = (
            (
                self.left_smoothing_frame,
                self.left_output_group,
                self.left_deadzone_frame,
            ),
            (
                self.right_smoothing_frame,
                self.right_output_group,
                self.right_deadzone_frame,
            ),
        )

        for smoothing_frame, output_group, deadzone_frame in groups:
            smoothing_frame.pack_forget()
            deadzone_frame.pack_forget()

            if centered:
                # 不隨放大的曲線 Canvas 拉滿寬度，讓兩個設定框各自
                # 以實際所需寬度位於頁籤正中央。
                smoothing_frame.pack(
                    pady=(4, 0),
                    anchor="center",
                )
                output_group.grid_configure(sticky="")
                deadzone_frame.pack(
                    pady=(4, 0),
                    anchor="center",
                )
            else:
                smoothing_frame.pack(
                    pady=(4, 0),
                    fill="x",
                )
                output_group.grid_configure(sticky="ew")
                deadzone_frame.pack(
                    pady=(4, 0),
                    fill="x",
                )

    def toggle_stick_settings_zoom(self):
        """放大 / 還原整個搖桿設定區域。"""

        if not self.stick_settings_zoomed:
            # =========================
            # 進入放大模式
            # =========================

            # 先保存目前正常模式的完整視窗尺寸與位置。
            self.root.update_idletasks()

            self.normal_geometry = (
                self.root.geometry()
            )

            self.stick_settings_zoomed = True

            # 只保留搖桿設定區域。
            self.right_frame.grid_remove()
            self.rumble_frame.grid_remove()
            self.action_frame.grid_remove()

            # 依目前螢幕解析度選擇適合的放大曲線尺寸。
            canvas_width, canvas_height = (
                self.get_zoom_canvas_size()
            )

            # 放大模式的座標圖邊距。
            margin_left = (
                self.ZOOM_MARGIN_LEFT
            )

            margin_right = (
                self.ZOOM_MARGIN_RIGHT
            )

            margin_top = (
                self.ZOOM_MARGIN_TOP
            )

            margin_bottom = (
                self.ZOOM_MARGIN_BOTTOM
            )

            # 讓整個搖桿設定區塊
            # 在放大視窗中水平置中。
            self.main_frame.columnconfigure(
                0,
                weight=1
            )

            self.content_frame.grid(
                sticky="n"
            )

            self.stick_frame.grid(
                sticky=""
            )

            # 曲線 Notebook 與下面的控制列置中。
            self.stick_frame.columnconfigure(
                0,
                weight=1
            )

            self.stick_frame.columnconfigure(
                1,
                weight=0
            )

            self.stick_frame.columnconfigure(
                2,
                weight=0
            )

            self.root.resizable(
                True,
                True
            )

            self.curve_notebook.grid_configure(
                column=0,
                columnspan=5
            )

            self.set_zoom_controls_centered(
                True
            )

        else:
            # =========================
            # 返回正常模式
            # =========================

            self.stick_settings_zoomed = False

            # 恢復被隱藏的區域。
            self.right_frame.grid()
            self.rumble_frame.grid()
            self.action_frame.grid()

            # 恢復正常曲線尺寸。
            canvas_width = (
                self.NORMAL_CANVAS_WIDTH
            )

            canvas_height = (
                self.NORMAL_CANVAS_HEIGHT
            )

            # 恢復正常座標圖邊距。
            margin_left = (
                self.NORMAL_MARGIN_LEFT
            )

            margin_right = (
                self.NORMAL_MARGIN_RIGHT
            )

            margin_top = (
                self.NORMAL_MARGIN_TOP
            )

            margin_bottom = (
                self.NORMAL_MARGIN_BOTTOM
            )

            # 恢復正常版面。
            self.main_frame.columnconfigure(
                0,
                weight=0
            )

            self.content_frame.grid(
                sticky=""
            )

            self.stick_frame.grid(
                sticky="ew"
            )

            self.stick_frame.columnconfigure(
                0,
                weight=0
            )

            # 恢復防抖與死區控制列排列。
            self.set_zoom_controls_centered(
                False
            )

            # 恢復 Notebook 原本跨 3 欄。
            self.curve_notebook.grid_configure(
                column=0,
                columnspan=3
            )

        # =========================
        # 套用曲線尺寸與邊距
        # =========================

        for editor in (
            self.left_curve_editor,
            self.right_curve_editor
        ):
            editor.set_zoomed(
                self.stick_settings_zoomed
            )

            editor.margin_left = (
                margin_left
            )

            editor.margin_right = (
                margin_right
            )

            editor.margin_top = (
                margin_top
            )

            editor.margin_bottom = (
                margin_bottom
            )

            editor.set_canvas_size(
                canvas_width,
                canvas_height
            )

        # =========================
        # 更新放大 / 返回按鈕文字
        # =========================

        zoom_text = (
            "返回"
            if self.stick_settings_zoomed
            else "放大"
        )

        self.curve_zoom_button.configure(
            text=self.tr(zoom_text)
        )
        self.curve_zoom_button.lift()

        # 讓 Tk 完成目前版面重新配置。
        self.root.update_idletasks()

        if self.stick_settings_zoomed:
            # =========================
            # 放大模式
            # =========================
            #
            # 放大模式依目前內容與螢幕大小
            # 自動計算視窗尺寸。

            self.root.after_idle(
                self.update_adaptive_window
            )

        else:
            # =========================
            # 返回正常模式
            # =========================
            #
            # 不再重新執行 update_adaptive_window()，
            # 否則會重新計算正常視窗尺寸，
            # 導致返回後和進入放大前不同。
            #
            # 直接恢復進入放大模式前保存的
            # 完整尺寸與位置。

            self.root.resizable(
                False,
                False
            )

            if self.normal_geometry:
                self.root.geometry(
                    self.normal_geometry
                )

    def update_driver_status(self):
        if self.vigembus_installed:
            self.driver_status_label.config(
                text=self.tr("● ViGEmBus 驅動程式：已安裝"),
                fg="green"
            )
        else:
            self.driver_status_label.config(
                text=self.tr(
                    "● 未偵測到 ViGEmBus 驅動程式，"
                    "目前無法建立 Xbox 虛擬控制器。"
                ),
                fg="red"
            )        

    def update_controller_status(self, schedule_next=True):
        state_text = {
            "starting": "正在啟動",
            "searching": "正在搜尋手把",
            "connected": "已連線",
            "disconnected": "已斷線，正在重新搜尋",
            "stopped": "連接程式未啟動",
        }
        mode_text = {
            "bluetooth": "Windows BLE",
            "esp32": "ESP32",
            "wired": "USB Wired",
        }
        text = "● 手把連線：連接程式未啟動"
        color = "#777777"

        try:
            data = json.loads(
                STATUS_PATH.read_text(encoding="utf-8")
            )
            age = time.time() - float(data.get("updated_at", 0.0))
            state = data.get("state", "stopped")
            if age > 3.0 and state != "stopped":
                state = "stopped"

            raw_mode = data.get("mode")
            mode = mode_text.get(raw_mode, "")
            detail = state_text.get(state, "狀態未知")
            text = f"● 手把連線：{detail}"
            if mode:
                text += f" · {mode}"

            if state == "connected":
                battery = data.get("battery_percent")
                if raw_mode == "wired":
                    text += " · USB 供電"
                    if data.get("wired_full_report") is False:
                        text += " · 基本模式"
                elif battery is not None:
                    battery = int(battery)
                    if battery >= 75:
                        battery_level = "高"
                    elif battery >= 40:
                        battery_level = "中"
                    else:
                        battery_level = "低"
                    text += f" · 電量 {battery_level}"
                    voltage = data.get("battery_voltage")
                    if voltage is not None:
                        text += f" ({float(voltage):.3f}V)"
                    if data.get("charging"):
                        text += "（充電中）"
                if not (
                    raw_mode == "wired"
                    and data.get("wired_full_report") is False
                ):
                    if data.get("sensor_mode") == "nine_axis":
                        text += " · 九軸"
                    elif data.get("sensor_mode") == "six_axis":
                        text += " · 六軸"
                color = "#138A36"
            elif state in ("starting", "searching", "disconnected"):
                color = "#D97A00"
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            pass

        self.controller_status_label.config(
            text=self.tr(text), fg=color
        )
        if schedule_next:
            try:
                self.root.after(500, self.update_controller_status)
            except tk.TclError:
                pass

    def pin_controller(self):
        try:
            COMMAND_PATH.write_text(
                "pin",
                encoding="utf-8"
            )

        except Exception as exc:
            messagebox.showerror(
                "Pin 失敗",
                f"無法呼叫手把：\n{exc}"
            )

    def calibrate_gyro(self):
        """Run gyro, six-position accelerometer, then 3D magnetic calibration."""
        try:
            status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
            age = time.time() - float(status.get("updated_at", 0.0))
            if age > 3.0 or status.get("state") != "connected":
                raise RuntimeError("controller_not_connected")
        except (OSError, ValueError, TypeError, json.JSONDecodeError, RuntimeError):
            messagebox.showerror(
                "無法校正感測器",
                "請先啟動連接程式並確認手把已連線。",
            )
            return

        confirmed = messagebox.askokcancel(
            "感測器校正（1/3）",
            "請將手把平放在穩固且不會晃動的平面上。\n\n"
            "按下「確定」後請勿碰觸手把、按鍵或桌面，"
            "直到顯示校正完成。穩定採樣約需 5 秒；"
            "若偵測到移動，計時會重新開始。",
        )
        if not confirmed:
            return

        try:
            COMMAND_PATH.write_text("calibrate_gyro", encoding="utf-8")
        except OSError as exc:
            messagebox.showerror(
                "無法校正感測器",
                f"無法送出校正指令：\n{exc}",
            )
            return

        started_at = time.time()
        gyro_deadline = time.monotonic() + 15.0
        gyro_seen_running = False
        self.gyro_calibration_button.state(["disabled"])
        self.gyro_calibration_button.configure(text=self.tr("靜止校正中..."))

        def finish(success, message):
            try:
                self.gyro_calibration_button.state(["!disabled"])
                self.gyro_calibration_button.configure(
                    text=self.tr("感測器校正")
                )
                if success:
                    messagebox.showinfo("感測器校正完成", message)
                else:
                    messagebox.showerror("感測器校正失敗", message)
            except tk.TclError:
                pass

        def start_magnetometer_stage(accel_completed=True):
            completed_text = (
                "陀螺儀與加速度計校正已完成並儲存。"
                if accel_completed
                else "陀螺儀校正已完成；加速度計沿用原有資料。"
            )
            confirmed_mag = messagebox.askokcancel(
                "感測器校正（3/3）",
                completed_text + "\n\n"
                "請先遠離喇叭、磁鐵、大型金屬與通電設備。按下「確定」後，"
                "拿起手把並持續做立體 8 字翻轉：左右、前後及上下三個方向都要轉到。\n請至少讓 CMD 的磁力 3D 覆蓋達到 18/26。\n\n"
                "約需 12～30 秒。若取消，已完成的其他校正仍會保留，"
                "原有磁力計資料不會被修改。",
            )
            if not confirmed_mag:
                finish(
                    True,
                    "陀螺儀與加速度計校正已儲存；磁力計校正已略過，原資料保持不變。",
                )
                return
            try:
                COMMAND_PATH.write_text(
                    "calibrate_magnetometer", encoding="utf-8"
                )
            except OSError as exc:
                finish(False, f"無法送出磁力計校正指令：\n{exc}")
                return
            self.gyro_calibration_button.configure(
                text=f"{self.tr('立體翻轉')} 0%"
            )
            mag_started_at = time.time()
            mag_deadline = time.monotonic() + 35.0
            mag_seen_running = False

            def poll_magnetometer():
                nonlocal mag_seen_running
                if time.monotonic() >= mag_deadline:
                    finish(
                        False,
                        "磁力計校正逾時。原有磁力計資料未被修改，請遠離磁性物體後重試。",
                    )
                    return
                try:
                    data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
                    state = data.get("mag_calibration_state", "idle")
                    updated_at = float(data.get("updated_at", 0.0))
                    if state == "running" and updated_at >= mag_started_at:
                        mag_seen_running = True
                        progress = max(
                            0, min(100, int(data.get("mag_calibration_progress", 0)))
                        )
                        self.gyro_calibration_button.configure(
                            text=f"{self.tr('立體翻轉')} {progress}%"
                        )
                    elif state == "success" and mag_seen_running:
                        gyro_quality = data.get("gyro_calibration_quality") or {}
                        accel_quality = data.get("accel_calibration_quality") or {}
                        mag_quality = data.get("mag_calibration_quality") or {}
                        quality_lines = []
                        gyro_stddev = gyro_quality.get("stddev")
                        if isinstance(gyro_stddev, (list, tuple)) and gyro_stddev:
                            quality_lines.append(
                                "陀螺儀噪音："
                                f"{max(float(value) for value in gyro_stddev) / 14.285714:.3f} °/s"
                            )
                        if accel_completed and "rms_residual" in accel_quality:
                            quality_lines.append(
                                "加速度重力 RMS："
                                f"{float(accel_quality['rms_residual']) * 100.0:.2f}%"
                            )
                        if "rms_residual" in mag_quality:
                            quality_lines.append(
                                "磁力橢球殘差："
                                f"{float(mag_quality['rms_residual']) * 100.0:.2f}%"
                            )
                        if "coverage" in mag_quality:
                            quality_lines.append(
                                "磁力三維覆蓋："
                                f"{float(mag_quality['coverage']) * 100.0:.1f}%"
                            )
                        quality_summary = (
                            "\n\n品質結果：\n" + "\n".join(quality_lines)
                            if quality_lines else ""
                        )
                        completion_text = (
                            "陀螺儀、多姿態加速度計與磁力計完整橢球校正已依目前這支手把分開儲存，"
                            if accel_completed
                            else "陀螺儀與磁力計完整橢球校正已儲存；加速度計沿用原有資料，"
                        )
                        finish(
                            True,
                            completion_text
                            + "並已立即重設姿態融合；不需要重新連線。"
                            + quality_summary,
                        )
                        return
                    elif (
                        state == "failed"
                        and updated_at >= mag_started_at
                        and (mag_seen_running or data.get("mag_calibration_message") == "disconnected")
                    ):
                        reason = data.get("mag_calibration_message", "")
                        messages = {
                            "disconnected": "校正期間手把已斷線；原有磁力計資料未被修改。",
                            "save_failed": "校正完成，但無法安全寫入 config.ini。",
                            "insufficient_3d_coverage": (
                                "磁力方向覆蓋不足（至少 18/26）。請讓手把各面都朝向不同方向，"
                                "完整做立體 8 字翻轉後重試；原有資料未被修改。"
                            ),
                            "non_ellipsoidal_samples": (
                                "樣本無法形成有效的三維磁場橢球。請完整翻轉所有方向後重試。"
                            ),
                            "excessive_soft_iron_distortion": (
                                "偵測到磁場形狀嚴重失真。請遠離喇叭、磁鐵、金屬桌架或"
                                "通電設備後重試；原有資料未被修改。"
                            ),
                            "excessive_magnetic_outliers": "磁場變化不穩定或離群值過多，請更換位置後重試。",
                            "poor_ellipsoid_fit": "磁力計橢球擬合殘差過大，請更換位置並完整翻轉後重試。",
                        }
                        finish(False, messages.get(reason, "磁力計校正未完成。"))
                        return
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    pass
                self.root.after(250, poll_magnetometer)

            self.root.after(250, poll_magnetometer)

        def start_accelerometer_stage():
            confirmed_accel = messagebox.askokcancel(
                "感測器校正（2/3）",
                "接下來校正加速度計，約需 18～40 秒。\n\n"
                "請非常緩慢地讓手把朝向所有方向，並每隔一小段角度停住約半秒。"
                "不要快速甩動；程式只會收集低角速度、接近 1g 且方向不重複的樣本。\n\n"
                "務必包含正面、背面、左右側、USB 端及握把端，並加入一些斜向姿勢；至少讓 CMD 的加速 3D 覆蓋達到 14/26。"
                "若取消，會保留原有資料並直接進入磁力計校正。",
            )
            if not confirmed_accel:
                start_magnetometer_stage(False)
                return
            try:
                COMMAND_PATH.write_text(
                    "calibrate_accelerometer", encoding="utf-8"
                )
            except OSError as exc:
                finish(False, f"無法送出加速度計校正指令：\n{exc}")
                return
            accel_started_at = time.time()
            accel_deadline = time.monotonic() + 45.0
            accel_seen_running = False
            self.gyro_calibration_button.configure(
                text=f"{self.tr('加速度')} 0%"
            )

            def poll_accelerometer():
                nonlocal accel_seen_running
                if time.monotonic() >= accel_deadline:
                    finish(False, "加速度計多姿態採樣逾時，請更慢地涵蓋所有方向後重試。")
                    return
                try:
                    data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
                    state = data.get("accel_calibration_state", "idle")
                    message = data.get("accel_calibration_message", "")
                    updated_at = float(data.get("updated_at", 0.0))
                    if state == "running" and updated_at >= accel_started_at:
                        accel_seen_running = True
                        progress = max(0, min(100, int(
                            data.get("accel_calibration_progress", 0)
                        )))
                        self.gyro_calibration_button.configure(
                            text=f"{self.tr('加速度')} {progress}%"
                        )
                    elif state == "success" and accel_seen_running:
                        if message != "saved":
                            self.root.after(100, poll_accelerometer)
                            return
                        start_magnetometer_stage()
                        return
                    elif (
                        state == "failed"
                        and updated_at >= accel_started_at
                        and (accel_seen_running or message == "disconnected")
                    ):
                        messages = {
                            "disconnected": "校正期間手把已斷線。",
                            "save_failed": "加速度計校正完成，但無法安全寫入 config.ini。",
                            "insufficient_accelerometer_coverage": "加速度計方向覆蓋不足（至少 14/26），請包含六個主要方向及更多斜向姿勢。",
                            "excessive_accelerometer_distortion": "加速度資料失真過大，請避免快速移動並重新校正。",
                            "poor_accelerometer_fit": "重力橢球殘差過大，請放慢動作並在更多方向短暫停住。",
                            "excessive_magnetic_outliers": "有效重力樣本不足或離群值過多，請放慢動作後重試。",
                        }
                        finish(False, messages.get(message, "加速度計校正未完成。"))
                        return
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    pass
                self.root.after(250, poll_accelerometer)

            self.root.after(250, poll_accelerometer)

        def poll_gyro():
            nonlocal gyro_seen_running
            if time.monotonic() >= gyro_deadline:
                finish(
                    False,
                    "校正逾時。請確認手把保持連線並放在穩固平面後重試。",
                )
                return
            try:
                data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
                state = data.get("gyro_calibration_state", "idle")
                updated_at = float(data.get("updated_at", 0.0))
                if state == "running" and updated_at >= started_at:
                    gyro_seen_running = True
                elif state == "success" and gyro_seen_running:
                    start_accelerometer_stage()
                    return
                elif (
                    state == "failed"
                    and updated_at >= started_at
                    and (gyro_seen_running or data.get("gyro_calibration_message") == "disconnected")
                ):
                    reason = data.get("gyro_calibration_message", "")
                    messages = {
                        "disconnected": "校正期間手把已斷線，請重新連線後再試。",
                        "save_failed": "校正完成，但無法安全寫入 config.ini。",
                        "unstable": "三軸數值變動過大。請勿碰觸手把或桌面後重試。",
                        "timeout_or_movement": "持續偵測到移動，無法取得五秒穩定資料。",
                    }
                    finish(False, messages.get(reason, "靜止陀螺儀校正未完成。"))
                    return
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
            self.root.after(250, poll_gyro)

        self.root.after(250, poll_gyro)

    def reset_tilt_neutral(self):
        """Reset the live Tilt neutral pose without changing gyro calibration."""
        try:
            status = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
            age = time.time() - float(status.get("updated_at", 0.0))
            if age > 3.0 or status.get("state") != "connected":
                raise RuntimeError("controller_not_connected")
            requested_at = time.time()
            COMMAND_PATH.write_text("reset_tilt_neutral", encoding="utf-8")
        except (OSError, ValueError, TypeError, json.JSONDecodeError, RuntimeError):
            messagebox.showerror(
                "無法重設中立",
                "請先啟動連接程式並確認手把已連線。",
            )
            return

        deadline = time.monotonic() + 3.0
        self.gyro_tilt_recenter_button.state(["disabled"])

        def finish(success, message):
            try:
                if self.gyro_motion_mode_var.get() == "TILT":
                    self.gyro_tilt_recenter_button.state(["!disabled"])
                if success:
                    messagebox.showinfo("中立角度已重設", message)
                else:
                    messagebox.showerror("無法重設中立", message)
            except tk.TclError:
                pass

        def poll_result():
            if time.monotonic() >= deadline:
                finish(False, "連接程式沒有回覆重設指令，請確認程式仍在執行。")
                return
            try:
                data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))
                completed_at = float(data.get("tilt_recenter_updated_at", 0.0))
                if completed_at >= requested_at:
                    if data.get("tilt_recenter_state") == "success":
                        finish(
                            True,
                            "已重設；若傾斜映射尚未啟用，將在下次啟用時取得中立角度。",
                        )
                    else:
                        finish(
                            False,
                            "連接程式目前未使用傾斜模式。請先儲存設定並重新啟動連接程式。",
                        )
                    return
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                pass
            self.root.after(100, poll_result)

        self.root.after(100, poll_result)

    def add_entry(
        self,
        parent,
        row,
        label,
        variable,
        help_text
    ):
        label_widget = ttk.Label(
            parent,
            text=label
        )
        label_widget.grid(
            row=row,
            column=0,
            sticky="e",
            pady=2
        )

        entry = ttk.Entry(
            parent,
            textvariable=variable,
            width=12
        )
        entry.grid(
            row=row,
            column=1,
            padx=12,
            pady=2
        )

        help_label = self.create_help(
            parent,
            help_text
        )
        help_label.grid(
            row=row,
            column=2,
            padx=(8, 0),
            pady=2,
            sticky="w"
        )

        return {
            "label": label_widget,
            "entry": entry,
            "help": help_label,
            "row": row,
        }

    @staticmethod
    def _format_gyro_button_summary(buttons):
        if not buttons:
            return "NONE"
        if len(buttons) <= 2:
            return ", ".join(buttons)
        return f"{buttons[0]}, {buttons[1]} +{len(buttons) - 2}"

    def _refresh_gyro_button_summaries(self):
        self.gyro_activation_buttons_summary_var.set(
            self._format_gyro_button_summary(
                self.gyro_activation_buttons
            )
        )
        self.gyro_stabilization_buttons_summary_var.set(
            self._format_gyro_button_summary(
                self.gyro_stabilization_buttons
            )
        )

    def open_gyro_curve_window(self):
        """Edit the Center-stick gyro response curve in a separate window."""
        window = tk.Toplevel(self.root)
        window.title(self.tr("陀螺儀感度曲線"))
        window.resizable(False, False)
        window.transient(self.root)

        content = ttk.Frame(window, padding=12)
        content.pack(fill="both", expand=True)
        canvas = tk.Canvas(
            content,
            width=320,
            height=240,
            background="#FFFFFF",
            highlightthickness=1,
            highlightbackground="#A0A0A0",
        )
        canvas.grid(row=0, column=0, columnspan=4)

        mode_var = tk.StringVar(value=self.gyro_response_curve_var.get())
        try:
            initial_strength = float(self.gyro_curve_strength_var.get())
        except (ValueError, tk.TclError):
            initial_strength = 5.0
        strength_var = tk.DoubleVar(
            value=max(0.0, min(10.0, initial_strength))
        )
        strength_text = tk.StringVar()

        def curve_value(value):
            if mode_var.get() == "LINEAR":
                return value
            blend = strength_var.get() / 10.0
            smooth = value * value * (3.0 - 2.0 * value)
            delta = smooth - value
            if mode_var.get() == "EARLY":
                delta = -delta
            return value + delta * blend

        def draw_curve(*_args):
            canvas.delete("all")
            left, top, right, bottom = 38, 18, 304, 190
            for step in range(5):
                ratio = step / 4.0
                x = left + (right - left) * ratio
                y = bottom - (bottom - top) * ratio
                canvas.create_line(x, top, x, bottom, fill="#E4E4E4")
                canvas.create_line(left, y, right, y, fill="#E4E4E4")
                canvas.create_text(
                    x, bottom + 13, text=f"{int(ratio * 100)}%",
                    fill="#666666", font=("Segoe UI", 8)
                )
            canvas.create_line(
                left, bottom, right, top,
                fill="#A8A8A8", dash=(4, 3), width=1
            )
            points = []
            for index in range(101):
                value = index / 100.0
                mapped = curve_value(value)
                points.extend((
                    left + (right - left) * value,
                    bottom - (bottom - top) * mapped,
                ))
            canvas.create_line(
                *points, fill="#1976D2", width=3, smooth=True
            )
            canvas.create_text(
                (left + right) / 2, 226,
                text=self.tr("基礎輸出"), fill="#444444"
            )
            canvas.create_text(
                12, (top + bottom) / 2,
                text=self.tr("曲線輸出"), fill="#444444", angle=90
            )
            strength_text.set(f"{strength_var.get():.0f}")

        mode_row = ttk.Frame(content)
        mode_row.grid(
            row=1, column=0, columnspan=4, sticky="ew", pady=(10, 4)
        )
        ttk.Label(mode_row, text=self.tr("模式")).place(
            relx=0.0, rely=0.5, anchor="w"
        )
        mode_controls = ttk.Frame(mode_row)
        mode_controls.pack(anchor="center")
        ttk.Radiobutton(
            mode_controls,
            text=self.tr("線性"),
            value="LINEAR",
            variable=mode_var,
            command=draw_curve,
        ).pack(side="left", padx=(0, 10))
        ttk.Radiobutton(
            mode_controls,
            text=self.tr("後段加速"),
            value="LATE",
            variable=mode_var,
            command=draw_curve,
        ).pack(side="left", padx=(0, 10))
        ttk.Radiobutton(
            mode_controls,
            text=self.tr("前段加速"),
            value="EARLY",
            variable=mode_var,
            command=draw_curve,
        ).pack(side="left")
        self.create_help(
            mode_row,
            "線性：維持原始輸出。\n\n"
            "後段加速：壓低慢速區、提高快速區，適合精細修正後快速轉向。\n\n"
            "前段加速：提高慢速區、壓縮後段，適合覺得中心反應不足時使用。\n\n"
            "僅作用於回中模式的搖桿輸出。"
        ).place(relx=1.0, rely=0.5, anchor="e")

        strength_row = ttk.Frame(content)
        strength_row.grid(
            row=2, column=0, columnspan=4, sticky="ew", pady=4
        )
        ttk.Label(strength_row, text=self.tr("強度")).place(
            relx=0.0, rely=0.5, anchor="w"
        )
        strength_controls = ttk.Frame(strength_row)
        strength_controls.pack(anchor="center")
        ttk.Scale(
            strength_controls,
            from_=0.0,
            to=10.0,
            variable=strength_var,
            orient="horizontal",
            length=210,
            command=draw_curve,
        ).pack(side="left", padx=(0, 8))
        ttk.Label(
            strength_controls, textvariable=strength_text, width=3, anchor="w"
        ).pack(side="left")
        self.create_help(
            strength_row,
            "控制前段／後段曲線偏離線性的程度。\n\n"
            "0：等同線性。\n5：中等效果。\n10：最大效果。\n\n"
            "不會改變 0% 與 100% 終點；線性模式下此設定不生效。"
        ).place(relx=1.0, rely=0.5, anchor="e")
        action_row = ttk.Frame(content)
        action_row.grid(
            row=3, column=0, columnspan=4, sticky="ew", pady=(10, 0)
        )

        def reset_curve():
            mode_var.set("LINEAR")
            strength_var.set(5.0)
            draw_curve()

        def accept_curve():
            self.gyro_response_curve_var.set(mode_var.get())
            self.gyro_curve_strength_var.set(
                f"{max(0.0, min(10.0, strength_var.get())):.0f}"
            )
            window.destroy()

        ttk.Button(
            action_row,
            text=self.tr("還原曲線"),
            command=reset_curve,
        ).pack(side="left")
        ttk.Button(
            action_row, text=self.tr("取消"), command=window.destroy
        ).pack(side="right")
        ttk.Button(
            action_row, text=self.tr("確定"), command=accept_curve
        ).pack(side="right", padx=(0, 6))

        draw_curve()
        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.update_idletasks()
        x = self.root.winfo_rootx() + max(
            0, (self.root.winfo_width() - window.winfo_width()) // 2
        )
        y = self.root.winfo_rooty() + max(
            0, (self.root.winfo_height() - window.winfo_height()) // 2
        )
        window.geometry(f"+{x}+{y}")
        window.grab_set()
        window.focus_force()
    def open_gyro_button_selector(self, selector_type):
        """Open a compact checklist for gyro activation or freeze buttons."""
        is_activation = selector_type == "activation"
        selected = set(
            self.gyro_activation_buttons
            if is_activation else self.gyro_stabilization_buttons
        )
        options = (
            self.gyro_activation_button_options
            if is_activation else self.gyro_stabilization_button_options
        )

        window = tk.Toplevel(self.root)
        window.title(self.tr(
            "選擇啟動按鍵" if is_activation else "選擇額外按鍵"
        ))
        window.resizable(False, False)
        window.transient(self.root)

        content = ttk.Frame(window, padding=12)
        content.pack(fill="both", expand=True)
        ttk.Label(
            content,
            text=self.tr("Switch 按鍵配置"),
            foreground="#666666",
        ).grid(
            row=0, column=0, columnspan=4,
            sticky="w", padx=7, pady=(0, 6)
        )
        button_vars = {}
        for index, button_name in enumerate(options):
            variable = tk.BooleanVar(value=button_name in selected)
            button_vars[button_name] = variable
            ttk.Checkbutton(
                content, text=button_name, variable=variable
            ).grid(
                row=1 + index // 4,
                column=index % 4,
                sticky="w",
                padx=7,
                pady=3,
            )

        next_row = 1 + (len(options) + 3) // 4
        match_var = tk.StringVar(
            value=self.gyro_activation_match_var.get()
        )
        if is_activation:
            match_frame = ttk.LabelFrame(
                content, text=self.tr("觸發條件"), padding=(8, 5)
            )
            match_frame.grid(
                row=next_row, column=0, columnspan=4,
                sticky="ew", pady=(8, 4)
            )
            ttk.Radiobutton(
                match_frame,
                text=self.tr("任一按鍵"),
                value="ANY",
                variable=match_var,
            ).pack(side="left", padx=(2, 10))
            ttk.Radiobutton(
                match_frame,
                text=self.tr("全部同時按下"),
                value="ALL",
                variable=match_var,
            ).pack(side="left", padx=2)
            next_row += 1

        action_row = ttk.Frame(content)
        action_row.grid(
            row=next_row, column=0, columnspan=4,
            sticky="ew", pady=(8, 0)
        )

        def clear_selection():
            for variable in button_vars.values():
                variable.set(False)

        def accept_selection():
            chosen = [
                name for name in options if button_vars[name].get()
            ]
            if is_activation and not chosen:
                messagebox.showwarning(
                    self.tr("選擇啟動按鍵"),
                    self.tr("至少選擇一個啟動按鍵。"),
                    parent=window,
                )
                return
            if is_activation:
                self.gyro_activation_buttons = chosen
                self.gyro_activation_match_var.set(match_var.get())
            else:
                self.gyro_stabilization_buttons = chosen
            self._refresh_gyro_button_summaries()
            window.destroy()

        ttk.Button(
            action_row, text=self.tr("清除"), command=clear_selection
        ).pack(side="left")
        ttk.Button(
            action_row, text=self.tr("取消"), command=window.destroy
        ).pack(side="right")
        ttk.Button(
            action_row, text=self.tr("確定"), command=accept_selection
        ).pack(side="right", padx=(0, 6))

        window.protocol("WM_DELETE_WINDOW", window.destroy)
        window.update_idletasks()
        x = self.root.winfo_rootx() + max(
            0, (self.root.winfo_width() - window.winfo_width()) // 2
        )
        y = self.root.winfo_rooty() + max(
            0, (self.root.winfo_height() - window.winfo_height()) // 2
        )
        window.geometry(f"+{x}+{y}")
        window.grab_set()
        window.focus_force()
    def tr(self, text):
        local_translations = {
            "還原所有音訊震動設置": (
                "Reset All Audio Haptics Settings"
            ),
            "還原陀螺儀預設": "Reset Gyro Defaults",
        }
        if self.language == "en" and text in local_translations:
            return local_translations[text]
        return translate_text(text, self.language)

    def toggle_language(self):
        """切換中英文並立即保存 GUI 語言偏好。"""
        self.language = "en" if self.language == "zh" else "zh"
        global _GUI_LANGUAGE
        _GUI_LANGUAGE = self.language

        if not self.config.has_section("gui"):
            self.config.add_section("gui")
        self.config.set("gui", "language", self.language)
        try:
            atomic_write_config(self.config, CONFIG_PATH)
        except OSError as exc:
            messagebox.showerror(
                "儲存失敗",
                f"無法安全寫入 config.ini。\n\n錯誤資訊：{exc}"
            )

        self.apply_language()
        self.root.after_idle(self.update_adaptive_window)

    def apply_language(self):
        """更新現有元件、頁籤、狀態文字與提示語言。"""
        def update_widget(widget):
            try:
                if "text" in widget.keys():
                    if not hasattr(widget, "_s2p_zh_text"):
                        widget._s2p_zh_text = widget.cget("text")
                    widget.configure(text=self.tr(widget._s2p_zh_text))
            except (tk.TclError, AttributeError, TypeError):
                pass

            if isinstance(widget, ttk.Notebook):
                if not hasattr(widget, "_s2p_zh_tabs"):
                    widget._s2p_zh_tabs = {
                        tab_id: widget.tab(tab_id, "text")
                        for tab_id in widget.tabs()
                    }
                for tab_id, source_text in widget._s2p_zh_tabs.items():
                    widget.tab(tab_id, text=self.tr(source_text))

            for child in widget.winfo_children():
                update_widget(child)

        update_widget(self.root)
        for combo, display_var, side, source_values in (
            self.stick_mode_selectors
        ):
            combo.configure(
                values=tuple(self.tr(value) for value in source_values)
            )
            display_var.set(
                self.tr(self.stick_direction_mode_vars[side].get())
            )
        self._refresh_gyro_button_summaries()
        self.curve_zoom_button.configure(
            text=self.tr(
                "返回" if self.stick_settings_zoomed else "放大"
            )
        )
        self.update_driver_status()
        self.update_controller_status(schedule_next=False)


    def open_keyboard_capture(self, variable, mouse_mode=None):
        """錄製鍵盤組合，並依映射來源接受滑鼠按鍵或滾輪。"""

        capture_window = tk.Toplevel(
            self.root
        )

        capture_window.title(
            self.tr("自定義輸入映射")
        )

        capture_window.resizable(
            False,
            False
        )

        capture_window.transient(
            self.root
        )

        capture_window.attributes("-topmost", True)

        capture_window.grab_set()

        if mouse_mode == "buttons":
            prompt = (
                "請按下要映射的鍵盤按鍵、複合按鍵或滑鼠按鍵。\n\n"
                "可使用滑鼠左鍵、右鍵及中鍵（滾輪按下）。"
            )
        elif mouse_mode == "wheel":
            prompt = (
                "請按下要映射的鍵盤按鍵或複合按鍵，\n"
                "也可以向上或向下滾動滑鼠滾輪。"
            )
        else:
            prompt = (
                "請按下要映射的鍵盤按鍵或複合按鍵。\n\n"
                "例如：F12、Ctrl + S、Ctrl + Shift + S"
            )

        ttk.Label(
            capture_window,
            text=self.tr(prompt),
            justify="center"
        ).pack(
            padx=25,
            pady=(20, 12)
        )

        result_var = tk.StringVar(
            value=self.tr("等待按鍵...")
        )

        result_label = ttk.Label(
            capture_window,
            textvariable=result_var,
            font=("", 12, "bold")
        )

        result_label.pack(
            padx=20,
            pady=10
        )

        pressed_keys = set()

        # Tkinter 按鍵名稱轉換成我們底層使用的名稱
        key_name_map = {
            "CONTROL_L": "CTRL",
            "CONTROL_R": "CTRL",
            "SHIFT_L": "SHIFT",
            "SHIFT_R": "SHIFT",
            "ALT_L": "ALT",
            "ALT_R": "ALT",
            "ESCAPE": "ESC",
            "RETURN": "ENTER",
            "PRIOR": "PAGEUP",
            "NEXT": "PAGEDOWN",
        }

        modifier_order = [
            "CTRL",
            "SHIFT",
            "ALT",
            "WIN",
        ]

        def normalize_key(event):
            key = event.keysym.upper()

            return key_name_map.get(
                key,
                key
            )

        def update_display():
            ordered_keys = []

            # 修飾鍵固定排序
            for key in modifier_order:
                if key in pressed_keys:
                    ordered_keys.append(
                        key
                    )

            # 其他按鍵放後面
            for key in pressed_keys:
                if key not in modifier_order:
                    ordered_keys.append(
                        key
                    )

            if ordered_keys:
                result_var.set(
                    " + ".join(
                        ordered_keys
                    )
                )

            return ordered_keys

        def on_key_press(event):
            key = normalize_key(
                event
            )

            pressed_keys.add(
                key
            )

            update_display()

            # 阻止按鍵操作到其他 GUI 元件
            return "break"

        def on_key_release(event):
            commit_keyboard_mapping()

            return "break"

        def commit_keyboard_mapping():
            ordered_keys = update_display()
            if not ordered_keys:
                return False
            variable.set("KEYBOARD:" + "+".join(ordered_keys))
            capture_window.destroy()
            return True

        def on_mouse_button(event):
            if mouse_mode != "buttons":
                return None
            action = {
                1: "LEFT",
                2: "MIDDLE",
                3: "RIGHT",
            }.get(event.num)
            if action is None:
                return None
            variable.set("MOUSE:" + action)
            capture_window.destroy()
            return "break"

        def on_mouse_wheel(event):
            if mouse_mode != "wheel" or event.delta == 0:
                return None
            variable.set(
                "MOUSE:WHEEL_UP" if event.delta > 0
                else "MOUSE:WHEEL_DOWN"
            )
            capture_window.destroy()
            return "break"

        captured_events = queue.SimpleQueue()
        suppressed_capture = _SuppressedKeyboardCapture(captured_events)
        hook_active = suppressed_capture.start()

        def poll_captured_keys():
            if not capture_window.winfo_exists():
                return
            while True:
                try:
                    is_pressed, vk_code = captured_events.get_nowait()
                except queue.Empty:
                    break
                key = _capture_name_from_vk(vk_code)
                if key is None:
                    continue
                if is_pressed:
                    pressed_keys.add(key)
                    update_display()
                elif commit_keyboard_mapping():
                    return
            capture_window.after(10, poll_captured_keys)

        def cleanup_keyboard_capture(event=None):
            if event is not None and event.widget is not capture_window:
                return
            suppressed_capture.stop()

        capture_window.bind("<Destroy>", cleanup_keyboard_capture, add="+")
        if hook_active:
            capture_window.after(10, poll_captured_keys)
        else:
            # Keep the original local capture as a safe fallback if Windows
            # refuses to install a low-level hook.
            capture_window.bind("<KeyPress>", on_key_press)
            capture_window.bind("<KeyRelease>", on_key_release)
        capture_window.bind("<ButtonPress-1>", on_mouse_button)
        capture_window.bind("<ButtonPress-2>", on_mouse_button)
        capture_window.bind("<ButtonPress-3>", on_mouse_button)
        capture_window.bind("<MouseWheel>", on_mouse_wheel)

        # 等待 Tkinter 計算完成視窗大小
        capture_window.update_idletasks()

        # 將錄製視窗置中於主 GUI
        parent_x = self.root.winfo_rootx()
        parent_y = self.root.winfo_rooty()
        parent_width = self.root.winfo_width()
        parent_height = self.root.winfo_height()

        window_width = capture_window.winfo_reqwidth()
        window_height = capture_window.winfo_reqheight()

        x = (
            parent_x
            + (parent_width - window_width) // 2
        )

        y = (
            parent_y
            + (parent_height - window_height) // 2
        )

        capture_window.geometry(
            f"+{x}+{y}"
        )

        capture_window.focus_force()

    def create_help(self, parent, text, illustration=None):
        label = tk.Label(
            parent,
            text="?",
            width=2,
            relief="solid",
            borderwidth=1,
            cursor="question_arrow"
        )

        ToolTip(
            label,
            text,
            translator=self.tr,
            illustration=illustration,
        )

        return label

    def reset_button_mapping(self):
        """只還原 Switch 2 Pro 到 XInput 的按鍵映射。"""
        default_buttons = {
            "Y": "X",
            "X": "Y",
            "B": "A",
            "A": "B",
            "R": "RB",
            "ZR": "RT",
            "L": "LB",
            "ZL": "LT",
            "MINUS": "BACK",
            "PLUS": "START",
            "L_STK": "L_STK",
            "R_STK": "R_STK",
            "HOME": "GUIDE",
            "CAPT": "NONE",
            "C": "NONE",
            "UP": "UP",
            "DOWN": "DOWN",
            "LEFT": "LEFT",
            "RIGHT": "RIGHT",
            "GR": "NONE",
            "GL": "NONE",
        }
        for button_name, default_value in default_buttons.items():
            variable = self.button_vars.get(button_name)
            if variable is not None:
                variable.set(default_value)

    def reset_stick_direction_mapping(self, side):
        """還原指定一側的方向映射與模式專用參數。"""
        side = str(side).upper()
        if side not in ("LEFT", "RIGHT"):
            return

        self.stick_direction_mode_vars[side].set("4WAY")
        for variable in self.stick_direction_vars[side].values():
            variable.set("NONE")
        self.stick_direction_deadzone_vars[side].set("5")
        self.stick_direction_trigger_vars[side].set("0.60")
        self.stick_direction_release_vars[side].set("0.50")
        self.stick_mouse_speed_vars[side].set("900")

        updater = self.stick_direction_mode_updaters.get(side)
        if updater is not None:
            updater()

    def reset_audio_haptics(self):
        """只還原音訊震動頁面的設定。"""
        self.audio_haptics_mode_var.set("GAME")
        self.audio_haptics_mix_ratio_var.set("0.35")
        self.audio_haptics_strength_var.set("0.60")
        for variable in self.audio_haptics_band_gain_vars:
            variable.set("1.00")
        self.audio_haptics_noise_gate_var.set("0.015")
        self.audio_haptics_attack_var.set("1")
        self.audio_haptics_release_var.set("140")
        self.audio_haptics_final_tail_strength_var.set("0.00")
        self.audio_haptics_final_tail_decay_var.set("350")

    def reset_gyro_mapping(self):
        """只還原陀螺儀映射頁面的設定。"""
        self.gyro_activation_mode_var.set("OFF")
        self.gyro_activation_buttons = ["ZL"]
        self.gyro_activation_match_var.set("ANY")
        self.gyro_target_var.set("RIGHT_STICK")
        self.gyro_motion_mode_var.set("CENTER")
        self.gyro_tilt_axis_var.set("HORIZONTAL")
        self.gyro_tilt_recenter_button_var.set("NONE")
        self.gyro_tilt_max_angle_var.set("35")
        self.gyro_tilt_deadzone_var.set("0.80")
        self.gyro_tilt_smoothing_var.set("30")
        self.gyro_stick_sensitivity_var.set("1.50")
        self.gyro_mouse_sensitivity_var.set("8.00")
        self.gyro_x_ratio_var.set("1.00")
        self.gyro_y_ratio_var.set("1.00")
        self.gyro_deadzone_var.set("0.60")
        self.gyro_stick_anti_deadzone_var.set("0")
        self.gyro_response_curve_var.set("LINEAR")
        self.gyro_curve_strength_var.set("5")
        self.gyro_smoothing_var.set("15")
        self.gyro_accel_suppression_var.set("70")
        self.gyro_adaptive_deadzone_var.set("85")
        self.gyro_button_freeze_var.set("35")
        self.gyro_stabilization_buttons = []
        self.gyro_invert_x_var.set(False)
        self.gyro_invert_y_var.set(False)
        self.gyro_player_space_var.set(True)
        self._refresh_gyro_button_summaries()

    def reset_to_defaults(self):
        """將可調設定恢復為預設值，不修改搖桿校正資料。"""

        confirmed = messagebox.askyesno(
            "還原預設",
            "確定要將所有可調設定恢復為預設值嗎？\n\n"
            "搖桿校正資料不會被修改。"
        )

        if not confirmed:
            return

        # =========================
        # 左右搖桿死區恢復預設
        # =========================

        # 左搖桿
        self.left_deadzone_var.set(
            "0.03"
        )

        self.left_outer_deadzone_var.set(
            "0.03"
        )

        self.left_deadzone_compress_var.set(
            True
        )

        self.left_outer_deadzone_compress_var.set(
            True
        )
        self.left_output_shape_var.set(0)
        self.left_interpolation_var.set("LINEAR")

        # 右搖桿
        self.right_deadzone_var.set(
            "0.03"
        )

        self.right_outer_deadzone_var.set(
            "0.03"
        )

        self.right_deadzone_compress_var.set(
            True
        )

        self.right_outer_deadzone_compress_var.set(
            True
        )
        self.right_output_shape_var.set(0)
        self.right_interpolation_var.set("LINEAR")

        # 左右搖桿 XY 曲線恢復為 1:1 線性
        default_curve = [
            0.00,
            0.25,
            0.50,
            0.75,
            1.00
        ]

        for i, value in enumerate(
            default_curve
        ):
            # 左搖桿
            self.left_curve_vars[i][
                "x"
            ].set(
                value
            )

            self.left_curve_vars[i][
                "y"
            ].set(
                value
            )

            # 右搖桿
            self.right_curve_vars[i][
                "x"
            ].set(
                value
            )

            self.right_curve_vars[i][
                "y"
            ].set(
                value
            )

        # 立即重畫曲線
        self.left_curve_editor.draw()
        self.right_curve_editor.draw()

        # 左右搖桿防抖恢復為 0
        self.left_stick_smoothing_var.set(
            0
        )

        self.right_stick_smoothing_var.set(
            0
        )
        # 震動設定
        self.lf_strength_var.set("1.00")
        self.hf_strength_var.set("1.00")
        self.lf_curve_var.set("1.00")
        self.hf_curve_var.set("1.00")
        self.lf_to_hf_compensation_var.set("0.00")
        self.hf_to_lf_compensation_var.set("0.00")
        self.lf_frequency_var.set("225")
        self.hf_frequency_var.set("481")
        self.max_amplitude_var.set("800")        

        # 音訊震動
        self.audio_haptics_mode_var.set("GAME")
        self.audio_haptics_mix_ratio_var.set("0.35")
        self.audio_haptics_strength_var.set("0.60")
        for variable in self.audio_haptics_band_gain_vars:
            variable.set("1.00")
        self.audio_haptics_noise_gate_var.set("0.015")
        self.audio_haptics_attack_var.set("1")
        self.audio_haptics_release_var.set("140")
        self.audio_haptics_final_tail_strength_var.set("0.00")
        self.audio_haptics_final_tail_decay_var.set("350")

        # 陀螺儀映射
        self.reset_gyro_mapping()

        # 按鍵映射恢復預設
        default_buttons = {
            "Y": "X",
            "X": "Y",
            "B": "A",
            "A": "B",
            "R": "RB",
            "ZR": "RT",
            "L": "LB",
            "ZL": "LT",
            "MINUS": "BACK",
            "PLUS": "START",
            "L_STK": "L_STK",
            "R_STK": "R_STK",
            "HOME": "GUIDE",
            "CAPT": "NONE",
            "C": "NONE",
            "UP": "UP",
            "DOWN": "DOWN",
            "LEFT": "LEFT",
            "RIGHT": "RIGHT",
            "GR": "NONE",
            "GL": "NONE",
        }

        for button_name, default_value in default_buttons.items():
            if button_name in self.button_vars:
                self.button_vars[button_name].set(
                    default_value
                )

        # =========================
        # 搖桿方向映射恢復預設
        # =========================

        # 左右搖桿
        for side in (
            "LEFT",
            "RIGHT"
        ):
            # 模式恢復為 4WAY
            self.stick_direction_mode_vars[
                side
            ].set(
                "4WAY"
            )
            self.stick_direction_deadzone_vars[side].set("5")
            self.stick_direction_trigger_vars[side].set("0.60")
            self.stick_direction_release_vars[side].set("0.50")
            self.stick_mouse_speed_vars[side].set("900")

            # 所有方向映射恢復為 NONE
            for variable in (
                self.stick_direction_vars[
                    side
                ].values()
            ):
                variable.set(
                    "NONE"
                )

            # 立即刷新 4WAY / 8WAY 顯示狀態
            updater = (
                self.stick_direction_mode_updaters.get(
                    side
                )
            )

            if updater is not None:
                updater()


        messagebox.showinfo(
            "還原完成",
            "可調設定已恢復為預設值。\n\n"
            "請按「儲存設定」套用變更。"
        )


    def run_calibration(self):
        calibration_path = Path(__file__).with_name("calibration.py")

        if not calibration_path.exists():
            messagebox.showerror(
                "錯誤",
                "找不到 calibration.py。"
            )
            return

        if (
            self.calibration_process is not None
            and self.calibration_process.poll() is None
        ):
            messagebox.showinfo(
                "校正已啟動",
                "搖桿校正程式已經在執行。"
            )
            return

        # 校正與主連接程式不能同時占用 ESP32 COM Port
        # 或同一支藍牙手把。先正常停止主程式，再稍後啟動。
        if (
            self.main_process is not None
            and self.main_process.poll() is None
        ):
            self.stop_main_process()
            self.root.after(
                500,
                lambda: self._start_calibration_process(
                    calibration_path
                )
            )
            return

        self._start_calibration_process(calibration_path)

    def _start_calibration_process(self, calibration_path):
        try:
            child_env = os.environ.copy()
            child_env["PYTHONUTF8"] = "1"
            self.calibration_process = subprocess.Popen(
                [
                    str(PYTHON_EXE),
                    str(calibration_path)
                ],
                cwd=str(calibration_path.parent),
                env=child_env,
                creationflags=getattr(
                    subprocess,
                    "CREATE_NEW_CONSOLE",
                    0
                )
            )
        except Exception as exc:
            messagebox.showerror(
                "錯誤",
                f"無法啟動校正程序：\n{exc}"
            )

    def stop_main_process(self):
        """先正常關閉主連接程式，失敗時再強制終止。"""

        if (
            self.main_process is None
            or self.main_process.poll() is not None
        ):
            self.main_process = None
            return

        try:
            # 先發送 CTRL_BREAK_EVENT，
            # 讓 main.py 有機會執行 finally：
            # 1. 釋放所有鍵盤按鍵
            # 2. 關閉 ESP32 連接
            self.main_process.send_signal(
                signal.CTRL_BREAK_EVENT
            )

            # 最多等待 2 秒正常退出
            self.main_process.wait(
                timeout=2
            )

        except subprocess.TimeoutExpired:
            # 2 秒後仍未退出，才強制終止整個程序樹
            subprocess.run(
                [
                    "taskkill",
                    "/PID",
                    str(self.main_process.pid),
                    "/T",
                    "/F"
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=getattr(
                    subprocess,
                    "CREATE_NO_WINDOW",
                    0
                )
            )

        except Exception:
            # 發送正常關閉訊號失敗時，
            # 使用原本的強制終止方式
            try:
                subprocess.run(
                    [
                        "taskkill",
                        "/PID",
                        str(self.main_process.pid),
                        "/T",
                        "/F"
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(
                        subprocess,
                        "CREATE_NO_WINDOW",
                        0
                    )
                )
            except Exception:
                pass

        self.main_process = None

    def start_main(self, main_path):
        try:
            self.main_process = subprocess.Popen(
                [
                    str(PYTHON_EXE),
                    str(main_path)
                ],
                cwd=str(main_path.parent),
                creationflags=(
                    getattr(
                        subprocess,
                        "CREATE_NEW_CONSOLE",
                        0
                    )
                    |
                    getattr(
                        subprocess,
                        "CREATE_NEW_PROCESS_GROUP",
                        0
                    )
                )
            )

        except Exception as exc:
            messagebox.showerror(
                "錯誤",
                f"無法啟動連接程式：\n{exc}"
            )

    def start_connection_on_launch(self):
        """啟動 GUI 時立即探測傳輸模式並刷新狀態檔。"""
        if not self.vigembus_installed:
            return
        if (
            self.main_process is not None
            and self.main_process.poll() is None
        ):
            return

        main_path = Path(__file__).with_name("main.py")
        if main_path.exists():
            self.start_main(main_path)

    def has_unsaved_changes(self):
        """檢查 GUI 目前設定是否與已載入的 config.ini 不同。"""

        # =========================
        # 左右搖桿死區設定
        # =========================

        try:
            # 左搖桿
            current_left_deadzone = float(
                self.left_deadzone_var.get()
            )

            saved_left_deadzone = (
                self.config.getfloat(
                    "stick_curve_left",
                    "deadzone",
                    fallback=0.03
                )
            )

            current_left_outer_deadzone = float(
                self.left_outer_deadzone_var.get()
            )

            saved_left_outer_deadzone = (
                self.config.getfloat(
                    "stick_curve_left",
                    "outer_deadzone",
                    fallback=0.03
                )
            )

            # 右搖桿
            current_right_deadzone = float(
                self.right_deadzone_var.get()
            )

            saved_right_deadzone = (
                self.config.getfloat(
                    "stick_curve_right",
                    "deadzone",
                    fallback=0.03
                )
            )

            current_right_outer_deadzone = float(
                self.right_outer_deadzone_var.get()
            )

            saved_right_outer_deadzone = (
                self.config.getfloat(
                    "stick_curve_right",
                    "outer_deadzone",
                    fallback=0.03
                )
            )

        except (
            ValueError,
            TypeError,
            configparser.Error
        ):
            return True

        # 左搖桿中心死區
        if abs(
            current_left_deadzone
            - saved_left_deadzone
        ) > 1e-9:
            return True

        # 左搖桿外圍死區
        if abs(
            current_left_outer_deadzone
            - saved_left_outer_deadzone
        ) > 1e-9:
            return True

        # 右搖桿中心死區
        if abs(
            current_right_deadzone
            - saved_right_deadzone
        ) > 1e-9:
            return True

        # 右搖桿外圍死區
        if abs(
            current_right_outer_deadzone
            - saved_right_outer_deadzone
        ) > 1e-9:
            return True

        if self.left_output_shape_var.get() != parse_output_shape_steps(
            self.config.get(
                "stick_curve_left", "output_shape", fallback="CIRCLE"
            )
        ):
            return True

        if self.right_output_shape_var.get() != parse_output_shape_steps(
            self.config.get(
                "stick_curve_right", "output_shape", fallback="CIRCLE"
            )
        ):
            return True

        if self.left_interpolation_var.get().strip().upper() != self.config.get(
            "stick_curve_left", "interpolation", fallback="LINEAR"
        ).strip().upper():
            return True

        if self.right_interpolation_var.get().strip().upper() != self.config.get(
            "stick_curve_right", "interpolation", fallback="LINEAR"
        ).strip().upper():
            return True

        # =========================
        # 左搖桿死區曲線壓縮
        # =========================

        saved_left_deadzone_compress = (
            self.config.getboolean(
                "stick_curve_left",
                "deadzone_compress",
                fallback=True
            )
        )

        current_left_deadzone_compress = (
            self.left_deadzone_compress_var.get()
        )

        if (
            current_left_deadzone_compress
            != saved_left_deadzone_compress
        ):
            return True

        saved_left_outer_deadzone_compress = (
            self.config.getboolean(
                "stick_curve_left",
                "outer_deadzone_compress",
                fallback=True
            )
        )

        current_left_outer_deadzone_compress = (
            self.left_outer_deadzone_compress_var.get()
        )

        if (
            current_left_outer_deadzone_compress
            != saved_left_outer_deadzone_compress
        ):
            return True

        # =========================
        # 右搖桿死區曲線壓縮
        # =========================

        saved_right_deadzone_compress = (
            self.config.getboolean(
                "stick_curve_right",
                "deadzone_compress",
                fallback=True
            )
        )

        current_right_deadzone_compress = (
            self.right_deadzone_compress_var.get()
        )

        if (
            current_right_deadzone_compress
            != saved_right_deadzone_compress
        ):
            return True

        saved_right_outer_deadzone_compress = (
            self.config.getboolean(
                "stick_curve_right",
                "outer_deadzone_compress",
                fallback=True
            )
        )

        current_right_outer_deadzone_compress = (
            self.right_outer_deadzone_compress_var.get()
        )

        if (
            current_right_outer_deadzone_compress
            != saved_right_outer_deadzone_compress
        ):
            return True


        # 左右搖桿 XY 曲線
        for i in range(5):
            # =========================
            # 左搖桿
            # =========================
            saved_left_x = self.config.getfloat(
                "stick_curve_left",
                f"point_{i}_x",
                fallback=i * 0.25
            )

            saved_left_y = self.config.getfloat(
                "stick_curve_left",
                f"point_{i}_y",
                fallback=self.config.getfloat(
                    "stick_curve_left",
                    f"point_{i}",
                    fallback=i * 0.25
                )
            )

            current_left_x = (
                self.left_curve_vars[i]["x"].get()
            )

            current_left_y = (
                self.left_curve_vars[i]["y"].get()
            )

            if abs(
                current_left_x - saved_left_x
            ) > 0.0005:
                return True

            if abs(
                current_left_y - saved_left_y
            ) > 0.0005:
                return True

            # =========================
            # 右搖桿
            # =========================
            saved_right_x = self.config.getfloat(
                "stick_curve_right",
                f"point_{i}_x",
                fallback=i * 0.25
            )

            saved_right_y = self.config.getfloat(
                "stick_curve_right",
                f"point_{i}_y",
                fallback=self.config.getfloat(
                    "stick_curve_right",
                    f"point_{i}",
                    fallback=i * 0.25
                )
            )

            current_right_x = (
                self.right_curve_vars[i]["x"].get()
            )

            current_right_y = (
                self.right_curve_vars[i]["y"].get()
            )

            if abs(
                current_right_x - saved_right_x
            ) > 0.0005:
                return True

            if abs(
                current_right_y - saved_right_y
            ) > 0.0005:
                return True

        # =========================
        # 左右搖桿防抖
        # =========================

        try:
            saved_left_smoothing = (
                self.config.getfloat(
                    "stick_curve_left",
                    "smoothing",
                    fallback=0.0
                )
            )

            current_left_smoothing = round(
                self.left_stick_smoothing_var.get(),
                1
            )

            saved_right_smoothing = (
                self.config.getfloat(
                    "stick_curve_right",
                    "smoothing",
                    fallback=0.0
                )
            )

            current_right_smoothing = round(
                self.right_stick_smoothing_var.get(),
                1
            )

        except (
            ValueError,
            TypeError,
            configparser.Error
        ):
            return True

        if abs(
            current_left_smoothing
            - saved_left_smoothing
        ) > 0.0001:
            return True

        if abs(
            current_right_smoothing
            - saved_right_smoothing
        ) > 0.0001:
            return True

        # =========================
        # 按鍵映射
        # =========================

        for (
            button_name,
            variable
        ) in self.button_vars.items():
            saved_value = self.config.get(
                "buttons",
                button_name,
                fallback="NONE"
            ).strip().upper()

            current_value = (
                variable.get()
                .strip()
                .upper()
            )

            if current_value != saved_value:
                return True

        # =========================
        # 震動設定
        # =========================

        # 浮點數設定使用數值比較，
        # 避免 1、1.0、1.00 被誤判為不同。
        current_rumble_float_values = {
            "lf_strength": self.lf_strength_var.get(),
            "hf_strength": self.hf_strength_var.get(),
            "lf_curve": self.lf_curve_var.get(),
            "hf_curve": self.hf_curve_var.get(),
            "lf_to_hf_compensation": (
                self.lf_to_hf_compensation_var.get()
            ),
            "hf_to_lf_compensation": (
                self.hf_to_lf_compensation_var.get()
            ),
        }

        for key, current_value in (
            current_rumble_float_values.items()
        ):
            try:
                current_number = float(
                    current_value
                )

                saved_number = self.config.getfloat(
                    "rumble",
                    key
                )

            except (
                ValueError,
                TypeError,
                configparser.Error
            ):
                return True

            if abs(
                current_number
                - saved_number
            ) > 1e-9:
                print(
                    "未儲存震動設定：",
                    key,
                    "GUI =",
                    current_number,
                    "CONFIG =",
                    saved_number
                )
                return True


        # 整數設定使用整數比較
        current_rumble_int_values = {
            "lf_frequency": self.lf_frequency_var.get(),
            "hf_frequency": self.hf_frequency_var.get(),
            "max_amplitude": self.max_amplitude_var.get(),
        }

        for key, current_value in (
            current_rumble_int_values.items()
        ):
            try:
                current_number = int(
                    current_value
                )

                saved_number = self.config.getint(
                    "rumble",
                    key
                )

            except (
                ValueError,
                TypeError,
                configparser.Error
            ):
                return True

            if current_number != saved_number:
                print(
                    "未儲存震動設定：",
                    key,
                    "GUI =",
                    current_number,
                    "CONFIG =",
                    saved_number
                )
                return True

        # 音訊震動
        saved_audio_mode = self.config.get(
            "audio_haptics", "mode", fallback="GAME"
        ).strip().upper()
        if self.audio_haptics_mode_var.get().strip().upper() != saved_audio_mode:
            return True

        audio_values = {
            "mix_ratio": (self.audio_haptics_mix_ratio_var, 0.35),
            "strength": (self.audio_haptics_strength_var, 0.60),
            "noise_gate": (self.audio_haptics_noise_gate_var, 0.015),
            "attack_ms": (self.audio_haptics_attack_var, 1.0),
            "release_ms": (self.audio_haptics_release_var, 140.0),
            "final_tail_strength": (
                self.audio_haptics_final_tail_strength_var, 0.0
            ),
            "final_tail_decay_ms": (
                self.audio_haptics_final_tail_decay_var, 350.0
            ),
        }
        audio_values.update({
            f"band_{index}_gain": (variable, fallback)
            for index, (variable, fallback) in enumerate(
                zip(
                    self.audio_haptics_band_gain_vars,
                    self.audio_haptics_band_gain_fallbacks,
                ),
                start=1,
            )
        })
        for option, (variable, fallback) in audio_values.items():
            try:
                current = float(variable.get())
                saved = self.config.getfloat(
                    "audio_haptics", option, fallback=fallback
                )
            except (ValueError, TypeError, configparser.Error):
                return True
            if abs(current - saved) > 1e-9:
                return True

        # 陀螺儀映射
        gyro_text_values = {
            "activation_mode": (self.gyro_activation_mode_var, "OFF"),
            "activation_match": (self.gyro_activation_match_var, "ANY"),
            "target": (self.gyro_target_var, "RIGHT_STICK"),
            "motion_mode": (self.gyro_motion_mode_var, "CENTER"),
            "tilt_axis": (self.gyro_tilt_axis_var, "HORIZONTAL"),
            "tilt_recenter_button": (
                self.gyro_tilt_recenter_button_var, "NONE"
            ),
        }
        for option, (variable, fallback) in gyro_text_values.items():
            saved = self.config.get(
                "gyro_mapping", option, fallback=fallback
            ).strip().upper()
            if variable.get().strip().upper() != saved:
                return True
        saved_response_curve = self.config.get(
            "gyro_mapping", "response_curve", fallback="LINEAR"
        ).strip().upper()
        if saved_response_curve == "DYNAMIC":
            saved_response_curve = "LATE"
        if saved_response_curve not in ("LINEAR", "LATE", "EARLY"):
            saved_response_curve = "LINEAR"
        if self.gyro_response_curve_var.get() != saved_response_curve:
            return True
        saved_activation_buttons = _parse_gyro_button_setting(
            self.config.get(
                "gyro_mapping",
                "activation_buttons",
                fallback=self.config.get(
                    "gyro_mapping", "activation_button", fallback="ZL"
                ),
            ),
            self.gyro_activation_button_options,
            fallback=("ZL",),
        )
        if self.gyro_activation_buttons != saved_activation_buttons:
            return True
        saved_stabilization_buttons = _parse_gyro_button_setting(
            self.config.get(
                "gyro_mapping",
                "stabilization_buttons",
                fallback=self.config.get(
                    "gyro_mapping", "stabilization_button", fallback="NONE"
                ),
            ),
            self.gyro_stabilization_button_options,
        )
        if self.gyro_stabilization_buttons != saved_stabilization_buttons:
            return True
        gyro_float_values = {
            "stick_sensitivity": (self.gyro_stick_sensitivity_var, 1.5),
            "mouse_sensitivity": (self.gyro_mouse_sensitivity_var, 8.0),
            "x_ratio": (self.gyro_x_ratio_var, 1.0),
            "y_ratio": (self.gyro_y_ratio_var, 1.0),
            "deadzone": (self.gyro_deadzone_var, 0.6),
            "stick_anti_deadzone": (self.gyro_stick_anti_deadzone_var, 0.0),
            "curve_strength": (self.gyro_curve_strength_var, 5.0),
            "smoothing_ms": (self.gyro_smoothing_var, 15.0),
            "tilt_max_angle": (self.gyro_tilt_max_angle_var, 35.0),
            "tilt_deadzone": (self.gyro_tilt_deadzone_var, 0.8),
            "tilt_smoothing_ms": (self.gyro_tilt_smoothing_var, 30.0),
            "accel_suppression": (self.gyro_accel_suppression_var, 70.0),
            "adaptive_deadzone": (self.gyro_adaptive_deadzone_var, 85.0),
            "button_freeze_ms": (self.gyro_button_freeze_var, 35.0),
        }
        for option, (variable, fallback) in gyro_float_values.items():
            try:
                current = float(variable.get())
                saved = self.config.getfloat(
                    "gyro_mapping", option, fallback=fallback
                )
            except (ValueError, TypeError, configparser.Error):
                return True
            if abs(current - saved) > 1e-9:
                return True
        for option, variable, fallback in (
            ("invert_x", self.gyro_invert_x_var, False),
            ("invert_y", self.gyro_invert_y_var, False),
            ("player_space", self.gyro_player_space_var, True),
        ):
            if variable.get() != self.config.getboolean(
                "gyro_mapping", option, fallback=fallback
            ):
                return True

        # =========================
        # 搖桿方向映射
        # =========================

        # 左右搖桿
        for side in (
            "LEFT",
            "RIGHT"
        ):
            section_name = (
                "stick_direction_"
                + side.lower()
            )

            legacy_fallbacks = {
                "trigger_threshold": self.config.get(
                    "stick_direction", "trigger_threshold", fallback="0.60"
                ),
                "release_threshold": self.config.get(
                    "stick_direction", "release_threshold", fallback="0.50"
                ),
                "direction_deadzone": self.config.get(
                    "stick_direction", "angle_hysteresis", fallback="5.0"
                ),
                "mouse_speed": self.config.get(
                    "stick_direction", "mouse_speed", fallback="900"
                ),
            }
            direction_variables = {
                "trigger_threshold": self.stick_direction_trigger_vars[side],
                "release_threshold": self.stick_direction_release_vars[side],
                "direction_deadzone": self.stick_direction_deadzone_vars[side],
                "mouse_speed": self.stick_mouse_speed_vars[side],
            }
            for option, variable in direction_variables.items():
                try:
                    current_value = float(variable.get())
                    saved_value = self.config.getfloat(
                        section_name,
                        option,
                        fallback=float(legacy_fallbacks[option]),
                    )
                except (ValueError, TypeError, configparser.Error):
                    return True
                if abs(current_value - saved_value) > 1e-9:
                    return True

            # -------------------------
            # 4WAY / 8WAY 模式
            # -------------------------
            saved_mode = self.config.get(
                section_name,
                "mode",
                fallback="4WAY"
            ).strip().upper()

            current_mode = (
                self.stick_direction_mode_vars[
                    side
                ]
                .get()
                .strip()
                .upper()
            )

            if current_mode != saved_mode:
                print(
                    "未儲存設定：",
                    section_name,
                    "mode",
                    "GUI =",
                    repr(current_mode),
                    "CONFIG =",
                    repr(saved_mode)
                )
                return True

            # -------------------------
            # 8 個方向的映射
            # -------------------------
            for (
                direction,
                variable
            ) in self.stick_direction_vars[
                side
            ].items():
                saved_value = self.config.get(
                    section_name,
                    direction.lower(),
                    fallback="NONE"
                ).strip().upper()

                current_value = (
                    variable.get()
                    .strip()
                    .upper()
                )

                if current_value != saved_value:
                    print(
                        "未儲存設定：",
                        section_name,
                        direction,
                        "GUI =",
                        repr(current_value),
                        "CONFIG =",
                        repr(saved_value)
                    )
                    return True

        return False


    def restart_main(self):
        """正常關閉舊的主連接程式，再重新啟動。"""

        # 檢查是否有尚未儲存的設定變更
        if self.has_unsaved_changes():
            choice = messagebox.askyesnocancel(
                "尚未儲存設定",
                "偵測到尚未儲存的設定變更。\n\n"
                "是否先儲存設定，再重新啟動連接程式？\n\n"
                "是：儲存後重新啟動\n"
                "否：不儲存，直接重新啟動\n"
                "取消：返回設定畫面"
            )

            # 取消
            if choice is None:
                return

            # 是：先儲存
            if choice:
                if not self.save_config():
                    return


        # 檢查 ViGEmBus
        if not self.vigembus_installed:
            messagebox.showerror(
                "缺少 ViGEmBus",
                "未偵測到 ViGEmBus 驅動程式。\n\n"
                "目前無法啟動 Xbox 虛擬控制器。"
            )
            return

        main_path = Path(__file__).with_name(
            "main.py"
        )

        if not main_path.exists():
            messagebox.showerror(
                "錯誤",
                "找不到 main.py。"
            )
            return

        # 如果舊的連接程式仍在執行，
        # 先正常關閉，再重新啟動
        if (
            self.main_process is not None
            and self.main_process.poll() is None
        ):
            self.stop_main_process()

            # 等待 COM Port 完全釋放
            self.root.after(
                500,
                lambda: self.start_main(
                    main_path
                )
            )
            return

        # 沒有舊程序時直接啟動
        self.start_main(
            main_path
        )

    def on_close(self):
        """關閉 GUI 前檢查未儲存設定，並清理所有子程序。"""

        # 放大曲線使用同一個主視窗。此時按右上角 X
        # 只離開放大模式，不關閉整個設定程式。
        if self.stick_settings_zoomed:
            self.toggle_stick_settings_zoom()
            return

        # 先檢查是否有尚未儲存的設定
        if self.has_unsaved_changes():
            confirmed = messagebox.askyesno(
                "尚未儲存",
                "目前有尚未儲存的設定。\n\n"
                "確定要放棄變更並關閉嗎？"
            )

            if not confirmed:
                return

        # 先正常關閉主連接程式
        # 讓 main.py 執行 finally，
        # 釋放鍵盤按鍵並關閉 ESP32 連接
        self.stop_main_process()

        # 關閉校正程式及其所有子程序
        if (
            self.calibration_process is not None
            and self.calibration_process.poll() is None
        ):
            try:
                subprocess.run(
                    [
                        "taskkill",
                        "/PID",
                        str(self.calibration_process.pid),
                        "/T",
                        "/F"
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(
                        subprocess,
                        "CREATE_NO_WINDOW",
                        0
                    )
                )
            except Exception:
                pass

        self.root.destroy()

    def save_config(self):
        try:
            # 手動陀螺儀校正由正在執行的連接程式寫入。
            # 儲存 GUI 設定前先合併磁碟最新版，避免覆蓋剛完成的
            # [gyro] / [gyro.<controller>] 校正資料。
            self.config.read(CONFIG_PATH, encoding="utf-8")
            # 驗證數值
            # =========================
            # 左右搖桿死區
            # =========================

            left_deadzone = float(
                self.left_deadzone_var.get()
            )

            left_outer_deadzone = float(
                self.left_outer_deadzone_var.get()
            )

            right_deadzone = float(
                self.right_deadzone_var.get()
            )

            right_outer_deadzone = float(
                self.right_outer_deadzone_var.get()
            )

            lf_strength = float(
                self.lf_strength_var.get()
            )

            hf_strength = float(
                self.hf_strength_var.get()
            )

            lf_curve = float(
                self.lf_curve_var.get()
            )

            hf_curve = float(
                self.hf_curve_var.get()
            )

            lf_to_hf_compensation = float(
                self.lf_to_hf_compensation_var.get()
            )

            hf_to_lf_compensation = float(
                self.hf_to_lf_compensation_var.get()
            )

            lf_frequency = int(
                self.lf_frequency_var.get()
            )

            hf_frequency = int(
                self.hf_frequency_var.get()
            )

            max_amplitude = int(
                self.max_amplitude_var.get()
            )

            audio_strength = float(self.audio_haptics_strength_var.get())
            audio_mix_ratio = float(self.audio_haptics_mix_ratio_var.get())
            audio_band_gains = [
                float(variable.get())
                for variable in self.audio_haptics_band_gain_vars
            ]
            audio_noise_gate = float(self.audio_haptics_noise_gate_var.get())
            audio_attack = float(self.audio_haptics_attack_var.get())
            audio_release = float(self.audio_haptics_release_var.get())
            audio_final_tail_strength = float(
                self.audio_haptics_final_tail_strength_var.get()
            )
            audio_final_tail_decay = float(
                self.audio_haptics_final_tail_decay_var.get()
            )
            stick_direction_values = {}
            for side in ("LEFT", "RIGHT"):
                stick_direction_values[side] = {
                    "trigger_threshold": float(
                        self.stick_direction_trigger_vars[side].get()
                    ),
                    "release_threshold": float(
                        self.stick_direction_release_vars[side].get()
                    ),
                    "direction_deadzone": float(
                        self.stick_direction_deadzone_vars[side].get()
                    ),
                    "mouse_speed": float(
                        self.stick_mouse_speed_vars[side].get()
                    ),
                }
            gyro_stick_sensitivity = float(
                self.gyro_stick_sensitivity_var.get()
            )
            gyro_mouse_sensitivity = float(
                self.gyro_mouse_sensitivity_var.get()
            )
            gyro_x_ratio = float(self.gyro_x_ratio_var.get())
            gyro_y_ratio = float(self.gyro_y_ratio_var.get())
            gyro_deadzone = float(self.gyro_deadzone_var.get())
            gyro_stick_anti_deadzone = float(
                self.gyro_stick_anti_deadzone_var.get()
            )
            gyro_response_curve = (
                self.gyro_response_curve_var.get().strip().upper()
            )
            gyro_curve_strength = float(
                self.gyro_curve_strength_var.get()
            )
            gyro_smoothing = float(self.gyro_smoothing_var.get())
            gyro_tilt_max_angle = float(self.gyro_tilt_max_angle_var.get())
            gyro_tilt_deadzone = float(self.gyro_tilt_deadzone_var.get())
            gyro_tilt_smoothing = float(self.gyro_tilt_smoothing_var.get())
            gyro_accel_suppression = float(
                self.gyro_accel_suppression_var.get()
            )
            gyro_adaptive_deadzone = float(
                self.gyro_adaptive_deadzone_var.get()
            )
            gyro_button_freeze = float(self.gyro_button_freeze_var.get())
            gyro_activation_mode = (
                self.gyro_activation_mode_var.get().strip().upper()
            )
            gyro_activation_buttons = list(self.gyro_activation_buttons)
            gyro_activation_match = (
                self.gyro_activation_match_var.get().strip().upper()
            )
            gyro_target = self.gyro_target_var.get().strip().upper()
            gyro_motion_mode = self.gyro_motion_mode_var.get().strip().upper()
            gyro_tilt_axis = self.gyro_tilt_axis_var.get().strip().upper()
            gyro_tilt_recenter_button = (
                self.gyro_tilt_recenter_button_var.get().strip().upper()
            )
            gyro_stabilization_buttons = list(
                self.gyro_stabilization_buttons
            )

            # 檢查範圍
            if not 0.0 <= left_deadzone <= 1.0:
                raise ValueError(
                    "左搖桿中心死區必須介於 0.00 ～ 1.00"
                )

            if not 0.0 <= left_outer_deadzone <= 1.0:
                raise ValueError(
                    "左搖桿外圍死區必須介於 0.00 ～ 1.00"
                )

            if not 0.0 <= right_deadzone <= 1.0:
                raise ValueError(
                    "右搖桿中心死區必須介於 0.00 ～ 1.00"
                )

            if not 0.0 <= right_outer_deadzone <= 1.0:
                raise ValueError(
                    "右搖桿外圍死區必須介於 0.00 ～ 1.00"
                )

            if not 0.0 <= lf_strength <= 1.0:
                raise ValueError(
                    "LF 強度必須介於 0.00 ～ 1.00。"
                )

            if not 0.0 <= hf_strength <= 1.0:
                raise ValueError(
                    "HF 強度必須介於 0.00 ～ 1.00。"
                )

            if not 0.1 <= lf_curve <= 5.0:
                raise ValueError(
                    "LF 曲線必須介於 0.10 ～ 5.00。"
                )

            if not 0.1 <= hf_curve <= 5.0:
                raise ValueError(
                    "HF 曲線必須介於 0.10 ～ 5.00。"
                )

            if not 0.0 <= lf_to_hf_compensation <= 1.0:
                raise ValueError(
                    "LF → HF 補償必須介於 0.00 ～ 1.00。"
                )

            if not 0.0 <= hf_to_lf_compensation <= 1.0:
                raise ValueError(
                    "HF → LF 補償必須介於 0.00 ～ 1.00。"
                )

            if not 1 <= lf_frequency <= 511:
                raise ValueError(
                    "LF 頻率命令值必須介於 1 ～ 511。"
                )

            if not 1 <= hf_frequency <= 511:
                raise ValueError(
                    "HF 頻率命令值必須介於 1 ～ 511。"
                )

            if not 0 <= max_amplitude <= 1023:
                raise ValueError(
                    "最大振幅必須介於 0 ～ 1023。"
                )         

            audio_mode = self.audio_haptics_mode_var.get().strip().upper()
            if audio_mode not in ("GAME", "AUDIO", "MIX"):
                raise ValueError("音訊震動模式無效。")
            audio_ranges = (
                (audio_mix_ratio, 0.0, 1.0, "混合比例"),
                (audio_strength, 0.0, 1.0, "音訊強度"),
                (audio_noise_gate, 0.0, 0.25, "噪音閘"),
                (audio_attack, 1.0, 500.0, "啟動 ms"),
                (audio_release, 5.0, 2000.0, "音訊釋放 ms"),
                (audio_final_tail_strength, 0.0, 1.0, "餘震強度"),
                (audio_final_tail_decay, 50.0, 2000.0, "餘震衰減 ms"),
                *(
                    (gain, 0.0, 2.0, f"頻段 {index} 增益")
                    for index, gain in enumerate(audio_band_gains, start=1)
                ),
            )
            for value, minimum, maximum, label in audio_ranges:
                if not minimum <= value <= maximum:
                    raise ValueError(
                        f"{label} 必須介於 {minimum:g} ～ {maximum:g}。"
                    )

            for side, values in stick_direction_values.items():
                side_label = "左搖桿" if side == "LEFT" else "右搖桿"
                trigger = values["trigger_threshold"]
                release = values["release_threshold"]
                if not 0.10 <= trigger <= 1.0:
                    raise ValueError(
                        f"{side_label}觸發門檻必須介於 10% ～ 100%。"
                    )
                if not 0.0 <= release <= 0.97:
                    raise ValueError(
                        f"{side_label}放開門檻必須介於 0% ～ 97%。"
                    )
                if trigger - release < 0.03 - 1e-9:
                    raise ValueError(
                        f"{side_label}放開門檻必須至少比觸發門檻低 3%。"
                    )
                if not 0.0 <= values["direction_deadzone"] <= 20.0:
                    raise ValueError(
                        f"{side_label}方向死區必須介於 0° ～ 20°。"
                    )
                if not 100.0 <= values["mouse_speed"] <= 3000.0:
                    raise ValueError(
                        f"{side_label}游標速度必須介於 100 ～ 3000。"
                    )
            if gyro_activation_mode not in ("OFF", "HOLD", "TOGGLE"):
                raise ValueError("陀螺儀啟動方式無效。")
            if gyro_response_curve == "DYNAMIC":
                gyro_response_curve = "LATE"
            if gyro_response_curve not in ("LINEAR", "LATE", "EARLY"):
                raise ValueError("陀螺儀感度曲線無效。")
            if not 0.0 <= gyro_curve_strength <= 10.0:
                raise ValueError("陀螺儀曲線強度必須介於 0 ～ 10。")
            if gyro_activation_match not in ("ANY", "ALL"):
                raise ValueError("陀螺儀啟動條件無效。")
            if gyro_activation_mode != "OFF" and not gyro_activation_buttons:
                raise ValueError("至少選擇一個啟動按鍵。")
            if any(
                button not in self.gyro_activation_button_options
                for button in gyro_activation_buttons
            ):
                raise ValueError("陀螺儀啟動按鍵無效。")
            if gyro_target not in ("LEFT_STICK", "RIGHT_STICK", "MOUSE"):
                raise ValueError("陀螺儀輸出目標無效。")
            if gyro_motion_mode not in ("CENTER", "TILT"):
                raise ValueError("陀螺儀控制模式無效。")
            if gyro_target == "MOUSE" and gyro_motion_mode == "TILT":
                raise ValueError("傾斜模式僅支援左右搖桿輸出。")
            if gyro_tilt_axis not in ("HORIZONTAL", "DUAL"):
                raise ValueError("傾斜軸向無效。")
            if (
                gyro_tilt_recenter_button
                not in self.gyro_tilt_recenter_button_options
            ):
                raise ValueError("重設中立按鍵無效。")
            if any(
                button not in self.gyro_stabilization_button_options
                for button in gyro_stabilization_buttons
            ):
                raise ValueError("陀螺儀防晃按鍵無效。")
            if gyro_tilt_deadzone >= gyro_tilt_max_angle:
                raise ValueError("傾斜死區必須小於最大傾斜角。")
            gyro_ranges = (
                (gyro_stick_sensitivity, 0.1, 10.0, "陀螺儀搖桿感度"),
                (gyro_mouse_sensitivity, 0.5, 30.0, "陀螺儀滑鼠感度"),
                (gyro_x_ratio, 0.5, 2.0, "陀螺儀 X 比例"),
                (gyro_y_ratio, 0.5, 2.0, "陀螺儀 Y 比例"),
                (gyro_deadzone, 0.0, 5.0, "陀螺儀死區"),
                (gyro_stick_anti_deadzone, 0.0, 30.0, "陀螺儀反死區"),
                (gyro_smoothing, 0.0, 100.0, "陀螺儀平滑 ms"),
                (gyro_tilt_max_angle, 10.0, 60.0, "最大傾斜角"),
                (gyro_tilt_deadzone, 0.0, 5.0, "傾斜死區"),
                (gyro_tilt_smoothing, 0.0, 150.0, "傾斜平滑 ms"),
                (gyro_accel_suppression, 0.0, 100.0, "加速度抑制"),
                (gyro_adaptive_deadzone, 0.0, 100.0, "自適應死區"),
                (gyro_button_freeze, 0.0, 120.0, "按鍵防晃 ms"),
            )
            for value, minimum, maximum, label in gyro_ranges:
                if not minimum <= value <= maximum:
                    raise ValueError(
                        f"{label} 必須介於 {minimum:g} ～ {maximum:g}。"
                    )

            # 儲存左右搖桿設定
            if not self.config.has_section(
                "stick_curve_left"
            ):
                self.config.add_section(
                    "stick_curve_left"
                )

            if not self.config.has_section(
                "stick_curve_right"
            ):
                self.config.add_section(
                    "stick_curve_right"
                )

            # =========================
            # 儲存左搖桿死區設定
            # =========================

            self.config.set(
                "stick_curve_left",
                "deadzone",
                f"{left_deadzone:.2f}"
            )

            self.config.set(
                "stick_curve_left",
                "outer_deadzone",
                f"{left_outer_deadzone:.2f}"
            )

            self.config.set(
                "stick_curve_left",
                "deadzone_compress",
                str(
                    self.left_deadzone_compress_var.get()
                ).lower()
            )

            self.config.set(
                "stick_curve_left",
                "outer_deadzone_compress",
                str(
                    self.left_outer_deadzone_compress_var.get()
                ).lower()
            )
            self.config.set(
                "stick_curve_left",
                "output_shape",
                str(self.left_output_shape_var.get()),
            )
            self.config.set(
                "stick_curve_left",
                "interpolation",
                self.left_interpolation_var.get().strip().upper(),
            )

            # =========================
            # 儲存右搖桿死區設定
            # =========================

            self.config.set(
                "stick_curve_right",
                "deadzone",
                f"{right_deadzone:.2f}"
            )

            self.config.set(
                "stick_curve_right",
                "outer_deadzone",
                f"{right_outer_deadzone:.2f}"
            )

            self.config.set(
                "stick_curve_right",
                "deadzone_compress",
                str(
                    self.right_deadzone_compress_var.get()
                ).lower()
            )

            self.config.set(
                "stick_curve_right",
                "outer_deadzone_compress",
                str(
                    self.right_outer_deadzone_compress_var.get()
                ).lower()
            )
            self.config.set(
                "stick_curve_right",
                "output_shape",
                str(self.right_output_shape_var.get()),
            )
            self.config.set(
                "stick_curve_right",
                "interpolation",
                self.right_interpolation_var.get().strip().upper(),
            )

            # 儲存左右搖桿的 5 點 XY 反應曲線
            for i in range(5):
                # point_N was the old one-dimensional curve format.  Keep
                # reading it as a migration fallback, but stop preserving a
                # second, unused copy once the XY format is saved.
                self.config.remove_option("stick_curve_left", f"point_{i}")
                self.config.remove_option("stick_curve_right", f"point_{i}")

                # 左搖桿 XY 曲線
                self.config.set(
                    "stick_curve_left",
                    f"point_{i}_x",
                    f"{self.left_curve_vars[i]['x'].get():.3f}"
                )

                self.config.set(
                    "stick_curve_left",
                    f"point_{i}_y",
                    f"{self.left_curve_vars[i]['y'].get():.3f}"
                )

                # 右搖桿 XY 曲線
                self.config.set(
                    "stick_curve_right",
                    f"point_{i}_x",
                    f"{self.right_curve_vars[i]['x'].get():.3f}"
                )

                self.config.set(
                    "stick_curve_right",
                    f"point_{i}_y",
                    f"{self.right_curve_vars[i]['y'].get():.3f}"
                )

            # 儲存左右搖桿防抖
            self.config.set(
                "stick_curve_left",
                "smoothing",
                f"{self.left_stick_smoothing_var.get():.1f}"
            )

            self.config.set(
                "stick_curve_right",
                "smoothing",
                f"{self.right_stick_smoothing_var.get():.1f}"
            )

            # 舊版的總開關已由 LF / HF 強度取代。
            self.config.remove_option("rumble", "enabled")

            self.config.set(
                "rumble",
                "lf_strength",
                f"{lf_strength:.2f}"
            )

            self.config.set(
                "rumble",
                "hf_strength",
                f"{hf_strength:.2f}"
            )

            self.config.set(
                "rumble",
                "lf_curve",
                f"{lf_curve:.2f}"
            )

            self.config.set(
                "rumble",
                "hf_curve",
                f"{hf_curve:.2f}"
            )


            self.config.set(
                "rumble",
                "lf_to_hf_compensation",
                f"{lf_to_hf_compensation:.2f}"
            )

            self.config.set(
                "rumble",
                "hf_to_lf_compensation",
                f"{hf_to_lf_compensation:.2f}"
            )

            self.config.set(
                "rumble",
                "lf_frequency",
                str(lf_frequency)
            )

            self.config.set(
                "rumble",
                "hf_frequency",
                str(hf_frequency)
            )

            self.config.set(
                "rumble",
                "max_amplitude",
                str(max_amplitude)
            )

            if not self.config.has_section("audio_haptics"):
                self.config.add_section("audio_haptics")
            audio_settings = {
                "mode": audio_mode,
                "mix_ratio": f"{audio_mix_ratio:.2f}",
                "strength": f"{audio_strength:.2f}",
                "noise_gate": f"{audio_noise_gate:.3f}",
                "attack_ms": f"{audio_attack:g}",
                "release_ms": f"{audio_release:g}",
                "final_tail_strength": f"{audio_final_tail_strength:.2f}",
                "final_tail_decay_ms": f"{audio_final_tail_decay:g}",
            }
            audio_settings.update({
                f"band_{index}_gain": f"{gain:.2f}"
                for index, gain in enumerate(audio_band_gains, start=1)
            })
            for legacy_option in ("low_gain", "high_gain", "crossover_hz"):
                self.config.remove_option("audio_haptics", legacy_option)
            for option, value in audio_settings.items():
                self.config.set("audio_haptics", option, value)

            if not self.config.has_section("gyro_mapping"):
                self.config.add_section("gyro_mapping")
            gyro_settings = {
                "activation_mode": gyro_activation_mode,
                "activation_buttons": ",".join(gyro_activation_buttons),
                "activation_match": gyro_activation_match,
                "activation_button": (
                    gyro_activation_buttons[0]
                    if gyro_activation_buttons else "ZL"
                ),
                "target": gyro_target,
                "motion_mode": gyro_motion_mode,
                "tilt_axis": gyro_tilt_axis,
                "tilt_recenter_button": gyro_tilt_recenter_button,
                "stabilization_buttons": (
                    ",".join(gyro_stabilization_buttons)
                    if gyro_stabilization_buttons else "NONE"
                ),
                "stabilization_button": (
                    gyro_stabilization_buttons[0]
                    if gyro_stabilization_buttons else "NONE"
                ),
                "stick_sensitivity": f"{gyro_stick_sensitivity:.2f}",
                "mouse_sensitivity": f"{gyro_mouse_sensitivity:.2f}",
                "x_ratio": f"{gyro_x_ratio:.2f}",
                "y_ratio": f"{gyro_y_ratio:.2f}",
                "deadzone": f"{gyro_deadzone:.2f}",
                "stick_anti_deadzone": f"{gyro_stick_anti_deadzone:g}",
                "response_curve": gyro_response_curve,
                "curve_strength": f"{gyro_curve_strength:g}",
                "smoothing_ms": f"{gyro_smoothing:g}",
                "tilt_max_angle": f"{gyro_tilt_max_angle:g}",
                "tilt_deadzone": f"{gyro_tilt_deadzone:.2f}",
                "tilt_smoothing_ms": f"{gyro_tilt_smoothing:g}",
                "accel_suppression": f"{gyro_accel_suppression:g}",
                "adaptive_deadzone": f"{gyro_adaptive_deadzone:g}",
                "button_freeze_ms": f"{gyro_button_freeze:g}",
                "invert_x": str(self.gyro_invert_x_var.get()).lower(),
                "invert_y": str(self.gyro_invert_y_var.get()).lower(),
                "player_space": str(self.gyro_player_space_var.get()).lower(),
            }
            for option, value in gyro_settings.items():
                self.config.set("gyro_mapping", option, value)

            # 寫入按鍵映射
            if not self.config.has_section("buttons"):
                self.config.add_section("buttons")

            for switch_name, variable in self.button_vars.items():
                self.config.set(
                    "buttons",
                    switch_name,
                    variable.get().strip().upper()
                )

            # =========================
            # 儲存搖桿方向映射設定
            # =========================

            # 左右搖桿方向映射
            for side in (
                "LEFT",
                "RIGHT"
            ):
                section_name = (
                    "stick_direction_"
                    + side.lower()
                )

                if not self.config.has_section(
                    section_name
                ):
                    self.config.add_section(
                        section_name
                    )

                # 儲存 4WAY / 8WAY
                self.config.set(
                    section_name,
                    "mode",
                    self.stick_direction_mode_vars[
                        side
                    ].get()
                )
                values = stick_direction_values[side]
                self.config.set(
                    section_name,
                    "direction_deadzone",
                    f"{values['direction_deadzone']:g}",
                )
                self.config.set(
                    section_name,
                    "trigger_threshold",
                    f"{values['trigger_threshold']:.2f}",
                )
                self.config.set(
                    section_name,
                    "release_threshold",
                    f"{values['release_threshold']:.2f}",
                )
                self.config.set(
                    section_name,
                    "mouse_speed",
                    f"{values['mouse_speed']:g}",
                )

                # 儲存 8 個方向
                for (
                    direction,
                    variable
                ) in self.stick_direction_vars[
                    side
                ].items():
                    self.config.set(
                        section_name,
                        direction.lower(),
                        variable.get()
                    )

            # 左右獨立設定已完整寫入，移除舊版共用區段，
            # 避免同一參數同時出現新舊兩份而造成誤解。
            self.config.remove_section("stick_direction")

            atomic_write_config(self.config, CONFIG_PATH)

            # 重新載入剛剛實際寫入的設定，
            # 確保 has_unsaved_changes() 使用最新內容。
            self.config.read(
                CONFIG_PATH,
                encoding="utf-8"
            )

            messagebox.showinfo(
                "儲存完成",
                "設定已成功儲存至 config.ini。\n"
                "重新啟動主程式後生效。"
            )

            return True
        
        except ValueError as exc:
            error_text = str(exc)

            # Python 內建的數字格式錯誤改成中文提示
            if (
                "could not convert string to float"
                in error_text
            ):
                error_text = (
                    "輸入的數值格式不正確。\n\n"
                    "請確認所有數值欄位只包含有效的數字。"
                )

            elif (
                "invalid literal for int()"
                in error_text
            ):
                error_text = (
                    "輸入的整數格式不正確。\n\n"
                    "請確認頻率命令值與最大振幅"
                    "只包含有效的整數。"
                )

            messagebox.showerror(
                self.tr("設定錯誤"),
                self.tr(error_text)
            )

            return False

        except OSError as exc:
            messagebox.showerror(
                "儲存失敗",
                "無法安全寫入 config.ini。\n\n"
                f"錯誤資訊：{exc}"
            )
            return False

def main():
    root = tk.Tk()
    ConfigGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
