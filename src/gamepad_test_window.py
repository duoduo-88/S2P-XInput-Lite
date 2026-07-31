"""Non-modal controller input, trajectory, mapping and rumble tester."""

from __future__ import annotations

import configparser
import ctypes
import heapq
import json
import math
import struct
import threading
import time
import tkinter as tk
import webbrowser
import zlib
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from tkinter import ttk, filedialog, font as tkfont, messagebox

from diagnostic_session import (
    DEFAULT_DIAGNOSTIC_SECONDS,
    DiagnosticSession,
    ESP32DiagnosticReader,
    diagnostic_firmware_needs_update,
    read_controller_status,
)
from command_queue import enqueue_controller_command
from gamepad_devices import (
    GamepadDevice,
    NativeGamepadSampler,
    S2P_MOBILE_HID_PROFILE,
    WindowsGamepadBackend,
)
from gyro_processing import _apply_gyro_response_curve
from raw_hid_probe import (
    RawHidAnalysisClient,
    RawHidStreamClient,
    enumerate_raw_hid_gamepads,
)
from switch2_input import SWITCH_BUTTONS
from support_log import format_support_log
from test_telemetry import SharedTestTelemetry
from tooltip_layout import wrap_tooltip_text
from update_manager import (
    UpdateCheckError,
    automatic_update_checks_enabled,
    check_latest_release,
    ignored_update_version,
    is_newer_version,
    save_update_preferences,
)
from version import APP_NAME, VERSION


PLOT_SIZE = 320
PLOT_CENTER = PLOT_SIZE / 2
PLOT_RADIUS = 146
SHAPE_BIN_COUNT = 72
TRAIL_TILE_SIZE = 40
MAX_CANVAS_TRAIL_ITEMS = 2000
TRIGGER_EVENT_THRESHOLD = 30.0 / 255.0
SWITCH_RAW_HID_BUTTONS = tuple(SWITCH_BUTTONS.items())
SHAPE_TRACE_REFRESH_HZ = 60.0
SHAPE_CAPTURE_SETTLE_SECONDS = 1.0
TRAIL_COLOR = "#1976D2"
RIGHT_TRAIL_COLOR = "#D32F2F"
SHAPE_COLOR = "#7B1FA2"
GRID_COLOR = "#D8D8D8"
AXIS_COLOR = "#B0B0B0"
DEADZONE_COLOR = "#D5D5D5"
OUTER_DEADZONE_COLOR = "#777777"
CURVE_COLORS = ("#B3D5F5", "#B8E0C2", "#F2D49B", "#E9B7B7")
GYRO_RESPONSE_COLORS = ("#1976D2", "#2E8B57", "#C47F00", "#C62828")
GYRO_RESPONSE_OUTER_INSET = 2.0
GYRO_BASE_COLOR = "#A0A0A0"
GYRO_ACTIVE_COLOR = "#1976D2"
GYRO_ANTI_DEADZONE_COLOR = "#00897B"
TEST_ICON_PATH = (
    Path(__file__).resolve().parent.parent / "image" / "testicon.png"
)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
ABOUT_ICON_PATH = PROJECT_ROOT / "image" / "icon.png"
LICENSE_PATH = PROJECT_ROOT / "LICENSE"
THIRD_PARTY_NOTICES_PATH = PROJECT_ROOT / "THIRD_PARTY_NOTICES.md"
GITHUB_URL = "https://github.com/duoduo-88/S2P-XInput-Lite/tree/main"
SPONSOR_URL = "https://ko-fi.com/duoduo88"
CONTROLLER_STATUS_PATH = Path(__file__).with_name("controller_status.json")
GAMEPAD_TESTER_TITLE = f"{APP_NAME} v{VERSION} GamepadTester"


def _gyro_mapping_enabled(gyro):
    """Return whether gyro mapping is enabled in the current telemetry."""
    return str(gyro.get("activation_mode") or "").strip().upper() != "OFF"


def diagnostic_telemetry_for_device(
    device,
    s2p_telemetry,
    selected_device_telemetry,
    s2p_telemetry_is_fresh,
):
    """Return telemetry only when it belongs to the diagnostic target."""
    if device is None:
        return {}
    if device.kind == "s2p":
        return (
            dict(s2p_telemetry or {})
            if s2p_telemetry_is_fresh else {}
        )
    selected_device_telemetry = selected_device_telemetry or {}
    if selected_device_telemetry.get("device_key") != device.key:
        return {}
    return dict(selected_device_telemetry)


def _gyro_targets_side(gyro, side):
    """Return whether enabled gyro output contributes to one stick."""
    return bool(
        _gyro_mapping_enabled(gyro)
        and gyro.get("available")
        and gyro.get("target") == side
    )


def _selected_plot_source(plot):
    """Read a plot source while supporting lightweight diagnostic stubs."""
    getter = getattr(plot, "selected_source", None)
    if callable(getter):
        return getter()
    return str(plot.source_var.get())


def build_device_display_mapping(devices):
    """Return unique selector labels while preserving stable device identities."""
    devices = tuple(devices)
    name_counts = {}
    for device in devices:
        name_counts[device.name] = name_counts.get(device.name, 0) + 1
    mapping = {}
    for device in devices:
        label = device.name
        if device.display_suffix:
            label = f"{device.name} [{device.display_suffix}]"
        elif name_counts[device.name] > 1:
            label = (
                f"{device.name} "
                f"[{device.kind.upper()} {device.index + 1}]"
            )
        # A driver-supplied name can itself look like our suffix. Retain every
        # device even in that unusual case.
        if label in mapping:
            identity = device.raw_hid_key or device.key
            if device.kind == "raw_hid":
                identity = identity.rsplit("#", 1)[-1][-16:]
            label = f"{label} <{identity}>"
        mapping[label] = device
    return mapping


def raw_hid_test_device(raw_device, index, translate=lambda text: text):
    """Expose one HID collection as an independently selectable test device."""
    interface = (
        str(raw_device.interface_number)
        if raw_device.interface_number >= 0 else "?"
    )
    virtual = f" {translate('虛擬')}" if raw_device.is_virtual else ""
    return GamepadDevice(
        key=f"raw_hid:{raw_device.key}",
        kind="raw_hid",
        index=int(index),
        name=raw_device.name,
        supports_rumble=False,
        raw_hid_key=raw_device.key,
        display_suffix=(
            f"Raw HID {raw_device.vendor_id:04X}:"
            f"{raw_device.product_id:04X} IF {interface}{virtual}"
        ),
    )


def localized_device_name(device, translate):
    """Translate only app-generated fallback names, never driver product names."""
    if not device.name_translation_key:
        return device.name
    return translate(device.name_translation_key).format(
        index=device.index + 1
    )


XINPUT_OUTPUT_BUTTONS = (
    (0x0001, "XInput ↑"),
    (0x0002, "XInput ↓"),
    (0x0004, "XInput ←"),
    (0x0008, "XInput →"),
    (0x0010, "XInput START"),
    (0x0020, "XInput BACK"),
    (0x0040, "XInput L3"),
    (0x0080, "XInput R3"),
    (0x0100, "XInput LB"),
    (0x0200, "XInput RB"),
    (0x0400, "XInput GUIDE"),
    (0x1000, "XInput A"),
    (0x2000, "XInput B"),
    (0x4000, "XInput X"),
    (0x8000, "XInput Y"),
)


class HoverTip:
    """Tooltip matching the settings UI's native question-mark help."""

    def __init__(self, widget, text, wraplength=320):
        self.widget = widget
        self.text = text
        self.wraplength = max(160, int(wraplength))
        self._window = None
        widget.bind("<Enter>", self.show, add="+")
        widget.bind("<Leave>", self.hide, add="+")

    def show(self, _event=None):
        if self._window is not None:
            return
        try:
            text = self.text() if callable(self.text) else self.text
            owner = self.widget.winfo_toplevel()
            tip = tk.Toplevel(owner)
            tip.withdraw()
            tip.overrideredirect(True)
            tip.transient(owner)
            screen_width = self.widget.winfo_screenwidth()
            screen_height = self.widget.winfo_screenheight()
            effective_wrap = min(
                self.wraplength,
                max(160, screen_width - 32),
            )
            label = tk.Label(
                tip,
                text="",
                justify="left",
                relief="solid",
                borderwidth=1,
                padx=8,
                pady=6,
                wraplength=0,
            )
            label_font = tkfont.Font(font=label.cget("font"))
            label.configure(text=wrap_tooltip_text(
                text,
                effective_wrap,
                label_font.measure,
            ))
            label.pack()
            tip.update_idletasks()
            width = tip.winfo_reqwidth()
            height = tip.winfo_reqheight()
            x = self.widget.winfo_rootx() + 25
            y = self.widget.winfo_rooty() + 25
            x = max(5, min(x, screen_width - width - 5))
            y = max(5, min(y, screen_height - height - 5))
            tip.geometry(f"+{x}+{y}")
            tip.deiconify()
            self._window = tip
        except tk.TclError:
            self._window = None

    def hide(self, _event=None):
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None


def display_refresh_rate(window=None):
    """Read the refresh rate for a Tk window's current display."""
    try:
        user32 = ctypes.windll.user32
        gdi32 = ctypes.windll.gdi32
        window_handle = (
            int(window.winfo_id()) if window is not None else 0
        )
        device_context = user32.GetDC(window_handle)
        if not device_context:
            return 60.0
        try:
            refresh_rate = int(gdi32.GetDeviceCaps(device_context, 116))
        finally:
            user32.ReleaseDC(window_handle, device_context)
        if 30 <= refresh_rate <= 500:
            return float(refresh_rate)
    except (
        AttributeError,
        OSError,
        RuntimeError,
        TypeError,
        ValueError,
        tk.TclError,
    ):
        pass
    return 60.0


def primary_display_refresh_rate():
    """Backward-compatible primary-display refresh-rate helper."""
    return display_refresh_rate()


def read_connection_status_summary(translator=None):
    """Read bridge status when the tester is running in its own process."""
    tr = translator or (lambda value: value)
    try:
        status = json.loads(
            CONTROLLER_STATUS_PATH.read_text(encoding="utf-8")
        )
        if time.time() - float(status.get("updated_at", 0.0)) > 3.0:
            return tr("● 未連線"), "#777777"
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return "", "#777777"

    state = str(status.get("state") or "stopped")
    mode = {"bluetooth": "BLE", "esp32": "ESP32", "wired": "USB"}.get(
        str(status.get("mode") or ""), ""
    )
    if state == "connected":
        parts = [part for part in (mode, tr("已連線")) if part]
        return "● " + " · ".join(parts), "#138A36"
    if state in {"starting", "searching", "disconnected", "idle_disconnected"}:
        return "● " + " · ".join(
            part for part in (mode, tr("搜尋中")) if part
        ), "#D97A00"
    return tr("● 未連線"), "#777777"


def clamp_unit(value):
    return max(-1.0, min(1.0, float(value)))


def blend_hex(background, foreground, amount):
    """Blend two #RRGGBB colours; Tk Canvas has no alpha channel."""
    amount = max(0.0, min(1.0, float(amount)))
    try:
        bg = tuple(int(background[index:index + 2], 16) for index in (1, 3, 5))
        fg = tuple(int(foreground[index:index + 2], 16) for index in (1, 3, 5))
    except (TypeError, ValueError):
        return foreground
    mixed = tuple(
        round(left + (right - left) * amount)
        for left, right in zip(bg, fg)
    )
    return "#{:02X}{:02X}{:02X}".format(*mixed)


def encode_rgba_png(width, height, scanlines):
    """Encode filter-prefixed RGBA rows as a small dependency-free PNG."""
    width = int(width)
    height = int(height)
    expected_size = height * (1 + width * 4)
    if width <= 0 or height <= 0 or len(scanlines) != expected_size:
        raise ValueError("invalid RGBA scanline buffer")

    def chunk(kind, payload):
        body = kind + payload
        return (
            struct.pack(">I", len(payload))
            + body
            + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
        )

    header = struct.pack(
        ">IIBBBBB",
        width,
        height,
        8,
        6,
        0,
        0,
        0,
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(scanlines, level=1))
        + chunk(b"IEND", b"")
    )


def encode_rgba_png_region(
    source_width,
    source_scanlines,
    left,
    top,
    width,
    height,
):
    """Encode one rectangular region from filter-prefixed RGBA rows."""
    source_width = int(source_width)
    left = int(left)
    top = int(top)
    width = int(width)
    height = int(height)
    source_stride = 1 + source_width * 4
    region_stride = 1 + width * 4
    region = bytearray(region_stride * height)
    byte_width = width * 4
    for row in range(height):
        source_start = (
            (top + row) * source_stride + 1 + left * 4
        )
        region_start = row * region_stride + 1
        region[region_start:region_start + byte_width] = (
            source_scanlines[source_start:source_start + byte_width]
        )
    return encode_rgba_png(width, height, region)


def curve_output_radii(points):
    """Return the four applied-curve Y ranges used by the radial overlay."""
    normalized = [
        (
            max(0.0, min(1.0, float(point[0]))),
            max(0.0, min(1.0, float(point[1]))),
        )
        for point in points
    ]
    return tuple(
        (normalized[index][1], normalized[index + 1][1])
        for index in range(max(0, len(normalized) - 1))
    )


def shape_ease_amount(delta_seconds, time_constant=0.05):
    delta = max(0.0, min(0.1, float(delta_seconds)))
    constant = max(1e-6, float(time_constant))
    return 1.0 - math.exp(-delta / constant)


def normalize_test_parameter(value, minimum, maximum, step):
    """Clamp and snap a tester parameter exactly like the settings sliders."""
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError("parameter value must be finite")
    minimum = float(minimum)
    maximum = float(maximum)
    step = float(step)
    if step <= 0.0:
        raise ValueError("step must be positive")
    numeric = max(minimum, min(maximum, numeric))
    numeric = minimum + round((numeric - minimum) / step) * step
    return max(minimum, min(maximum, numeric))


@dataclass
class StickHistory:
    # Five seconds at 8 kHz needs 40,000 points. Keep headroom without
    # allocating per-report UI objects.
    trail: deque = field(default_factory=lambda: deque(maxlen=65536))
    next_sequence: int = 0
    shape_target: list = field(
        default_factory=lambda: [0.0] * SHAPE_BIN_COUNT
    )
    shape_display: list = field(
        default_factory=lambda: [0.0] * SHAPE_BIN_COUNT
    )
    shape_target_angles: list = field(
        default_factory=lambda: [
            index / SHAPE_BIN_COUNT * 2.0 * math.pi
            for index in range(SHAPE_BIN_COUNT)
        ]
    )
    shape_display_angles: list = field(
        default_factory=lambda: [
            index / SHAPE_BIN_COUNT * 2.0 * math.pi
            for index in range(SHAPE_BIN_COUNT)
        ]
    )
    covered_bins: set = field(default_factory=set)
    maximum_radius: float = 0.0
    shape_revision: int = 0
    shape_pending: bool = False
    shape_frozen: bool = False
    last_shape_growth_at: float = 0.0

    def add(self, x, y, now, record_shape):
        x = clamp_unit(x)
        y = clamp_unit(y)
        radius = min(1.5, math.hypot(x, y))
        sequence = self.next_sequence
        self.next_sequence += 1
        self.trail.append((float(now), x, y, sequence))
        if record_shape and not self.shape_frozen:
            self.maximum_radius = max(self.maximum_radius, radius)
        if record_shape and not self.shape_frozen and radius > 0.03:
            angle = math.atan2(y, x) % (2.0 * math.pi)
            center_bin = int(
                angle / (2.0 * math.pi) * SHAPE_BIN_COUNT
            ) % SHAPE_BIN_COUNT
            center_was_measured = center_bin in self.covered_bins
            self.covered_bins.add(center_bin)
            shape_grew = False
            for offset, weight in ((0, 1.0), (-1, 0.86), (1, 0.86)):
                index = (center_bin + offset) % SHAPE_BIN_COUNT
                candidate_radius = radius * weight
                if offset == 0:
                    if (
                        not center_was_measured
                        or candidate_radius > self.shape_target[index]
                    ):
                        self.shape_target[index] = candidate_radius
                        # Preserve the actual sample direction. Re-projecting
                        # this radius onto the 5-degree bin centre visibly
                        # shears a square output envelope.
                        self.shape_target_angles[index] = angle
                        shape_grew = True
                    continue
                # Neighbours only bridge temporarily unmeasured sectors. A
                # real sample always owns its own sector and cannot later be
                # displaced by a synthetic neighbour.
                if (
                    index not in self.covered_bins
                    and candidate_radius > self.shape_target[index]
                ):
                    self.shape_target[index] = candidate_radius
                    self.shape_target_angles[index] = (
                        index / SHAPE_BIN_COUNT * 2.0 * math.pi
                    )
                    shape_grew = True
            if shape_grew:
                self.shape_pending = True
                self.last_shape_growth_at = float(now)

    def advance_shape(self, amount=0.18):
        if self.shape_frozen:
            return False
        changed = False
        pending = False
        for index, target in enumerate(self.shape_target):
            current = self.shape_display[index]
            self.shape_display_angles[index] = (
                self.shape_target_angles[index]
            )
            remaining = target - current
            if remaining <= 0.0:
                continue
            if remaining <= 1e-4:
                self.shape_display[index] = target
                changed = True
                continue
            updated = current + (
                target - current
            ) * max(0.01, min(1.0, amount))
            self.shape_display[index] = updated
            if target - updated > 1e-4:
                pending = True
            changed = True
        self.shape_pending = pending
        if changed:
            self.shape_revision += 1
        return changed

    def freeze_shape_if_complete(
        self,
        now,
        settle_seconds=SHAPE_CAPTURE_SETTLE_SECONDS,
    ):
        if self.shape_frozen:
            return False
        if len(self.covered_bins) < SHAPE_BIN_COUNT or self.shape_pending:
            return False
        if (
            float(now) - self.last_shape_growth_at
            < max(0.0, float(settle_seconds))
        ):
            return False
        self.shape_frozen = True
        return True

    def prune(self, now, length_seconds):
        cutoff = float(now) - max(0.1, float(length_seconds))
        while self.trail and self.trail[0][0] < cutoff:
            self.trail.popleft()

    def shape_statistics(self):
        coverage = len(self.covered_bins) / SHAPE_BIN_COUNT * 100.0
        measured = [
            self.shape_target[index]
            for index in self.covered_bins
            if self.shape_target[index] > 0.0
        ]
        error = (
            sum(abs(radius - 1.0) for radius in measured)
            / len(measured) * 100.0
            if measured else None
        )
        return coverage, error, self.maximum_radius * 100.0

    def reset(self):
        self.trail.clear()
        self.next_sequence = 0
        self.shape_target[:] = [0.0] * SHAPE_BIN_COUNT
        self.shape_display[:] = [0.0] * SHAPE_BIN_COUNT
        self.shape_target_angles[:] = [
            index / SHAPE_BIN_COUNT * 2.0 * math.pi
            for index in range(SHAPE_BIN_COUNT)
        ]
        self.shape_display_angles[:] = list(self.shape_target_angles)
        self.covered_bins.clear()
        self.maximum_radius = 0.0
        self.shape_revision += 1
        self.shape_pending = False
        self.shape_frozen = False
        self.last_shape_growth_at = 0.0


@dataclass
class ButtonEvent:
    source: str
    target: str
    layer: str
    started_at: float
    released_at: float | None = None


class StickPlot:
    def __init__(self, owner, parent, side):
        self.owner = owner
        self.side = side
        self.trail_color = (
            TRAIL_COLOR if side == "left" else RIGHT_TRAIL_COLOR
        )
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self._drag_start = None
        self._static_key = None
        self._last_trace_draw_at = 0.0
        self._drawn_shape_revision = None
        self._trail_bucket_items = {}
        self._trail_bucket_coords = {}
        self._trail_free_items = []
        self._trail_selected_sequences = deque()
        self._trail_last_processed_sequence = -1
        self._trail_render_percent = None
        # Transparent tiles keep high-rate report count independent from the
        # Canvas item count while avoiding full 320x320 PNG updates.
        self._bitmap_enabled = True
        self._bitmap_photo = None
        self._bitmap_item = None
        self._bitmap_scanlines = None
        self._bitmap_pixel_expiry = None
        self._bitmap_expiry_heap = []
        self._bitmap_last_processed_sequence = -1
        self._bitmap_render_config = None
        self._bitmap_tile_photos = {}
        self._bitmap_tile_items = {}
        self._bitmap_tile_live_counts = {}
        self._bitmap_presented = False
        self._dot_item = None
        self._dot_coords = None
        self._dynamic_deadzone_item = None
        self._dynamic_deadzone_coords = None
        self._dynamic_deadzone_visible = False
        self._presentation_times = deque(maxlen=512)
        self._fps_item = None
        self._fps_text = None
        self._last_fps_draw_at = 0.0
        tr = getattr(
            getattr(self.owner, "gui", None),
            "tr",
            lambda value: value,
        )
        self.frame = ttk.LabelFrame(
            parent,
            text=tr("左搖桿" if side == "left" else "右搖桿"),
            padding=(6, 4),
        )
        self.frame.columnconfigure(0, weight=1)
        # Keep the header, canvas and labels in one fixed-width column.
        # The outer LabelFrame can then remain compact instead of stretching
        # a wide grey area around the radar plot.
        self.plot_content = ttk.Frame(self.frame)
        self.plot_content.grid(row=0, column=0, sticky="n")
        self.plot_content.columnconfigure(0, weight=1)
        self.header = ttk.Frame(self.plot_content)
        self.header.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        self.header.columnconfigure(0, weight=1)
        self.source_var = tk.StringVar(value=tr("合成結果"))
        self.source_combo = ttk.Combobox(
            self.header,
            textvariable=self.source_var,
            state="readonly",
            width=12,
            values=tuple(
                tr(source)
                for source in ("實體搖桿", "陀螺儀", "合成結果")
            ),
        )
        self.source_combo.grid(row=0, column=1, sticky="e")
        self.source_label = ttk.Label(
            self.header, text=tr("實際輸入")
        )
        ttk.Button(
            self.header,
            text="−",
            width=3,
            command=lambda: self.change_zoom(1.0 / 1.25),
        ).grid(row=0, column=2, padx=(8, 2))
        ttk.Button(
            self.header,
            text="+",
            width=3,
            command=lambda: self.change_zoom(1.25),
        ).grid(row=0, column=3, padx=2)
        ttk.Button(
            self.header,
            text=self.owner.gui.tr("重設"),
            width=5,
            command=self.reset_view,
        ).grid(row=0, column=4, padx=(2, 0))

        self.canvas = tk.Canvas(
            self.plot_content,
            width=PLOT_SIZE,
            height=PLOT_SIZE,
            background="#FFFFFF",
            highlightthickness=1,
            highlightbackground="#A0A0A0",
        )
        self.canvas.grid(row=1, column=0)
        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._drag)
        self.canvas.bind("<ButtonRelease-1>", self._end_drag)
        self.canvas.bind("<MouseWheel>", self._mouse_wheel)
        # Source-specific descriptions have very different text lengths.
        # Contain all three information rows in a non-propagating fixed-size
        # panel so their requested widths cannot resize the radar column.
        self.info_panel = ttk.Frame(
            self.plot_content,
            width=PLOT_SIZE,
            height=64,
        )
        self.info_panel.grid(
            row=2, column=0, sticky="ew", pady=(5, 0)
        )
        self.info_panel.grid_propagate(False)
        self.info_panel.columnconfigure(0, weight=1)
        self.value_var = tk.StringVar(
            value=tr("X 0.000   Y 0.000   半徑 0.0%")
        )
        ttk.Label(
            self.info_panel, textvariable=self.value_var, anchor="center"
        ).grid(row=0, column=0, sticky="ew")
        self.detail_var = tk.StringVar(value=tr("等待輸入"))
        ttk.Label(
            self.info_panel,
            textvariable=self.detail_var,
            anchor="center",
            foreground="#666666",
        ).grid(row=1, column=0, sticky="ew", pady=(2, 0))
        self.metrics_var = tk.StringVar(
            value=tr("輸出形狀記錄未啟用")
        )
        self.metrics_label = ttk.Label(
            self.info_panel,
            textvariable=self.metrics_var,
            anchor="center",
            foreground=SHAPE_COLOR,
        )
        self.metrics_label.grid(
            row=2, column=0, sticky="ew", pady=(2, 0)
        )
        self.gyro_legend = ttk.Frame(self.info_panel)
        self.gyro_legend.grid(
            row=2, column=0, sticky="ew", pady=(2, 0)
        )
        legend_content = ttk.Frame(self.gyro_legend)
        legend_content.pack(anchor="center")
        ttk.Label(
            legend_content, text=tr("反應")
        ).pack(side="left", padx=(0, 3))
        for text, color in zip(
            ("25%", "50%", "75%", "100%"), GYRO_RESPONSE_COLORS
        ):
            ttk.Label(
                legend_content, text=text, foreground=color
            ).pack(side="left", padx=(0, 3))
        ttk.Separator(
            legend_content, orient="vertical"
        ).pack(side="left", fill="y", padx=3)
        for text, color in (
            ("基礎", GYRO_BASE_COLOR),
            ("動態", GYRO_ACTIVE_COLOR),
            ("反死區", GYRO_ANTI_DEADZONE_COLOR),
        ):
            ttk.Label(
                legend_content, text=tr(text), foreground=color
            ).pack(side="left", padx=(0, 4))
        # Keep both alternatives managed in the same cell. Raising one instead
        # of grid_remove()/grid() prevents Tk from recalculating this row and
        # nudging the radar when the source changes.
        self.metrics_label.lift()
        self._gyro_legend_visible = False
        self.source_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self.owner._on_source_changed(self.side),
        )

    def change_zoom(self, factor):
        previous = self.zoom
        self.zoom = max(1.0, min(4.0, self.zoom * float(factor)))
        if self.zoom <= 1.001:
            self.zoom = 1.0
            self.pan_x = 0.0
            self.pan_y = 0.0
        elif previous > 0.0:
            ratio = self.zoom / previous
            self.pan_x *= ratio
            self.pan_y *= ratio
            self._clamp_pan()

    def reset_view(self):
        self.zoom = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0

    def _clamp_pan(self):
        if self.zoom <= 1.0:
            self.pan_x = 0.0
            self.pan_y = 0.0
            return
        limit = PLOT_RADIUS * self.zoom * 0.82
        self.pan_x = max(-limit, min(limit, self.pan_x))
        self.pan_y = max(-limit, min(limit, self.pan_y))

    def _start_drag(self, event):
        if self.zoom <= 1.0:
            return
        self._drag_start = (event.x, event.y)
        self.canvas.configure(cursor="fleur")

    def _drag(self, event):
        if self._drag_start is None or self.zoom <= 1.0:
            return
        start_x, start_y = self._drag_start
        self.pan_x += event.x - start_x
        self.pan_y += event.y - start_y
        self._drag_start = (event.x, event.y)
        self._clamp_pan()

    def _end_drag(self, _event=None):
        self._drag_start = None
        self.canvas.configure(cursor="")

    def _mouse_wheel(self, event):
        self.change_zoom(1.25 if event.delta > 0 else 1.0 / 1.25)

    def _apply_view_transform(self, tag):
        if self.zoom != 1.0:
            self.canvas.scale(
                tag,
                PLOT_CENTER,
                PLOT_CENTER,
                self.zoom,
                self.zoom,
            )
        if self.pan_x or self.pan_y:
            self.canvas.move(tag, self.pan_x, self.pan_y)

    def _view_point(self, x, y):
        return (
            PLOT_CENTER + x * PLOT_RADIUS * self.zoom + self.pan_x,
            PLOT_CENTER - y * PLOT_RADIUS * self.zoom + self.pan_y,
        )

    def selected_source(self):
        """Return the stable source key behind the localized combobox label."""
        selected = self.source_var.get()
        tr = getattr(
            getattr(self.owner, "gui", None),
            "tr",
            lambda value: value,
        )
        for source in ("實體搖桿", "陀螺儀", "合成結果", "實際輸入"):
            if selected in (source, tr(source)):
                return source
        return "合成結果"

    def set_source_capability(self, is_s2p, gyro_available):
        tr = self.owner.gui.tr
        if not is_s2p:
            self.source_combo.grid_remove()
            self.source_label.grid(row=0, column=1, sticky="e")
            self.source_var.set(tr("實際輸入"))
            return
        self.source_label.grid_remove()
        selected = self.selected_source()
        sources = ["實體搖桿", "合成結果"]
        if gyro_available:
            sources.insert(1, "陀螺儀")
        self.source_combo.configure(
            values=tuple(tr(source) for source in sources)
        )
        if selected not in sources:
            selected = "合成結果"
        self.source_var.set(tr(selected))
        self.source_combo.grid(row=0, column=1, sticky="e")

    def _oval(self, radius, tag=None, **options):
        center = PLOT_CENTER
        if tag is not None:
            options["tags"] = tag
        return self.canvas.create_oval(
            center - radius,
            center - radius,
            center + radius,
            center + radius,
            **options,
        )

    def _draw_base(self):
        center = PLOT_CENTER
        radius = PLOT_RADIUS
        self.canvas.create_line(
            center - radius, center, center + radius, center,
            fill=AXIS_COLOR,
            tags="static",
        )
        self.canvas.create_line(
            center, center - radius, center, center + radius,
            fill=AXIS_COLOR,
            tags="static",
        )
        self._oval(radius * 0.5, tag="static", outline=GRID_COLOR)
        self._oval(radius, tag="static", outline=GRID_COLOR)

    def _draw_curve_overlay(self, side_data, history=None, tag="static"):
        points = side_data.get("curve_points") or ()
        ranges = tuple(curve_output_radii(points))[:len(CURVE_COLORS)]
        profile = None
        if history is not None and max(history.shape_display, default=0.0) > 0.0:
            profile = tuple(
                max(0.0, min(1.4, radius))
                for radius in history.shape_display
            )

        def draw_profile(scale, color):
            profile_points = []
            for point_index, radius in enumerate(profile):
                angles = getattr(
                    history,
                    "shape_display_angles",
                    (),
                )
                angle = (
                    angles[point_index]
                    if point_index < len(angles)
                    else point_index / SHAPE_BIN_COUNT * 2.0 * math.pi
                )
                x = math.cos(angle) * radius * scale
                y = math.sin(angle) * radius * scale
                if tag == "static":
                    profile_points.extend((
                        PLOT_CENTER + x * PLOT_RADIUS,
                        PLOT_CENTER - y * PLOT_RADIUS,
                    ))
                else:
                    profile_points.extend(self._view_point(x, y))
            if len(profile_points) >= 6:
                self.canvas.create_polygon(
                    *profile_points, fill=color, outline="", tags=tag
                )

        # Fill each output band rather than using thick outlines. Drawing from
        # outside in makes adjacent curve regions meet exactly, with no white
        # seams left by Canvas stroke rasterisation.
        for index in range(len(ranges) - 1, -1, -1):
            inner, outer = ranges[index]
            inner_radius = max(0.0, inner) * PLOT_RADIUS
            outer_radius = max(inner_radius, outer * PLOT_RADIUS)
            if profile is not None:
                draw_profile(outer, CURVE_COLORS[index])
                if inner > 0.0:
                    draw_profile(inner, "#FFFFFF")
            elif outer_radius > 0.5:
                self._oval(
                    outer_radius,
                    tag=tag,
                    fill=CURVE_COLORS[index],
                    outline="",
                )
                if inner_radius > 0.5:
                    self._oval(
                        inner_radius,
                        tag=tag,
                        fill="#FFFFFF",
                        outline="",
                    )

    def _draw_curve_limits(self, side_data, tag="static"):
        def draw_limit(radius, **options):
            if tag == "static":
                self._oval(radius, tag=tag, **options)
                return
            center_x = PLOT_CENTER + self.pan_x
            center_y = PLOT_CENTER + self.pan_y
            scaled_radius = radius * self.zoom
            self.canvas.create_oval(
                center_x - scaled_radius,
                center_y - scaled_radius,
                center_x + scaled_radius,
                center_y + scaled_radius,
                tags=tag,
                **options,
            )

        deadzone = max(0.0, min(1.0, float(
            side_data.get("deadzone", 0.0) or 0.0
        )))
        if deadzone > 0.0:
            draw_limit(
                max(2.0, deadzone * PLOT_RADIUS),
                fill=DEADZONE_COLOR,
                outline=OUTER_DEADZONE_COLOR,
            )
        outer = max(0.0, min(1.0, float(
            side_data.get("outer_deadzone", 0.0) or 0.0
        )))
        if outer > 0.0:
            draw_limit(
                max(1.0, (1.0 - outer) * PLOT_RADIUS),
                outline=OUTER_DEADZONE_COLOR,
                dash=(4, 3),
                width=2,
            )

    def _draw_gyro_overlay(self, telemetry):
        gyro = telemetry.get("gyro") or {}
        motion_mode = str(gyro.get("motion_mode", "CENTER"))
        sensitivity = max(
            1e-6,
            float(gyro.get("stick_sensitivity", 1.0) or 1.0),
        )
        full_speed = (
            max(1e-6, float(gyro.get("tilt_max_angle", 35.0) or 35.0))
            if motion_mode == "TILT"
            else 1.0 / (sensitivity * 0.016)
        )
        base = max(0.0, float(
            gyro.get(
                "tilt_deadzone" if motion_mode == "TILT" else "base_deadzone",
                0.0,
            ) or 0.0
        ))
        mode = str(gyro.get("response_curve", "LINEAR"))
        strength = max(0.0, min(1.0, float(
            gyro.get("curve_strength", 0.0) or 0.0
        )))
        thresholds = (0.25, 0.50, 0.75, 1.0)
        mapped = []
        for value in thresholds:
            mapped_value, _ = _apply_gyro_response_curve(
                value, 0.0, mode, strength
            )
            mapped.append(abs(mapped_value))
        # Gyro response is not a stick-sector map. Show its response curve as
        # unobtrusive contour guides instead of filled coloured blocks.
        for index, output in enumerate(mapped):
            # Keep the 100% contour just inside the radar boundary. The dark
            # outer frame is drawn last and otherwise occupies the exact same
            # pixels, hiding the red guide completely.
            radius = min(
                output * PLOT_RADIUS,
                PLOT_RADIUS - GYRO_RESPONSE_OUTER_INSET,
            )
            self._oval(
                radius,
                tag="static",
                outline=GYRO_RESPONSE_COLORS[index],
                width=1,
            )

        base_radius = max(3.0, min(0.30, base / full_speed) * PLOT_RADIUS)
        self._oval(
            base_radius,
            tag="static",
            outline=GYRO_BASE_COLOR,
            dash=(4, 3),
            width=2,
        )
        anti_deadzone = max(0.0, min(0.30, float(
            gyro.get("stick_anti_deadzone", 0.0) or 0.0
        )))
        if anti_deadzone > 0.0:
            self._oval(
                max(2.0, anti_deadzone * PLOT_RADIUS),
                tag="static",
                outline=GYRO_ANTI_DEADZONE_COLOR,
                dash=(2, 2),
                width=2,
            )

    def _draw_dynamic_gyro_deadzone(self, telemetry):
        gyro = telemetry.get("gyro") or {}
        motion_mode = str(gyro.get("motion_mode", "CENTER"))
        sensitivity = max(
            1e-6, float(gyro.get("stick_sensitivity", 1.0) or 1.0)
        )
        full_speed = (
            max(1e-6, float(gyro.get("tilt_max_angle", 35.0) or 35.0))
            if motion_mode == "TILT"
            else 1.0 / (sensitivity * 0.016)
        )
        active = max(
            0.0, float(gyro.get("active_deadzone", 0.0) or 0.0)
        )
        radius = max(2.0, min(0.30, active / full_speed) * PLOT_RADIUS)
        center_x, center_y = self._view_point(0.0, 0.0)
        radius *= self.zoom
        item = getattr(self, "_dynamic_deadzone_item", None)
        if item is None:
            item = self.canvas.create_oval(
                0, 0, 0, 0,
                outline=GYRO_ACTIVE_COLOR,
                width=2,
                tags="dynamic",
            )
            self._dynamic_deadzone_item = item
        coordinates = (
            center_x - radius,
            center_y - radius,
            center_x + radius,
            center_y + radius,
        )
        if self._dynamic_deadzone_coords != coordinates:
            self.canvas.coords(item, *coordinates)
            self._dynamic_deadzone_coords = coordinates
        if not self._dynamic_deadzone_visible:
            self.canvas.itemconfigure(item, state="normal")
            self._dynamic_deadzone_visible = True

    def _update_gyro_legend_visibility(self, telemetry, is_s2p, source):
        legend = getattr(self, "gyro_legend", None)
        metrics = getattr(self, "metrics_label", None)
        if legend is None or metrics is None:
            return
        gyro = telemetry.get("gyro") or {}
        gyro_visible = bool(
            is_s2p
            and (
                (
                    source == "陀螺儀"
                    and _gyro_mapping_enabled(gyro)
                )
                or (
                    source == "合成結果"
                    and _gyro_targets_side(gyro, self.side)
                )
            )
        )
        show = bool(
            gyro_visible and self.owner.show_gyro_legend_var.get()
        )
        if getattr(self, "_gyro_legend_visible", None) == show:
            return
        if show:
            legend.lift()
        else:
            metrics.lift()
        self._gyro_legend_visible = show

    def _set_text_if_changed(self, variable_name, text):
        cache_name = f"_last_{variable_name}"
        if getattr(self, cache_name, None) == text:
            return
        getattr(self, variable_name).set(text)
        setattr(self, cache_name, text)

    def _draw_shape(self, history):
        if not self.owner.shape_enabled_var.get():
            return
        points = []
        for index, radius in enumerate(history.shape_display):
            angles = getattr(history, "shape_display_angles", ())
            angle = (
                angles[index]
                if index < len(angles)
                else index / SHAPE_BIN_COUNT * 2.0 * math.pi
            )
            display_radius = min(1.4, radius)
            points.extend((
                *self._view_point(
                    math.cos(angle) * display_radius,
                    math.sin(angle) * display_radius,
                ),
            ))
        if len(points) >= 6 and max(history.shape_display, default=0.0) > 0.0:
            points.extend(points[:2])
            self.canvas.create_line(
                *points,
                fill=SHAPE_COLOR,
                width=2,
                # Preserve the true measured sample coordinates and restore
                # the light visual rounding between adjacent points.
                smooth=True,
                tags="trace",
            )

    def _draw_trail(self, history, now):
        if not hasattr(self, "_trail_bucket_items"):
            self._trail_bucket_items = {}
            self._trail_bucket_coords = {}
            self._trail_free_items = []
            self._trail_selected_sequences = deque()
            self._trail_last_processed_sequence = -1
            self._trail_render_percent = None
        trail = history.trail
        if not trail:
            for item in self._trail_bucket_items.values():
                self.canvas.itemconfigure(item, state="hidden")
                self._trail_free_items.append(item)
            self._trail_bucket_items.clear()
            self._trail_bucket_coords.clear()
            self._trail_selected_sequences.clear()
            self._trail_last_processed_sequence = -1
            return
        # Every visible point is one original controller report. The stable
        # sequence selection makes 100% exact, while lower percentages retain
        # an evenly distributed subset without reassigning old Canvas items.
        display_percent = int(round(max(
            1.0,
            min(
                100.0,
                float(self.owner.sample_display_percent_var.get()),
            ),
        )))
        requested_count = (
            len(trail) * display_percent + 99
        ) // 100
        cap_stride = (
            max(1, math.ceil(len(trail) / MAX_CANVAS_TRAIL_ITEMS))
            if requested_count > MAX_CANVAS_TRAIL_ITEMS
            else 1
        )
        selection_key = (display_percent, cap_stride)

        def selected_for_canvas(sample):
            sequence = sample[3]
            if cap_stride > 1:
                return sequence % cap_stride == 0
            return (
                display_percent >= 100
                or (
                    sequence * display_percent
                ) % 100 < display_percent
            )

        newest_sequence = trail[-1][3]
        rebuild = (
            self._trail_render_percent != selection_key
            or newest_sequence < self._trail_last_processed_sequence
        )
        if rebuild:
            for item in self._trail_bucket_items.values():
                self.canvas.itemconfigure(item, state="hidden")
                self._trail_free_items.append(item)
            self._trail_bucket_items.clear()
            self._trail_bucket_coords.clear()
            self._trail_selected_sequences.clear()
            self._trail_last_processed_sequence = -1
            self._trail_render_percent = selection_key
            new_samples = [
                sample for sample in trail
                if selected_for_canvas(sample)
            ]
            selection_preapplied = True
            if cap_stride > 1 and new_samples:
                # Keep the full requested time span represented on the first
                # fallback frame without increasing the bounded item count.
                new_samples[0] = trail[0]
                new_samples[-1] = trail[-1]
        else:
            selection_preapplied = False
            new_samples = []
            for sample in reversed(trail):
                if sample[3] <= self._trail_last_processed_sequence:
                    break
                new_samples.append(sample)
            new_samples.reverse()

        oldest_sequence = trail[0][3]
        while (
            self._trail_selected_sequences
            and self._trail_selected_sequences[0] < oldest_sequence
        ):
            sequence = self._trail_selected_sequences.popleft()
            item = self._trail_bucket_items.pop(sequence, None)
            self._trail_bucket_coords.pop(sequence, None)
            if item is None:
                continue
            self.canvas.itemconfigure(item, state="hidden")
            self._trail_free_items.append(item)

        for _time_value, x, y, sequence in new_samples:
            self._trail_last_processed_sequence = sequence
            if (
                not selection_preapplied
                and not selected_for_canvas(
                    (_time_value, x, y, sequence)
                )
            ):
                continue
            bucket = sequence
            item = self._trail_bucket_items.get(bucket)
            is_new = item is None
            if is_new:
                if self._trail_free_items:
                    item = self._trail_free_items.pop()
                else:
                    item = self.canvas.create_oval(
                        0, 0, 0, 0,
                        fill=self.trail_color,
                        outline="",
                        tags="trail_points",
                        state="hidden",
                    )
                self._trail_bucket_items[bucket] = item
                self._trail_selected_sequences.append(bucket)
            point_x, point_y = self._view_point(x, y)
            radius = 1.15
            coordinates = (
                point_x - radius,
                point_y - radius,
                point_x + radius,
                point_y + radius,
            )
            if self._trail_bucket_coords.get(bucket) != coordinates:
                self.canvas.coords(item, *coordinates)
                self._trail_bucket_coords[bucket] = coordinates
            if is_new:
                self.canvas.itemconfigure(item, state="normal")
            while (
                len(self._trail_selected_sequences)
                > MAX_CANVAS_TRAIL_ITEMS
            ):
                expired = self._trail_selected_sequences.popleft()
                expired_item = self._trail_bucket_items.pop(expired, None)
                self._trail_bucket_coords.pop(expired, None)
                if expired_item is not None:
                    self.canvas.itemconfigure(
                        expired_item, state="hidden"
                    )
                    self._trail_free_items.append(expired_item)
        self._trail_last_processed_sequence = newest_sequence

    def _draw_trail_bitmap(self, history, now=None):
        now = time.perf_counter() if now is None else float(now)
        self._bitmap_presented = False
        trail = history.trail
        display_percent = int(round(max(
            1.0,
            min(
                100.0,
                float(self.owner.sample_display_percent_var.get()),
            ),
        )))
        trail_length = max(
            0.25, float(self.owner.trail_length_var.get())
        )
        newest_sequence = trail[-1][3] if trail else -1
        render_config = (
            display_percent,
            self.zoom,
            self.pan_x,
            self.pan_y,
            trail_length,
        )
        try:
            pixel_count = PLOT_SIZE * PLOT_SIZE
            row_stride = 1 + PLOT_SIZE * 4
            tile_photos = getattr(self, "_bitmap_tile_photos", None)
            tile_items = getattr(self, "_bitmap_tile_items", None)
            tile_live_counts = getattr(
                self, "_bitmap_tile_live_counts", None
            )
            if tile_photos is None:
                tile_photos = {}
                self._bitmap_tile_photos = tile_photos
            if tile_items is None:
                tile_items = {}
                self._bitmap_tile_items = tile_items
            if tile_live_counts is None:
                tile_live_counts = {}
                self._bitmap_tile_live_counts = tile_live_counts
            if self._bitmap_scanlines is None:
                self._bitmap_scanlines = bytearray(
                    row_stride * PLOT_SIZE
                )
                self._bitmap_pixel_expiry = [0.0] * pixel_count
            rebuild = (
                self._bitmap_render_config != render_config
                or newest_sequence
                < self._bitmap_last_processed_sequence
            )
            dirty_tiles = set()
            if rebuild:
                if tile_items or self._bitmap_item is not None:
                    self.canvas.delete("trail_bitmap")
                tile_photos.clear()
                tile_items.clear()
                tile_live_counts.clear()
                self._bitmap_photo = None
                self._bitmap_item = None
                self._bitmap_scanlines[:] = (
                    b"\x00" * len(self._bitmap_scanlines)
                )
                self._bitmap_pixel_expiry[:] = [0.0] * pixel_count
                self._bitmap_expiry_heap.clear()
                self._bitmap_last_processed_sequence = -1
                self._bitmap_render_config = render_config
                new_samples = trail
            else:
                new_samples = []
                for sample in reversed(trail):
                    if (
                        sample[3]
                        <= self._bitmap_last_processed_sequence
                    ):
                        break
                    new_samples.append(sample)
                new_samples.reverse()

            # Merge the final pixel coverage rather than only rounded sample
            # centres. This preserves the exact sub-pixel dot footprint while
            # ensuring each output pixel is touched at most once per frame.
            frame_pixels = {}
            radius = 1.15
            radius_squared = radius * radius
            for time_value, x, y, sequence in new_samples:
                self._bitmap_last_processed_sequence = sequence
                if (
                    display_percent < 100
                    and (sequence * display_percent) % 100
                    >= display_percent
                ):
                    continue
                point_x, point_y = self._view_point(x, y)
                expiry = float(time_value) + trail_length
                if expiry <= now:
                    continue
                minimum_x = max(
                    0, int(math.floor(point_x - radius))
                )
                maximum_x = min(
                    PLOT_SIZE - 1,
                    int(math.ceil(point_x + radius)),
                )
                minimum_y = max(
                    0, int(math.floor(point_y - radius))
                )
                maximum_y = min(
                    PLOT_SIZE - 1,
                    int(math.ceil(point_y + radius)),
                )
                for pixel_y in range(minimum_y, maximum_y + 1):
                    delta_y = (pixel_y + 0.5) - point_y
                    for pixel_x in range(minimum_x, maximum_x + 1):
                        delta_x = (pixel_x + 0.5) - point_x
                        if (
                            delta_x * delta_x + delta_y * delta_y
                            > radius_squared
                        ):
                            continue
                        pixel_index = pixel_y * PLOT_SIZE + pixel_x
                        if expiry > frame_pixels.get(pixel_index, 0.0):
                            frame_pixels[pixel_index] = expiry

            while (
                self._bitmap_expiry_heap
                and self._bitmap_expiry_heap[0][0] <= now
            ):
                expiry, pixel_index = heapq.heappop(
                    self._bitmap_expiry_heap
                )
                if (
                    self._bitmap_pixel_expiry[pixel_index]
                    != expiry
                ):
                    continue
                self._bitmap_pixel_expiry[pixel_index] = 0.0
                pixel_y, pixel_x = divmod(
                    pixel_index, PLOT_SIZE
                )
                rgba_index = (
                    pixel_y * row_stride + 1 + pixel_x * 4
                )
                if self._bitmap_scanlines[rgba_index + 3]:
                    self._bitmap_scanlines[
                        rgba_index:rgba_index + 4
                    ] = b"\x00\x00\x00\x00"
                    tile_key = (
                        pixel_x // TRAIL_TILE_SIZE,
                        pixel_y // TRAIL_TILE_SIZE,
                    )
                    tile_live_counts[tile_key] = max(
                        0, tile_live_counts.get(tile_key, 1) - 1
                    )
                    dirty_tiles.add(tile_key)

            color = tuple(
                int(self.trail_color[index:index + 2], 16)
                for index in (1, 3, 5)
            ) + (255,)
            color_bytes = bytes(color)
            for pixel_index, expiry in frame_pixels.items():
                if expiry <= self._bitmap_pixel_expiry[pixel_index]:
                    continue
                self._bitmap_pixel_expiry[pixel_index] = expiry
                heapq.heappush(
                    self._bitmap_expiry_heap,
                    (expiry, pixel_index),
                )
                pixel_y, pixel_x = divmod(pixel_index, PLOT_SIZE)
                rgba_index = pixel_y * row_stride + 1 + pixel_x * 4
                if not self._bitmap_scanlines[rgba_index + 3]:
                    self._bitmap_scanlines[
                        rgba_index:rgba_index + 4
                    ] = color_bytes
                    tile_key = (
                        pixel_x // TRAIL_TILE_SIZE,
                        pixel_y // TRAIL_TILE_SIZE,
                    )
                    tile_live_counts[tile_key] = (
                        tile_live_counts.get(tile_key, 0) + 1
                    )
                    dirty_tiles.add(tile_key)

            live_pixels = sum(tile_live_counts.values())
            if len(self._bitmap_expiry_heap) > max(
                2048, live_pixels * 8
            ):
                self._bitmap_expiry_heap = [
                    (expiry, pixel_index)
                    for pixel_index, expiry in enumerate(
                        self._bitmap_pixel_expiry
                    )
                    if expiry > now
                ]
                heapq.heapify(self._bitmap_expiry_heap)

            for tile_key in dirty_tiles:
                tile_x, tile_y = tile_key
                item = tile_items.get(tile_key)
                if tile_live_counts.get(tile_key, 0) <= 0:
                    tile_live_counts.pop(tile_key, None)
                    if item is not None:
                        self.canvas.delete(item)
                        tile_items.pop(tile_key, None)
                    # Drop the Tcl image only after no Canvas item references
                    # it. Reversing this order can crash Tk during a native
                    # Windows move/resize repaint.
                    tile_photos.pop(tile_key, None)
                    continue
                left = tile_x * TRAIL_TILE_SIZE
                top = tile_y * TRAIL_TILE_SIZE
                width = min(TRAIL_TILE_SIZE, PLOT_SIZE - left)
                height = min(TRAIL_TILE_SIZE, PLOT_SIZE - top)
                bitmap_data = encode_rgba_png_region(
                    PLOT_SIZE,
                    self._bitmap_scanlines,
                    left,
                    top,
                    width,
                    height,
                )
                photo = tile_photos.get(tile_key)
                if photo is None:
                    photo = tk.PhotoImage(
                        master=self.canvas,
                        data=bitmap_data,
                        format="png",
                    )
                    tile_photos[tile_key] = photo
                    item = self.canvas.create_image(
                        left,
                        top,
                        anchor="nw",
                        image=photo,
                        tags="trail_bitmap",
                    )
                    tile_items[tile_key] = item
                else:
                    photo.configure(data=bitmap_data, format="png")

            if tile_items:
                first_key = next(iter(tile_items))
                self._bitmap_item = tile_items[first_key]
                self._bitmap_photo = tile_photos[first_key]
                self.canvas.tag_raise("trail_bitmap")
            else:
                self._bitmap_item = None
                self._bitmap_photo = None
            self._bitmap_presented = bool(dirty_tiles)
            return True
        except (
            AttributeError,
            OSError,
            RuntimeError,
            TypeError,
            ValueError,
            tk.TclError,
        ):
            # An older Tk build without PNG PhotoImage support remains usable.
            if self._bitmap_item is not None:
                try:
                    self.canvas.delete("trail_bitmap")
                except tk.TclError:
                    pass
            self._bitmap_item = None
            self._bitmap_photo = None
            self._bitmap_tile_photos = {}
            self._bitmap_tile_items = {}
            self._bitmap_tile_live_counts = {}
            self._bitmap_enabled = False
            self._bitmap_render_config = None
            return False

    def _static_signature(self, side_data, telemetry, is_s2p):
        """Only include configuration data, never the live stick samples."""
        source = self.selected_source()
        key = (
            is_s2p,
            source,
            self.zoom,
            self.pan_x,
            self.pan_y,
            bool(self.owner.shape_enabled_var.get()),
        )
        if not is_s2p:
            return key
        if source == "實體搖桿":
            return key + (
                tuple(tuple(point) for point in side_data.get("curve_points") or ()),
                side_data.get("deadzone"),
                side_data.get("outer_deadzone"),
            )
        gyro = telemetry.get("gyro") or {}
        gyro_targets_side = _gyro_targets_side(gyro, self.side)
        if (
            source == "陀螺儀"
            and _gyro_mapping_enabled(gyro)
        ) or (
            source == "合成結果" and gyro_targets_side
        ):
            return key + (
                side_data.get("deadzone") if source == "合成結果" else None,
                (
                    side_data.get("outer_deadzone")
                    if source == "合成結果" else None
                ),
                gyro.get("base_deadzone"),
                gyro.get("stick_anti_deadzone"),
                gyro.get("response_curve"),
                gyro.get("curve_strength"),
                gyro.get("activation_mode"),
                tuple(gyro.get("activation_buttons") or ()),
                gyro.get("motion_mode"),
                gyro.get("stick_sensitivity"),
                gyro.get("tilt_max_angle"),
                gyro.get("tilt_deadzone"),
                gyro.get("x_ratio"),
                gyro.get("y_ratio"),
                gyro.get("smoothing_ms"),
                gyro.get("tilt_smoothing_ms"),
                gyro.get("adaptive_deadzone"),
                gyro.get("accel_suppression"),
                gyro.get("button_freeze_ms"),
                tuple(gyro.get("stabilization_buttons") or ()),
            )
        return key

    def _draw_static(self, side_data, telemetry, is_s2p):
        self.canvas.delete("all")
        self._trail_bucket_items = {}
        self._trail_bucket_coords = {}
        self._trail_free_items = []
        self._trail_selected_sequences = deque()
        self._trail_last_processed_sequence = -1
        self._trail_render_percent = None
        self._bitmap_item = None
        self._bitmap_photo = None
        self._bitmap_tile_photos = {}
        self._bitmap_tile_items = {}
        self._bitmap_tile_live_counts = {}
        self._bitmap_render_config = None
        self._dot_item = None
        self._dynamic_deadzone_item = None
        self._dynamic_deadzone_coords = None
        self._dynamic_deadzone_visible = False
        self._last_trace_draw_at = 0.0
        self._drawn_shape_revision = None
        self._draw_base()
        source = self.selected_source()
        if is_s2p and source == "實體搖桿":
            if not self.owner.shape_enabled_var.get():
                self._draw_curve_overlay(side_data)
            self._draw_curve_limits(side_data)
        elif is_s2p:
            gyro = telemetry.get("gyro") or {}
            if (
                source == "陀螺儀"
                and _gyro_mapping_enabled(gyro)
            ):
                self._draw_gyro_overlay(telemetry)
            elif (
                source == "合成結果"
                and _gyro_targets_side(gyro, self.side)
            ):
                # The combined output still obeys the stick's configured
                # centre/outer limits, while gyro contributes its own trigger
                # and anti-deadzone thresholds.
                self._draw_curve_limits(side_data)
                self._draw_gyro_overlay(telemetry)
        self._oval(PLOT_RADIUS, tag="static", outline="#555555", width=1)
        self._apply_view_transform("static")

    def draw(
        self,
        history,
        x,
        y,
        telemetry,
        is_s2p,
        update_details=True,
    ):
        frame_changed = False
        side_data = telemetry.get(self.side, {}) if is_s2p else {}
        source = self.selected_source()
        self._update_gyro_legend_visibility(telemetry, is_s2p, source)
        static_key = self._static_signature(side_data, telemetry, is_s2p)
        if static_key != self._static_key:
            self._draw_static(side_data, telemetry, is_s2p)
            self._static_key = static_key
            frame_changed = True
            if self._bitmap_item is not None:
                self.canvas.tag_raise("trail_bitmap", "static")
        now = time.perf_counter()
        # Redraw the measured envelope at up to 60 FPS only while its data is
        # changing. Once settled, keeping the tester open emits no trace Canvas
        # work until the capture is explicitly reset.
        trace_redrawn = False
        shape_enabled = bool(self.owner.shape_enabled_var.get())
        shape_needs_redraw = (
            shape_enabled
            and getattr(self, "_drawn_shape_revision", None)
            != history.shape_revision
        )
        if (
            shape_needs_redraw
            and now - self._last_trace_draw_at
            >= 1.0 / SHAPE_TRACE_REFRESH_HZ
        ):
            shape_enabled = bool(self.owner.shape_enabled_var.get())
            if shape_enabled:
                self.canvas.delete("trace")
                if is_s2p and source == "實體搖桿":
                    self._draw_curve_overlay(
                        side_data, history=history, tag="trace"
                    )
                    self._draw_curve_limits(side_data, tag="trace")
                self._draw_shape(history)
                self._drawn_shape_revision = history.shape_revision
                trace_redrawn = True
                frame_changed = True
            self._last_trace_draw_at = now
        # Incremental trail processing usually handles only the reports added
        # since the previous display frame and emits no Canvas command when
        # nothing changed. Running it at display cadence removes the old
        # visible 30 FPS stepping without rebuilding the complete history.
        if getattr(self, "_bitmap_enabled", False):
            if not self._draw_trail_bitmap(history, now):
                self._draw_trail(history, now)
                frame_changed = bool(history.trail) or frame_changed
            else:
                frame_changed = (
                    getattr(self, "_bitmap_presented", False)
                    or frame_changed
                )
        else:
            self._draw_trail(history, now)
            frame_changed = bool(history.trail) or frame_changed
        if trace_redrawn and self._bitmap_item is not None:
            # Animated physical-stick colour bands are recreated after the
            # raster item. Restore the intended bands -> trail -> live-dot
            # stacking order only on those 30 Hz overlay frames.
            self.canvas.tag_raise("trail_bitmap")
        gyro = telemetry.get("gyro") or {}
        show_dynamic_deadzone = bool(is_s2p and (
            (
                source == "陀螺儀"
                and _gyro_mapping_enabled(gyro)
            )
            or (
                source == "合成結果"
                and _gyro_targets_side(gyro, self.side)
            )
        ))
        if show_dynamic_deadzone:
            self._draw_dynamic_gyro_deadzone(telemetry)
        else:
            dynamic_deadzone = getattr(
                self, "_dynamic_deadzone_item", None
            )
            if dynamic_deadzone is not None:
                if self._dynamic_deadzone_visible:
                    self.canvas.itemconfigure(
                        dynamic_deadzone, state="hidden"
                    )
                    self._dynamic_deadzone_visible = False
                    frame_changed = True

        dot_x, dot_y = self._view_point(clamp_unit(x), clamp_unit(y))
        dot_radius = 2.0
        dot_item = getattr(self, "_dot_item", None)
        dot_is_new = dot_item is None
        if dot_item is None:
            dot_item = self.canvas.create_oval(
                0, 0, 0, 0,
                fill=self.trail_color,
                outline="",
                tags="dynamic",
            )
            self._dot_item = dot_item
        dot_coords = (
            dot_x - dot_radius,
            dot_y - dot_radius,
            dot_x + dot_radius,
            dot_y + dot_radius,
        )
        if getattr(self, "_dot_coords", None) != dot_coords:
            self.canvas.coords(dot_item, *dot_coords)
            self._dot_coords = dot_coords
            frame_changed = True
        if (
            dot_is_new
            or trace_redrawn
            or not getattr(self, "_bitmap_enabled", False)
        ):
            self.canvas.tag_raise(dot_item)
        if not update_details:
            return frame_changed
        tr = getattr(
            getattr(self.owner, "gui", None),
            "tr",
            lambda value: value,
        )
        radius = math.hypot(x, y)
        self._set_text_if_changed("value_var",
            tr(
                "X {x:+.3f}   Y {y:+.3f}   半徑 {radius:.1f}%"
            ).format(x=x, y=y, radius=radius * 100.0)
        )
        if (
            is_s2p
            and source == "陀螺儀"
            and _gyro_mapping_enabled(gyro)
        ):
            gyro = telemetry.get("gyro") or {}
            mode = str(gyro.get("motion_mode", "CENTER"))
            curve_names = {
                "LINEAR": tr("線性"),
                "LATE": tr("後段加速"),
                "EARLY": tr("前段加速"),
            }
            curve = curve_names.get(
                str(gyro.get("response_curve", "LINEAR")), tr("線性")
            )
            adaptive = float(
                gyro.get("adaptive_deadzone", 0.0) or 0.0
            ) * 100.0
            accel = float(
                gyro.get("accel_suppression", 0.0) or 0.0
            ) * 100.0
            freeze = float(gyro.get("button_freeze_ms", 0.0) or 0.0)
            self._set_text_if_changed("metrics_var",
                tr(
                    "{curve}   自適死區 {adaptive:.0f}%   "
                    "加速抑制 {accel:.0f}%   防晃 {freeze:.0f} ms"
                ).format(
                    curve=curve,
                    adaptive=adaptive,
                    accel=accel,
                    freeze=freeze,
                )
            )
        elif self.owner.shape_enabled_var.get():
            coverage, error, maximum = history.shape_statistics()
            error_text = "—" if error is None else f"{error:.2f}%"
            self._set_text_if_changed("metrics_var",
                tr(
                    "覆蓋 {coverage:.0f}%   圓度誤差 {error}   "
                    "最大 {maximum:.1f}%"
                ).format(
                    coverage=coverage,
                    error=error_text,
                    maximum=maximum,
                )
            )
        else:
            self._set_text_if_changed(
                "metrics_var", tr("輸出形狀記錄未啟用")
            )
        if is_s2p and source == "實體搖桿":
            segment = int(side_data.get("curve_segment", 0) or 0)
            deadzone = float(side_data.get("deadzone", 0.0) or 0.0)
            outer = float(side_data.get("outer_deadzone", 0.0) or 0.0)
            self._set_text_if_changed("detail_var",
                tr(
                    "區段 P{start}–P{end}   中心死區 {deadzone:.1f}%   "
                    "外圈死區 {outer:.1f}%"
                ).format(
                    start=segment,
                    end=segment + 1,
                    deadzone=deadzone * 100.0,
                    outer=outer * 100.0,
                )
            )
        elif is_s2p and source == "陀螺儀":
            gyro = telemetry.get("gyro") or {}
            mode = str(gyro.get("motion_mode", "CENTER"))
            active_deadzone = float(
                gyro.get("active_deadzone", 0.0) or 0.0
            )
            if mode == "TILT":
                maximum = float(
                    gyro.get("tilt_max_angle", 35.0) or 35.0
                )
                self._set_text_if_changed("detail_var",
                    tr(
                        "傾斜   最大角 {maximum:.0f}°   "
                        "死區 {deadzone:.2f}°"
                    ).format(
                        maximum=maximum,
                        deadzone=active_deadzone,
                    )
                )
            else:
                sensitivity = float(
                    gyro.get("stick_sensitivity", 1.0) or 1.0
                )
                self._set_text_if_changed("detail_var",
                    tr(
                        "回中   感度 {sensitivity:.1f}   "
                        "死區 {deadzone:.2f}°/s"
                    ).format(
                        sensitivity=sensitivity,
                        deadzone=active_deadzone,
                    )
                )
        elif is_s2p:
            gyro = telemetry.get("gyro") or {}
            if (
                source == "合成結果"
                and _gyro_targets_side(gyro, self.side)
            ):
                activation_mode = {
                    "HOLD": tr("按住"),
                    "TOGGLE": tr("切換"),
                }.get(str(gyro.get("activation_mode") or ""), tr("觸發"))
                button_items = [
                    str(button)
                    for button in gyro.get("activation_buttons") or ()
                ]
                if len(button_items) > 2:
                    buttons = (
                        "/".join(button_items[:2])
                        + f"+{len(button_items) - 2}"
                    )
                else:
                    buttons = "/".join(button_items)
                buttons = buttons or tr("無按鍵")
                active_text = (
                    tr("啟用") if gyro.get("active") else tr("待命")
                )
                active_deadzone = float(
                    gyro.get("active_deadzone", 0.0) or 0.0
                )
                anti_deadzone = float(
                    gyro.get("stick_anti_deadzone", 0.0) or 0.0
                ) * 100.0
                stick_deadzone = float(
                    side_data.get("deadzone", 0.0) or 0.0
                ) * 100.0
                outer_deadzone = float(
                    side_data.get("outer_deadzone", 0.0) or 0.0
                ) * 100.0
                self._set_text_if_changed("detail_var",
                    tr(
                        "{gyro}：{active}｜{mode} {buttons}｜"
                        "DZ {deadzone:.2f}｜ADZ {anti_deadzone:.0f}%"
                    ).format(
                        gyro=tr("陀螺"),
                        active=active_text,
                        mode=activation_mode,
                        buttons=buttons,
                        deadzone=active_deadzone,
                        anti_deadzone=anti_deadzone,
                    )
                )
                self._set_text_if_changed("metrics_var",
                    tr(
                        "搖桿中心死區 {stick_deadzone:.1f}%   "
                        "外圈死區 {outer_deadzone:.1f}%   "
                        "觸發 {mode}"
                    ).format(
                        stick_deadzone=stick_deadzone,
                        outer_deadzone=outer_deadzone,
                        mode=activation_mode,
                    )
                )
            else:
                self._set_text_if_changed(
                    "detail_var", tr("實體搖桿的最終輸出")
                )
        else:
            self._set_text_if_changed(
                "detail_var",
                tr("原始裝置輸出（無曲線／死區設定資料）"),
            )
        return frame_changed

    def record_presentation_fps(self, now, frame_changed):
        """Display this plot's own Canvas presentation rate in its corner."""
        # A resize/theme rebuild redraws the radar with canvas.delete("all").
        # Tk keeps the old numeric id in Python, so recreate the FPS overlay
        # when that canvas item no longer exists.
        if self._fps_item is not None and not self.canvas.type(self._fps_item):
            self._fps_item = None
            self._fps_text = None
        if frame_changed:
            self._presentation_times.append(now)
        while (
            self._presentation_times
            and now - self._presentation_times[0] > 1.0
        ):
            self._presentation_times.popleft()
        if (
            self._fps_item is not None
            and now - self._last_fps_draw_at < 0.25
        ):
            return
        if len(self._presentation_times) < 2:
            text = "0 FPS"
        else:
            elapsed = (
                self._presentation_times[-1]
                - self._presentation_times[0]
            )
            fps = (
                (len(self._presentation_times) - 1) / elapsed
                if elapsed > 0.0 else 0.0
            )
            text = f"{fps:.0f} FPS"
        if self._fps_item is None:
            self._fps_item = self.canvas.create_text(
                PLOT_SIZE - 6,
                6,
                text=text,
                anchor="ne",
                fill="#777777",
                font=("Segoe UI", 8),
                tags="fps",
            )
        elif text != self._fps_text:
            self.canvas.itemconfigure(self._fps_item, text=text)
        self._fps_text = text
        self._last_fps_draw_at = now
        self.canvas.tag_raise(self._fps_item)


class GamepadTestWindow:
    def __init__(self, gui):
        self.gui = gui
        self.root = gui.root
        self.window = None
        self.test_notebook = None
        self.input_tab = None
        self.rumble_tab = None
        self.high_rate_tab = None
        self.diagnostic_tab = None
        self.about_tab = None
        # hidapi has been observed corrupting its heap when a device is
        # removed while a handle is open. Keep this long-running Tk process on
        # XInput/WinMM only; Raw HID work is owned by crash-isolated helpers.
        self.backend = WindowsGamepadBackend(
            enable_s2p_mobile_hid=False
        )
        self.native_sampler = None
        self.telemetry = None
        self.latest_telemetry = {}
        self.latest_diagnostic_input = {}
        self.devices = {}
        self._native_test_devices = ()
        self._device_enumeration_initialized = False
        self._device_selection_explicit = False
        self.selected_device_var = tk.StringVar()
        self.status_var = tk.StringVar(
            value=self.gui.tr("正在搜尋手把...")
        )
        self.raw_hid_probe = RawHidAnalysisClient()
        self.raw_hid_stream = RawHidStreamClient()
        self.raw_hid_stream_enabled_var = tk.BooleanVar(value=True)
        self.raw_hid_stream_toggle = None
        self.raw_hid_stream_status_var = tk.StringVar(value="")
        self._raw_hid_stream_path = None
        self._raw_hid_stream_sequence = 0
        self._raw_hid_stream_dropped = 0
        self._raw_hid_stream_latest_axes = None
        self._raw_hid_stream_latest_sample = None
        self._nintendo_winmm_pair_key = None
        self._nintendo_winmm_pair_score = 0
        self.raw_hid_devices = {}
        self.raw_hid_duration_var = tk.StringVar(value="10")
        self.raw_hid_state_var = tk.StringVar(value=self.gui.tr("尚未量測"))
        self.raw_hid_rate_var = tk.StringVar(value="— Hz")
        self.raw_hid_effective_rate_var = tk.StringVar(value="— Hz")
        self.raw_hid_count_var = tk.StringVar(value="0")
        self.raw_hid_remaining_var = tk.StringVar(value="—")
        self.raw_hid_analysis_var = tk.StringVar(
            value=self.gui.tr("判讀：尚未量測。")
        )
        self.raw_hid_stats_vars = {
            key: tk.StringVar(value="—")
            for key in ("p50", "p95", "p99", "min", "mean", "max")
        }
        self.raw_hid_stat_labels = {}
        self.raw_hid_stat_quality = {
            key: "neutral"
            for key in ("p50", "p95", "p99", "min", "mean", "max")
        }
        self.raw_hid_percentile_info_vars = {
            key: tk.StringVar()
            for key in ("p50", "p95", "p99")
        }
        self._update_raw_hid_percentile_info(0)
        self.raw_hid_canvas = None
        self.raw_hid_start_button = None
        self.raw_hid_stop_button = None
        self.raw_hid_duration_combo = None
        self._raw_hid_countdown_job = None
        self._raw_hid_countdown_deadline = 0.0
        self._raw_hid_pending_start = None
        self._raw_hid_countdown_cancelled = False
        self._raw_hid_last_distribution = None
        self._raw_hid_chart_data = ((), 0, 0.0, 0.0, 0.0)
        self.shape_enabled_var = tk.BooleanVar(value=False)
        self._shape_capture_signature = None
        self.show_gyro_legend_var = tk.BooleanVar(value=True)
        self.sample_display_percent_var = tk.DoubleVar(value=100.0)
        self.sample_display_percent_text = tk.StringVar(value="100%")
        self.trail_length_var = tk.DoubleVar(value=2.5)
        self.trail_length_text = tk.StringVar(
            value=self.gui.tr("{seconds:.1f} 秒").format(seconds=2.5)
        )
        self.histories = {
            "left": StickHistory(),
            "right": StickHistory(),
        }
        self.plots = {}
        self._poll_job = None
        self._device_refresh_job = None
        self._device_refresh_in_progress = False
        self.device_refresh_button = None
        self._rumble_jobs = {}
        self._rumble_layers = {}
        self._repeat_rumble_jobs = {}
        self._rumble_layer_templates = {}
        self._active_rumble_templates = {}
        self._next_rumble_layer_id = 0
        self._manual_rumble = (0.0, 0.0)
        self._active_rumble_slot = None
        self._rumble_supported_state = None
        self._last_rumble_notice = None
        self.manual_lf_var = tk.DoubleVar(value=0.0)
        self.manual_hf_var = tk.DoubleVar(value=0.0)
        self.manual_lf_text = tk.StringVar(value="0%")
        self.manual_hf_text = tk.StringVar(value="0%")
        self.repeat_rumble_var = tk.BooleanVar(value=False)
        self.rumble_repeat_hz_var = tk.DoubleVar(value=1.0)
        self.rumble_repeat_hz_text = tk.StringVar(value="1.0 Hz")
        self.rumble_strength_var = tk.DoubleVar(value=100.0)
        self.rumble_strength_text = tk.StringVar(value="100%")
        self.template_lf_enabled_var = tk.BooleanVar(value=True)
        self.template_hf_enabled_var = tk.BooleanVar(value=True)
        self._last_consumed_token = None
        self._last_trail_sequence = 0
        self._trail_overwrite_count = 0
        self._raw_hid_resume_after_measurement = False
        self._button_events = {}
        self._recent_events = deque(maxlen=8)
        self._last_state = None
        self._display_refresh_hz = 60.0
        self._frame_interval = 1.0 / 60.0
        self._next_frame_at = 0.0
        self._last_detail_refresh = 0.0
        self._last_connection_refresh = 0.0
        self._last_shape_advance_at = 0.0
        self._window_motion_until = 0.0
        self._last_window_geometry = None
        self._high_resolution_timer_active = False
        self._window_icon = None
        self._about_logo = None
        self._about_fonts = ()
        self.automatic_update_checks_var = tk.BooleanVar(
            value=automatic_update_checks_enabled()
        )
        self.update_check_status_var = tk.StringVar(
            value=self.gui.tr("尚未檢查版本")
        )
        self.update_check_button = None
        self._update_check_in_progress = False
        self._automatic_update_check_started = False
        self._update_prompt_window = None
        self._parameter_editor_window = None
        self.diagnostic_session = DiagnosticSession(
            DEFAULT_DIAGNOSTIC_SECONDS
        )
        self.diagnostic_reader = ESP32DiagnosticReader()
        self.diagnostic_duration_var = tk.StringVar(
            value=str(DEFAULT_DIAGNOSTIC_SECONDS)
        )
        self.diagnostic_state_var = tk.StringVar(
            value=self.gui.tr("尚未開始診斷")
        )
        self.diagnostic_remaining_var = tk.StringVar(value="—")
        self.diagnostic_progress_var = tk.DoubleVar(value=0.0)
        self.diagnostic_summary_vars = {
            key: tk.StringVar(value="—")
            for key in (
                "mode", "connection", "input", "latency",
                "calibration", "sensor", "gyro",
                "rumble_input", "rumble_output", "rumble_transport",
                "verdict", "findings", "advice",
            )
        }
        self.diagnostic_start_button = None
        self.diagnostic_stop_button = None
        self.diagnostic_export_button = None
        self.diagnostic_event_text = None
        self._diagnostic_last_event_signature = None
        self._diagnostic_firmware_update_notice_shown = False
        self._diagnostic_target_key = None
        self._diagnostic_target_kind = None
        self._diagnostic_target_name = None
        self._diagnostic_firmware_source = None

    def _apply_window_icon(self, window):
        """Apply the dedicated tester icon without changing the main app icon."""
        self._window_icon = None
        if not TEST_ICON_PATH.is_file():
            return
        try:
            icon = tk.PhotoImage(master=window, file=str(TEST_ICON_PATH))
            window.iconphoto(False, icon)
            # Tk image objects must remain referenced for the window lifetime.
            self._window_icon = icon
        except tk.TclError:
            self._window_icon = None

    @staticmethod
    def _create_hidden_window(root):
        """Create the tester without exposing Tk's default top-left geometry."""
        window = tk.Toplevel(root)
        window.withdraw()
        return window

    @staticmethod
    def _reveal_positioned_window(window, geometry):
        """Commit final geometry before the tester's first visible frame."""
        window.geometry(geometry)
        window.update_idletasks()
        window.deiconify()
        window.lift()

    def open(self):
        if self.window is not None and self.window.winfo_exists():
            self.window.deiconify()
            self.window.lift()
            self.window.focus_force()
            return
        if self.telemetry is None:
            self.telemetry = SharedTestTelemetry()
        self._last_trail_sequence = self.telemetry.latest_trail_sequence()
        if self.native_sampler is None:
            self.native_sampler = NativeGamepadSampler(
                self.backend,
                isolate_enumeration=True,
            )
        if not self.native_sampler.start():
            self.root.after(
                25,
                lambda sampler=self.native_sampler:
                    self._retry_native_sampler_start(sampler),
            )
        self._enable_high_resolution_timer()
        self._display_refresh_hz = primary_display_refresh_rate()
        self._frame_interval = 1.0 / self._display_refresh_hz
        self._next_frame_at = time.perf_counter()
        self._last_shape_advance_at = self._next_frame_at
        window = self._create_hidden_window(self.root)
        self.window = window
        window.title(GAMEPAD_TESTER_TITLE)
        self._apply_window_icon(window)
        # Keep the tester layout and its two high-refresh canvases at the
        # validated size. This also disables the native maximize button.
        window.resizable(False, False)
        self._update_display_refresh_rate(window)
        screen_width = max(800, window.winfo_screenwidth())
        screen_height = max(600, window.winfo_screenheight())
        initial_width = min(750, max(720, screen_width - 80))
        # Give the lower trigger/event row enough room, then let that row
        # absorb any spare height instead of leaving an empty notebook area.
        initial_height = min(720, max(660, screen_height - 80))
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Configure>", self._on_window_configure, add="+")

        content = ttk.Frame(window, padding=12)
        content.pack(fill="both", expand=True)
        content.columnconfigure(0, weight=1)
        content.rowconfigure(1, weight=1)

        selector = ttk.Frame(content)
        selector.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        selector.columnconfigure(1, weight=1)
        ttk.Label(selector, text=self.gui.tr("測試手把")).grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        self.device_combo = ttk.Combobox(
            selector,
            textvariable=self.selected_device_var,
            state="readonly",
            width=20,
        )
        self.device_combo.grid(row=0, column=1, sticky="ew")
        self.device_combo.bind(
            "<<ComboboxSelected>>", self._on_device_selected
        )
        self.device_refresh_button = ttk.Button(
            selector,
            text=self.gui.tr("重新整理"),
            width=8,
            command=self._request_device_refresh,
        )
        self.device_refresh_button.grid(row=0, column=2, padx=(5, 0))
        self.status_label = None
        # Per-radar presentation FPS is rendered in each plot's upper-right
        # corner, leaving this header's spare width for device selection.
        self.draw_fps_label = None

        notebook = ttk.Notebook(content)
        notebook.grid(row=1, column=0, sticky="nsew")
        input_tab = ttk.Frame(notebook, padding=(8, 6))
        rumble_tab = ttk.Frame(notebook, padding=(10, 8))
        high_rate_tab = ttk.Frame(notebook, padding=(10, 8))
        diagnostic_tab = ttk.Frame(notebook, padding=(10, 8))
        about_tab = ttk.Frame(notebook, padding=(10, 8))
        self.test_notebook = notebook
        self.input_tab = input_tab
        self.rumble_tab = rumble_tab
        self.high_rate_tab = high_rate_tab
        self.diagnostic_tab = diagnostic_tab
        self.about_tab = about_tab
        notebook.add(input_tab, text=f" {self.gui.tr('輸入監看')} ")
        notebook.add(rumble_tab, text=f" {self.gui.tr('震動測試')} ")
        notebook.add(high_rate_tab, text=f" {self.gui.tr('回報率量測')} ")
        notebook.add(
            diagnostic_tab, text=f" {self.gui.tr('診斷模式')} "
        )
        notebook.add(about_tab, text=f" {self.gui.tr('關於')} ")
        notebook.bind(
            "<<NotebookTabChanged>>", self._on_test_tab_changed, add="+"
        )
        input_tab.columnconfigure(0, weight=1)
        input_tab.rowconfigure(0, weight=1)

        panel_stack = ttk.Frame(input_tab)
        panel_stack.grid(row=0, column=0, sticky="nsew")
        panel_stack.columnconfigure(0, weight=1, uniform="test_panel")
        panel_stack.columnconfigure(1, weight=1, uniform="test_panel")
        panel_stack.rowconfigure(2, weight=1)
        self.plots["left"] = StickPlot(self, panel_stack, "left")
        self.plots["right"] = StickPlot(self, panel_stack, "right")
        self.plots["left"].frame.grid(
            row=0, column=0, sticky="n", padx=(0, 5)
        )
        self.plots["right"].frame.grid(
            row=0, column=1, sticky="n", padx=(5, 0)
        )

        display_controls = ttk.Frame(panel_stack)
        display_controls.grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(8, 6)
        )
        display_controls.columnconfigure(5, weight=1)
        display_controls.columnconfigure(8, weight=1)
        ttk.Button(
            display_controls,
            text=self.gui.tr("清除"),
            command=self.clear_measurements,
        ).grid(row=0, column=0, padx=(0, 7))
        ttk.Checkbutton(
            display_controls,
            text=self.gui.tr("輸出形狀"),
            variable=self.shape_enabled_var,
            command=self._on_shape_enabled_changed,
        ).grid(row=0, column=1, padx=(0, 7))
        ttk.Checkbutton(
            display_controls,
            text=self.gui.tr("陀螺儀圖例"),
            variable=self.show_gyro_legend_var,
        ).grid(row=0, column=2, padx=(0, 7))
        raw_hid_stream_toggle = ttk.Checkbutton(
            display_controls,
            text=self.gui.tr("實際採樣"),
            variable=self.raw_hid_stream_enabled_var,
            command=self._on_raw_hid_stream_changed,
        )
        self.raw_hid_stream_toggle = raw_hid_stream_toggle
        raw_hid_stream_toggle.grid(row=0, column=3, padx=(0, 7))
        ttk.Label(
            display_controls, text=self.gui.tr("軌跡")
        ).grid(row=0, column=4, padx=(0, 3))
        trail_scale = ttk.Scale(
            display_controls,
            from_=0.5,
            to=5.0,
            variable=self.trail_length_var,
            command=self._update_trail_length_text,
            orient="horizontal",
        )
        trail_scale.grid(row=0, column=5, sticky="ew")
        trail_value_label = ttk.Label(
            display_controls,
            textvariable=self.trail_length_text,
            width=7,
            anchor="e",
        )
        trail_value_label.grid(row=0, column=6, padx=(3, 7))
        self._bind_parameter_control(
            trail_value_label,
            self.trail_length_var,
            "軌跡",
            0.5,
            5.0,
            step=0.1,
            number_format=".1f",
            on_change=self._update_trail_length_text,
            state_widget=trail_scale,
        )
        ttk.Label(
            display_controls, text=self.gui.tr("採樣點")
        ).grid(row=0, column=7, padx=(0, 3))
        sample_scale = ttk.Scale(
            display_controls,
            from_=1.0,
            to=100.0,
            length=80,
            variable=self.sample_display_percent_var,
            command=self._update_sample_display_percent,
            orient="horizontal",
        )
        sample_scale.grid(row=0, column=8, sticky="ew")
        sample_value_label = ttk.Label(
            display_controls,
            textvariable=self.sample_display_percent_text,
            width=5,
            anchor="e",
        )
        sample_value_label.grid(row=0, column=9, padx=(3, 3))
        self._bind_parameter_control(
            sample_value_label,
            self.sample_display_percent_var,
            "採樣點",
            1.0,
            100.0,
            step=1.0,
            number_format=".0f",
            on_change=self._update_sample_display_percent,
            state_widget=sample_scale,
        )
        sample_help = tk.Label(
            display_controls,
            text="?",
            width=2,
            relief="solid",
            borderwidth=1,
            cursor="question_arrow",
        )
        sample_help.grid(row=0, column=10)
        HoverTip(
            sample_help,
            self.gui.tr(
                "採樣點100%\n"
                "顯示目前軌跡長度內，所選Windows輸入介面實際收到的全部"
                "座標點。\n\n"
                "XInput限制\n"
                "XInput只保留最新座標，無法把兩次讀取之間已被覆蓋的"
                "中間座標還原成路徑點。\n\n"
                "Raw HID 實際採樣\n"
                "啟用後會直接使用 Windows 收到的每筆 Raw HID 回報；"
                "此時100%代表顯示緩衝區內全部實際回報點。\n\n"
                "顯示百分比\n"
                "降低百分比只會減少畫面上的路徑點，不會改變實際輸入。"
            ),
        )

        details = ttk.Frame(panel_stack)
        details.grid(
            row=2, column=0, columnspan=2, sticky="nsew"
        )
        details.columnconfigure(
            0, weight=1, uniform="detail_panel_ratio"
        )
        details.columnconfigure(
            1, weight=3, uniform="detail_panel_ratio"
        )
        details.rowconfigure(0, weight=1)

        triggers = ttk.LabelFrame(
            details,
            text=self.gui.tr("線性扳機"),
            padding=(8, 5),
        )
        triggers.grid(row=0, column=0, sticky="nsew", padx=(0, 5))
        triggers.columnconfigure(0, weight=1)
        triggers.rowconfigure(0, weight=1)
        trigger_content = ttk.Frame(triggers)
        # Keep the two trigger readouts centered when the adjacent event
        # table makes this shared row taller.
        trigger_content.grid(row=0, column=0, sticky="ew")
        self.trigger_bars = {}
        self.trigger_value_vars = {}
        self.trigger_source_vars = {}
        for row, trigger_name in enumerate(("LT", "RT")):
            ttk.Label(
                trigger_content,
                text=trigger_name,
                width=3,
            ).grid(row=row * 2, column=0, sticky="w")
            value_var = tk.StringVar(value="0 / 255")
            source_var = tk.StringVar(value=self.gui.tr("等待輸入"))
            bar = ttk.Progressbar(
                trigger_content,
                orient="horizontal",
                mode="determinate",
                maximum=255,
                length=55,
            )
            bar.grid(
                row=row * 2,
                column=1,
                sticky="ew",
                padx=(4, 6),
            )
            ttk.Label(
                trigger_content,
                textvariable=value_var,
                width=8,
                anchor="e",
            ).grid(row=row * 2, column=2, sticky="e")
            ttk.Label(
                trigger_content,
                textvariable=source_var,
                foreground="#666666",
                anchor="w",
            ).grid(
                row=row * 2 + 1,
                column=0,
                columnspan=3,
                sticky="ew",
                pady=(0, 4 if row == 0 else 0),
            )
            self.trigger_bars[trigger_name] = bar
            self.trigger_value_vars[trigger_name] = value_var
            self.trigger_source_vars[trigger_name] = source_var
        trigger_content.columnconfigure(1, weight=1)

        events = ttk.LabelFrame(
            details,
            text=self.gui.tr("按鍵與映射事件"),
            padding=(6, 4),
        )
        events.grid(row=0, column=1, sticky="nsew", padx=(5, 0))
        events.columnconfigure(0, weight=1)
        events.rowconfigure(0, weight=1)
        self.event_tree = ttk.Treeview(
            events,
            columns=("source", "state", "duration", "target", "layer"),
            show="headings",
            height=3,
        )
        headings = (
            ("source", "來源按鍵", 55),
            ("state", "狀態", 48),
            ("duration", "持續時間", 55),
            ("target", "生效映射", 92),
            ("layer", "映射層", 55),
        )
        for column, text, width in headings:
            self.event_tree.heading(column, text=self.gui.tr(text))
            self.event_tree.column(
                column, width=width, minwidth=40, stretch=True
            )
        self.event_tree.grid(row=0, column=0, sticky="nsew")

        self._build_rumble_tab(rumble_tab)
        self._build_high_rate_tab(high_rate_tab)
        self._build_diagnostic_tab(diagnostic_tab)
        self._build_about_tab(about_tab)
        self._refresh_devices(force=True)
        self._update_raw_hid_availability()
        # The first telemetry frame is published only after read_latest() has
        # established the reader heartbeat. Continue scanning at a low rate so
        # controllers that connect later appear without a manual refresh.
        self._device_refresh_job = window.after(
            300, self._refresh_devices_after_telemetry
        )
        self._schedule_poll()
        window.update_idletasks()
        root_width = self.root.winfo_width()
        root_height = self.root.winfo_height()
        if (
            self.root.winfo_viewable()
            and root_width > 100
            and root_height > 100
        ):
            x = self.root.winfo_rootx() + (
                root_width - initial_width
            ) // 2
            y = self.root.winfo_rooty() + (
                root_height - initial_height
            ) // 2
        else:
            # The standalone tester's host root is intentionally withdrawn
            # and reports a 1x1 geometry at (0, 0). Centre on the display
            # instead of mistaking that hidden host for the settings window.
            x = (screen_width - initial_width) // 2
            y = (screen_height - initial_height) // 2
        x = max(0, min(screen_width - initial_width, x))
        y = max(0, min(screen_height - initial_height, y))
        self._reveal_positioned_window(
            window,
            f"{initial_width}x{initial_height}+{x}+{y}",
        )
        if (
            getattr(self.gui, "allow_automatic_update_prompt", False)
            and self.automatic_update_checks_var.get()
            and not self._automatic_update_check_started
        ):
            self._automatic_update_check_started = True
            window.after(
                1400,
                lambda: self.check_for_updates(manual=False),
            )

    def _retry_native_sampler_start(self, sampler):
        """Restart after a previous sampler generation finishes winding down."""
        if sampler is not self.native_sampler:
            return
        window = self.window
        if window is None or not window.winfo_exists():
            return
        if sampler.start():
            return
        try:
            window.after(
                25,
                lambda: self._retry_native_sampler_start(sampler),
            )
        except tk.TclError:
            pass

    def _build_high_rate_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=0)

        controls = ttk.LabelFrame(
            parent, text=self.gui.tr("量測設定"), padding=(10, 8)
        )
        controls.grid(row=0, column=0, sticky="ew")
        controls.columnconfigure(3, weight=1, uniform="raw_actions")
        controls.columnconfigure(4, weight=1, uniform="raw_actions")
        ttk.Label(
            controls, text=self.gui.tr("量測秒數")
        ).grid(row=0, column=0, padx=(0, 5))
        self.raw_hid_duration_combo = ttk.Combobox(
            controls,
            textvariable=self.raw_hid_duration_var,
            values=("5", "10", "30", "60"),
            width=5,
        )
        self.raw_hid_duration_combo.grid(row=0, column=1)
        raw_hid_help = tk.Label(
            controls,
            text="?",
            width=2,
            relief="solid",
            borderwidth=1,
            cursor="question_arrow",
        )
        raw_hid_help.grid(row=0, column=2, padx=(9, 6))
        HoverTip(
            raw_hid_help,
            self.gui.tr(
                "回報率量測需要先在「測試手把」選擇 Raw HID collection。"
                "XInput、WinMM 與 S2P 橋接輸出本身不能直接量測原始 HID 回報率。"
            ),
        )
        self.raw_hid_start_button = ttk.Button(
            controls,
            text=self.gui.tr("開始量測"),
            command=self._start_raw_hid_measurement,
            width=16,
        )
        self.raw_hid_start_button.grid(
            row=0, column=3, sticky="ew", padx=(0, 4)
        )
        self.raw_hid_stop_button = ttk.Button(
            controls,
            text=self.gui.tr("提前停止"),
            command=self._stop_raw_hid_measurement,
            state="disabled",
            width=15,
        )
        self.raw_hid_stop_button.grid(row=0, column=4, sticky="ew")
        summary = ttk.Frame(parent)
        summary.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        for column in range(4):
            summary.columnconfigure(column, weight=1, uniform="raw_summary")
        summary_items = (
            ("HID 回報率", self.raw_hid_rate_var),
            ("有效回報率", self.raw_hid_effective_rate_var),
            ("收到回報數", self.raw_hid_count_var),
            ("剩餘時間", self.raw_hid_remaining_var),
        )
        for column, (title, variable) in enumerate(summary_items):
            box = ttk.LabelFrame(
                summary, text=self.gui.tr(title), padding=(8, 5)
            )
            box.grid(
                row=0, column=column, sticky="ew",
                padx=(0 if column == 0 else 4, 0 if column == 3 else 4),
            )
            ttk.Label(
                box,
                textvariable=variable,
                anchor="center",
            ).pack(fill="both", expand=True, pady=(3, 5))

        stats = ttk.LabelFrame(
            parent, text=self.gui.tr("前後兩筆回報的時間差（ms）"), padding=(8, 6)
        )
        stats.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        stat_items = (
            ("P50", "p50"),
            ("P95", "p95"),
            ("P99", "p99"),
            ("最小", "min"),
            ("平均", "mean"),
            ("最大", "max"),
        )
        for column, (title, key) in enumerate(stat_items):
            stats.columnconfigure(column, weight=1, uniform="raw_stat")
            ttk.Label(stats, text=self.gui.tr(title), anchor="center").grid(
                row=0, column=column, sticky="ew"
            )
            value_label = tk.Label(
                stats,
                textvariable=self.raw_hid_stats_vars[key],
                anchor="center",
                cursor="question_arrow",
            )
            value_label.grid(
                row=1, column=column, sticky="nsew", pady=(3, 2)
            )
            self.raw_hid_stat_labels[key] = value_label
            HoverTip(
                value_label,
                lambda stat_key=key: self._raw_hid_stat_tooltip_text(
                    stat_key
                ),
            )
        chart_frame = ttk.LabelFrame(
            parent, text=self.gui.tr("回報時間差分佈"), padding=(8, 5)
        )
        chart_frame.grid(row=3, column=0, sticky="ew")
        chart_frame.columnconfigure(0, weight=1)
        self.raw_hid_canvas = tk.Canvas(
            chart_frame,
            width=680,
            height=240,
            background="#FFFFFF",
            highlightthickness=0,
        )
        self.raw_hid_canvas.grid(row=0, column=0, sticky="ew")
        self.raw_hid_canvas.bind(
            "<Configure>", lambda _event: self._redraw_raw_hid_chart()
        )
        legend = ttk.Frame(parent)
        legend.grid(row=4, column=0, sticky="ew", pady=(5, 0))
        legend.columnconfigure(2, weight=1)
        for row, (key, color, text) in enumerate((
            ("p50", "#42A5F5", "P50｜一般表現：一半的回報時間差不超過這個數值"),
            ("p95", "#FB8C00", "P95｜大多數表現：95% 的回報時間差不超過這個數值"),
            ("p99", "#E53935", "P99｜偶發抖動：99% 的回報時間差不超過這個數值"),
        )):
            tk.Label(legend, text="■", foreground=color).grid(
                row=row, column=0, sticky="nw"
            )
            ttk.Label(
                legend,
                text=self.gui.tr(text),
                justify="left",
            ).grid(
                row=row, column=1, sticky="ew", padx=(2, 0),
            )
            ttk.Label(
                legend,
                textvariable=self.raw_hid_percentile_info_vars[key],
                anchor="w",
            ).grid(
                row=row, column=2, sticky="w", padx=(10, 0),
            )
        legend.grid_remove()
        analysis_label = ttk.Label(
            parent,
            textvariable=self.raw_hid_analysis_var,
            foreground="#666666",
            anchor="w",
            justify="left",
            # The usable width changes with DPI scaling and localisation.
            # Keep the paragraph's wrap width aligned to the actual widget
            # width instead of a fixed 670 px, which can split "2 倍".
            wraplength=1,
        )
        analysis_label.grid(row=4, column=0, sticky="ew", pady=(5, 0))

        def fit_analysis_wrap(event):
            wraplength = max(1, int(event.width))
            if int(event.widget.cget("wraplength")) != wraplength:
                event.widget.configure(wraplength=wraplength)

        analysis_label.bind("<Configure>", fit_analysis_wrap)
        self._draw_raw_hid_chart((), 0, 0.0, 0.0, 0.0)

    def _update_raw_hid_availability(self):
        if self.raw_hid_probe.read_snapshot().get("state") in {
            "opening", "running"
        }:
            return
        selected = self._selected_device()
        is_raw_hid = selected is not None and selected.kind == "raw_hid"
        if not self.raw_hid_probe.available:
            self.raw_hid_state_var.set(
                self.gui.tr("Raw HID 量測元件不可用")
            )
        elif not self.raw_hid_devices:
            self.raw_hid_state_var.set(
                self.gui.tr("找不到 Raw HID 遊戲手把介面")
            )
        elif (
            is_raw_hid
            and self.raw_hid_probe.read_snapshot().get("state") == "idle"
        ):
            self.raw_hid_state_var.set(self.gui.tr("尚未量測"))
        self._set_raw_hid_controls_active(False)
        self._sync_raw_hid_stream()

    def _on_raw_hid_stream_changed(self):
        self.clear_measurements()
        self._sync_raw_hid_stream()

    def _stop_raw_hid_stream(self):
        stopped = self.raw_hid_stream.stop()
        if stopped:
            self._raw_hid_stream_path = None
            self._raw_hid_stream_sequence = 0
            self._raw_hid_stream_latest_axes = None
            self._raw_hid_stream_latest_sample = None
        return stopped

    def _sync_raw_hid_stream(self):
        """Own one Raw HID stream for a selected HID collection or mobile WinMM."""
        if self.window is None:
            return
        probe_state = self.raw_hid_probe.read_snapshot().get("state")
        selected = self._selected_device()
        is_selected_raw_hid = selected is not None and selected.kind == "raw_hid"
        is_mobile_winmm = bool(
            selected is not None
            and selected.kind == "winmm"
            and selected.input_profile == S2P_MOBILE_HID_PROFILE
        )
        has_nintendo_raw_hid = any(
            (raw.vendor_id, raw.product_id) == (0x057E, 0x2069)
            for raw in getattr(self, "raw_hid_devices", {}).values()
        )
        is_winmm_pair_candidate = bool(
            selected is not None
            and selected.kind == "winmm"
            and has_nintendo_raw_hid
        )
        enabled = (
            probe_state not in {"opening", "running"}
            and (
                (is_selected_raw_hid and self.raw_hid_stream_enabled_var.get())
                # WinMM has no reliable standard for trigger axes.  For this
                # known mobile descriptor, keep its native buttons but use the
                # crash-isolated Raw HID stream to fill LT/RT.
                or is_mobile_winmm
                # A physical Nintendo controller can expose more than one
                # generic WinMM mirror.  Start a passive Raw HID comparison;
                # it is not used until movement proves the selected mirror is
                # the same physical device.
                or is_winmm_pair_candidate
            )
        )
        if is_selected_raw_hid:
            device = self._selected_raw_hid_device()
        elif is_mobile_winmm:
            device = next((
                raw for raw in self.raw_hid_devices.values()
                if (raw.vendor_id, raw.product_id) == (0xCAFE, 0x4021)
            ), None)
        elif is_winmm_pair_candidate:
            device = next(
                raw for raw in self.raw_hid_devices.values()
                if (raw.vendor_id, raw.product_id) == (0x057E, 0x2069)
            )
        else:
            device = None
        path = device.path if device is not None else None
        if path == self._raw_hid_stream_path and self.raw_hid_stream.active:
            status = self.raw_hid_stream.status()
            if status.get("state") == "running":
                self.raw_hid_stream_status_var.set(
                    self.gui.tr("正在使用 Raw HID 實際回報")
                )
            return
        if not self._stop_raw_hid_stream():
            self.raw_hid_stream_status_var.set(
                self.gui.tr("Raw HID 實際採樣無法停止")
            )
            return
        if not enabled:
            self.raw_hid_stream_status_var.set("")
            return
        if device is None:
            self.raw_hid_stream_status_var.set(
                self.gui.tr("選取的 Raw HID collection 已不存在")
            )
            return
        if not self.raw_hid_stream.start(path):
            self.raw_hid_stream_status_var.set(
                self.gui.tr("Raw HID 實際採樣無法啟動")
            )
            return
        self._raw_hid_stream_path = path
        self._raw_hid_stream_sequence = 0
        self._raw_hid_stream_dropped = 0
        self._raw_hid_stream_latest_axes = None
        self._raw_hid_stream_latest_sample = None
        self.raw_hid_stream_status_var.set(
            self.gui.tr("正在開啟 Raw HID 實際採樣...")
        )

    @staticmethod
    def _merge_cached_raw_hid_sample(sample, cached):
        """Keep native buttons while holding Raw HID axes/controls between reports."""
        if cached is None:
            return sample
        if sample is None:
            return dict(cached)
        merged = dict(sample)
        for key in ("left", "right", "triggers", "token"):
            if key in cached:
                merged[key] = cached[key]
        return merged

    def _consume_raw_hid_stream(self, sample, record_trail=True):
        if record_trail:
            samples, newest, dropped = self.raw_hid_stream.read_samples(
                self._raw_hid_stream_sequence,
                include_axes=True,
                include_controls=True,
                include_buttons=True,
            )
        else:
            latest, newest, dropped = self.raw_hid_stream.read_latest(
                self._raw_hid_stream_sequence
            )
            samples = (
                () if latest is None else (latest + (0x0F,),)
            )
        self._raw_hid_stream_sequence = newest
        self._raw_hid_stream_dropped += dropped
        if not samples:
            cached = getattr(
                self, "_raw_hid_stream_latest_sample", None
            )
            return self._merge_cached_raw_hid_sample(sample, cached), False
        if record_trail:
            record_shape = self.shape_enabled_var.get()
            for item in samples:
                sample_time, left, right, _sequence, axes_mask = item[:5]
                if axes_mask & 0x03 == 0x03:
                    self.histories["left"].add(
                        left[0], left[1], sample_time, record_shape
                    )
                if axes_mask & 0x0C == 0x0C:
                    self.histories["right"].add(
                        right[0], right[1], sample_time, record_shape
                    )
        left_sample = next((
            item for item in reversed(samples)
            if item[4] & 0x03 == 0x03
        ), None)
        right_sample = next((
            item for item in reversed(samples)
            if item[4] & 0x0C == 0x0C
        ), None)
        if left_sample is None and right_sample is None:
            cached = getattr(
                self, "_raw_hid_stream_latest_sample", None
            )
            return self._merge_cached_raw_hid_sample(sample, cached), False
        previous_axes = getattr(
            self, "_raw_hid_stream_latest_axes", None
        )
        left = (
            left_sample[1]
            if left_sample is not None
            else previous_axes[0] if previous_axes is not None
            else (0.0, 0.0)
        )
        right = (
            right_sample[2]
            if right_sample is not None
            else previous_axes[1] if previous_axes is not None
            else (0.0, 0.0)
        )
        sequence = max(
            item[3] for item in (left_sample, right_sample)
            if item is not None
        )
        self._raw_hid_stream_latest_axes = (left, right)
        if sample is None:
            sample = {
                "buttons": (),
                "buttons_mask": 0,
                "triggers": (0.0, 0.0),
                "source_rate_hz": None,
                "rate_is_independent": True,
                "mappings": [],
                "linear_triggers": [],
                "layer": "原始輸入",
            }
        else:
            sample = dict(sample)
        trigger_values = list(sample.get("triggers") or (0.0, 0.0))
        trigger_values = (trigger_values + [0.0, 0.0])[:2]
        for index, trigger_mask in enumerate((0x10, 0x20)):
            trigger_sample = next((
                item for item in reversed(samples)
                if len(item) > 5 and int(item[4]) & trigger_mask
            ), None)
            if trigger_sample is not None:
                trigger_values[index] = trigger_sample[5][index]
        button_sample = next((
            item for item in reversed(samples)
            if len(item) > 6
        ), None)
        is_nintendo_raw_hid = "VID_057E&PID_2069" in str(
            getattr(self, "_raw_hid_stream_path", "")
        ).upper()
        if (
            button_sample is not None
            and (is_nintendo_raw_hid or sample.get("buttons") in (None, ()))
        ):
            buttons_mask = int(button_sample[6])
            sample["buttons_mask"] = buttons_mask
            sample["buttons"] = tuple(
                name for name, mask in SWITCH_RAW_HID_BUTTONS
                if buttons_mask & mask
            )
        sample.update({
            "left": left,
            "right": right,
            "triggers": tuple(trigger_values),
            "token": ("raw_hid", sequence),
        })
        self._raw_hid_stream_latest_sample = dict(sample)
        return sample, True

    def _discard_pending_monitor_samples(self):
        """Advance every input queue without recording hidden-tab movement."""
        if self.raw_hid_stream.active:
            _latest, newest, dropped = self.raw_hid_stream.read_latest(
                self._raw_hid_stream_sequence
            )
            self._raw_hid_stream_sequence = newest
            self._raw_hid_stream_dropped += dropped
        if self.native_sampler is not None:
            self.native_sampler.read_snapshot()
        self._baseline_trail_sequence()
        self._last_consumed_token = None

    def _update_raw_hid_stream_status(self):
        if not self.raw_hid_stream_enabled_var.get():
            return
        status = self.raw_hid_stream.status()
        state = status.get("state")
        if state == "running":
            axes_mask = int(status.get("axes_mask", 0) or 0)
            axis_names = ("LX", "LY", "RX", "RY")
            available_axes = [
                name for index, name in enumerate(axis_names)
                if axes_mask & (1 << index)
            ]
            unavailable_axes = [
                name for index, name in enumerate(axis_names)
                if not axes_mask & (1 << index)
            ]
            if available_axes:
                capability = (
                    self.gui.tr("可用軸：{axes}").format(
                        axes=", ".join(available_axes)
                    )
                )
                if unavailable_axes:
                    capability += (
                        "；"
                        + self.gui.tr("無法解析：{axes}").format(
                            axes=", ".join(unavailable_axes)
                        )
                    )
            else:
                capability = self.gui.tr(
                    "無法解析標準搖桿軸；仍可量測原始 HID reports"
                )
            if self._raw_hid_stream_dropped:
                self.raw_hid_stream_status_var.set(
                    self.gui.tr("Raw HID 實際回報；緩衝區遺失 {count} 筆")
                    .format(count=self._raw_hid_stream_dropped)
                    + f"；{capability}"
                )
            else:
                axes = self._raw_hid_stream_latest_axes
                reports = int(status.get("raw_reports", 0) or 0)
                if axes is None:
                    detail = (
                        f"{self.gui.tr('等待第一筆座標')}   {capability}"
                        if available_axes else capability
                    )
                else:
                    left, right = axes
                    detail = (
                        f"L {left[0]:+.3f}, {left[1]:+.3f}   "
                        f"R {right[0]:+.3f}, {right[1]:+.3f}"
                        f"   {capability}"
                    )
                self.raw_hid_stream_status_var.set(
                    self.gui.tr("Raw HID 實際回報")
                    + f"  {reports:,}   {detail}"
                )
        elif state == "error" and self._raw_hid_stream_path is not None:
            self.raw_hid_stream_status_var.set(
                self.gui.tr("Raw HID 實際採樣失敗（錯誤代碼 {code}）")
                .format(code=int(status.get("error_code", 0) or 0))
            )
        elif state == "stopped" and self._raw_hid_stream_path is not None:
            self.raw_hid_stream_status_var.set(
                self.gui.tr("Raw HID 實際採樣已停止")
            )

    def _selected_raw_hid_device(self):
        """Return the explicitly selected Raw HID collection."""
        selected = self._selected_device()
        if (
            selected is None
            or selected.kind != "raw_hid"
            or not selected.raw_hid_key
        ):
            return None
        return self.raw_hid_devices.get(selected.raw_hid_key)

    def _set_raw_hid_controls_active(self, active):
        if self.raw_hid_start_button is None:
            return
        if self.device_combo is not None:
            self.device_combo.configure(
                state="disabled" if active else "readonly"
            )
        if getattr(self, "device_refresh_button", None) is not None:
            self.device_refresh_button.configure(
                state="disabled" if active else "normal"
            )
        self.raw_hid_duration_combo.configure(
            state="disabled" if active else "normal"
        )
        selected = self._selected_device()
        is_raw_hid = selected is not None and selected.kind == "raw_hid"
        device = self._selected_raw_hid_device()
        can_start = (
            not active
            and self.raw_hid_probe.available
            and is_raw_hid
            and device is not None
        )
        self.raw_hid_start_button.configure(
            state="normal" if can_start else "disabled"
        )
        self.raw_hid_stop_button.configure(
            state="normal" if active else "disabled"
        )

    def _start_raw_hid_measurement(self):
        self._update_raw_hid_availability()
        device = self._selected_raw_hid_device()
        if device is None:
            self.raw_hid_state_var.set(
                self.gui.tr("請先選擇要量測的 Raw HID collection")
            )
            return
        try:
            duration = float(self.raw_hid_duration_var.get().strip())
        except (TypeError, ValueError):
            duration = 0.0
        if not math.isfinite(duration) or not 1.0 <= duration <= 300.0:
            self.raw_hid_state_var.set(
                self.gui.tr("量測秒數必須介於 1 到 300 秒")
            )
            return
        self.raw_hid_duration_var.set(f"{duration:g}")
        self._raw_hid_countdown_cancelled = False
        self.raw_hid_rate_var.set("— Hz")
        self.raw_hid_effective_rate_var.set("— Hz")
        self.raw_hid_count_var.set("0")
        self.raw_hid_analysis_var.set(self.gui.tr(
            "判讀：量測中，完成後顯示分析結果。"
        ))
        for variable in self.raw_hid_stats_vars.values():
            variable.set("—")
        self._update_raw_hid_stat_colors({})
        self._update_raw_hid_percentile_info(0)
        self._raw_hid_last_distribution = None
        self._draw_raw_hid_chart((), 0, 0.0, 0.0, 0.0)
        # A HID collection must have only one active reader. Suspend the
        # trajectory stream before the finite timing measurement opens it.
        self._raw_hid_resume_after_measurement = bool(
            self.raw_hid_stream_enabled_var.get()
        )
        self._stop_raw_hid_stream()
        self._raw_hid_pending_start = (device.path, duration)
        self._raw_hid_countdown_deadline = time.perf_counter() + 3.0
        self.raw_hid_state_var.set(self.gui.tr("準備量測..."))
        self.raw_hid_remaining_var.set(
            self.gui.tr("{seconds:.1f} 秒後開始").format(seconds=3.0)
        )
        self._set_raw_hid_controls_active(True)
        self._tick_raw_hid_countdown()

    def _stop_raw_hid_measurement(self):
        if self._cancel_raw_hid_countdown():
            self._raw_hid_countdown_cancelled = True
            self.raw_hid_state_var.set(self.gui.tr("已取消量測"))
            self.raw_hid_remaining_var.set(self.gui.tr("已取消量測"))
            self._set_raw_hid_controls_active(False)
            self._resume_actual_sampling_after_measurement()
            return
        self.raw_hid_probe.stop()
        self._update_raw_hid_measurement()

    def _resume_actual_sampling_after_measurement(self):
        if not getattr(self, "_raw_hid_resume_after_measurement", False):
            return False
        self._raw_hid_resume_after_measurement = False
        # Ensure the finite measurement no longer owns the HID handle before
        # reopening it for continuous trajectory sampling.
        self.raw_hid_probe.stop(timeout=0.1)
        self._sync_raw_hid_stream()
        return True

    def _cancel_raw_hid_countdown(self):
        was_pending = self._raw_hid_pending_start is not None
        job = self._raw_hid_countdown_job
        self._raw_hid_countdown_job = None
        self._raw_hid_countdown_deadline = 0.0
        self._raw_hid_pending_start = None
        if job is not None and self.window is not None:
            try:
                self.window.after_cancel(job)
            except tk.TclError:
                pass
        return was_pending

    def _tick_raw_hid_countdown(self):
        self._raw_hid_countdown_job = None
        pending = self._raw_hid_pending_start
        if pending is None or self.window is None:
            return
        remaining = self._raw_hid_countdown_deadline - time.perf_counter()
        if remaining > 0:
            self.raw_hid_remaining_var.set(
                self.gui.tr("{seconds:.1f} 秒後開始").format(
                    seconds=remaining
                )
            )
            self._raw_hid_countdown_job = self.window.after(
                50, self._tick_raw_hid_countdown
            )
            return
        device_path, duration = pending
        self._raw_hid_pending_start = None
        self._raw_hid_countdown_deadline = 0.0
        # Re-check immediately before opening the measurement handle. This
        # also covers a stream process that was still shutting down when the
        # user pressed Start.
        if not self._stop_raw_hid_stream():
            self._raw_hid_resume_after_measurement = False
            self.raw_hid_state_var.set(
                self.gui.tr("Raw HID 介面仍被實際採樣占用")
            )
            self.raw_hid_remaining_var.set(self.gui.tr("量測失敗"))
            self._set_raw_hid_controls_active(False)
            return
        if not self.raw_hid_probe.start(device_path, duration):
            self._raw_hid_resume_after_measurement = False
            self.raw_hid_state_var.set(
                self.gui.tr("Raw HID 量測器無法啟動")
            )
            self.raw_hid_remaining_var.set(self.gui.tr("量測失敗"))
            self._set_raw_hid_controls_active(False)
            return
        self._raw_hid_countdown_cancelled = False
        self.raw_hid_remaining_var.set(
            self.gui.tr("{seconds:.1f} 秒").format(seconds=duration)
        )
        self.raw_hid_state_var.set(self.gui.tr("正在開啟 Raw HID 介面..."))

    @staticmethod
    def _format_interval_us(value):
        value = float(value or 0.0) / 1000.0
        if value <= 0.0:
            return "—"
        if value < 1.0:
            return f"{value:.3f}"
        if value < 10.0:
            return f"{value:.2f}"
        return f"{value:.1f}"

    def _update_raw_hid_percentile_info(self, interval_count):
        total = max(0, int(interval_count or 0))
        values = (
            ("p50", 0.50),
            ("p95", 0.95),
            ("p99", 0.99),
        )
        for key, percentile in values:
            covered = math.ceil(total * percentile) if total else 0
            self.raw_hid_percentile_info_vars[key].set(
                self.gui.tr("{count} 筆資料").format(
                    count=f"{covered:,}",
                )
            )

    def _update_raw_hid_stat_colors(self, snapshot):
        neutral = "#333333"
        mean_us = float(snapshot.get("mean_us", 0.0) or 0.0)
        if mean_us <= 0:
            for key, label in self.raw_hid_stat_labels.items():
                self.raw_hid_stat_quality[key] = "neutral"
                label.configure(foreground=neutral)
            return
        for key, label in self.raw_hid_stat_labels.items():
            value_us = float(snapshot.get(f"{key}_us", 0.0) or 0.0)
            if key in {"min", "mean", "max"} or value_us <= 0:
                quality = "neutral"
                color = neutral
            else:
                ratio = value_us / mean_us
                if 0.75 <= ratio <= 1.25:
                    quality = "good"
                    color = "#2E7D32"
                elif 0.50 <= ratio <= 2.00:
                    quality = "fair"
                    color = "#B26A00"
                else:
                    quality = "poor"
                    color = "#C62828"
            self.raw_hid_stat_quality[key] = quality
            label.configure(foreground=color)

    def _raw_hid_stat_tooltip_text(self, key):
        if key == "mean":
            return self.gui.tr(
                "平均值是本次量測的比較基準，不單獨判定好壞。"
            )
        if key in {"min", "max"}:
            return self.gui.tr(
                "最小值與最大值是單筆極端資料，不適合單獨判定好壞。"
            )
        quality_text = {
            "good": "綠色：與本次平均時間差接近，回報分佈穩定。",
            "fair": "橘色：與本次平均時間差有些差距，可能存在輕微波動。",
            "poor": "紅色：與本次平均時間差偏差明顯，可能有集中到達或較大的抖動。",
        }.get(
            self.raw_hid_stat_quality.get(key),
            "尚無足夠資料可供判讀。",
        )
        return self.gui.tr(quality_text)

    def _update_raw_hid_measurement(self, redraw_chart=True):
        if (
            self._raw_hid_pending_start is not None
            or self._raw_hid_countdown_cancelled
        ):
            return
        snapshot = self.raw_hid_probe.read_snapshot()
        state = snapshot.get("state", "idle")
        if state == "idle":
            return
        rate = float(snapshot.get("rate_hz", 0.0) or 0.0)
        self.raw_hid_rate_var.set(f"{rate:,.0f} Hz" if rate > 0 else "— Hz")
        effective_rate = float(
            snapshot.get("effective_rate_hz", 0.0) or 0.0
        )
        show_effective = self._raw_hid_effective_rate_visible(snapshot)
        self.raw_hid_effective_rate_var.set(
            f"{effective_rate:,.0f} Hz" if show_effective else "— Hz"
        )
        self.raw_hid_count_var.set(f"{int(snapshot.get('reports', 0)):,}")
        self._update_raw_hid_percentile_info(
            snapshot.get("intervals", 0)
        )
        remaining = float(snapshot.get("remaining_ms", 0.0) or 0.0)
        remaining_text = {
            "complete": "量測完成",
            "stopped": "已提前停止",
            "error": "量測失敗",
        }.get(state)
        self.raw_hid_remaining_var.set(
            self.gui.tr("{seconds:.1f} 秒").format(
                seconds=remaining / 1000.0
            )
            if state in {"opening", "running"}
            else self.gui.tr(remaining_text) if remaining_text else "—"
        )
        for key in ("p50", "p95", "p99", "min", "mean", "max"):
            self.raw_hid_stats_vars[key].set(
                self._format_interval_us(snapshot.get(f"{key}_us"))
            )
        self._update_raw_hid_stat_colors(snapshot)
        percentile_markers = tuple(
            float(snapshot.get(f"{key}_us", 0.0) or 0.0)
            for key in ("p50", "p95", "p99")
        )
        counts = tuple(snapshot.get("histogram_counts") or ())
        histogram_max_us = int(snapshot.get("histogram_max_us", 0) or 0)
        distribution = (counts, histogram_max_us, percentile_markers)
        if (
            redraw_chart
            and distribution != self._raw_hid_last_distribution
        ):
            self._raw_hid_last_distribution = distribution
            self._draw_raw_hid_chart(
                counts,
                histogram_max_us,
                *percentile_markers,
            )
        state_text = {
            "opening": "正在開啟 Raw HID 介面...",
            "running": "量測中",
            "complete": "量測完成",
            "stopped": "已提前停止",
            "error": "Raw HID 量測失敗（錯誤代碼 {code}）",
        }.get(state, "尚未量測")
        self.raw_hid_state_var.set(
            self.gui.tr(state_text).format(
                code=int(snapshot.get("error_code", 0) or 0)
            )
        )
        self.raw_hid_analysis_var.set(
            self._raw_hid_analysis_text(snapshot)
        )
        self._set_raw_hid_controls_active(
            state in {"opening", "running"}
        )
        if (
            state in {"complete", "stopped", "error"}
            and getattr(self, "_raw_hid_resume_after_measurement", False)
        ):
            self._resume_actual_sampling_after_measurement()

    @staticmethod
    def _raw_hid_effective_rate_visible(snapshot):
        return bool(
            snapshot.get("axes_available")
            and snapshot.get("activity_sufficient")
            and float(snapshot.get("effective_rate_hz", 0.0) or 0.0) > 0
        )

    def _raw_hid_analysis_text(self, snapshot):
        """Build a compact two-line interpretation from the measurement."""
        state = str(snapshot.get("state") or "idle")
        if state in {"opening", "running"}:
            return self.gui.tr("判讀：量測中，完成後顯示分析結果。")
        if state == "error":
            return self.gui.tr(
                "判讀：量測失敗，無法分析回報穩定度與有效狀態更新率。"
            )
        if state == "idle":
            return self.gui.tr("判讀：尚未量測。")

        rate = float(snapshot.get("rate_hz", 0.0) or 0.0)
        p50 = float(snapshot.get("p50_us", 0.0) or 0.0) / 1000.0
        p95 = float(snapshot.get("p95_us", 0.0) or 0.0) / 1000.0
        p99 = float(snapshot.get("p99_us", 0.0) or 0.0) / 1000.0
        maximum = float(snapshot.get("max_us", 0.0) or 0.0) / 1000.0
        if rate <= 0 or p50 <= 0:
            cadence = self.gui.tr("回報間隔資料不足，無法判讀穩定度。")
        else:
            expected = 1000.0 / rate
            typical_ratio = p50 / expected
            p95_ratio = p95 / p50 if p50 > 0 else 0.0
            tail_ratio = p99 / p50 if p50 > 0 else 0.0
            maximum_ratio = maximum / p50 if p50 > 0 else 0.0
            if (
                0.85 <= typical_ratio <= 1.15
                and p95_ratio <= 1.30
                and tail_ratio <= 1.35
                and 1.70 <= maximum_ratio <= 2.30
            ):
                cadence = self.gui.tr(
                    "主要回報間隔穩定；最大間隔約為典型間隔的 2 倍，代表少數回報跨到下一個傳輸週期。這可能是低延遲傳輸策略的預期特性，不代表平均輸入延遲加倍。"
                )
            elif (
                0.75 <= typical_ratio <= 1.25
                and 1.70 <= tail_ratio <= 2.30
                and maximum_ratio >= 1.70
            ):
                cadence = self.gui.tr(
                    "P99 與最大間隔皆接近典型間隔的 2 倍，跨週期情形並非單一極端值；可能存在較頻繁的排程等待或傳輸波動。"
                )
            elif (
                0.85 <= typical_ratio <= 1.15
                and p95_ratio <= 1.20
                and tail_ratio <= 1.35
            ):
                cadence = self.gui.tr(
                    "主要回報間隔接近預期值，P95／P99 尾端也集中，回報穩定。"
                )
            elif (
                0.75 <= typical_ratio <= 1.25
                and p95_ratio <= 1.30
                and tail_ratio <= 2.25
            ):
                cadence = self.gui.tr(
                    "主要回報間隔接近預期值，尾端有少量跨週期回報，未見持續堆積。"
                )
            else:
                cadence = self.gui.tr(
                    "回報間隔分散或偏離預期值，存在較明顯的排程波動。"
                )

        if not snapshot.get("axes_available"):
            effective = self.gui.tr(
                "無法解析標準搖桿軸，因此本次不提供有效回報率。"
            )
        elif not snapshot.get("activity_sufficient"):
            effective = self.gui.tr(
                "搖桿活動量或資料量不足，本次有效回報率不具判讀條件。"
            )
        else:
            ratio = float(snapshot.get("effective_ratio", 0.0) or 0.0)
            dominant = int(snapshot.get("dominant_run_length", 0) or 0)
            if snapshot.get("regular_repeat") and dominant >= 2:
                effective = self.gui.tr(
                    "偵測到規律重複狀態：每個搖桿狀態通常維持 {count} 筆；有效更新率明顯低於 HID 回報率。"
                ).format(count=dominant)
            elif ratio >= 0.90:
                effective = self.gui.tr(
                    "有效狀態更新率接近 HID 回報率，未發現明顯規律重複。"
                )
            elif ratio >= 0.75:
                effective = self.gui.tr(
                    "有效狀態更新率略低於 HID 回報率，未發現固定重複規律。"
                )
            else:
                effective = self.gui.tr(
                    "有效狀態更新率明顯較低，但未發現固定重複規律；可能受軸解析度、濾波或轉動速度影響。"
                )
        return self.gui.tr("判讀：{cadence}\n{effective}").format(
            cadence=cadence, effective=effective
        )

    def _draw_raw_hid_chart(
        self, counts, histogram_max_us, p50_us, p95_us, p99_us
    ):
        self._raw_hid_chart_data = (
            tuple(counts),
            int(histogram_max_us or 0),
            float(p50_us or 0.0),
            float(p95_us or 0.0),
            float(p99_us or 0.0),
        )
        canvas = self.raw_hid_canvas
        if canvas is None:
            return
        canvas.delete("all")
        if not counts or max(counts, default=0) <= 0:
            width = max(640, int(canvas.winfo_width() or 680))
            height = max(200, int(canvas.winfo_height() or 240))
            canvas.create_text(
                width / 2,
                height / 2,
                text=self.gui.tr("尚未開始量測\n無數據顯示"),
                fill="#888888",
                justify="center",
            )
            return
        width = max(640, int(canvas.winfo_width() or 680))
        height = max(200, int(canvas.winfo_height() or 240))
        left, top, right, bottom = 10, 8, width - 8, height - 34
        canvas.create_rectangle(
            left, top, right, bottom, outline="#B8B8B8"
        )
        values = (float(p50_us), float(p95_us), float(p99_us))
        x_max_us = max(1.0, float(histogram_max_us or 0))
        visible_start, visible_end = self._raw_hid_chart_visible_range(
            counts, x_max_us, values
        )
        bucket_count = max(1, len(counts) - 1)
        visible_min_us = x_max_us * visible_start / bucket_count
        visible_max_us = x_max_us * visible_end / bucket_count
        visible_span_us = max(1.0, visible_max_us - visible_min_us)
        for index in range(5):
            x = left + (right - left) * index / 4
            value_ms = (
                visible_min_us + visible_span_us * index / 4
            ) / 1000.0
            canvas.create_line(x, top, x, bottom, fill="#E8E8E8")
            label_x = min(max(x, 22), width - 22)
            canvas.create_text(
                label_x, bottom + 8, text=f"{value_ms:.3f}",
                anchor="n",
                fill="#666666",
            )
            y = top + (bottom - top) * index / 4
            canvas.create_line(left, y, right, y, fill="#E8E8E8")
        peak = max(counts)
        points = [(left, bottom)]
        for index in range(visible_start, visible_end + 1):
            count = counts[index]
            x = left + (right - left) * (
                index - visible_start
            ) / max(1, visible_end - visible_start)
            # Preserve visible headroom for percentile labels and make a
            # narrow high-frequency peak easier to read.
            y = bottom - count / peak * (bottom - top) * 0.82
            points.append((x, y))
        points.append((right, bottom))
        canvas.create_polygon(
            *[coordinate for point in points for coordinate in point],
            fill="#BBDEFB", outline="#1976D2", width=2,
            smooth=True, splinesteps=12,
        )
        label_y_offsets = (8, 30, 52)
        marker_labels = []
        for label_index, (color, label, value) in enumerate(zip(
            ("#42A5F5", "#FB8C00", "#E53935"),
            ("P50", "P95", "P99"),
            values,
        )):
            x = left + (
                min(max(value, visible_min_us), visible_max_us)
                - visible_min_us
            ) / visible_span_us * (right - left)
            canvas.create_line(x, top, x, bottom, fill=color, width=2)
            text_id = canvas.create_text(
                x, top + label_y_offsets[label_index],
                text=label, fill=color, anchor="n"
            )
            bounds = canvas.bbox(text_id)
            if bounds is not None:
                box_id = canvas.create_rectangle(
                    bounds[0] - 3, bounds[1] - 2,
                    bounds[2] + 3, bounds[3] + 2,
                    fill="#FFFFFF", outline=color,
                )
                canvas.tag_lower(box_id, text_id)
                marker_labels.append((box_id, text_id))
        # Marker lines are painted in percentile order. Raise the completed
        # white label cards afterwards so a nearby P95/P99 line cannot cross
        # an earlier P50/P95 label.
        for box_id, text_id in marker_labels:
            canvas.tag_raise(box_id)
            canvas.tag_raise(text_id)

    @staticmethod
    def _raw_hid_chart_visible_range(counts, x_max_us, percentiles):
        """Return a padded bucket range that fills the chart with data."""
        bucket_count = len(counts)
        if bucket_count <= 1 or x_max_us <= 0:
            return 0, max(0, bucket_count - 1)

        active_buckets = [
            index for index, count in enumerate(counts) if count > 0
        ]
        if not active_buckets:
            return 0, bucket_count - 1

        last_bucket = bucket_count - 1
        marker_buckets = [
            min(last_bucket, max(0, round(value / x_max_us * last_bucket)))
            for value in percentiles if value > 0
        ]
        start = min(active_buckets + marker_buckets)
        end = max(active_buckets + marker_buckets)
        padding = max(2, int(math.ceil((end - start + 1) * 0.15)))
        return max(0, start - padding), min(last_bucket, end + padding)

    def _redraw_raw_hid_chart(self):
        self._draw_raw_hid_chart(*self._raw_hid_chart_data)

    def _enable_high_resolution_timer(self):
        """Request 1 ms Windows timer granularity while this tester is open."""
        if self._high_resolution_timer_active:
            return
        try:
            result = ctypes.windll.winmm.timeBeginPeriod(1)
            self._high_resolution_timer_active = result == 0
        except (AttributeError, OSError):
            self._high_resolution_timer_active = False

    def _disable_high_resolution_timer(self):
        if not self._high_resolution_timer_active:
            return
        try:
            ctypes.windll.winmm.timeEndPeriod(1)
        except (AttributeError, OSError):
            pass
        self._high_resolution_timer_active = False

    @staticmethod
    def _parameter_control_is_disabled(widget):
        try:
            return bool(widget.instate(["disabled"]))
        except (AttributeError, tk.TclError):
            return False

    def _apply_parameter_value(
        self,
        variable,
        value,
        minimum,
        maximum,
        step,
        on_change,
    ):
        numeric = normalize_test_parameter(
            value,
            minimum,
            maximum,
            step,
        )
        variable.set(numeric)
        if on_change is not None:
            on_change()
        return numeric

    def _bind_parameter_control(
        self,
        widget,
        variable,
        title,
        minimum,
        maximum,
        *,
        step,
        number_format,
        on_change,
        state_widget=None,
    ):
        """Add click-to-enter and four-direction scrubbing to a value label."""
        pixels_per_step = 8
        state_widget = state_widget or widget
        state = {
            "start_x": 0,
            "start_y": 0,
            "start_value": float(minimum),
            "last_steps": 0,
            "dragged": False,
            "enabled": False,
        }
        widget.configure(cursor="sb_h_double_arrow")

        def begin(event):
            state["enabled"] = not self._parameter_control_is_disabled(
                state_widget
            )
            if not state["enabled"]:
                return "break"
            try:
                start_value = float(variable.get())
            except (TypeError, ValueError, tk.TclError):
                start_value = float(minimum)
            state.update({
                "start_x": event.x_root,
                "start_y": event.y_root,
                "start_value": start_value,
                "last_steps": 0,
                "dragged": False,
            })
            return "break"

        def drag(event):
            if not state["enabled"]:
                return "break"
            delta_x = event.x_root - state["start_x"]
            delta_y = event.y_root - state["start_y"]
            distance = delta_x if abs(delta_x) >= abs(delta_y) else -delta_y
            step_count = int(distance / pixels_per_step)
            if step_count == 0 or step_count == state["last_steps"]:
                return "break"
            state["dragged"] = True
            state["last_steps"] = step_count
            self._apply_parameter_value(
                variable,
                state["start_value"] + step_count * float(step),
                minimum,
                maximum,
                step,
                on_change,
            )
            return "break"

        def finish(_event):
            if not state["enabled"]:
                return "break"
            if not state["dragged"]:
                self._open_parameter_editor(
                    variable,
                    title,
                    minimum,
                    maximum,
                    step=step,
                    number_format=number_format,
                    on_change=on_change,
                    state_widget=state_widget,
                )
            return "break"

        widget.bind("<ButtonPress-1>", begin)
        widget.bind("<B1-Motion>", drag)
        widget.bind("<ButtonRelease-1>", finish)

    def _open_parameter_editor(
        self,
        variable,
        title,
        minimum,
        maximum,
        *,
        step,
        number_format,
        on_change,
        state_widget,
    ):
        if self._parameter_control_is_disabled(state_widget):
            return
        current = self._parameter_editor_window
        try:
            if current is not None and current.winfo_exists():
                current.deiconify()
                current.lift()
                return
        except tk.TclError:
            pass

        owner = self.window or self.root
        dialog = tk.Toplevel(owner)
        dialog.withdraw()
        dialog.title(
            f"{self.gui.tr('輸入參數')} - {self.gui.tr(title)}"
        )
        dialog.resizable(False, False)
        dialog.transient(owner)
        self._parameter_editor_window = dialog

        content = ttk.Frame(dialog, padding=12)
        content.grid(row=0, column=0, sticky="nsew")
        ttk.Label(
            content,
            text=self.gui.tr("目前數值"),
        ).grid(row=0, column=0, sticky="e", padx=(0, 8), pady=(0, 8))
        try:
            initial = format(float(variable.get()), number_format)
        except (TypeError, ValueError, tk.TclError):
            initial = format(float(minimum), number_format)
        input_var = tk.StringVar(master=dialog, value=initial)
        entry = ttk.Entry(content, textvariable=input_var, width=16)
        entry.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        ttk.Label(
            content,
            text=(
                f"{self.gui.tr('設定範圍')}：{minimum:g} ～ {maximum:g}\n"
                f"{self.gui.tr('步進')}：{step:g}"
            ),
            justify="left",
        ).grid(row=1, column=0, columnspan=2, sticky="w")

        actions = ttk.Frame(content)
        actions.grid(row=2, column=0, columnspan=2, pady=(12, 0))

        def close_dialog():
            if self._parameter_editor_window is dialog:
                self._parameter_editor_window = None
            try:
                dialog.destroy()
            except tk.TclError:
                pass

        def apply_value(_event=None):
            try:
                self._apply_parameter_value(
                    variable,
                    input_var.get(),
                    minimum,
                    maximum,
                    step,
                    on_change,
                )
            except (TypeError, ValueError, tk.TclError):
                messagebox.showerror(
                    self.gui.tr("設定錯誤"),
                    self.gui.tr("請輸入有效數字。"),
                    parent=dialog,
                )
                entry.focus_set()
                entry.selection_range(0, "end")
                return
            close_dialog()

        ttk.Button(
            actions,
            text=self.gui.tr("確定"),
            command=apply_value,
            width=9,
        ).pack(side="left", padx=(0, 6))
        ttk.Button(
            actions,
            text=self.gui.tr("取消"),
            command=close_dialog,
            width=9,
        ).pack(side="left")
        dialog.bind("<Return>", apply_value)
        dialog.bind("<Escape>", lambda _event: close_dialog())
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        dialog.update_idletasks()

        width = max(290, dialog.winfo_reqwidth())
        height = dialog.winfo_reqheight()
        try:
            x = owner.winfo_rootx() + (owner.winfo_width() - width) // 2
            y = owner.winfo_rooty() + (owner.winfo_height() - height) // 2
            screen_width = owner.winfo_screenwidth()
            screen_height = owner.winfo_screenheight()
            x = max(0, min(screen_width - width, x))
            y = max(0, min(screen_height - height, y))
            dialog.geometry(f"{width}x{height}+{x}+{y}")
        except tk.TclError:
            pass
        dialog.deiconify()
        entry.focus_set()
        entry.selection_range(0, "end")
        dialog.grab_set()

    @staticmethod
    def _read_about_document(path):
        """Read a bundled notice without making the About page fragile."""
        try:
            return Path(path).read_text(encoding="utf-8")
        except OSError:
            return ""

    @staticmethod
    def _open_about_link(url):
        """Open an official project link in the user's default browser."""
        try:
            return bool(webbrowser.open(url, new=2))
        except (OSError, webbrowser.Error):
            return False

    def _save_automatic_update_preference(self):
        """Persist the About-page update toggle for both desktop programs."""
        enabled = bool(self.automatic_update_checks_var.get())
        try:
            save_update_preferences(automatic_checks=enabled)
        except (OSError, ValueError, configparser.Error) as exc:
            self.automatic_update_checks_var.set(not enabled)
            messagebox.showerror(
                self.gui.tr("無法儲存設定"),
                str(exc),
                parent=self.window,
            )

    def check_for_updates(self, manual=True):
        """Check GitHub in the background and update the About-page status."""
        if self._update_check_in_progress:
            return False
        self._update_check_in_progress = True
        self.update_check_status_var.set(
            self.gui.tr("正在檢查版本…")
        )
        if self.update_check_button is not None:
            self.update_check_button.configure(state="disabled")

        def worker():
            release = None
            error = None
            try:
                release = check_latest_release(VERSION)
            except (UpdateCheckError, OSError, ValueError) as exc:
                error = exc
            window = self.window
            if window is None:
                self._update_check_in_progress = False
                return
            try:
                window.after(
                    0,
                    lambda: self._finish_update_check(
                        release,
                        error,
                        bool(manual),
                    ),
                )
            except (AttributeError, tk.TclError):
                self._update_check_in_progress = False

        threading.Thread(
            target=worker,
            daemon=True,
            name="GamepadTesterUpdateCheck",
        ).start()
        return True

    def _finish_update_check(self, release, error, manual):
        """Apply a completed check on Tk's UI thread."""
        self._update_check_in_progress = False
        if self.update_check_button is not None:
            self.update_check_button.configure(state="normal")
        if error is not None:
            self.update_check_status_var.set(
                self.gui.tr("版本檢查失敗")
            )
            if manual:
                messagebox.showerror(
                    self.gui.tr("無法檢查更新"),
                    self.gui.tr(
                        "目前無法連線至 GitHub 檢查版本，請稍後再試。"
                    )
                    + f"\n\n{error}",
                    parent=self.window,
                )
            return
        if release is None:
            return
        try:
            newer = is_newer_version(release.version, VERSION)
        except ValueError as exc:
            self.update_check_status_var.set(
                self.gui.tr("版本檢查失敗")
            )
            if manual:
                messagebox.showerror(
                    self.gui.tr("無法檢查更新"),
                    str(exc),
                    parent=self.window,
                )
            return
        if not newer:
            self.update_check_status_var.set(
                self.gui.tr("目前為最新版本：v{version}").format(
                    version=VERSION
                )
            )
            return
        self.update_check_status_var.set(
            self.gui.tr("已有新版本：v{version}").format(
                version=release.version
            )
        )
        if (
            not manual
            and ignored_update_version() == release.version
        ):
            return
        self._show_update_available_prompt(release)

    def _show_update_available_prompt(self, release):
        """Show the simplified manual-download prompt."""
        current = self._update_prompt_window
        if current is not None:
            try:
                if current.winfo_exists():
                    current.deiconify()
                    current.lift()
                    return
            except (AttributeError, tk.TclError):
                pass

        window = tk.Toplevel(self.window)
        window.withdraw()
        window.title(self.gui.tr("發現新版本"))
        window.resizable(False, False)
        window.transient(self.window)
        self._update_prompt_window = window
        suppress_var = tk.BooleanVar(master=window, value=False)
        content = ttk.Frame(window, padding=(18, 16))
        content.pack(fill="both", expand=True)
        ttk.Label(
            content,
            text=(
                self.gui.tr("S2P-XInput-Lite 有新版本可用。")
                + f"\n\n{self.gui.tr('目前版本')}：{VERSION}\n"
                + f"{self.gui.tr('最新版本')}：{release.version}\n\n"
                + self.gui.tr(
                    "按下「開啟下載頁」後，請從官方 GitHub Release "
                    "下載並手動解壓縮更新。"
                )
            ),
            justify="left",
            wraplength=430,
        ).pack(fill="x")
        ttk.Checkbutton(
            content,
            text=self.gui.tr("不再提醒此版本"),
            variable=suppress_var,
        ).pack(anchor="w", pady=(14, 0))
        actions = ttk.Frame(content)
        actions.pack(fill="x", pady=(16, 0))

        def close_prompt(open_release=False):
            if suppress_var.get():
                try:
                    save_update_preferences(
                        ignored_version=release.version
                    )
                except (OSError, ValueError, configparser.Error) as exc:
                    messagebox.showerror(
                        self.gui.tr("無法儲存設定"),
                        str(exc),
                        parent=self.window,
                    )
            if self._update_prompt_window is window:
                self._update_prompt_window = None
            try:
                window.destroy()
            except tk.TclError:
                pass
            if (
                open_release
                and not self._open_about_link(release.html_url)
            ):
                messagebox.showerror(
                    self.gui.tr("無法開啟下載頁"),
                    release.html_url,
                    parent=self.window,
                )

        ttk.Button(
            actions,
            text=self.gui.tr("稍後"),
            command=close_prompt,
            width=9,
        ).pack(side="right")
        ttk.Button(
            actions,
            text=self.gui.tr("開啟下載頁"),
            command=lambda: close_prompt(True),
            width=15,
        ).pack(side="right", padx=(0, 8))
        window.protocol("WM_DELETE_WINDOW", close_prompt)
        window.bind("<Escape>", lambda _event: close_prompt())
        window.update_idletasks()
        width = max(480, window.winfo_reqwidth())
        height = window.winfo_reqheight()
        try:
            x = self.window.winfo_rootx() + (
                self.window.winfo_width() - width
            ) // 2
            y = self.window.winfo_rooty() + (
                self.window.winfo_height() - height
            ) // 2
            window.geometry(f"{width}x{height}+{max(0, x)}+{max(0, y)}")
        except tk.TclError:
            pass
        window.deiconify()
        window.lift()

    def _build_about_document(self, notebook, title, path):
        page = ttk.Frame(notebook, padding=(6, 6))
        page.columnconfigure(0, weight=1)
        page.rowconfigure(0, weight=1)

        text = tk.Text(
            page,
            wrap="word",
            relief="flat",
            borderwidth=0,
            padx=9,
            pady=8,
            font=("Segoe UI", 9),
        )
        scrollbar = ttk.Scrollbar(
            page,
            orient="vertical",
            command=text.yview,
        )
        text.configure(yscrollcommand=scrollbar.set)
        text.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")

        content = self._read_about_document(path)
        if not content:
            content = self.gui.tr("找不到授權文件。")
        text.insert("1.0", content)
        text.configure(state="disabled")
        notebook.add(page, text=f" {self.gui.tr(title)} ")

    def _build_about_tab(self, parent):
        """Build a fixed About panel with independently scrollable notices."""
        parent.columnconfigure(0, weight=1, uniform="about_half")
        parent.columnconfigure(2, weight=1, uniform="about_half")
        parent.rowconfigure(0, weight=1)

        left = ttk.Frame(parent, padding=(16, 12))
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(0, weight=1)

        ttk.Separator(parent, orient="vertical").grid(
            row=0, column=1, sticky="ns", padx=5
        )

        row = 1
        self._about_logo = None
        if ABOUT_ICON_PATH.is_file():
            try:
                logo = tk.PhotoImage(
                    master=self.window,
                    file=str(ABOUT_ICON_PATH),
                )
                divisor = max(1, round(logo.width() / 128))
                if divisor > 1:
                    logo = logo.subsample(divisor, divisor)
                self._about_logo = logo
                ttk.Label(left, image=logo).grid(
                    row=row, column=0, pady=(0, 12)
                )
                row += 1
            except tk.TclError:
                self._about_logo = None

        title_font = tkfont.Font(
            family="Segoe UI",
            size=14,
            weight="bold",
        )
        ttk.Label(
            left,
            text=APP_NAME,
            font=title_font,
            anchor="center",
        ).grid(row=row, column=0, sticky="ew")
        row += 1
        ttk.Label(
            left,
            text=f"{self.gui.tr('版本')} {VERSION}",
            foreground="#666666",
            anchor="center",
        ).grid(row=row, column=0, sticky="ew", pady=(3, 8))
        row += 1
        ttk.Label(
            left,
            textvariable=self.update_check_status_var,
            foreground="#666666",
            anchor="center",
        ).grid(row=row, column=0, sticky="ew", pady=(0, 6))
        row += 1
        self.update_check_button = ttk.Button(
            left,
            text=self.gui.tr("檢查更新"),
            command=lambda: self.check_for_updates(manual=True),
            width=18,
        )
        self.update_check_button.grid(row=row, column=0, pady=(0, 6))
        row += 1
        ttk.Checkbutton(
            left,
            text=self.gui.tr("自動檢查更新"),
            variable=self.automatic_update_checks_var,
            command=self._save_automatic_update_preference,
        ).grid(row=row, column=0, pady=(0, 12))
        row += 1

        link_font = tkfont.Font(
            family="Segoe UI",
            size=10,
            underline=True,
        )
        # Named Tk fonts are deleted when their Python wrappers are collected.
        self._about_fonts = (title_font, link_font)
        background = (
            ttk.Style(parent).lookup("TFrame", "background") or "#F0F0F0"
        )
        for label, url in (
            ("GitHub", GITHUB_URL),
            ("贊助開發（Ko-fi）", SPONSOR_URL),
        ):
            link = tk.Label(
                left,
                text=self.gui.tr(label),
                foreground="#1565C0",
                background=background,
                cursor="hand2",
                font=link_font,
                padx=4,
                pady=4,
            )
            link.grid(row=row, column=0)
            link.bind(
                "<Button-1>",
                lambda _event, target=url: self._open_about_link(target),
            )
            row += 1
        left.rowconfigure(row, weight=1)

        right = ttk.Frame(parent, padding=(10, 4, 4, 4))
        right.grid(row=0, column=2, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(0, weight=1)
        document_notebook = ttk.Notebook(right)
        document_notebook.grid(row=0, column=0, sticky="nsew")
        self._build_about_document(
            document_notebook,
            "許可協議",
            LICENSE_PATH,
        )
        self._build_about_document(
            document_notebook,
            "第三方程式",
            THIRD_PARTY_NOTICES_PATH,
        )

    def _build_diagnostic_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(3, weight=1)

        controls = ttk.Frame(parent)
        controls.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        controls.columnconfigure(7, weight=1)
        self.diagnostic_start_button = ttk.Button(
            controls,
            text=self.gui.tr("開始診斷"),
            command=self.start_diagnostic,
        )
        self.diagnostic_start_button.grid(row=0, column=0, padx=(0, 5))
        self.diagnostic_stop_button = ttk.Button(
            controls,
            text=self.gui.tr("停止診斷"),
            command=self.stop_diagnostic,
            state="disabled",
        )
        self.diagnostic_stop_button.grid(row=0, column=1, padx=(0, 5))
        self.diagnostic_export_button = ttk.Button(
            controls,
            text=self.gui.tr("匯出支援 Log"),
            command=self.export_diagnostic_log,
        )
        self.diagnostic_export_button.grid(row=0, column=2, padx=(0, 12))
        ttk.Label(controls, text=self.gui.tr("診斷時間")).grid(
            row=0, column=3, padx=(0, 5)
        )
        ttk.Combobox(
            controls,
            textvariable=self.diagnostic_duration_var,
            values=("30", "60", "120"),
            state="readonly",
            width=5,
        ).grid(row=0, column=4)
        diagnostic_help = tk.Label(
            controls,
            text="?",
            width=2,
            relief="solid",
            borderwidth=1,
            cursor="question_arrow",
        )
        diagnostic_help.grid(row=0, column=6, padx=(5, 0))
        HoverTip(
            diagnostic_help,
            self.gui.tr(
                "診斷模式會在選擇的時間內記錄手把輸入頻率、延遲、校正狀態與震動資料。\n\n"
                "測試期間仍可繼續操作手把，也可以縮小此視窗。\n\n"
                "「匯出支援 Log」可隨時使用，會包含程式啟動紀錄；完成診斷後，"
                "還會一併包含手把診斷資料，供 AI 或支援人員分析。"
            ),
            wraplength=380,
        )
        ttk.Label(controls, text=self.gui.tr("秒")).grid(
            row=0, column=5, padx=(4, 0)
        )
        ttk.Label(
            controls,
            textvariable=self.diagnostic_remaining_var,
            anchor="e",
        ).grid(row=0, column=7, sticky="e")

        status = ttk.Frame(parent)
        status.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        status.columnconfigure(0, weight=1)
        ttk.Label(
            status,
            textvariable=self.diagnostic_state_var,
            anchor="w",
        ).grid(row=0, column=0, sticky="ew")
        ttk.Progressbar(
            status,
            variable=self.diagnostic_progress_var,
            maximum=100.0,
        ).grid(row=1, column=0, sticky="ew", pady=(4, 0))

        summary = ttk.LabelFrame(
            parent,
            text=self.gui.tr("即時診斷摘要"),
            padding=(9, 7),
        )
        summary.grid(row=2, column=0, sticky="ew", pady=(0, 8))
        summary.configure(text=self.gui.tr("\u8a3a\u65b7\u5224\u8b80\u6458\u8981"))
        for column in range(2):
            summary.columnconfigure(column, weight=1, uniform="diagnostic")
        items = (
            ("模式", "mode"),
            ("連線", "connection"),
            ("輸入回報", "input"),
            ("延遲／佇列", "latency"),
            ("校正建議", "calibration"),
            ("感測器", "sensor"),
            ("陀螺儀", "gyro"),
            ("震動輸入", "rumble_input"),
            ("震動輸出", "rumble_output"),
            ("震動傳輸", "rumble_transport"),
        )
        # Keep technical telemetry in Recent Status.  This compact area is
        # reserved for the conclusion the user should act on.
        items = (
            ("\u6574\u9ad4\u5224\u8b80", "verdict"),
            ("\u5075\u6e2c\u5230\u7684\u72c0\u6cc1", "findings"),
            ("\u5efa\u8b70\u52d5\u4f5c", "advice"),
        )
        for index, (label, key) in enumerate(items):
            row, column = index, 0
            box = ttk.Frame(summary)
            box.grid(
                row=row, column=column, sticky="ew",
                padx=0,
                pady=2,
            )
            box.columnconfigure(1, weight=1)
            ttk.Label(
                box,
                text=self.gui.tr(label),
                width=11,
                anchor="nw",
            ).grid(row=0, column=0, sticky="nw")
            value_label = ttk.Label(
                box,
                textvariable=self.diagnostic_summary_vars[key],
                anchor="nw",
                justify="left",
                wraplength=560,
            )
            value_label.grid(row=0, column=1, sticky="new")
            box.bind(
                "<Configure>",
                lambda event, label=value_label: label.configure(
                    wraplength=max(120, event.width - 100)
                ),
                add="+",
            )

        event_group = ttk.LabelFrame(
            parent,
            text=self.gui.tr("最近狀態"),
            padding=(8, 6),
        )
        event_group.grid(row=3, column=0, sticky="nsew")
        event_group.configure(text=self.gui.tr("\u8a3a\u65b7\u8a73\u7d30\u72c0\u614b"))
        event_group.columnconfigure(0, weight=1)
        event_group.rowconfigure(0, weight=1)
        self.diagnostic_event_text = tk.Text(
            event_group,
            height=6,
            wrap="word",
            state="disabled",
            relief="flat",
            borderwidth=0,
            background=(
                ttk.Style(parent).lookup("TFrame", "background")
                or "#F0F0F0"
            ),
            font=("Consolas", 9),
        )
        self.diagnostic_event_text.grid(row=0, column=0, sticky="nsew")

    @staticmethod
    def _diagnostic_pair(value, default=(0, 0)):
        value = value if isinstance(value, (list, tuple)) else default
        return tuple(value[:2]) if len(value) >= 2 else tuple(default)

    @staticmethod
    def _diagnostic_rssi_quality(rssi_dbm):
        if rssi_dbm >= -55:
            return "\u6975\u4f73"
        if rssi_dbm >= -67:
            return "\u826f\u597d"
        if rssi_dbm >= -75:
            return "\u666e\u901a"
        if rssi_dbm >= -85:
            return "\u504f\u5f31"
        return "\u5f88\u5f31"

    def _is_s2p_standalone_device(self, device):
        """Return whether a tester entry belongs to S2P standalone USB."""
        if device is None:
            return False
        if device.kind == "raw_hid" and device.raw_hid_key:
            raw = self.raw_hid_devices.get(device.raw_hid_key)
            return bool(
                raw is not None
                and int(raw.vendor_id) == 0xCAFE
                and int(raw.product_id) in {0x4020, 0x4021}
            )
        if device.kind == "xinput":
            has_standalone_xinput = any(
                int(raw.vendor_id) == 0xCAFE
                and int(raw.product_id) == 0x4020
                for raw in self.raw_hid_devices.values()
            )
            xinput_devices = tuple(
                item for item in self._native_test_devices
                if item.kind == "xinput"
            )
            return bool(
                has_standalone_xinput
                and len(xinput_devices) == 1
                and xinput_devices[0].key == device.key
            )
        if device.kind == "winmm":
            caps = getattr(self.backend, "_winmm_caps", {}).get(
                device.index
            )
            return bool(
                (
                    caps is not None
                    and int(getattr(caps, "wMid", -1)) == 0xCAFE
                    and int(getattr(caps, "wPid", -1)) == 0x4021
                )
                or "s2p mobile gamepad" in device.name.casefold()
            )
        return False

    def _diagnostic_target_device(self):
        target_key = self._diagnostic_target_key
        if not target_key:
            return None
        selected = self._selected_device()
        if selected is not None and selected.key == target_key:
            return selected
        return next(
            (
                device for device in self.devices.values()
                if device.key == target_key
            ),
            None,
        )

    def start_diagnostic(self):
        target = self._selected_device()
        if target is None:
            messagebox.showinfo(
                self.gui.tr("診斷模式"),
                self.gui.tr("請先選擇要診斷的測試手把。"),
                parent=self.window,
            )
            return
        try:
            duration = float(self.diagnostic_duration_var.get())
        except (TypeError, ValueError, tk.TclError):
            duration = DEFAULT_DIAGNOSTIC_SECONDS
        self.diagnostic_session = DiagnosticSession(duration)
        self.diagnostic_session.start()
        self._diagnostic_last_event_signature = None
        self._diagnostic_firmware_update_notice_shown = False
        self._diagnostic_target_key = target.key
        self._diagnostic_target_kind = target.kind
        self._diagnostic_target_name = (
            self.selected_device_var.get() or target.name
        )
        self._diagnostic_firmware_source = None
        self.latest_diagnostic_input = {}
        self.diagnostic_session.add_event(
            "diagnostic_target_selected",
            device_key=target.key,
            device_kind=target.kind,
            device_name=self._diagnostic_target_name,
        )
        self.diagnostic_state_var.set(self.gui.tr("診斷執行中"))
        self.diagnostic_progress_var.set(0.0)
        self.diagnostic_start_button.configure(state="disabled")
        self.diagnostic_stop_button.configure(state="normal")

        connector = read_controller_status(CONTROLLER_STATUS_PATH)
        connector_mode = connector.get("mode")
        bridge_owns_port = (
            target.kind == "s2p"
            and connector.get("state") == "connected"
            and connector_mode == "esp32"
        )
        if bridge_owns_port:
            self._diagnostic_firmware_source = "bridge"
            enqueue_controller_command("diagnostic_start")
            self.diagnostic_session.add_event(
                "firmware_channel",
                state="owned_by_bridge_connector",
            )
        elif target.kind == "s2p":
            self.diagnostic_session.add_event(
                "basic_diagnostic_channel",
                mode=connector_mode or "unknown",
            )
        elif self._is_s2p_standalone_device(target):
            self._diagnostic_firmware_source = "standalone"
            self.diagnostic_reader.start()
        else:
            self.diagnostic_session.add_event(
                "basic_diagnostic_channel",
                mode=target.kind,
            )

    def _show_diagnostic_firmware_update_notice(self):
        if self._diagnostic_firmware_update_notice_shown:
            return
        self._diagnostic_firmware_update_notice_shown = True
        self.diagnostic_session.add_event(
            "firmware_update_required",
            required_product="S2P-FW",
            required_version="1.0.0",
            required_protocol="s2p_bridge 1.0.0",
        )
        self.stop_diagnostic("firmware_update_required")
        self.diagnostic_state_var.set(
            self.gui.tr("需要更新韌體才能使用診斷模式")
        )
        messagebox.showwarning(
            self.gui.tr("需要更新 ESP32-S3 韌體"),
            self.gui.tr(
                "目前 ESP32 韌體不支援診斷模式。\n\n"
                "請回到設定頁按「刷入韌體」，刷入 S2P-FW 1.0.0 後，"
                "按 RESET / EN 或重新插拔 ESP32，再重新開啟手把測試。"
            ),
            parent=self.window,
        )

    def stop_diagnostic(self, reason="stopped_by_user"):
        session = self.diagnostic_session
        if self._diagnostic_firmware_source == "bridge":
            enqueue_controller_command("diagnostic_stop")
        if session.running:
            session.stop(reason)
        self.diagnostic_reader.stop(timeout=0.25)
        if session.started_monotonic is None:
            return
        self.diagnostic_state_var.set(
            self.gui.tr("診斷完成")
            if session.completed else self.gui.tr("診斷已停止")
        )
        self.diagnostic_start_button.configure(state="normal")
        self.diagnostic_stop_button.configure(state="disabled")
        self.diagnostic_export_button.configure(state="normal")
        self._update_diagnostic_display()
        if session.completed:
            self.diagnostic_progress_var.set(100.0)

    def export_diagnostic_log(self):
        session = self.diagnostic_session
        diagnostic_log = (
            session.format_log()
            if session.started_monotonic is not None
            else None
        )
        default_name = (
            "S2P-Support-"
            + time.strftime("%Y%m%d-%H%M%S")
            + ".txt"
        )
        filename = filedialog.asksaveasfilename(
            parent=self.window,
            title=self.gui.tr("匯出支援 Log"),
            defaultextension=".txt",
            initialfile=default_name,
            filetypes=((self.gui.tr("文字檔"), "*.txt"),),
        )
        if not filename:
            return
        try:
            Path(filename).write_text(
                format_support_log(diagnostic_log),
                encoding="utf-8",
            )
        except OSError as exc:
            messagebox.showerror(
                self.gui.tr("匯出支援 Log"),
                self.gui.tr("無法寫入支援 Log：{error}").format(error=exc),
                parent=self.window,
            )
            return
        messagebox.showinfo(
            self.gui.tr("匯出支援 Log"),
            self.gui.tr("支援 Log 已匯出。"),
            parent=self.window,
        )

    def _poll_diagnostic(self, now):
        session = self.diagnostic_session
        if not session.running:
            return
        target = self._diagnostic_target_device()
        selected = self._selected_device()
        if (
            target is None
            or selected is None
            or selected.key != self._diagnostic_target_key
        ):
            self.stop_diagnostic("device_changed")
            self.diagnostic_state_var.set(
                self.gui.tr("診斷已停止：測試手把已變更")
            )
            return

        connector = read_controller_status(CONTROLLER_STATUS_PATH)
        selected_input = self.latest_diagnostic_input
        sampled_at = selected_input.get("sampled_at_monotonic")
        if (
            isinstance(sampled_at, (int, float))
            and now - float(sampled_at) > 1.5
        ):
            selected_input = {}
        telemetry = diagnostic_telemetry_for_device(
            target,
            self.latest_telemetry,
            selected_input,
            self._telemetry_is_fresh(self.latest_telemetry),
        )

        if target.kind == "s2p":
            status = dict(connector)
            status.setdefault(
                "state", "connected" if telemetry else "disconnected"
            )
            status.setdefault("mode", "s2p")
        else:
            status = {
                "state": "connected" if telemetry else "disconnected",
                "mode": (
                    "standalone"
                    if self._diagnostic_firmware_source == "standalone"
                    else target.kind
                ),
            }
        status["diagnostic_target"] = {
            "device_key": target.key,
            "device_kind": target.kind,
            "device_name": self._diagnostic_target_name or target.name,
        }

        firmware = {}
        if self._diagnostic_firmware_source == "bridge":
            bridge_firmware = connector.get("firmware_diagnostics")
            if isinstance(bridge_firmware, dict):
                firmware.update(bridge_firmware)
        elif self._diagnostic_firmware_source == "standalone":
            firmware.update(self.diagnostic_reader.snapshot())

        if session.due(now):
            session.add_sample(
                telemetry,
                status,
                firmware,
                now=now,
            )
        if (
            self._diagnostic_firmware_source is not None
            and diagnostic_firmware_needs_update(
                firmware, status, session.elapsed(now)
            )
        ):
            self._show_diagnostic_firmware_update_notice()
            return
        self._update_diagnostic_display(now)
        if not session.running:
            self.stop_diagnostic("completed")

    def _update_diagnostic_display_legacy(self, now=None):
        session = self.diagnostic_session
        if session.started_monotonic is None:
            return
        remaining = session.remaining(now)
        elapsed = session.elapsed(now)
        self.diagnostic_remaining_var.set(
            self.gui.tr("剩餘 {seconds:.0f} 秒").format(seconds=remaining)
            if session.running else self.gui.tr("已完成")
        )
        self.diagnostic_progress_var.set(min(
            100.0, elapsed / session.duration_seconds * 100.0
        ))
        latest = session.samples[-1] if session.samples else {}
        telemetry = latest.get("telemetry") or {}
        status = latest.get("status") or {}
        firmware = latest.get("firmware") or {}
        capabilities = firmware.get("capabilities") or {}
        runtime = firmware.get("runtime_status") or {}
        latency = firmware.get("latency_status") or {}
        rumble = dict(status.get("rumble") or {})
        rumble.update(firmware.get("rumble_status") or {})

        mode = capabilities.get("mode") or status.get("mode") or "unknown"
        mode_names = {
            "esp32": self.gui.tr("橋接模式"),
            "standalone": self.gui.tr("獨立 XInput 模式"),
            "standalone_hid": self.gui.tr("獨立 HID 模式"),
            "wired": self.gui.tr("USB 有線模式"),
            "bluetooth": self.gui.tr("Windows BLE 模式"),
        }
        self.diagnostic_summary_vars["mode"].set(
            mode_names.get(str(mode), str(mode))
        )
        connection = status.get("state") or firmware.get("state") or "unknown"
        self.diagnostic_summary_vars["connection"].set(str(connection))

        rate = telemetry.get("source_rate_hz") or status.get(
            "input_report_rate"
        )
        self.diagnostic_summary_vars["input"].set(
            f"{float(rate):.1f} Hz"
            if isinstance(rate, (int, float)) else "—"
        )
        p95 = session.summary().get("input_interval_ms_p95")
        queue_drops = int(
            latency.get("notify_queue_drops", 0) or 0
        )
        source_gaps = int(latency.get("source_gap_events", 0) or 0)
        usb_wait_us = int(latency.get("usb_wait_avg_us", 0) or 0)
        timing = (
            f"P95 {p95:.2f} ms"
            if p95 is not None
            else f"USB {usb_wait_us / 1000.0:.2f} ms"
            if usb_wait_us > 0 else "—"
        )
        self.diagnostic_summary_vars["latency"].set(
            f"{timing} · gaps {source_gaps} · drops {queue_drops}"
        )

        bias_samples = int(
            status.get("gyro_bias_samples", 0)
            or runtime.get("gyro_bias_samples", 0)
            or 0
        )
        calibration_state = str(
            status.get("gyro_calibration_state") or "idle"
        )
        calibration_text = (
            self.gui.tr("建議保持手把靜止完成初始化")
            if connection == "connected" and bias_samples < 16
            else self.gui.tr("暫時不需要校正")
        )
        if calibration_state in {"failed", "error"}:
            calibration_text = self.gui.tr("建議重新校正")
        self.diagnostic_summary_vars["calibration"].set(calibration_text)

        sensor_mode = status.get("sensor_mode")
        fusion = runtime.get("gyro_active")
        self.diagnostic_summary_vars["sensor"].set(
            str(sensor_mode or (
                "fusion active" if fusion else "—"
            ))
        )
        gyro = status.get("gyro_raw") or runtime.get("gyro_rate")
        if isinstance(gyro, (list, tuple)) and len(gyro) >= 3:
            self.diagnostic_summary_vars["gyro"].set(
                "X {0:.1f} · Y {1:.1f} · Z {2:.1f}".format(
                    *[float(value) for value in gyro[:3]]
                )
            )
        else:
            self.diagnostic_summary_vars["gyro"].set("—")

        raw = self._diagnostic_pair(
            rumble.get("input") or rumble.get("latest_input")
        )
        output = self._diagnostic_pair(
            rumble.get("output") or rumble.get("latest_output")
        )
        frequency = self._diagnostic_pair(rumble.get("frequency"))
        self.diagnostic_summary_vars["rumble_input"].set(
            f"Large {raw[0]} · Small {raw[1]} · "
            f"Max {max(session.rumble_peak_input)}"
        )
        self.diagnostic_summary_vars["rumble_output"].set(
            f"LF {frequency[0]}/{output[0]} · "
            f"HF {frequency[1]}/{output[1]} · "
            f"Max {max(session.rumble_peak_output)}"
        )
        attempts = rumble.get(
            "transport_send_attempts", rumble.get("sent", 0)
        )
        failures = rumble.get("transport_send_failures", 0)
        overwritten = rumble.get("transport_overwritten", 0)
        self.diagnostic_summary_vars["rumble_transport"].set(
            f"sent {attempts} · failed {failures} · latest {overwritten}"
        )

        summary_stats = session.summary()
        warnings = summary_stats.get("warnings", ())
        if not session.samples:
            verdict = self.gui.tr("\u6b63\u5728\u6536\u96c6\u8cc7\u6599")
            findings = self.gui.tr("\u5c1a\u7121\u8db3\u5920\u6a23\u672c\u53ef\u4f9b\u5224\u8b80")
            advice = self.gui.tr("\u8acb\u7e7c\u7e8c\u64cd\u4f5c\u624b\u628a\u81f3\u8a3a\u65b7\u5b8c\u6210")
        elif warnings:
            verdict = self.gui.tr("\u9700\u8981\u6ce8\u610f")
            findings = "\u3001".join(str(item) for item in warnings)
            if "FIRMWARE_NOTIFY_QUEUE_DROPS" in warnings:
                advice = self.gui.tr("\u5efa\u8b70\uff1a\u6e1b\u5c11 USB \u8ca0\u8f09\u4e26\u6aa2\u67e5\u7dda\u6750\u8207\u96fb\u6e90")
            elif "INPUT_SOURCE_GAPS" in warnings:
                advice = self.gui.tr("\u5efa\u8b70\uff1a\u6aa2\u67e5\u624b\u628a\u96fb\u91cf\u8207\u540c\u983b\u5e72\u64fe\uff1b\u50c5\u5728 RSSI \u4f4e\u65bc -75 dBm \u6642\u624d\u9700\u9760\u8fd1\u6a4b\u63a5\u5668")
            else:
                advice = self.gui.tr("\u5efa\u8b70\uff1a\u532f\u51fa Log \u4ee5\u4fbf\u9032\u4e00\u6b65\u5206\u6790")
        else:
            verdict = self.gui.tr("\u76ee\u524d\u672a\u767c\u73fe\u660e\u986f\u7570\u5e38")
            findings = self.gui.tr("\u8f38\u5165\u3001\u5ef6\u9072\u8207\u9707\u52d5\u672a\u51fa\u73fe\u8b66\u793a")
            advice = self.gui.tr("\u53ef\u532f\u51fa Log \u4f5c\u70ba\u672c\u6b21\u8a3a\u65b7\u8a18\u9304")
        self.diagnostic_summary_vars["verdict"].set(verdict)
        self.diagnostic_summary_vars["findings"].set(findings)
        self.diagnostic_summary_vars["advice"].set(advice)

        event_lines = []
        reader_state = firmware.get("state")
        bridge_owned = (
            status.get("state") == "connected"
            and status.get("mode") == "esp32"
        )
        if bridge_owned:
            event_lines.append(self.gui.tr(
                "\u8cc7\u6599\u4f86\u6e90\uff1a\u4e3b\u7a0b\u5f0f\u6a4b\u63a5\u901a\u9053\uff08ESP32 \u5e8f\u5217\u57e0\u7531\u4e3b\u7a0b\u5f0f\u4f7f\u7528\uff09"
            ))
        elif reader_state == "running":
            event_lines.append(
                self.gui.tr("\u8cc7\u6599\u4f86\u6e90\uff1aESP32 \u7368\u7acb\u8a3a\u65b7\u901a\u9053")
                + (f" ({firmware.get('port')})" if firmware.get("port") else "")
            )
        elif reader_state == "unavailable":
            error = str(firmware.get("error") or "")
            if "diagnostic port not found" in error:
                event_lines.extend((
                    self.gui.tr("ESP32 \u8a3a\u65b7\u901a\u9053\uff1a\u672a\u5075\u6e2c\u5230\u53ef\u7528\u88dd\u7f6e"),
                    self.gui.tr(
                        "\u63d0\u793a\uff1a\u82e5\u4f7f\u7528\u6a4b\u63a5\u6a21\u5f0f\uff0c\u8acb\u5148\u7531\u4e3b\u7a0b\u5f0f\u9023\u7dda\uff1b\u82e5\u662f\u820a\u97cc\u9ad4\uff0c\u8acb\u5237\u5165 S2P-FW 1.0.0\u3002"
                    ),
                ))
            else:
                event_lines.append(
                    self.gui.tr("ESP32 \u8a3a\u65b7\u901a\u9053\u7121\u6cd5\u4f7f\u7528\uff1a{error}").format(
                        error=error or self.gui.tr("\u7b49\u5f85\u56de\u8986\u903e\u6642")
                    )
                )
        elif reader_state == "opening":
            event_lines.append(self.gui.tr("ESP32 \u8a3a\u65b7\u901a\u9053\uff1a\u6b63\u5728\u9023\u7dda"))

        event_lines.extend((
            self.gui.tr("\u9023\u7dda\uff1a{connection}\uff1b\u6a21\u5f0f\uff1a{mode}").format(
                connection=connection,
                mode=mode_names.get(str(mode), str(mode)),
            ),
            self.gui.tr("\u6821\u6b63\uff1a{calibration}\uff1b\u611f\u6e2c\u5668\uff1a{sensor}\uff1b\u9640\u87ba\u5100\uff1a{gyro}").format(
                calibration=calibration_text,
                sensor=self.diagnostic_summary_vars["sensor"].get(),
                gyro=self.diagnostic_summary_vars["gyro"].get(),
            ),
            self.gui.tr("\u9707\u52d5\uff1a{input}\uff1b{output}\uff1b{transport}").format(
                input=self.diagnostic_summary_vars["rumble_input"].get(),
                output=self.diagnostic_summary_vars["rumble_output"].get(),
                transport=self.diagnostic_summary_vars["rumble_transport"].get(),
            ),
        ))

        if capabilities:
            event_lines.append(
                self.gui.tr("\u97cc\u9ad4\uff1a{product} {version}\uff1b\u5354\u8b70\uff1a{protocol} {protocol_version}").format(
                    product=capabilities.get("product") or "S2P-FW",
                    version=capabilities.get("version") or "?",
                    protocol=capabilities.get("protocol") or "?",
                    protocol_version=capabilities.get("protocol_version") or "?",
                )
            )
        link_status = firmware.get("link_status") or {}
        if isinstance(link_status, dict) and link_status:
            bridge_mac = link_status.get("bridge_mac")
            if bridge_mac:
                event_lines.append(
                    self.gui.tr("\u6a4b\u63a5\u5668 MAC\uff1a{mac}").format(
                        mac=bridge_mac
                    )
                )
            for link in (link_status.get("links") or [])[:3]:
                if not isinstance(link, (list, tuple)) or len(link) < 6:
                    continue
                channel, controller_mac, ready, interval, rssi, age = link[:6]
                try:
                    rssi = int(rssi)
                except (TypeError, ValueError):
                    rssi = 127
                if -127 <= rssi <= 20:
                    signal = f"{rssi} dBm ({self.gui.tr(self._diagnostic_rssi_quality(rssi))})"
                else:
                    signal = self.gui.tr("\u97cc\u9ad4\u672a\u63d0\u4f9b")
                event_lines.append(
                    self.gui.tr(
                        "\u624b\u628a CH{channel} MAC\uff1a{mac}\uff1b\u8a0a\u865f\uff1a{signal}\uff1b\u9023\u7dda\u9593\u9694\uff1a{interval:.2f} ms"
                    ).format(
                        channel=channel,
                        mac=controller_mac,
                        signal=signal,
                        interval=float(interval or 0.0),
                    )
                )
        summary_stats = session.summary()
        p50 = summary_stats.get("input_interval_ms_p50")
        p95 = summary_stats.get("input_interval_ms_p95")
        p99 = summary_stats.get("input_interval_ms_p99")
        if any(value is not None for value in (p50, p95, p99)):
            event_lines.append(
                self.gui.tr(
                    "\u684c\u9762\u7aef\u8f38\u5165\u9593\u9694\uff1aP50 {p50:.2f} ms\uff1bP95 {p95:.2f} ms\uff1bP99 {p99:.2f} ms"
                ).format(
                    p50=float(p50),
                    p95=float(p95),
                    p99=float(p99),
                )
            )
        raw_rate_average = summary_stats.get("ble_raw_report_rate_hz_avg")
        if raw_rate_average is not None:
            event_lines.append(
                self.gui.tr(
                    "BLE \u539f\u59cb\u56de\u5831\u7387\uff1a\u5e73\u5747 {average:.1f} Hz\uff1bP50 {p50:.1f} Hz\uff1bP95 {p95:.1f} Hz\uff1bP99 {p99:.1f} Hz"
                ).format(
                    average=float(raw_rate_average),
                    p50=float(summary_stats.get("ble_raw_report_rate_hz_p50") or 0.0),
                    p95=float(summary_stats.get("ble_raw_report_rate_hz_p95") or 0.0),
                    p99=float(summary_stats.get("ble_raw_report_rate_hz_p99") or 0.0),
                )
            )
        if latency:
            event_lines.append(
                self.gui.tr(
                    "\u97cc\u9ad4\u8f38\u5165\uff1a{reports} \u5831\u544a\uff1b\u6f0f\u5931 {gaps}\uff1b\u4f47\u5217\u4e1f\u68c4 {drops}\uff1bUSB \u7b49\u5f85\u5e73\u5747 {wait:.2f} ms"
                ).format(
                    reports=int(latency.get("ble_input_reports", 0) or 0),
                    gaps=int(latency.get("source_gap_events", 0) or 0),
                    drops=int(latency.get("notify_queue_drops", 0) or 0),
                    wait=float(latency.get("usb_wait_avg_us", 0) or 0) / 1000.0,
                )
            )
        self_tests = firmware.get("self_tests") or []
        setup_errors = firmware.get("setup_errors") or []
        if self_tests or setup_errors:
            event_lines.append(
                self.gui.tr("\u97cc\u9ad4\u81ea\u6e2c\uff1a{passed} \u9805\u5df2\u56de\u8986\uff1b{failed} \u9805\u672a\u5b8c\u6210").format(
                    passed=len(self_tests), failed=len(setup_errors)
                )
            )
        if reader_state in {"opening", "unavailable"}:
            event_lines.append(self.gui.tr(
                "\u63d0\u793a\uff1a\u6c92\u6709\u97cc\u9ad4\u8cc7\u6599\u6642\uff0c\u4ecd\u53ef\u8a18\u9304\u624b\u628a\u8f38\u5165\u8207\u4e3b\u7a0b\u5f0f\u9707\u52d5\u8f38\u51fa\u3002"
            ))
        for warning in session.summary().get("warnings", ()):
            event_lines.append(f"WARN: {warning}")
        event_lines.extend(
            self.gui.tr("\u91cd\u8981\u4e8b\u4ef6\uff1a{event}").format(
                event=event.get("type", "unknown")
            )
            for event in session.events[-4:]
            if event.get("type") not in {
                "session_started", "session_stopped", "firmware_channel",
            }
        )
        signature = tuple(event_lines)
        if (
            self.diagnostic_event_text is not None
            and signature != self._diagnostic_last_event_signature
        ):
            self._diagnostic_last_event_signature = signature
            self.diagnostic_event_text.configure(state="normal")
            self.diagnostic_event_text.delete("1.0", "end")
            self.diagnostic_event_text.insert(
                "1.0", "\n".join(event_lines)
            )
            self.diagnostic_event_text.configure(state="disabled")

    def _update_diagnostic_display(self, now=None):
        """Render one stable assessment plus grouped technical details."""
        session = self.diagnostic_session
        if session.started_monotonic is None:
            return

        def text(zh, en):
            return (
                en
                if getattr(self.gui, "language", "zh") == "en"
                else zh
            )

        def number(value, digits=1, suffix=""):
            if not isinstance(value, (int, float)):
                return "—"
            return f"{float(value):.{digits}f}{suffix}"

        remaining = session.remaining(now)
        elapsed = session.elapsed(now)
        self.diagnostic_remaining_var.set(
            text(f"\u5269\u9918 {remaining:.0f} \u79d2", f"{remaining:.0f} s remaining")
            if session.running else text("\u5df2\u5b8c\u6210", "Complete")
        )
        self.diagnostic_progress_var.set(
            100.0 if session.completed else min(
                100.0,
                elapsed / session.duration_seconds * 100.0,
            )
        )

        latest = session.samples[-1] if session.samples else {}
        status = latest.get("status") or {}
        firmware = latest.get("firmware") or {}
        capabilities = firmware.get("capabilities") or {}
        runtime = firmware.get("runtime_status") or {}
        rumble = dict(status.get("rumble") or {})
        rumble.update(firmware.get("rumble_status") or {})
        stats = session.summary()
        warnings = tuple(stats.get("warnings") or ())
        notices = tuple(stats.get("notices") or ())

        warning_text = {
            "FIRMWARE_NOTIFY_QUEUE_DROPS": text(
                "\u97cc\u9ad4\u8f38\u5165\u4f47\u5217\u6709\u8cc7\u6599\u4e1f\u68c4",
                "Firmware input queue dropped data",
            ),
            "INPUT_SOURCE_GAPS": text(
                "\u539f\u59cb BLE \u8f38\u5165\u9593\u9694\u6bd4\u4f8b\u504f\u9ad8",
                "Raw BLE input gaps exceed the threshold",
            ),
            "RUMBLE_SEND_FAILURES": text(
                "\u9707\u52d5\u8a0a\u865f\u50b3\u9001\u5931\u6557",
                "Rumble transport failures detected",
            ),
            "SENSOR_MODE_UNAVAILABLE": text(
                "\u5df2\u9023\u7dda\u4f46\u672a\u53d6\u5f97\u611f\u6e2c\u5668\u6a21\u5f0f",
                "Connected, but sensor mode is unavailable",
            ),
            "BLE_SIGNAL_VERY_WEAK": text(
                "BLE \u8a0a\u865f\u5f88\u5f31",
                "BLE signal is very weak",
            ),
            "BLE_RAW_REPORT_RATE_LOW": text(
                "BLE \u539f\u59cb\u56de\u5831\u7387\u4f4e\u65bc\u9023\u7dda\u9593\u9694\u7684\u9810\u671f",
                "BLE raw report rate is below the expected link rate",
            ),
            "FIRMWARE_SELF_TEST_INCOMPLETE": text(
                "\u97cc\u9ad4\u81ea\u6e2c\u672a\u5168\u90e8\u5b8c\u6210",
                "Firmware self-tests did not all complete",
            ),
        }
        findings = [warning_text.get(code, code) for code in warnings]
        gap_ratio = stats.get("source_gap_ratio")
        if "OCCASIONAL_INPUT_SOURCE_GAPS" in notices:
            findings.append(text(
                "\u5075\u6e2c\u5230\u5c11\u91cf\u539f\u59cb\u8f38\u5165\u9593\u9694"
                f"\uff08{float(gap_ratio or 0.0) * 100.0:.2f}%\uff0c"
                "\u672a\u8d85\u904e 1% \u8b66\u793a\u9580\u6abb\uff09",
                "A small number of raw input gaps were observed "
                f"({float(gap_ratio or 0.0) * 100.0:.2f}%, below the 1% warning threshold)",
            ))
        if "BLE_SIGNAL_WEAK" in notices:
            findings.append(text(
                "BLE \u8a0a\u865f\u504f\u5f31\uff0c\u4f46\u5c1a\u672a\u9054\u56b4\u91cd\u7b49\u7d1a",
                "BLE signal is weak, but not critical",
            ))

        if not session.samples:
            verdict = text("\u6b63\u5728\u6536\u96c6\u8cc7\u6599", "Collecting data")
            finding_text = text(
                "\u5c1a\u7121\u8db3\u5920\u6a23\u672c\u53ef\u4f9b\u5224\u8b80",
                "Not enough samples to assess yet",
            )
            advice = text(
                "\u8acb\u7e7c\u7e8c\u64cd\u4f5c\u624b\u628a\uff0c\u76f4\u5230\u8a3a\u65b7\u5b8c\u6210",
                "Keep using the controller until Diagnostics completes",
            )
        elif warnings:
            verdict = text("\u9700\u8981\u6ce8\u610f", "Attention needed")
            finding_text = "\uff1b".join(findings)
            rssi = stats.get("link_rssi_dbm")
            if "FIRMWARE_NOTIFY_QUEUE_DROPS" in warnings:
                advice = text(
                    "\u6aa2\u67e5 USB \u7dda\u6750\u3001\u4f9b\u96fb\u8207\u96fb\u8166 USB \u8ca0\u8f09\uff0c\u518d\u91cd\u65b0\u8a3a\u65b7",
                    "Check the USB cable, power, and host USB load, then run Diagnostics again",
                )
            elif (
                "BLE_SIGNAL_VERY_WEAK" in warnings
                or (
                    "INPUT_SOURCE_GAPS" in warnings
                    and isinstance(rssi, (int, float))
                    and rssi < -75
                )
            ):
                advice = text(
                    "\u8acb\u9760\u8fd1 ESP32\uff0c\u4e26\u6e1b\u5c11 2.4 GHz \u5e72\u64fe",
                    "Move closer to the ESP32 and reduce 2.4 GHz interference",
                )
            elif "INPUT_SOURCE_GAPS" in warnings:
                advice = text(
                    "\u8a0a\u865f\u4e26\u4e0d\u5f31\uff1b\u8acb\u512a\u5148\u6aa2\u67e5\u624b\u628a\u96fb\u91cf\u3001"
                    "2.4 GHz \u5e72\u64fe\u8207\u5176\u4ed6\u85cd\u7259\u88dd\u7f6e\uff0c\u4e0d\u9700\u518d\u9760\u8fd1 ESP32",
                    "Signal is not weak; check controller battery, 2.4 GHz interference, "
                    "and other Bluetooth devices. Moving closer is unnecessary",
                )
            elif "RUMBLE_SEND_FAILURES" in warnings:
                advice = text(
                    "\u91cd\u65b0\u57f7\u884c\u9707\u52d5\u6e2c\u8a66\uff1b\u82e5\u4ecd\u5931\u6557\uff0c\u8acb\u532f\u51fa Log",
                    "Repeat the rumble test; export the log if failures continue",
                )
            elif "FIRMWARE_SELF_TEST_INCOMPLETE" in warnings:
                advice = text(
                    "\u91cd\u65b0\u63d2\u62d4 ESP32 \u5f8c\u518d\u6e2c\uff1b\u82e5\u4ecd\u5931\u6557\uff0c\u91cd\u5237\u6700\u65b0\u97cc\u9ad4",
                    "Reconnect the ESP32 and retry; reflash current firmware if it still fails",
                )
            else:
                advice = text(
                    "\u532f\u51fa TXT Log \u9032\u4e00\u6b65\u5206\u6790",
                    "Export the TXT log for further analysis",
                )
        else:
            verdict = (
                text("\u8a3a\u65b7\u5b8c\u6210\uff0c\u672a\u767c\u73fe\u660e\u986f\u7570\u5e38",
                     "Diagnostics complete; no obvious issue detected")
                if session.completed else
                text("\u76ee\u524d\u72c0\u614b\u6b63\u5e38", "Current status is normal")
            )
            finding_text = (
                "\uff1b".join(findings)
                if findings
                else text(
                    "\u8f38\u5165\u3001\u50b3\u8f38\u8207\u9707\u52d5\u672a\u51fa\u73fe\u8b66\u793a",
                    "Input, transport, and rumble have no warnings",
                )
            )
            advice = text(
                "\u7121\u9700\u8abf\u6574\uff1b\u53ef\u532f\u51fa Log \u7559\u5b58\u672c\u6b21\u7d50\u679c",
                "No adjustment is needed; you may export the log for reference",
            )

        self.diagnostic_summary_vars["verdict"].set(verdict)
        self.diagnostic_summary_vars["findings"].set(finding_text)
        self.diagnostic_summary_vars["advice"].set(advice)

        mode = capabilities.get("mode") or status.get("mode") or "unknown"
        mode_text = {
            "esp32": text("\u6a4b\u63a5\u6a21\u5f0f", "Bridge mode"),
            "standalone": text("\u7368\u7acb XInput \u6a21\u5f0f", "Standalone XInput mode"),
            "standalone_hid": text("\u7368\u7acb HID \u6a21\u5f0f", "Standalone HID mode"),
            "wired": text("USB \u6709\u7dda\u6a21\u5f0f", "Wired USB mode"),
            "bluetooth": text("Windows BLE \u6a21\u5f0f", "Windows BLE mode"),
        }.get(str(mode), str(mode))
        full_firmware_diagnostics = (
            str(mode) in {"esp32", "standalone", "standalone_hid"}
            and bool(capabilities)
        )
        diagnostic_level = (
            text("\u5b8c\u6574\u97cc\u9ad4\u8a3a\u65b7", "Full firmware diagnostics")
            if full_firmware_diagnostics
            else text("\u6709\u7dda\u50b3\u8f38\u8a3a\u65b7", "Wired transport diagnostics")
            if str(mode) == "wired"
            else text("\u57fa\u672c\u85cd\u7259\u8a3a\u65b7", "Basic Bluetooth diagnostics")
            if str(mode) == "bluetooth"
            else text("\u57fa\u672c\u8f38\u5165\u8a3a\u65b7", "Basic input diagnostics")
        )
        reader_state = firmware.get("state")
        port = firmware.get("port")
        bridge_owned = (
            status.get("state") == "connected"
            and status.get("mode") == "esp32"
        )
        if bridge_owned:
            source_text = text(
                "\u4e3b\u7a0b\u5f0f\u6a4b\u63a5\u901a\u9053\uff08\u5e8f\u5217\u57e0\u7531\u4e3b\u7a0b\u5f0f\u4f7f\u7528\uff09",
                "Desktop bridge channel (the application owns the serial port)",
            )
        elif reader_state == "running":
            source_text = text(
                "ESP32 \u7368\u7acb\u8a3a\u65b7\u901a\u9053",
                "Standalone ESP32 diagnostic channel",
            )
            if port:
                source_text += f" ({port})"
        elif reader_state == "opening":
            source_text = text("\u6b63\u5728\u9023\u7dda ESP32", "Connecting to ESP32")
        elif str(mode) == "wired":
            source_text = text(
                "Windows \u6709\u7dda\u624b\u628a\u8f38\u5165",
                "Windows wired controller input",
            )
        elif str(mode) == "bluetooth":
            source_text = text(
                "Windows \u85cd\u7259\u624b\u628a\u8f38\u5165",
                "Windows Bluetooth controller input",
            )
        else:
            source_text = text(
                "Windows \u624b\u628a\u8f38\u5165",
                "Windows controller input",
            )

        ready_link = bool(stats.get("controller_mac"))
        controller_connected = (
            status.get("state") == "connected"
            or ready_link
            or int(stats.get("ble_input_reports", 0) or 0) > 0
        )
        connection_text = (
            text("\u624b\u628a\u5df2\u9023\u7dda", "Controller connected")
            if controller_connected
            else text(
                "\u8a3a\u65b7\u901a\u9053\u5df2\u9023\u7dda\uff0c\u672a\u78ba\u8a8d\u624b\u628a\u9023\u7dda",
                "Diagnostic channel connected; controller link not confirmed",
            )
            if reader_state == "running"
            else text("\u624b\u628a\u672a\u9023\u7dda", "Controller not connected")
        )

        diagnostic_target = status.get("diagnostic_target") or {}
        target_name = (
            diagnostic_target.get("device_name")
            or self._diagnostic_target_name
            or "—"
        )
        target_kind = (
            diagnostic_target.get("device_kind")
            or self._diagnostic_target_kind
            or "unknown"
        )
        details = [
            text("\u6e2c\u8a66\u624b\u628a\uff1a", "Test controller: ")
            + f"{target_name} [{target_kind}]",
            text("\u8cc7\u6599\u4f86\u6e90\uff1a", "Source: ") + source_text,
            text("\u8a3a\u65b7\u5c64\u7d1a\uff1a", "Diagnostic level: ")
            + diagnostic_level,
            text("\u72c0\u614b\uff1a", "Status: ")
            + connection_text
            + text("\uff1b\u6a21\u5f0f\uff1a", "; mode: ")
            + mode_text,
        ]
        if capabilities:
            details.append(
                text("\u97cc\u9ad4\uff1a", "Firmware: ")
                + f"{capabilities.get('product') or 'S2P-FW'} "
                + f"{capabilities.get('version') or '?'}"
                + text("\uff1b\u5354\u8b70\uff1a", "; protocol: ")
                + f"{capabilities.get('protocol') or '?'} "
                + f"{capabilities.get('protocol_version') or '?'}"
            )

        if (
            full_firmware_diagnostics
            and (stats.get("controller_mac") or stats.get("bridge_mac"))
        ):
            device_parts = []
            if stats.get("bridge_mac"):
                device_parts.append(
                    text("\u6a4b\u63a5\u5668 MAC ", "bridge MAC ")
                    + str(stats["bridge_mac"])
                )
            if stats.get("controller_mac"):
                device_parts.append(
                    text("\u624b\u628a MAC ", "controller MAC ")
                    + str(stats["controller_mac"])
                )
            details.append(
                text("BLE \u88dd\u7f6e\uff1a", "BLE devices: ")
                + "\uff1b".join(device_parts)
            )

            link_parts = []
            rssi = stats.get("link_rssi_dbm")
            if isinstance(rssi, (int, float)):
                link_parts.append(
                    f"RSSI {int(rssi)} dBm ("
                    + text(
                        self._diagnostic_rssi_quality(int(rssi)),
                        {
                            "\u6975\u4f73": "excellent",
                            "\u826f\u597d": "good",
                            "\u666e\u901a": "fair",
                            "\u504f\u5f31": "weak",
                            "\u5f88\u5f31": "very weak",
                        }[self._diagnostic_rssi_quality(int(rssi))],
                    )
                    + ")"
                )
            if stats.get("link_interval_ms") is not None:
                link_parts.append(
                    text("\u9023\u7dda\u9593\u9694 ", "interval ")
                    + number(stats["link_interval_ms"], 2, " ms")
                )
            if link_parts:
                details.append(
                    text("BLE \u8a0a\u865f\uff1a", "BLE signal: ")
                    + "\uff1b".join(link_parts)
                )
        elif full_firmware_diagnostics:
            details.append(text(
                "BLE \u9023\u7dda\uff1a\u97cc\u9ad4\u672a\u63d0\u4f9b MAC / RSSI \u8cc7\u6599",
                "BLE link: firmware did not provide MAC / RSSI data",
            ))

        raw_average = stats.get("ble_raw_report_rate_hz_avg")
        if full_firmware_diagnostics and raw_average is not None:
            details.append(
                text("BLE \u539f\u59cb\u56de\u5831\u7387\uff1a", "BLE raw report rate: ")
                + text("\u5e73\u5747 ", "average ")
                + number(raw_average, 1, " Hz")
                + f"\uff1bP50 {number(stats.get('ble_raw_report_rate_hz_p50'), 1, ' Hz')}"
                + f"\uff1bP95 {number(stats.get('ble_raw_report_rate_hz_p95'), 1, ' Hz')}"
                + f"\uff1bP99 {number(stats.get('ble_raw_report_rate_hz_p99'), 1, ' Hz')}"
            )
        elif full_firmware_diagnostics:
            details.append(text(
                "BLE \u539f\u59cb\u56de\u5831\u7387\uff1a\u6536\u96c6\u4e2d\u6216\u97cc\u9ad4\u672a\u63d0\u4f9b",
                "BLE raw report rate: collecting or unavailable from firmware",
            ))

        interval_p50 = stats.get("input_interval_ms_p50")
        if interval_p50 is not None:
            details.append(
                text("\u684c\u9762\u7aef\u8f38\u5165\u9593\u9694\uff1a", "Desktop input intervals: ")
                + f"P50 {number(interval_p50, 2, ' ms')}"
                + f"\uff1bP95 {number(stats.get('input_interval_ms_p95'), 2, ' ms')}"
                + f"\uff1bP99 {number(stats.get('input_interval_ms_p99'), 2, ' ms')}"
            )

        if full_firmware_diagnostics:
            gap_ratio_percent = (
                float(gap_ratio) * 100.0 if gap_ratio is not None else None
            )
            gap_state = (
                text("\u8d85\u904e\u8b66\u793a\u9580\u6abb", "above warning threshold")
                if "INPUT_SOURCE_GAPS" in warnings
                else text("\u672a\u8d85\u904e\u8b66\u793a\u9580\u6abb", "below warning threshold")
            )
            details.append(
                text("\u539f\u59cb\u9593\u9694\uff1a", "Raw input gaps: ")
                + f"{stats.get('source_gap_events', 0)}"
                + (
                    f" ({gap_ratio_percent:.2f}%)"
                    if gap_ratio_percent is not None else ""
                )
                + text("\uff1b\u6700\u5927 ", "; maximum ")
                + f"{stats.get('source_gap_max_ms', 0)} ms"
                + "\uff1b"
                + gap_state
            )
            details.append(
                text("\u50b3\u8f38\uff1a\u4f47\u5217\u4e1f\u68c4 ", "Transport: queue drops ")
                + str(stats.get("notify_queue_drops", 0))
                + text("\uff1bUSB \u7b49\u5f85\u5e73\u5747 ", "; average USB wait ")
                + number(stats.get("usb_wait_avg_us", 0) / 1000.0, 2, " ms")
                + text("\uff1b\u6700\u5927 ", "; maximum ")
                + number(stats.get("usb_wait_max_us", 0) / 1000.0, 2, " ms")
            )

        sensor_mode = status.get("sensor_mode")
        gyro = status.get("gyro_raw") or runtime.get("gyro_rate")
        if isinstance(gyro, (list, tuple)) and len(gyro) >= 3:
            gyro_text = "X {:.1f} / Y {:.1f} / Z {:.1f}".format(
                *[float(value) for value in gyro[:3]]
            )
        else:
            gyro_text = "—"
        calibration_state = str(
            status.get("gyro_calibration_state") or "idle"
        )
        if full_firmware_diagnostics or sensor_mode or gyro:
            details.append(
                text("\u611f\u6e2c\u5668\uff1a", "Sensors: ")
                + str(sensor_mode or "—")
                + text("\uff1b\u6821\u6b63\u72c0\u614b ", "; calibration ")
                + calibration_state
                + text("\uff1b\u9640\u87ba\u5100 ", "; gyro ")
                + gyro_text
            )

        raw_rumble = self._diagnostic_pair(
            rumble.get("input") or rumble.get("latest_input")
        )
        output_rumble = self._diagnostic_pair(
            rumble.get("output") or rumble.get("latest_output")
        )
        details.append(
            text("\u9707\u52d5\uff1a\u8f38\u5165 ", "Rumble: input ")
            + f"{raw_rumble[0]}/{raw_rumble[1]}"
            + text("\uff1b\u8f38\u51fa ", "; output ")
            + f"{output_rumble[0]}/{output_rumble[1]}"
            + text("\uff1b\u5cf0\u503c ", "; peak ")
            + f"{max(session.rumble_peak_output)}"
            + text("\uff1b\u5931\u6557 ", "; failures ")
            + str(
                rumble.get(
                    "transport_send_failures",
                    rumble.get("send_failures", 0),
                )
                or 0
            )
        )
        if full_firmware_diagnostics:
            details.append(
                text("\u97cc\u9ad4\u81ea\u6e2c\uff1a", "Firmware self-tests: ")
                + f"{stats.get('self_test_replies', 0)} "
                + text("\u9805\u5df2\u56de\u8986", "replies")
                + f"\uff1b{stats.get('self_test_failures', 0)} "
                + text("\u9805\u672a\u5b8c\u6210", "incomplete")
            )

        signature = tuple(details)
        if (
            self.diagnostic_event_text is not None
            and signature != self._diagnostic_last_event_signature
        ):
            self._diagnostic_last_event_signature = signature
            self.diagnostic_event_text.configure(state="normal")
            self.diagnostic_event_text.delete("1.0", "end")
            self.diagnostic_event_text.insert("1.0", "\n".join(details))
            self.diagnostic_event_text.configure(state="disabled")

    def _build_rumble_tab(self, parent):
        parent.columnconfigure(0, weight=1)
        manual_group = ttk.LabelFrame(
            parent,
            text=self.gui.tr("手動震動輸出"),
            padding=(10, 8),
        )
        manual_group.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        manual_group.columnconfigure(1, weight=1)
        self.manual_rumble_widgets = []
        for row, (label, variable, value_text) in enumerate((
            ("LF（左馬達）", self.manual_lf_var, self.manual_lf_text),
            ("HF（右馬達）", self.manual_hf_var, self.manual_hf_text),
        )):
            ttk.Label(manual_group, text=self.gui.tr(label), width=14).grid(
                row=row, column=0, sticky="w", padx=(0, 8)
            )
            scale = ttk.Scale(
                manual_group,
                from_=0.0,
                to=100.0,
                variable=variable,
                command=self._on_manual_rumble_changed,
            )
            scale.grid(row=row, column=1, sticky="ew", pady=3)
            value_label = ttk.Label(
                manual_group,
                textvariable=value_text,
                width=6,
            )
            value_label.grid(
                row=row, column=2, sticky="e", padx=(8, 0)
            )
            self._bind_parameter_control(
                value_label,
                variable,
                label,
                0.0,
                100.0,
                step=1.0,
                number_format=".0f",
                on_change=self._on_manual_rumble_changed,
                state_widget=scale,
            )
            self.manual_rumble_widgets.append(scale)

        group = ttk.LabelFrame(
            parent,
            text=self.gui.tr("震動模板"),
            padding=(10, 8),
        )
        # The rumble module itself follows the tab's outer width.  Only the
        # individual pattern buttons stay compact.
        group.grid(row=1, column=0, sticky="ew")
        self.rumble_notice_var = tk.StringVar(
            value=self.gui.tr("選擇支援震動的 XInput 手把")
        )
        ttk.Label(
            group, textvariable=self.rumble_notice_var
        ).grid(row=0, column=0, columnspan=5, sticky="w", pady=(0, 8))
        buttons = (
            ("LF 脈衝", "lf"),
            ("HF 脈衝", "hf"),
            ("交替", "alternate"),
            ("撞擊", "impact"),
            ("漸強", "ramp"),
            ("雙擊", "double_tap"),
            ("連射", "rapid_fire"),
            ("引擎", "engine"),
            ("波浪", "wave"),
            ("警示", "alert"),
            ("心跳", "heartbeat"),
            ("節拍", "footsteps"),
            ("路感", "terrain"),
            ("低鳴", "low_rumble"),
            ("爆裂", "burst"),
            ("機槍", "machine_gun"),
            ("散彈", "shotgun"),
            ("加速", "turbo"),
            ("旋翼", "rotor"),
            ("倒數", "countdown"),
        )
        self.rumble_buttons = []
        self.rumble_buttons_by_template = {}
        template_columns = 5
        for index, (label, template) in enumerate(buttons):
            row = 1 + index // template_columns
            column = index % template_columns
            group.columnconfigure(column, weight=1, uniform="rumble-template")
            button = ttk.Button(
                group,
                text=self.gui.tr(label),
                command=lambda name=template: self.play_rumble(name),
            )
            button.grid(
                row=row,
                column=column,
                sticky="ew",
                padx=(0, 4) if column < len(buttons) - 1 else (0, 0),
                pady=2,
            )
            self.rumble_buttons.append(button)
            self.rumble_buttons_by_template[template] = button
            button.bind(
                "<Leave>",
                lambda _event, name=template:
                    self._restore_repeating_button_state(name),
                add="+",
            )

        self.rumble_option_widgets = []
        options_row = 1 + (len(buttons) + template_columns - 1) // template_columns
        repeat = ttk.Checkbutton(
            group,
            text=self.gui.tr("重複播放"),
            variable=self.repeat_rumble_var,
            command=self._on_repeat_rumble_changed,
        )
        repeat.grid(row=options_row, column=0, sticky="w", pady=(10, 0))
        template_lf = ttk.Checkbutton(
            group,
            text=self.gui.tr("模板 LF（左馬達）"),
            variable=self.template_lf_enabled_var,
        )
        template_lf.grid(row=options_row, column=1, sticky="w", pady=(10, 0))
        template_hf = ttk.Checkbutton(
            group,
            text=self.gui.tr("模板 HF（右馬達）"),
            variable=self.template_hf_enabled_var,
        )
        template_hf.grid(row=options_row, column=2, sticky="w", pady=(10, 0))
        controls = ttk.Frame(group)
        controls.grid(
            row=options_row + 1, column=0, columnspan=5,
            sticky="ew", pady=(8, 0)
        )
        controls.columnconfigure(1, weight=1)
        ttk.Label(controls, text=self.gui.tr("播放頻率"), width=14).grid(
            row=0, column=0, sticky="w", padx=(0, 8)
        )
        repeat_rate = ttk.Scale(
            controls,
            from_=0.1,
            to=100.0,
            variable=self.rumble_repeat_hz_var,
            command=self._on_repeat_rate_changed,
        )
        repeat_rate.grid(
            row=0, column=1, sticky="ew"
        )
        repeat_rate_value = ttk.Label(
            controls,
            textvariable=self.rumble_repeat_hz_text,
            width=8,
        )
        repeat_rate_value.grid(
            row=0, column=2, sticky="e", padx=(8, 0)
        )
        self._bind_parameter_control(
            repeat_rate_value,
            self.rumble_repeat_hz_var,
            "播放頻率",
            0.1,
            100.0,
            step=0.1,
            number_format=".1f",
            on_change=self._on_repeat_rate_changed,
            state_widget=repeat_rate,
        )
        ttk.Label(controls, text=self.gui.tr("模板強度"), width=14).grid(
            row=1, column=0, sticky="w", padx=(0, 8), pady=(8, 0)
        )
        strength = ttk.Scale(
            controls,
            from_=0.0,
            to=100.0,
            variable=self.rumble_strength_var,
            command=self._on_rumble_strength_changed,
        )
        strength.grid(row=1, column=1, sticky="ew", pady=(8, 0))
        strength_value = ttk.Label(
            controls,
            textvariable=self.rumble_strength_text,
            width=8,
        )
        strength_value.grid(
            row=1, column=2, sticky="e", padx=(8, 0), pady=(8, 0)
        )
        self._bind_parameter_control(
            strength_value,
            self.rumble_strength_var,
            "模板強度",
            0.0,
            100.0,
            step=1.0,
            number_format=".0f",
            on_change=self._on_rumble_strength_changed,
            state_widget=strength,
        )
        self.rumble_option_widgets.extend(
            (repeat, template_lf, template_hf, repeat_rate, strength)
        )

        stop_footer = ttk.Frame(parent)
        stop_footer.grid(row=2, column=0, sticky="ew", pady=(12, 0))
        stop_footer.columnconfigure(0, weight=1)
        stop_footer.columnconfigure(1, weight=1)
        self.reset_rumble_button = ttk.Button(
            stop_footer,
            text=self.gui.tr("還原震動預設"),
            command=self.reset_rumble_settings,
        )
        self.reset_rumble_button.grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        self.stop_rumble_button = ttk.Button(
            stop_footer,
            text=self.gui.tr("停止所有震動"),
            command=self.stop_rumble,
        )
        self.stop_rumble_button.grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )

    def _on_manual_rumble_changed(self, _value=None):
        left = max(0.0, min(100.0, self.manual_lf_var.get()))
        right = max(0.0, min(100.0, self.manual_hf_var.get()))
        self.manual_lf_text.set(f"{left:.0f}%")
        self.manual_hf_text.set(f"{right:.0f}%")
        self._manual_rumble = (left / 100.0, right / 100.0)
        self._apply_rumble_mix()

    def _on_repeat_rate_changed(self, _value=None):
        frequency = max(0.1, min(100.0, self.rumble_repeat_hz_var.get()))
        self.rumble_repeat_hz_text.set(
            f"{frequency:.1f} Hz"
        )

    def _on_rumble_strength_changed(self, _value=None):
        strength = max(0.0, min(100.0, self.rumble_strength_var.get()))
        self.rumble_strength_text.set(f"{strength:.0f}%")

    def reset_rumble_settings(self):
        self.stop_rumble()
        self.manual_lf_var.set(0.0)
        self.manual_hf_var.set(0.0)
        self.manual_lf_text.set("0%")
        self.manual_hf_text.set("0%")
        self.repeat_rumble_var.set(False)
        self.rumble_repeat_hz_var.set(1.0)
        self.rumble_repeat_hz_text.set("1.0 Hz")
        self.rumble_strength_var.set(100.0)
        self.rumble_strength_text.set("100%")
        self.template_lf_enabled_var.set(True)
        self.template_hf_enabled_var.set(True)

    def _on_repeat_rumble_changed(self):
        if self.repeat_rumble_var.get():
            self._refresh_rumble_template_styles()
            return
        layer_ids = tuple(
            layer_id
            for active_layers in self._active_rumble_templates.values()
            for layer_id in active_layers
        )
        for layer_id in layer_ids:
            self._cancel_rumble_layer(layer_id)
        self._refresh_rumble_template_styles()

    def _update_trail_length_text(self, _value=None):
        self.trail_length_text.set(
            self.gui.tr("{seconds:.1f} 秒").format(
                seconds=float(self.trail_length_var.get())
            )
        )

    def _update_sample_display_percent(self, _value=None):
        percentage = int(round(max(
            1.0, min(100.0, self.sample_display_percent_var.get())
        )))
        self.sample_display_percent_var.set(float(percentage))
        self.sample_display_percent_text.set(f"{percentage}%")

    def _schedule_poll(self):
        if self.window is None or not self.window.winfo_exists():
            return
        current = time.perf_counter()
        frame_interval = self._frame_interval
        if self._active_test_tab() == "high_rate":
            frame_interval = max(frame_interval, 1.0 / 60.0)
        if current < self._window_motion_until:
            # A Canvas redraw competes with Windows' move/resize loop. Keep
            # the tester responsive while the user is dragging its window.
            frame_interval = max(frame_interval, 1.0 / 30.0)
        if self._next_frame_at <= current:
            missed = int(
                (current - self._next_frame_at) / frame_interval
            ) + 1
            self._next_frame_at += missed * frame_interval
        delay_ms = max(
            1, int(math.ceil(
                (self._next_frame_at - current) * 1000.0
            ))
        )
        self._poll_job = self.window.after(delay_ms, self._poll)

    def _active_test_tab(self):
        notebook = self.test_notebook
        if notebook is None:
            return "input"
        try:
            selected = notebook.select()
        except tk.TclError:
            return "input"
        if self.high_rate_tab is not None and selected == str(
            self.high_rate_tab
        ):
            return "high_rate"
        if self.rumble_tab is not None and selected == str(self.rumble_tab):
            return "rumble"
        if (
            self.diagnostic_tab is not None
            and selected == str(self.diagnostic_tab)
        ):
            return "diagnostic"
        if self.about_tab is not None and selected == str(self.about_tab):
            return "about"
        return "input"

    def _on_test_tab_changed(self, _event=None):
        self._next_frame_at = time.perf_counter()
        # Drain at the event boundary. A fast away-and-back switch can happen
        # before the next background poll and must not replay hidden movement.
        self._discard_pending_monitor_samples()
        if self._active_test_tab() == "high_rate":
            self._raw_hid_last_distribution = None
        self._sync_raw_hid_stream()

    def _update_display_refresh_rate(self, window=None):
        window = self.window if window is None else window
        refresh_rate = display_refresh_rate(window)
        if abs(refresh_rate - self._display_refresh_hz) < 0.5:
            return False
        self._display_refresh_hz = refresh_rate
        self._frame_interval = 1.0 / refresh_rate
        self._next_frame_at = time.perf_counter()
        return True

    def _on_window_configure(self, _event=None):
        """Temporarily reduce drawing while Windows is moving/resizing us."""
        if _event is None or _event.widget is not self.window:
            return
        geometry = (_event.x, _event.y, _event.width, _event.height)
        if geometry == self._last_window_geometry:
            return
        self._last_window_geometry = geometry
        self._update_display_refresh_rate()
        self._window_motion_until = time.perf_counter() + 0.10

    def _monitor_presentation_allowed(self, active_tab, now):
        return (
            active_tab == "input"
            and float(now) >= self._window_motion_until
        )

    def _poll(self):
        self._poll_job = None
        if self.window is None or not self.window.winfo_exists():
            return
        now = time.perf_counter()
        # DiagnosticSession measures duration with monotonic time. Keep this
        # clock domain separate from high-refresh presentation scheduling.
        self._poll_diagnostic(time.monotonic())
        monitor_visible = self._active_test_tab() == "input"
        telemetry = self.telemetry.read_latest() if self.telemetry else {}
        telemetry = telemetry or {}
        if telemetry:
            self.latest_telemetry = telemetry
        device = self._selected_device()
        sample = None
        consumed_trail = False
        is_s2p = device is not None and device.kind == "s2p"
        self._sync_shape_capture_context(device, is_s2p)
        if is_s2p and self._telemetry_is_fresh(self.latest_telemetry):
            sample = self._sample_from_telemetry(self.latest_telemetry)
        elif device is not None:
            if self.native_sampler is not None:
                native_state, native_samples, native_rate = (
                    self.native_sampler.read_snapshot()
                )
            else:
                native_state = self.backend.read_state(device)
                native_samples = ()
                native_rate = None
            if native_state is not None:
                sample = self._sample_from_native(
                    device, native_state, native_rate
                )
                if monitor_visible and not self.raw_hid_stream.active:
                    for sample_time, queued_state in native_samples:
                        self._consume_sample(
                            self._sample_from_native(
                                device, queued_state, native_rate
                            ),
                            False,
                            sample_time,
                        )
                    consumed_trail = bool(native_samples)
        if (
            not is_s2p
            and device is not None
            and self.raw_hid_stream.active
        ):
            pair_candidate = (
                device.kind == "winmm"
                and any(
                    (raw.vendor_id, raw.product_id) == (0x057E, 0x2069)
                    for raw in self.raw_hid_devices.values()
                )
            )
            if (
                pair_candidate
                and getattr(self, "_nintendo_winmm_pair_key", None)
                not in {None, device.key}
            ):
                self._nintendo_winmm_pair_key = None
                self._nintendo_winmm_pair_score = 0
            pair_confirmed = (
                getattr(self, "_nintendo_winmm_pair_key", None)
                == device.key
            )
            native_sample = sample
            sample, raw_consumed = self._consume_raw_hid_stream(
                sample,
                record_trail=(
                    monitor_visible and (not pair_candidate or pair_confirmed)
                ),
            )
            if pair_candidate and not pair_confirmed:
                native_left = tuple((native_sample or {}).get("left") or ())
                raw_left = tuple((sample or {}).get("left") or ())
                if len(native_left) == len(raw_left) == 2:
                    dot = (
                        float(native_left[0]) * float(raw_left[0])
                        + float(native_left[1]) * float(raw_left[1])
                    )
                    if dot >= 0.35:
                        self._nintendo_winmm_pair_score = getattr(
                            self, "_nintendo_winmm_pair_score", 0
                        ) + 1
                    else:
                        self._nintendo_winmm_pair_score = 0
                    if self._nintendo_winmm_pair_score >= 6:
                        self._nintendo_winmm_pair_key = device.key
                        pair_confirmed = True
                if not pair_confirmed:
                    sample = native_sample
                    raw_consumed = False
            consumed_trail = consumed_trail or raw_consumed
        if sample is not None and device is not None:
            self.latest_diagnostic_input = {
                "device_key": device.key,
                "device_kind": device.kind,
                "device_name": device.name,
                "sampled_at_monotonic": time.monotonic(),
                "source_rate_hz": sample.get("source_rate_hz"),
                "buttons": list(sample.get("buttons") or ()),
                "buttons_mask": int(sample.get("buttons_mask", 0) or 0),
                "triggers": list(sample.get("triggers") or (0.0, 0.0)),
                "left": list(sample.get("left") or (0.0, 0.0)),
                "right": list(sample.get("right") or (0.0, 0.0)),
            }
        if sample is not None:
            sample_token = sample.get("token")
            if is_s2p and self.telemetry is not None:
                trail_samples, newest_sequence, dropped = (
                    self.telemetry.read_trail_samples(
                        self._last_trail_sequence
                    )
                )
                self._last_trail_sequence = newest_sequence
                self._trail_overwrite_count += dropped
                if trail_samples and monitor_visible:
                    self._consume_s2p_trail_samples(trail_samples, now)
                    consumed_trail = True
            if (
                monitor_visible
                and
                not consumed_trail
                and sample_token != self._last_consumed_token
            ):
                self._last_consumed_token = sample_token
                self._consume_sample(sample, is_s2p, now)
            elif consumed_trail:
                self._last_consumed_token = sample_token
        else:
            self._last_consumed_token = None
        shape_delta = max(
            0.0, min(0.1, now - self._last_shape_advance_at)
        )
        self._last_shape_advance_at = now
        shape_amount = shape_ease_amount(shape_delta)
        for history in self.histories.values():
            history.prune(now, self.trail_length_var.get())
            if self.shape_enabled_var.get():
                history.advance_shape(shape_amount)
                history.freeze_shape_if_complete(now)
        refresh_details = (
            now - self._last_detail_refresh >= 1.0 / 30.0
        )
        active_tab = self._active_test_tab()
        window_in_motion = now < self._window_motion_until
        if self._monitor_presentation_allowed(active_tab, now):
            # LT/RT are a compact direct input readout.  Keep them in the
            # presentation loop so they do not inherit the deliberately
            # slower 30 Hz text/event refresh below.
            self._update_triggers(sample, is_s2p)
            if active_tab == "input":
                event_signature = tuple(sorted(
                    self._event_inputs(sample, is_s2p).items()
                ))
                if event_signature != getattr(
                    self, "_last_event_input_signature", None
                ):
                    self._last_event_input_signature = event_signature
                    self._update_events(sample, is_s2p, now)
            self._draw_plots(
                is_s2p, update_details=refresh_details
            )
        if refresh_details and not window_in_motion:
            self._last_detail_refresh = now
            if now - self._last_connection_refresh >= 0.25:
                self._last_connection_refresh = now
                self._sync_connection_status(sample)
            if active_tab == "input":
                self._update_events(sample, is_s2p, now)
            if active_tab == "rumble":
                self._update_rumble_availability(device)
            self._update_raw_hid_stream_status()
        # Update the distribution independently from the slower text details
        # refresh so its adaptive range follows incoming reports live.
        self._update_raw_hid_measurement(
            redraw_chart=active_tab == "high_rate"
        )
        self._schedule_poll()

    @staticmethod
    def _telemetry_is_fresh(telemetry):
        timestamp_ns = int(telemetry.get("timestamp_ns", 0) or 0)
        if timestamp_ns <= 0:
            return False
        return time.monotonic_ns() - timestamp_ns <= 1_500_000_000

    def _sample_from_telemetry(self, telemetry):
        left = telemetry.get("left") or {}
        right = telemetry.get("right") or {}
        return {
            "left": tuple(left.get("final") or (0.0, 0.0)),
            "right": tuple(right.get("final") or (0.0, 0.0)),
            "buttons": tuple(telemetry.get("buttons") or ()),
            "buttons_mask": int(telemetry.get("buttons_mask", 0) or 0),
            "triggers": tuple(telemetry.get("triggers") or (0.0, 0.0)),
            "token": int(telemetry.get("timestamp_ns", 0) or 0),
            "source_rate_hz": telemetry.get("source_rate_hz"),
            "mappings": list(telemetry.get("pressed_mappings") or ()),
            "linear_triggers": list(
                telemetry.get("linear_trigger_mappings") or ()
            ),
            "layer": str(telemetry.get("mapping_layer") or "主方案"),
        }

    @staticmethod
    def _sample_from_native(device, native_state, source_rate=None):
        return {
            "left": native_state.left,
            "right": native_state.right,
            "buttons": native_state.buttons,
            "buttons_mask": native_state.buttons_mask,
            "triggers": (
                native_state.left_trigger,
                native_state.right_trigger,
            ),
            "token": (
                device.key,
                native_state.packet_number,
                native_state.buttons_mask,
                native_state.buttons,
                native_state.left,
                native_state.right,
            ),
            "source_rate_hz": source_rate,
            "rate_is_independent": True,
            "mappings": [],
            "linear_triggers": [],
            "layer": "原始輸入",
        }

    def _selected_xy(self, side, sample, is_s2p):
        if not is_s2p:
            return tuple(sample.get(side) or (0.0, 0.0))
        side_data = self.latest_telemetry.get(side) or {}
        source = _selected_plot_source(self.plots[side])
        key = {
            "實體搖桿": "physical",
            "陀螺儀": "gyro",
            "合成結果": "final",
        }.get(source, "final")
        value = side_data.get(key) or (0.0, 0.0)
        return float(value[0]), float(value[1])

    def _consume_sample(self, sample, is_s2p, now):
        for side in ("left", "right"):
            x, y = self._selected_xy(side, sample, is_s2p)
            self.histories[side].add(
                x, y, now, self.shape_enabled_var.get()
            )

    def _consume_s2p_trail_samples(self, samples, now):
        """Add every unseen bridge report, including reports between frames."""
        newest_timestamp_ns = max(
            int(item.get("timestamp_ns", 0) or 0) for item in samples
        )
        gyro = self.latest_telemetry.get("gyro") or {}
        gyro_target = (
            gyro.get("target")
            if (
                gyro.get("available")
                and _gyro_mapping_enabled(gyro)
            )
            else None
        )
        record_shape = self.shape_enabled_var.get()
        for item in samples:
            timestamp_ns = int(item.get("timestamp_ns", 0) or 0)
            age = max(
                0.0,
                (newest_timestamp_ns - timestamp_ns) / 1_000_000_000.0,
            )
            sample_time = now - age
            for side in ("left", "right"):
                source = _selected_plot_source(self.plots[side])
                if source == "實體搖桿":
                    values = item.get(f"physical_{side}") or (0.0, 0.0)
                elif source == "陀螺儀":
                    values = (
                        item.get("gyro") or (0.0, 0.0)
                        if gyro_target == side
                        else (0.0, 0.0)
                    )
                else:
                    values = item.get(f"final_{side}") or (0.0, 0.0)
                self.histories[side].add(
                    values[0], values[1], sample_time, record_shape
                )

    def _draw_plots(self, is_s2p, update_details=True):
        telemetry = self.latest_telemetry if is_s2p else {}
        frame_changed = False
        presentation_now = time.perf_counter()
        for side, plot in self.plots.items():
            history = self.histories[side]
            if history.trail:
                _, x, y, _sequence = history.trail[-1]
            else:
                x = y = 0.0
            side_changed = plot.draw(
                history,
                x,
                y,
                telemetry,
                is_s2p,
                update_details=update_details,
            )
            plot.record_presentation_fps(presentation_now, side_changed)
            frame_changed = side_changed or frame_changed
        return frame_changed

    def _on_source_changed(self, side):
        self.histories[side].reset()
        self._last_consumed_token = None
        self._baseline_trail_sequence()
        # The ring may not receive another report until the next transport
        # interval. Seed the newly selected source from the latest telemetry so
        # draw() never renders a transient (0, 0) frame during the switch.
        device = self._selected_device()
        if (
            device is not None
            and device.kind == "s2p"
            and self._telemetry_is_fresh(self.latest_telemetry)
        ):
            x, y = self._selected_xy(side, {}, True)
            self.histories[side].add(
                x,
                y,
                time.perf_counter(),
                self.shape_enabled_var.get(),
            )
        self._shape_capture_signature = self._current_shape_capture_signature(
            device,
            bool(device is not None and device.kind == "s2p"),
        )

    def _current_shape_capture_signature(self, device, is_s2p):
        """Identify every input/configuration source behind the shape trace."""
        if device is None:
            return None
        signature = [getattr(device, "key", None), bool(is_s2p)]
        for side in ("left", "right"):
            plot = self.plots.get(side)
            source = (
                _selected_plot_source(plot) if plot is not None else ""
            )
            signature.extend((side, source))
            if not is_s2p:
                continue
            side_data = self.latest_telemetry.get(side) or {}
            signature.extend((
                tuple(
                    tuple(point)
                    for point in side_data.get("curve_points") or ()
                ),
                side_data.get("deadzone"),
                side_data.get("outer_deadzone"),
                side_data.get("output_shape"),
            ))
        if is_s2p:
            gyro = self.latest_telemetry.get("gyro") or {}
            signature.extend((
                gyro.get("target"),
                gyro.get("activation_mode"),
                gyro.get("motion_mode"),
                gyro.get("response_curve"),
                gyro.get("curve_strength"),
                gyro.get("stick_anti_deadzone"),
                gyro.get("stick_sensitivity"),
                gyro.get("tilt_max_angle"),
                gyro.get("x_ratio"),
                gyro.get("y_ratio"),
            ))
        return tuple(signature)

    def _sync_shape_capture_context(self, device, is_s2p):
        """Discard a maximum envelope when its underlying mapping changes."""
        if not self.shape_enabled_var.get():
            self._shape_capture_signature = None
            return
        signature = self._current_shape_capture_signature(device, is_s2p)
        if signature == self._shape_capture_signature:
            return
        for history in self.histories.values():
            history.reset()
        self._baseline_trail_sequence()
        self._shape_capture_signature = signature

    def _on_shape_enabled_changed(self):
        """Always start a new measurement when output-shape capture begins."""
        self._shape_capture_signature = None
        if not self.shape_enabled_var.get():
            return
        for history in self.histories.values():
            history.reset()
        self._baseline_trail_sequence()
        device = self._selected_device()
        self._shape_capture_signature = self._current_shape_capture_signature(
            device,
            bool(device is not None and device.kind == "s2p"),
        )

    def _on_device_selected(self, _event=None):
        self._device_selection_explicit = True
        if self.diagnostic_session.running:
            self.stop_diagnostic("device_changed")
            self.diagnostic_state_var.set(
                self.gui.tr("診斷已停止：測試手把已變更")
            )
        self.stop_rumble()
        self.clear_measurements()
        self._configure_source_controls()
        self._last_consumed_token = None
        self._baseline_trail_sequence()
        self._configure_native_sampler()
        self._update_raw_hid_availability()

    def _selected_device(self):
        return self.devices.get(self.selected_device_var.get())

    def _request_device_refresh(self):
        """Safely replace device snapshots after a physical hot-plug."""
        if self._device_refresh_in_progress:
            return
        self._device_refresh_in_progress = True
        if self.device_refresh_button is not None:
            self.device_refresh_button.configure(state="disabled")
        self.stop_rumble()
        if self.native_sampler is not None:
            self.native_sampler.set_device(None)
        self._stop_raw_hid_stream()
        try:
            self._refresh_devices(force=True)
        except Exception:
            # Do not let a Python/backend enumeration error escape a Tk
            # callback. Native hidapi enumeration is additionally isolated in
            # a child process because access violations cannot be caught here.
            self._configure_native_sampler()
            self._sync_raw_hid_stream()
        finally:
            self._device_refresh_in_progress = False
            if (
                self.device_refresh_button is not None
                and self.window is not None
                and self.window.winfo_exists()
            ):
                probe_state = self.raw_hid_probe.read_snapshot().get("state")
                self.device_refresh_button.configure(
                    state=(
                        "disabled"
                        if probe_state in {"opening", "running"}
                        else "normal"
                    )
                )

    def _refresh_devices(self, force=False):
        probe_state = self.raw_hid_probe.read_snapshot().get("state")
        should_enumerate = (
            (force or not self._device_enumeration_initialized)
            and probe_state not in {"opening", "running"}
        )
        if should_enumerate:
            self.raw_hid_devices = {
                device.key: device
                for device in enumerate_raw_hid_gamepads()
            }
            if self.native_sampler is not None:
                native_devices = self.native_sampler.enumerate_devices(
                    excluded_xinput_slot=None
                )
            else:
                native_devices = self.backend.enumerate_devices(
                    excluded_xinput_slot=None
                )
            self._native_test_devices = tuple(native_devices)
            self._device_enumeration_initialized = True
        previous_label = self.selected_device_var.get()
        previous_device = self.devices.get(previous_label)
        previous_device_key = (
            previous_device.key if previous_device is not None else None
        )
        had_s2p = any(
            device.kind == "s2p" for device in self.devices.values()
        )
        devices = []
        telemetry_fresh = self._telemetry_is_fresh(self.latest_telemetry)
        s2p_slot = None
        if telemetry_fresh:
            raw_slot = self.latest_telemetry.get("xinput_slot")
            if isinstance(raw_slot, int) and 0 <= raw_slot <= 3:
                s2p_slot = raw_slot
            devices.append(GamepadDevice(
                key="s2p",
                kind="s2p",
                index=s2p_slot if s2p_slot is not None else -1,
                name=self.gui.tr("S2P-XInput-Lite（目前橋接輸出）"),
                supports_rumble=s2p_slot is not None,
            ))
        native_devices = [
            device for device in self._native_test_devices
            if not (
                s2p_slot is not None
                and device.kind == "xinput"
                and device.index == s2p_slot
            )
        ]
        devices.extend(native_devices)
        for raw_index, raw_device in enumerate(
            self.raw_hid_devices.values()
        ):
            devices.append(raw_hid_test_device(
                raw_device, raw_index, self.gui.tr
            ))
        devices = [
            replace(
                device,
                name=localized_device_name(device, self.gui.tr),
            )
            if device.name_translation_key else device
            for device in devices
        ]
        devices = [
            replace(
                device,
                display_suffix=(
                    f"XInput {device.index + 1}"
                    if device.kind == "xinput"
                    else f"WinMM {device.index + 1}"
                    if device.kind == "winmm"
                    else device.display_suffix
                ),
            )
            for device in devices
        ]
        mapping = build_device_display_mapping(devices)
        current_keys = {device.key for device in self.devices.values()}
        new_keys = {device.key for device in mapping.values()}
        if (
            not force
            and new_keys == current_keys
            and tuple(mapping) == tuple(self.devices)
        ):
            self.devices = mapping
            self._set_raw_hid_controls_active(
                probe_state in {"opening", "running"}
            )
            return
        self.devices = mapping
        names = tuple(mapping)
        self.device_combo.configure(values=names)
        new_s2p_name = next(
            (
                name for name, device in mapping.items()
                if device.kind == "s2p"
            ),
            None,
        )
        previous_name = next(
            (
                name for name, device in mapping.items()
                if device.key == previous_device_key
            ),
            None,
        )
        if (
            new_s2p_name is not None
            and not had_s2p
            and not getattr(self, "_device_selection_explicit", False)
        ):
            next_name = new_s2p_name
            self.clear_measurements()
        elif previous_name is not None:
            next_name = previous_name
        elif names:
            next_name = names[0]
        else:
            next_name = ""
        if previous_device_key is not None and previous_name is None:
            self._device_selection_explicit = False
        next_device = mapping.get(next_name)
        next_device_key = next_device.key if next_device is not None else None
        if previous_device_key != next_device_key:
            self.stop_rumble()
        self.selected_device_var.set(next_name)
        self._configure_source_controls()
        self._configure_native_sampler()
        self._set_raw_hid_controls_active(
            probe_state in {"opening", "running"}
        )
        self._sync_raw_hid_stream()

    def _refresh_devices_after_telemetry(self):
        self._device_refresh_job = None
        if self.window is None or not self.window.winfo_exists():
            return
        self._refresh_devices(force=False)
        self._device_refresh_job = self.window.after(
            1000, self._refresh_devices_after_telemetry
        )

    def _baseline_trail_sequence(self):
        if self.telemetry is not None:
            self._last_trail_sequence = (
                self.telemetry.latest_trail_sequence()
            )

    def _configure_source_controls(self):
        device = self._selected_device()
        is_s2p = device is not None and device.kind == "s2p"
        is_raw_hid = device is not None and device.kind == "raw_hid"
        raw_hid_toggle = getattr(self, "raw_hid_stream_toggle", None)
        if raw_hid_toggle is not None:
            raw_hid_toggle.configure(
                state="normal" if is_raw_hid else "disabled"
            )
        gyro = self.latest_telemetry.get("gyro") or {}
        target = (
            gyro.get("target")
            if (
                gyro.get("available")
                and _gyro_mapping_enabled(gyro)
            )
            else None
        )
        for side, plot in self.plots.items():
            plot.set_source_capability(is_s2p, target == side)

    def _native_device_for_raw_hid(self):
        """Return an unambiguous Windows snapshot source for Raw HID buttons.

        Raw HID remains the source for stick positions and report timing. XInput
        or WinMM is used only to fill button/trigger fields that stream version 1
        does not publish. Never guess when several plausible devices remain.
        """
        raw_device = self._selected_raw_hid_device()
        if raw_device is None:
            return None
        candidates = tuple(
            device for device in self._native_test_devices
            if device.kind in {"xinput", "winmm"}
        )

        # The running bridge exposes its exact ViGEm XInput slot through shared
        # telemetry, which is the strongest possible association.
        raw_slot = self.latest_telemetry.get("xinput_slot")
        if (
            raw_device.is_virtual
            and self._telemetry_is_fresh(self.latest_telemetry)
            and isinstance(raw_slot, int)
        ):
            match = next((
                device for device in candidates
                if device.kind == "xinput" and device.index == raw_slot
            ), None)
            if match is not None:
                return match

        xinput = tuple(device for device in candidates if device.kind == "xinput")
        winmm = tuple(device for device in candidates if device.kind == "winmm")
        vid_pid = (int(raw_device.vendor_id), int(raw_device.product_id))

        # Standalone PC mode has a known XInput output contract. Prefer its
        # single XInput slot over the parallel WinMM view so LT/RT remain
        # available as analog axes and button names stay semantic.
        if vid_pid == (0xCAFE, 0x4020) and len(xinput) == 1:
            return xinput[0]

        # WinMM retains USB vendor/product identifiers in JOYCAPS, so physical
        # third-party HID collections can be paired without guessing by name.
        caps_by_index = getattr(self.backend, "_winmm_caps", {})
        exact_winmm = tuple(
            device for device in winmm
            if (
                int(getattr(
                    caps_by_index.get(device.index), "wMid", -1
                ))
                == vid_pid[0]
                and int(getattr(
                    caps_by_index.get(device.index), "wPid", -1
                ))
                == vid_pid[1]
            )
        )
        if len(exact_winmm) == 1:
            return exact_winmm[0]

        # XInput does not expose VID/PID, but an explicitly selected &IG_
        # collection and exactly one visible XInput device form an unambiguous
        # association for ordinary physical or software-created gamepads.
        if "&ig_" in raw_device.path.casefold() and len(xinput) == 1:
            return xinput[0]

        # Mobile HID is represented through WinMM and retains a meaningful name.
        if vid_pid == (0xCAFE, 0x4021):
            named = tuple(
                device for device in winmm
                if "s2p mobile gamepad" in device.name.casefold()
            )
            if len(named) == 1:
                return named[0]
            if len(winmm) == 1:
                return winmm[0]

        if raw_device.is_virtual and len(xinput) == 1:
            return xinput[0]
        return None

    def _configure_native_sampler(self):
        if self.native_sampler is None:
            return
        device = self._selected_device()
        if device is not None and device.kind in {"xinput", "winmm"}:
            native_device = device
        elif device is not None and device.kind == "raw_hid":
            native_device = self._native_device_for_raw_hid()
        else:
            native_device = None
        self.native_sampler.set_device(native_device)

    def _sync_connection_status(self, sample):
        """Mirror the settings UI's connection summary in the tester header."""
        def set_status(text, color):
            self.status_var.set(text)
            if self.status_label is not None:
                self.status_label.configure(foreground=color)

        device = self._selected_device()
        if device is not None and device.kind == "raw_hid":
            stream_status = self.raw_hid_stream.status()
            connected = stream_status.get("state") in {"opening", "running"}
            set_status(
                self.gui.tr("● 已連線" if connected else "● 未連線"),
                "#138A36" if connected else "#777777",
            )
            return
        if device is not None and device.kind != "s2p":
            connected = sample is not None
            set_status(
                self.gui.tr("● 已連線" if connected else "● 未連線"),
                "#138A36" if connected else "#777777",
            )
            return
        source_label = getattr(self.gui, "controller_status_label", None)
        if source_label is not None:
            try:
                text = str(source_label.cget("text") or "")
                color = str(source_label.cget("fg") or "#777777")
                if text:
                    set_status(text, color)
                    return
            except (AttributeError, tk.TclError):
                pass
        text, color = read_connection_status_summary(self.gui.tr)
        if text:
            set_status(text, color)
            return
        connected = sample is not None
        set_status(
            self.gui.tr("● 已連線" if connected else "● 未連線"),
            "#138A36" if connected else "#777777",
        )

    def _event_inputs(self, sample, is_s2p):
        if sample is None:
            return {}
        if is_s2p:
            mapped = {
                str(item.get("source")): str(item.get("target") or "無")
                for item in sample.get("mappings", ())
                if item.get("source")
            }
            for source in sample.get("buttons", ()):
                mapped.setdefault(str(source), "無")
            return mapped
        active = {
            str(button): "原始輸入"
            for button in sample.get("buttons", ())
        }
        # XInput exposes LT/RT as axes rather than buttons.  Show their
        # normal pressed/released state in the event table too, using the
        # standard XInput trigger threshold, while preserving their analog
        # readout in the linear-trigger panel.
        trigger_values = tuple(sample.get("triggers") or ())
        # The mobile HID descriptor also publishes digital Button usages 9/10
        # (L2/R2) as compatibility mirrors for older games.  When their
        # analog LT/RT values are available, present a single coherent trigger
        # instead of duplicate L2/R2 and LT/RT events.
        for index, digital_name in enumerate(("L2", "R2")):
            if index < len(trigger_values) and trigger_values[index] is not None:
                active.pop(digital_name, None)
        for index, trigger_name in enumerate(("LT", "RT")):
            if index >= len(trigger_values):
                continue
            try:
                pressed = float(trigger_values[index]) >= (
                    TRIGGER_EVENT_THRESHOLD
                )
            except (TypeError, ValueError):
                pressed = False
            if pressed:
                active.setdefault(trigger_name, "原始輸入")
        return active

    def _update_triggers(self, sample, is_s2p):
        tr = self.gui.tr
        trigger_values = (
            tuple(sample.get("triggers") or (0.0, 0.0))
            if sample is not None else (0.0, 0.0)
        )
        if len(trigger_values) < 2:
            trigger_values = tuple(trigger_values) + (0.0,) * (
                2 - len(trigger_values)
            )
        linear_mappings = {
            str(item.get("trigger")): item
            for item in (
                sample.get("linear_triggers", ()) if sample else ()
            )
            if item.get("trigger")
        }
        direction_names = {
            "UP": "↑",
            "DOWN": "↓",
            "LEFT": "←",
            "RIGHT": "→",
        }
        # The monitor calls this at presentation rate.  Avoid sending three
        # unchanged Tk updates per trigger every frame while preserving an
        # immediate update for every actual input/source change.
        signature = (
            tuple(trigger_values),
            bool(sample),
            bool(is_s2p),
            tuple(sorted(
                (name, str(item.get("source") or ""),
                 str(item.get("direction") or ""))
                for name, item in linear_mappings.items()
            )),
        )
        if signature == getattr(self, "_last_trigger_display_signature", None):
            return
        self._last_trigger_display_signature = signature
        for index, trigger_name in enumerate(("LT", "RT")):
            raw_value = trigger_values[index]
            if raw_value is None:
                self.trigger_bars[trigger_name].configure(value=0)
                self.trigger_value_vars[trigger_name].set("— / 255")
                self.trigger_source_vars[trigger_name].set(
                    tr("裝置未提供標準扳機軸")
                )
                continue
            normalized = max(0.0, min(1.0, float(raw_value or 0.0)))
            value = int(round(normalized * 255.0))
            self.trigger_bars[trigger_name].configure(value=value)
            self.trigger_value_vars[trigger_name].set(f"{value} / 255")
            mapping = linear_mappings.get(trigger_name)
            if is_s2p and mapping:
                source = tr(str(mapping.get("source") or "搖桿"))
                direction = direction_names.get(
                    str(mapping.get("direction") or "").upper(),
                    str(mapping.get("direction") or ""),
                )
                description = f"{source} {direction} → {trigger_name}"
            elif is_s2p:
                description = tr("按鍵／原生扳機輸出")
            elif sample is not None:
                description = tr("裝置實際輸出")
            else:
                description = tr("等待輸入")
            self.trigger_source_vars[trigger_name].set(description)

    def _update_events(self, sample, is_s2p, now):
        tr = self.gui.tr
        active = self._event_inputs(sample, is_s2p)
        layer = (
            str(sample.get("layer") or "主方案")
            if sample is not None else "主方案"
        )
        for source, target in active.items():
            event = self._button_events.get(source)
            if event is None:
                self._button_events[source] = ButtonEvent(
                    source, target, layer, now
                )
            else:
                event.target = target
                event.layer = layer
        for source in tuple(self._button_events):
            if source in active:
                continue
            event = self._button_events.pop(source)
            event.released_at = now
            self._recent_events.appendleft(event)
        while self._recent_events and (
            now - float(self._recent_events[-1].released_at or now) > 2.0
        ):
            self._recent_events.pop()

        rows = []
        for event in self._button_events.values():
            rows.append((
                tr(event.source),
                tr("持續按壓"),
                tr("{seconds:.2f} 秒").format(
                    seconds=now - event.started_at
                ),
                tr(event.target),
                tr(event.layer),
            ))
        for event in self._recent_events:
            rows.append((
                tr(event.source),
                tr("已放開"),
                tr("{seconds:.2f} 秒").format(
                    seconds=(event.released_at or now) - event.started_at
                ),
                tr(event.target),
                tr(event.layer),
            ))
        for item in self.event_tree.get_children():
            self.event_tree.delete(item)
        if not rows:
            rows = [(
                "—",
                tr("等待輸入"),
                tr("{seconds:.2f} 秒").format(seconds=0.0),
                "—",
                tr(layer),
            )]
        for row in rows[:6]:
            self.event_tree.insert("", "end", values=row)

    def clear_measurements(self):
        for history in self.histories.values():
            history.reset()
        self._button_events.clear()
        self._recent_events.clear()
        self._baseline_trail_sequence()
        self._raw_hid_stream_sequence = int(
            self.raw_hid_stream.status().get("latest_sequence", 0) or 0
        )
        self._raw_hid_stream_dropped = 0
        self._raw_hid_stream_latest_axes = None

    def _update_rumble_availability(self, device):
        supported = bool(device and device.supports_rumble)
        # Reconfiguring a ttk button's state clears the native Windows hover
        # animation even when the value stays "normal". This method runs at
        # 30 Hz, so only touch widget state when availability truly changes.
        if supported != self._rumble_supported_state:
            self._rumble_supported_state = supported
            state = "normal" if supported else "disabled"
            for button in self.rumble_buttons:
                button.configure(state=state)
            for widget in self.rumble_option_widgets:
                if isinstance(widget, ttk.Scale):
                    widget.state(
                        ["!disabled"] if supported else ["disabled"]
                    )
                else:
                    widget.configure(state=state)
            for scale in self.manual_rumble_widgets:
                scale.state(
                    ["!disabled"] if supported else ["disabled"]
                )
            self.stop_rumble_button.configure(state=state)
            self.reset_rumble_button.configure(state=state)
        if not supported:
            notice = self.gui.tr("此手把沒有可用的 XInput 震動介面")
        elif device.kind == "s2p" and (
            self.latest_telemetry.get("audio_haptics_mode") == "AUDIO"
        ):
            notice = self.gui.tr(
                "目前為純音訊震動模式，遊戲震動模板不會輸出"
            )
        else:
            notice = self.gui.tr("震動會套用目前方案的 LF／HF 設定")
        if notice != self._last_rumble_notice:
            self._last_rumble_notice = notice
            self.rumble_notice_var.set(notice)

    def _rumble_target(self):
        device = self._selected_device()
        if device is None or not device.supports_rumble:
            return None
        if device.kind == "s2p":
            slot = self.latest_telemetry.get("xinput_slot")
            return int(slot) if isinstance(slot, int) and 0 <= slot <= 3 else None
        return device.index if device.kind == "xinput" else None

    def _set_rumble(self, left, right, slot=None):
        slot = self._rumble_target() if slot is None else slot
        if slot is None:
            return False
        left = max(0.0, min(1.0, float(left)))
        right = max(0.0, min(1.0, float(right)))
        applied = self.backend.set_xinput_rumble(slot, left, right)
        if applied:
            if left > 0.0 or right > 0.0:
                self._active_rumble_slot = slot
            elif getattr(self, "_active_rumble_slot", None) == slot:
                self._active_rumble_slot = None
        return applied

    def stop_rumble(self):
        active_slot = getattr(self, "_active_rumble_slot", None)
        if self.window is not None:
            for jobs in self._rumble_jobs.values():
                for job in jobs:
                    try:
                        self.window.after_cancel(job)
                    except tk.TclError:
                        pass
        self._rumble_jobs.clear()
        if self.window is not None:
            for job in self._repeat_rumble_jobs.values():
                try:
                    self.window.after_cancel(job)
                except tk.TclError:
                    pass
        self._repeat_rumble_jobs.clear()
        self._rumble_layers.clear()
        self._rumble_layer_templates.clear()
        self._active_rumble_templates.clear()
        self._manual_rumble = (0.0, 0.0)
        for attribute, value in (
            ("manual_lf_var", 0.0),
            ("manual_hf_var", 0.0),
            ("manual_lf_text", "0%"),
            ("manual_hf_text", "0%"),
        ):
            variable = getattr(self, attribute, None)
            if variable is not None:
                variable.set(value)
        self._refresh_rumble_template_styles()
        try:
            if active_slot is not None:
                self._set_rumble(0.0, 0.0, slot=active_slot)
            else:
                self._set_rumble(0.0, 0.0)
        finally:
            # A disconnected XInput slot cannot acknowledge the stop. Do not
            # retain it and later target an unrelated controller reusing it.
            self._active_rumble_slot = None

    def play_rumble(self, name):
        """Play once, or toggle one repeating template layer on/off."""
        if self._rumble_pattern(name) is None:
            return
        repeating = self.repeat_rumble_var.get()
        if repeating:
            active_layers = tuple(self._active_rumble_templates.get(name, ()))
            if active_layers:
                for layer_id in active_layers:
                    self._cancel_rumble_layer(layer_id)
                return
        self._next_rumble_layer_id += 1
        layer_id = self._next_rumble_layer_id
        self._rumble_layers[layer_id] = (0.0, 0.0)
        self._rumble_jobs[layer_id] = []
        self._rumble_layer_templates[layer_id] = name
        if repeating:
            self._active_rumble_templates.setdefault(name, set()).add(layer_id)
        self._refresh_rumble_template_styles()
        self._play_rumble_pattern(name, layer_id)

    def _rumble_pattern(self, name):
        patterns = {
            "lf": ((0, 0.85, 0.0), (900, 0.0, 0.0)),
            "hf": ((0, 0.0, 0.85), (900, 0.0, 0.0)),
            "alternate": (
                (0, 0.80, 0.0), (220, 0.0, 0.80),
                (440, 0.80, 0.0), (660, 0.0, 0.80),
                (880, 0.0, 0.0),
            ),
            "impact": (
                (0, 0.90, 0.70), (90, 0.60, 0.25),
                (220, 0.30, 0.08), (450, 0.0, 0.0),
            ),
            "ramp": tuple(
                (index * 80, index / 10.0, index / 12.0)
                for index in range(1, 11)
            ) + ((920, 0.0, 0.0),),
            "double_tap": (
                (0, 0.90, 0.35), (95, 0.0, 0.0),
                (190, 0.90, 0.35), (310, 0.0, 0.0),
            ),
            "rapid_fire": tuple(
                (index * 70, 0.42, 0.78) if index % 2 == 0
                else (index * 70, 0.0, 0.0)
                for index in range(10)
            ) + ((700, 0.0, 0.0),),
            "engine": (
                (0, 0.20, 0.04), (120, 0.35, 0.07),
                (240, 0.52, 0.10), (360, 0.68, 0.14),
                (480, 0.52, 0.10), (600, 0.35, 0.07),
                (720, 0.20, 0.04), (840, 0.0, 0.0),
            ),
            "wave": (
                (0, 0.15, 0.10), (130, 0.35, 0.28),
                (260, 0.60, 0.55), (390, 0.82, 0.78),
                (520, 0.60, 0.55), (650, 0.35, 0.28),
                (780, 0.15, 0.10), (900, 0.0, 0.0),
            ),
            "alert": (
                (0, 0.0, 0.95), (70, 0.0, 0.0),
                (150, 0.0, 0.95), (220, 0.0, 0.0),
                (300, 0.0, 0.95), (390, 0.0, 0.0),
            ),
            "heartbeat": (
                (0, 0.76, 0.14), (110, 0.0, 0.0),
                (235, 0.48, 0.08), (360, 0.0, 0.0),
            ),
            "footsteps": (
                (0, 0.38, 0.20), (90, 0.0, 0.0),
                (310, 0.46, 0.24), (410, 0.0, 0.0),
                (650, 0.38, 0.20), (740, 0.0, 0.0),
            ),
            "terrain": (
                (0, 0.12, 0.42), (65, 0.26, 0.18),
                (130, 0.10, 0.56), (195, 0.32, 0.16),
                (260, 0.14, 0.46), (325, 0.28, 0.20),
                (390, 0.0, 0.0),
            ),
            "low_rumble": (
                (0, 0.18, 0.02), (180, 0.36, 0.03),
                (360, 0.58, 0.05), (540, 0.76, 0.06),
                (720, 0.48, 0.04), (900, 0.20, 0.02),
                (1080, 0.0, 0.0),
            ),
            "burst": (
                (0, 1.0, 0.86), (55, 0.78, 0.54),
                (125, 0.42, 0.20), (260, 0.12, 0.04),
                (420, 0.0, 0.0),
            ),
            "machine_gun": tuple(
                (index * 55, 0.18, 0.90) if index % 2 == 0
                else (index * 55, 0.05, 0.08)
                for index in range(18)
            ) + ((990, 0.0, 0.0),),
            "shotgun": (
                (0, 0.96, 0.82), (70, 0.44, 0.28),
                (155, 0.16, 0.06), (320, 0.0, 0.0),
            ),
            "turbo": (
                (0, 0.22, 0.30), (170, 0.32, 0.42),
                (310, 0.44, 0.56), (420, 0.58, 0.68),
                (500, 0.72, 0.80), (555, 0.86, 0.92),
                (610, 0.0, 0.0),
            ),
            "rotor": tuple(
                (index * 95, 0.34 + (index % 3) * 0.10, 0.18)
                for index in range(8)
            ) + ((760, 0.0, 0.0),),
            "countdown": (
                (0, 0.0, 0.62), (90, 0.0, 0.0),
                (300, 0.0, 0.70), (380, 0.0, 0.0),
                (520, 0.0, 0.80), (580, 0.0, 0.0),
                (670, 0.0, 0.92), (720, 0.0, 0.0),
            ),
        }
        return patterns.get(name)

    def _apply_rumble_mix(self):
        left, right = self._manual_rumble
        for layer_left, layer_right in self._rumble_layers.values():
            left += layer_left
            right += layer_right
        self._set_rumble(left, right)

    def _set_rumble_layer(self, layer_id, left, right):
        if layer_id not in self._rumble_layers:
            return
        self._rumble_layers[layer_id] = (left, right)
        self._apply_rumble_mix()

    def _cancel_rumble_layer(self, layer_id):
        if self.window is not None:
            for job in self._rumble_jobs.get(layer_id, ()):
                try:
                    self.window.after_cancel(job)
                except tk.TclError:
                    pass
            repeat_job = self._repeat_rumble_jobs.pop(layer_id, None)
            if repeat_job is not None:
                try:
                    self.window.after_cancel(repeat_job)
                except tk.TclError:
                    pass
        self._clear_rumble_layer(layer_id)

    def _clear_rumble_layer(self, layer_id):
        if self._rumble_layers.pop(layer_id, None) is not None:
            self._rumble_jobs.pop(layer_id, None)
            template = self._rumble_layer_templates.pop(layer_id, None)
            if template is not None:
                active_layers = self._active_rumble_templates.get(template)
                if active_layers is not None:
                    active_layers.discard(layer_id)
                    if not active_layers:
                        self._active_rumble_templates.pop(template, None)
            self._refresh_rumble_template_styles()
            self._apply_rumble_mix()

    def _refresh_rumble_template_styles(self):
        buttons = getattr(self, "rumble_buttons_by_template", {})
        repeating = self.repeat_rumble_var.get()
        for name, button in buttons.items():
            active = repeating and bool(self._active_rumble_templates.get(name))
            button.configure(style="TButton")
            if active:
                button.state(["active"])
            else:
                try:
                    pointer_widget = button.winfo_containing(
                        button.winfo_pointerx(), button.winfo_pointery()
                    )
                except tk.TclError:
                    pointer_widget = None
                if pointer_widget is not button:
                    button.state(["!active"])

    def _restore_repeating_button_state(self, name):
        button = self.rumble_buttons_by_template.get(name)
        if button is None or self.window is None:
            return
        # Leave normal hover entirely to the native Windows ttk binding.
        # Only restore ``active`` after Leave when this is a latched repeating
        # template; this produces the same blue hover appearance without the
        # two handlers fighting each other.
        def restore():
            if (
                self.repeat_rumble_var.get()
                and self._active_rumble_templates.get(name)
            ):
                button.state(["active"])

        self.window.after_idle(restore)

    def _play_rumble_pattern(self, name, layer_id):
        pattern = self._rumble_pattern(name)
        if not pattern or self.window is None:
            return
        pattern_duration_ms = max(delay for delay, _, _ in pattern)
        frequency_hz = max(
            0.1, min(100.0, round(self.rumble_repeat_hz_var.get(), 1))
        )
        cycle_duration_ms = max(1, int(round(1000.0 / frequency_hz)))
        time_scale = cycle_duration_ms / pattern_duration_ms
        strength = max(0.0, min(1.0, self.rumble_strength_var.get() / 100.0))
        for delay, left, right in pattern:
            if not self.template_lf_enabled_var.get():
                left = 0.0
            if not self.template_hf_enabled_var.get():
                right = 0.0
            left *= strength
            right *= strength
            job = self.window.after(
                max(0, int(round(delay * time_scale))),
                lambda lf=left, hf=right, key=layer_id:
                self._set_rumble_layer(key, lf, hf),
            )
            self._rumble_jobs.setdefault(layer_id, []).append(job)
        if self.repeat_rumble_var.get():
            self._repeat_rumble_jobs[layer_id] = self.window.after(
                cycle_duration_ms,
                lambda key=layer_id, pattern_name=name:
                self._repeat_rumble_pattern(pattern_name, key),
            )
        else:
            cleanup = self.window.after(
                cycle_duration_ms + 1,
                lambda key=layer_id: self._clear_rumble_layer(key),
            )
            self._rumble_jobs.setdefault(layer_id, []).append(cleanup)

    def _repeat_rumble_pattern(self, name, layer_id):
        self._repeat_rumble_jobs.pop(layer_id, None)
        if self.repeat_rumble_var.get() and layer_id in self._rumble_layers:
            # All callbacks in the previous cycle have run by this point.
            # Retain only the next cycle's cancellable callbacks.
            self._rumble_jobs[layer_id] = []
            self._play_rumble_pattern(name, layer_id)

    def close(self):
        if self.diagnostic_session.running:
            self.diagnostic_session.stop("window_closed")
        self.diagnostic_reader.stop(timeout=0.5)
        self._cancel_raw_hid_countdown()
        self.raw_hid_probe.stop()
        self._stop_raw_hid_stream()
        self.stop_rumble()
        parameter_editor = self._parameter_editor_window
        self._parameter_editor_window = None
        if parameter_editor is not None:
            try:
                parameter_editor.destroy()
            except tk.TclError:
                pass
        if self.window is not None:
            for job in (self._poll_job, self._device_refresh_job):
                if job is not None:
                    try:
                        self.window.after_cancel(job)
                    except tk.TclError:
                        pass
            try:
                self.window.destroy()
            except tk.TclError:
                pass
        self.window = None
        self._window_icon = None
        self._about_logo = None
        self._about_fonts = ()
        self._poll_job = None
        self._device_refresh_job = None
        self._disable_high_resolution_timer()
        if self.native_sampler is not None:
            if self.native_sampler.stop():
                self.native_sampler = None
        try:
            if self.telemetry is not None:
                self.telemetry.close()
        except Exception:
            pass
        self.telemetry = None
