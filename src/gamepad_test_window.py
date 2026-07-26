"""Non-modal controller input, trajectory, mapping and rumble tester."""

from __future__ import annotations

import ctypes
import heapq
import json
import math
import struct
import time
import tkinter as tk
import zlib
from collections import deque
from dataclasses import dataclass, field, replace
from pathlib import Path
from tkinter import ttk, font as tkfont, messagebox

from gamepad_devices import (
    GamepadDevice,
    NativeGamepadSampler,
    WindowsGamepadBackend,
)
from gyro_processing import _apply_gyro_response_curve
from raw_hid_probe import RawHidProbeClient, enumerate_raw_hid_gamepads
from test_telemetry import SharedTestTelemetry
from tooltip_layout import wrap_tooltip_text


PLOT_SIZE = 320
PLOT_CENTER = PLOT_SIZE / 2
PLOT_RADIUS = 146
SHAPE_BIN_COUNT = 72
TRAIL_RASTER_REFRESH_HZ = 30.0
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


def _gyro_mapping_enabled(gyro):
    """Return whether gyro mapping is enabled in the current telemetry."""
    return str(gyro.get("activation_mode") or "").strip().upper() != "OFF"


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
        if name_counts[device.name] > 1:
            label = (
                f"{device.name} "
                f"[{device.kind.upper()} {device.index + 1}]"
            )
        # A driver-supplied name can itself look like our suffix. Retain every
        # device even in that unusual case.
        if label in mapping:
            label = f"{label} <{device.key}>"
        mapping[label] = device
    return mapping


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
    status_path = Path(__file__).with_name("controller_status.json")
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
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
    trail: deque = field(default_factory=lambda: deque(maxlen=4096))
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
        # A single transparent PhotoImage keeps the Canvas item count constant
        # even when every high-rate controller report is displayed.
        self._bitmap_enabled = True
        self._bitmap_photo = None
        self._bitmap_item = None
        self._bitmap_scanlines = None
        self._bitmap_pixel_expiry = None
        self._bitmap_expiry_heap = []
        self._bitmap_last_processed_sequence = -1
        self._bitmap_render_config = None
        self._bitmap_dirty = False
        self._bitmap_next_present_at = 0.0
        self._dot_item = None
        self._dot_coords = None
        self._dynamic_deadzone_item = None
        self._dynamic_deadzone_coords = None
        self._dynamic_deadzone_visible = False
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
            10.0,
            min(
                100.0,
                float(self.owner.sample_display_percent_var.get()),
            ),
        )))
        newest_sequence = trail[-1][3]
        rebuild = (
            self._trail_render_percent != display_percent
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
            self._trail_render_percent = display_percent
            new_samples = trail
        else:
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
                display_percent < 100
                and (sequence * display_percent) % 100 >= display_percent
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

    def _draw_trail_bitmap(self, history, now=None):
        now = time.perf_counter() if now is None else float(now)
        if (
            self._bitmap_photo is not None
            and self._bitmap_item is not None
            and now < self._bitmap_next_present_at
        ):
            # Samples remain in StickHistory and are consumed together on the
            # next raster frame. The monitor-rate live dot therefore avoids
            # all PNG, Tcl-variable and per-pixel trail work between updates.
            return True
        trail = history.trail
        display_percent = int(round(max(
            10.0,
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
            dirty = False
            if rebuild:
                self._bitmap_scanlines[:] = (
                    b"\x00" * len(self._bitmap_scanlines)
                )
                self._bitmap_pixel_expiry[:] = [0.0] * pixel_count
                self._bitmap_expiry_heap.clear()
                self._bitmap_last_processed_sequence = -1
                self._bitmap_render_config = render_config
                new_samples = trail
                dirty = True
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
                    dirty = True

            radius = 1.15
            radius_squared = radius * radius
            color = tuple(
                int(self.trail_color[index:index + 2], 16)
                for index in (1, 3, 5)
            ) + (255,)
            color_bytes = bytes(color)
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
                minimum_x = max(0, int(math.floor(point_x - radius)))
                maximum_x = min(
                    PLOT_SIZE - 1,
                    int(math.ceil(point_x + radius)),
                )
                minimum_y = max(0, int(math.floor(point_y - radius)))
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
                        if (
                            expiry
                            <= self._bitmap_pixel_expiry[pixel_index]
                        ):
                            continue
                        self._bitmap_pixel_expiry[pixel_index] = expiry
                        heapq.heappush(
                            self._bitmap_expiry_heap,
                            (expiry, pixel_index),
                        )
                        rgba_index = (
                            pixel_y * row_stride + 1 + pixel_x * 4
                        )
                        if (
                            self._bitmap_scanlines[
                                rgba_index:rgba_index + 4
                            ]
                            != color_bytes
                        ):
                            self._bitmap_scanlines[
                                rgba_index:rgba_index + 4
                            ] = color_bytes
                            dirty = True
            self._bitmap_dirty = self._bitmap_dirty or dirty
            should_present = bool(
                self._bitmap_photo is None
                or (
                    self._bitmap_dirty
                    and now >= self._bitmap_next_present_at
                )
            )
            bitmap_data = (
                encode_rgba_png(
                    PLOT_SIZE,
                    PLOT_SIZE,
                    self._bitmap_scanlines,
                )
                if should_present else None
            )
            first_present = self._bitmap_photo is None
            if first_present:
                self._bitmap_photo = tk.PhotoImage(
                    master=self.canvas,
                    data=bitmap_data,
                    format="png",
                )
            elif should_present:
                self._bitmap_photo.configure(
                    data=bitmap_data,
                    format="png",
                )
            if self._bitmap_item is None:
                self._bitmap_item = self.canvas.create_image(
                    0,
                    0,
                    anchor="nw",
                    image=self._bitmap_photo,
                    tags="trail_bitmap",
                )
                self.canvas.tag_raise(self._bitmap_item)
            if should_present:
                self._bitmap_dirty = False
                self._bitmap_next_present_at = (
                    now + 1.0 / TRAIL_RASTER_REFRESH_HZ
                )
                if (
                    first_present
                    and getattr(self, "side", "left") == "right"
                ):
                    # Do not make both 320x320 PNG layers update in the same
                    # 180 Hz display frame.
                    self._bitmap_next_present_at += (
                        0.5 / TRAIL_RASTER_REFRESH_HZ
                    )
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
                    self.canvas.delete(self._bitmap_item)
                except tk.TclError:
                    pass
            self._bitmap_item = None
            self._bitmap_photo = None
            self._bitmap_enabled = False
            self._bitmap_render_config = None
            self._bitmap_dirty = False
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
        side_data = telemetry.get(self.side, {}) if is_s2p else {}
        source = self.selected_source()
        self._update_gyro_legend_visibility(telemetry, is_s2p, source)
        static_key = self._static_signature(side_data, telemetry, is_s2p)
        if static_key != self._static_key:
            self._draw_static(side_data, telemetry, is_s2p)
            self._static_key = static_key
            if self._bitmap_item is not None:
                self.canvas.tag_raise(self._bitmap_item, "static")
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
            self._last_trace_draw_at = now
        # Incremental trail processing usually handles only the reports added
        # since the previous display frame and emits no Canvas command when
        # nothing changed. Running it at display cadence removes the old
        # visible 30 FPS stepping without rebuilding the complete history.
        if getattr(self, "_bitmap_enabled", False):
            if not self._draw_trail_bitmap(history, now):
                self._draw_trail(history, now)
        else:
            self._draw_trail(history, now)
        if trace_redrawn and self._bitmap_item is not None:
            # Animated physical-stick colour bands are recreated after the
            # raster item. Restore the intended bands -> trail -> live-dot
            # stacking order only on those 30 Hz overlay frames.
            self.canvas.tag_raise(self._bitmap_item)
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
        if (
            dot_is_new
            or trace_redrawn
            or not getattr(self, "_bitmap_enabled", False)
        ):
            self.canvas.tag_raise(dot_item)
        if not update_details:
            return
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


class GamepadTestWindow:
    def __init__(self, gui):
        self.gui = gui
        self.root = gui.root
        self.window = None
        self.test_notebook = None
        self.input_tab = None
        self.rumble_tab = None
        self.high_rate_tab = None
        self.backend = WindowsGamepadBackend()
        self.native_sampler = None
        self.telemetry = None
        self.latest_telemetry = {}
        self.devices = {}
        self._device_selection_explicit = False
        self.selected_device_var = tk.StringVar()
        self.status_var = tk.StringVar(
            value=self.gui.tr("正在搜尋手把...")
        )
        self.draw_fps_var = tk.StringVar(value="— FPS")
        self.raw_hid_probe = RawHidProbeClient()
        self.raw_hid_devices = {}
        self.raw_hid_duration_var = tk.StringVar(value="10")
        self.raw_hid_state_var = tk.StringVar(value=self.gui.tr("尚未量測"))
        self.raw_hid_rate_var = tk.StringVar(value="— Hz")
        self.raw_hid_count_var = tk.StringVar(value="0")
        self.raw_hid_remaining_var = tk.StringVar(value="—")
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
        self.sample_display_percent_var = tk.DoubleVar(value=30.0)
        self.sample_display_percent_text = tk.StringVar(value="30%")
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
        self._draw_times = deque(maxlen=512)
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
        self._parameter_editor_window = None

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
            self.native_sampler = NativeGamepadSampler(self.backend)
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
        window.title(self.gui.tr("手把測試"))
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
        selector.columnconfigure(3, weight=1)
        ttk.Label(selector, text=self.gui.tr("測試手把")).grid(
            row=0, column=0, sticky="w", padx=(0, 6)
        )
        self.device_combo = ttk.Combobox(
            selector,
            textvariable=self.selected_device_var,
            state="readonly",
            width=30,
        )
        self.device_combo.grid(row=0, column=1, sticky="w")
        self.device_combo.bind(
            "<<ComboboxSelected>>", self._on_device_selected
        )
        ttk.Button(
            selector,
            text=self.gui.tr("重新整理"),
            width=8,
            command=lambda: self._refresh_devices(force=True),
        ).grid(row=0, column=2, padx=(5, 0))
        self.status_label = ttk.Label(
            selector, textvariable=self.status_var, foreground="#138A36"
        )
        self.status_label.grid(
            row=0, column=3, sticky="e", padx=(10, 0)
        )
        self.draw_fps_label = ttk.Label(
            selector, textvariable=self.draw_fps_var, foreground="#777777"
        )
        self.draw_fps_label.grid(
            row=0, column=4, sticky="e", padx=(8, 0)
        )

        notebook = ttk.Notebook(content)
        notebook.grid(row=1, column=0, sticky="nsew")
        input_tab = ttk.Frame(notebook, padding=(8, 6))
        rumble_tab = ttk.Frame(notebook, padding=(10, 8))
        high_rate_tab = ttk.Frame(notebook, padding=(10, 8))
        self.test_notebook = notebook
        self.input_tab = input_tab
        self.rumble_tab = rumble_tab
        self.high_rate_tab = high_rate_tab
        notebook.add(input_tab, text=f" {self.gui.tr('輸入監看')} ")
        notebook.add(rumble_tab, text=f" {self.gui.tr('震動測試')} ")
        notebook.add(high_rate_tab, text=f" {self.gui.tr('回報率量測')} ")
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
        display_controls.columnconfigure(8, weight=1)
        ttk.Button(
            display_controls,
            text=self.gui.tr("清除軌跡與統計"),
            command=self.clear_measurements,
        ).grid(row=0, column=0, padx=(0, 10))
        ttk.Checkbutton(
            display_controls,
            text=self.gui.tr("顯示輸出形狀"),
            variable=self.shape_enabled_var,
            command=self._on_shape_enabled_changed,
        ).grid(row=0, column=1, padx=(0, 10))
        ttk.Checkbutton(
            display_controls,
            text=self.gui.tr("顯示陀螺圖例"),
            variable=self.show_gyro_legend_var,
        ).grid(row=0, column=2, padx=(0, 8))
        ttk.Label(
            display_controls, text=self.gui.tr("採樣點")
        ).grid(row=0, column=3, padx=(0, 3))
        sample_scale = ttk.Scale(
            display_controls,
            from_=10.0,
            to=100.0,
            length=80,
            variable=self.sample_display_percent_var,
            command=self._update_sample_display_percent,
            orient="horizontal",
        )
        sample_scale.grid(row=0, column=4)
        sample_value_label = ttk.Label(
            display_controls,
            textvariable=self.sample_display_percent_text,
            width=5,
            anchor="e",
        )
        sample_value_label.grid(row=0, column=5, padx=(3, 3))
        self._bind_parameter_control(
            sample_value_label,
            self.sample_display_percent_var,
            "採樣點",
            10.0,
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
        sample_help.grid(row=0, column=6, padx=(0, 8))
        HoverTip(
            sample_help,
            self.gui.tr(
                "採樣點100%\n"
                "顯示目前軌跡長度內，所選Windows輸入介面實際收到的全部"
                "座標點。\n\n"
                "XInput限制\n"
                "XInput只保留最新座標，無法把兩次讀取之間已被覆蓋的"
                "中間座標還原成路徑點。\n\n"
                "顯示百分比\n"
                "降低百分比只會減少畫面上的路徑點，不會改變實際輸入。"
            ),
        )
        ttk.Label(
            display_controls, text=self.gui.tr("軌跡長度")
        ).grid(row=0, column=7, padx=(0, 4))
        trail_scale = ttk.Scale(
            display_controls,
            from_=0.5,
            to=5.0,
            variable=self.trail_length_var,
            command=self._update_trail_length_text,
            orient="horizontal",
        )
        trail_scale.grid(row=0, column=8, sticky="ew")
        trail_value_label = ttk.Label(
            display_controls,
            textvariable=self.trail_length_text,
            width=7,
            anchor="e",
        )
        trail_value_label.grid(row=0, column=9, padx=(4, 0))
        self._bind_parameter_control(
            trail_value_label,
            self.trail_length_var,
            "軌跡長度",
            0.5,
            5.0,
            step=0.1,
            number_format=".1f",
            on_change=self._update_trail_length_text,
            state_widget=trail_scale,
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
        self._refresh_devices(force=True)
        self._refresh_raw_hid_devices()
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
        controls.columnconfigure(2, weight=1, uniform="raw_actions")
        controls.columnconfigure(3, weight=1, uniform="raw_actions")
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
        self.raw_hid_start_button = ttk.Button(
            controls,
            text=self.gui.tr("開始量測"),
            command=self._start_raw_hid_measurement,
            width=22,
        )
        self.raw_hid_start_button.grid(
            row=0, column=2, sticky="ew", padx=(14, 4)
        )
        self.raw_hid_stop_button = ttk.Button(
            controls,
            text=self.gui.tr("提前停止"),
            command=self._stop_raw_hid_measurement,
            state="disabled",
            width=20,
        )
        self.raw_hid_stop_button.grid(row=0, column=3, sticky="ew")

        summary = ttk.Frame(parent)
        summary.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        for column in range(3):
            summary.columnconfigure(column, weight=1, uniform="raw_summary")
        summary_items = (
            ("目前回報率", self.raw_hid_rate_var),
            ("收到回報數", self.raw_hid_count_var),
            ("剩餘時間", self.raw_hid_remaining_var),
        )
        for column, (title, variable) in enumerate(summary_items):
            box = ttk.LabelFrame(
                summary, text=self.gui.tr(title), padding=(8, 5)
            )
            box.grid(
                row=0, column=column, sticky="ew",
                padx=(0 if column == 0 else 4, 0 if column == 2 else 4),
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
        ttk.Label(
            parent,
            text=self.gui.tr("判讀：三個數值越小、彼此越接近，代表回報越穩定。\n預期時間差（ms）＝1000 ÷ 回報率（Hz）；P50 接近預期值、P99 接近 P50，表示表現穩定。"),
            foreground="#666666",
            anchor="w",
            justify="left",
            wraplength=670,
        ).grid(row=5, column=0, sticky="ew", pady=(3, 0))
        ttk.Label(
            parent,
            text=self.gui.tr("山形顯示回報時間差分佈；僅反映資料到達 Windows 的穩定度，不代表按鍵到遊戲反應的延遲。"),
            foreground="#666666",
            anchor="w",
            justify="left",
        ).grid(row=6, column=0, sticky="ew", pady=(2, 0))
        self._draw_raw_hid_chart((), 0, 0.0, 0.0, 0.0)

    def _refresh_raw_hid_devices(self):
        if self.raw_hid_probe.read_snapshot().get("state") in {
            "opening", "running"
        }:
            return
        self.raw_hid_devices = {
            device.key: device
            for device in enumerate_raw_hid_gamepads()
        }
        if not self.raw_hid_probe.available:
            self.raw_hid_state_var.set(
                self.gui.tr("Raw HID 量測元件不可用")
            )
        elif not self.raw_hid_devices:
            self.raw_hid_state_var.set(
                self.gui.tr("找不到 Raw HID 遊戲手把介面")
            )
        elif self.raw_hid_probe.read_snapshot().get("state") == "idle":
            self.raw_hid_state_var.set(self.gui.tr("尚未量測"))
        self._set_raw_hid_controls_active(False)

    @staticmethod
    def _raw_hid_name_key(value):
        return "".join(
            character for character in str(value).casefold()
            if character.isalnum()
        )

    @staticmethod
    def _is_vigem_xbox_360_raw_hid(device):
        """Ignore this app's virtual Xbox 360 collection when possible."""
        return (
            device.vendor_id == 0x045E
            and device.product_id == 0x028E
            and "ig_" in device.path.casefold()
        )

    def _selected_raw_hid_device(self):
        """Resolve the selected tester device without exposing a second UI."""
        selected = self._selected_device()
        devices = tuple(self.raw_hid_devices.values())
        if selected is None or not devices:
            return None
        selected_key = self._raw_hid_name_key(selected.name)
        matches = [
            device for device in devices
            if selected_key
            and (
                selected_key in self._raw_hid_name_key(device.name)
                or self._raw_hid_name_key(device.name) in selected_key
            )
        ]
        if len(matches) == 1:
            return matches[0]
        xinput_collections = [
            device for device in devices
            if device.usage_page == 0x01
            and device.usage == 0x05
            and "ig_" in device.path.casefold()
        ]
        vigem_collections = [
            device for device in xinput_collections
            if self._is_vigem_xbox_360_raw_hid(device)
        ]
        physical_xinput = [
            device for device in xinput_collections
            if not self._is_vigem_xbox_360_raw_hid(device)
        ]
        if selected.kind in {"xinput", "s2p"}:
            telemetry = getattr(self, "latest_telemetry", {}) or {}
            vigem_slot = telemetry.get("xinput_slot")
            if (
                isinstance(vigem_slot, int)
                and selected.index == vigem_slot
                and len(vigem_collections) == 1
            ):
                return vigem_collections[0]
            physical_slots = [
                slot for slot in range(4) if slot != vigem_slot
            ]
            if selected.index in physical_slots:
                physical_index = physical_slots.index(selected.index)
                if physical_index < len(physical_xinput):
                    return physical_xinput[physical_index]
        elif (
            selected.kind == "winmm"
            and selected.index < len(physical_xinput)
        ):
            return physical_xinput[selected.index]
        physical_candidates = [
            device for device in devices
            if not self._is_vigem_xbox_360_raw_hid(device)
        ]
        if len(physical_candidates) == 1:
            return physical_candidates[0]
        # XInput does not expose a HID device path. With exactly one Raw HID
        # gamepad collection, it is still unambiguous and safe to use it.
        if len(devices) == 1:
            return devices[0]
        return None

    def _set_raw_hid_controls_active(self, active):
        if self.raw_hid_start_button is None:
            return
        if self.device_combo is not None:
            self.device_combo.configure(
                state="disabled" if active else "readonly"
            )
        self.raw_hid_duration_combo.configure(
            state="disabled" if active else "normal"
        )
        can_start = (
            not active
            and self.raw_hid_probe.available
            and self._selected_raw_hid_device() is not None
        )
        self.raw_hid_start_button.configure(
            state="normal" if can_start else "disabled"
        )
        self.raw_hid_stop_button.configure(
            state="normal" if active else "disabled"
        )

    def _start_raw_hid_measurement(self):
        self._refresh_raw_hid_devices()
        device = self._selected_raw_hid_device()
        if device is None:
            self.raw_hid_state_var.set(
                self.gui.tr("無法將目前測試手把對應至 Raw HID 介面")
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
        self.raw_hid_count_var.set("0")
        for variable in self.raw_hid_stats_vars.values():
            variable.set("—")
        self._update_raw_hid_stat_colors({})
        self._update_raw_hid_percentile_info(0)
        self._raw_hid_last_distribution = None
        self._draw_raw_hid_chart((), 0, 0.0, 0.0, 0.0)
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
            return
        self.raw_hid_probe.stop()
        self._update_raw_hid_measurement()

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
        if not self.raw_hid_probe.start(device_path, duration):
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
        self._set_raw_hid_controls_active(
            state in {"opening", "running"}
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
        for index in range(5):
            x = left + (right - left) * index / 4
            value_ms = x_max_us * index / 4 / 1000.0
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
        for index, count in enumerate(counts):
            x = left + (right - left) * index / max(1, len(counts) - 1)
            # Preserve visible headroom for percentile labels and make a
            # narrow high-frequency peak easier to read.
            y = bottom - count / peak * (bottom - top) * 0.82
            points.append((x, y))
        points.append((right, bottom))
        canvas.create_polygon(
            *[coordinate for point in points for coordinate in point],
            fill="#BBDEFB", outline="#1976D2", width=2,
        )
        label_y_offsets = (8, 30, 52)
        for label_index, (color, label, value) in enumerate(zip(
            ("#42A5F5", "#FB8C00", "#E53935"),
            ("P50", "P95", "P99"),
            values,
        )):
            x = left + min(value, x_max_us) / x_max_us * (right - left)
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
            10.0, min(100.0, self.sample_display_percent_var.get())
        )))
        self.sample_display_percent_var.set(float(percentage))
        self.sample_display_percent_text.set(f"{percentage}%")

    def _schedule_poll(self):
        if self.window is None or not self.window.winfo_exists():
            return
        current = time.perf_counter()
        frame_interval = self._frame_interval
        if self._active_test_tab() == "high_rate":
            frame_interval = max(frame_interval, 1.0 / 30.0)
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
            1, int(round((self._next_frame_at - current) * 1000.0))
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
        return "input"

    def _on_test_tab_changed(self, _event=None):
        self._next_frame_at = time.perf_counter()
        if self._active_test_tab() == "high_rate":
            self._raw_hid_last_distribution = None

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
        self._window_motion_until = time.perf_counter() + 0.25

    def _poll(self):
        self._poll_job = None
        if self.window is None or not self.window.winfo_exists():
            return
        now = time.perf_counter()
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
                for sample_time, queued_state in native_samples:
                    self._consume_sample(
                        self._sample_from_native(
                            device, queued_state, native_rate
                        ),
                        False,
                        sample_time,
                    )
                consumed_trail = bool(native_samples)
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
                if trail_samples:
                    self._consume_s2p_trail_samples(trail_samples, now)
                    consumed_trail = True
            if (
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
        if active_tab == "input":
            self._draw_plots(is_s2p, update_details=refresh_details)
        self._draw_times.append(now)
        while self._draw_times and now - self._draw_times[0] > 1.0:
            self._draw_times.popleft()
        if refresh_details:
            self._last_detail_refresh = now
            if active_tab == "input":
                self._update_triggers(sample, is_s2p)
            if now - self._last_connection_refresh >= 0.25:
                self._last_connection_refresh = now
                self._sync_connection_status(sample)
            if active_tab == "input":
                self._update_events(sample, is_s2p, now)
            self._update_draw_fps()
            if active_tab == "rumble":
                self._update_rumble_availability(device)
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
        for side, plot in self.plots.items():
            history = self.histories[side]
            if history.trail:
                _, x, y, _sequence = history.trail[-1]
            else:
                x = y = 0.0
            plot.draw(
                history,
                x,
                y,
                telemetry,
                is_s2p,
                update_details=update_details,
            )

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
        self.stop_rumble()
        self.clear_measurements()
        self._configure_source_controls()
        self._last_consumed_token = None
        self._baseline_trail_sequence()
        self._configure_native_sampler()
        self._refresh_raw_hid_devices()

    def _selected_device(self):
        return self.devices.get(self.selected_device_var.get())

    def _refresh_devices(self, force=False):
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
        if self.native_sampler is not None:
            native_devices = self.native_sampler.enumerate_devices(
                excluded_xinput_slot=s2p_slot
            )
        else:
            native_devices = self.backend.enumerate_devices(
                excluded_xinput_slot=s2p_slot
            )
        devices.extend(native_devices)
        devices = [
            replace(
                device,
                name=localized_device_name(device, self.gui.tr),
            )
            if device.name_translation_key else device
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

    def _configure_native_sampler(self):
        if self.native_sampler is None:
            return
        device = self._selected_device()
        self.native_sampler.set_device(
            device
            if device is not None and device.kind in {"xinput", "winmm"}
            else None
        )

    def _sync_connection_status(self, sample):
        """Mirror the settings UI's connection summary in the tester header."""
        device = self._selected_device()
        if device is not None and device.kind != "s2p":
            connected = sample is not None
            self.status_var.set(
                self.gui.tr("● 已連線" if connected else "● 未連線")
            )
            self.status_label.configure(
                foreground="#138A36" if connected else "#777777"
            )
            return
        source_label = getattr(self.gui, "controller_status_label", None)
        if source_label is not None:
            try:
                text = str(source_label.cget("text") or "")
                color = str(source_label.cget("fg") or "#777777")
                if text:
                    self.status_var.set(text)
                    self.status_label.configure(foreground=color)
                    return
            except (AttributeError, tk.TclError):
                pass
        text, color = read_connection_status_summary(self.gui.tr)
        if text:
            self.status_var.set(text)
            self.status_label.configure(foreground=color)
            return
        connected = sample is not None
        self.status_var.set(
            self.gui.tr("● 已連線" if connected else "● 未連線")
        )
        self.status_label.configure(
            foreground="#138A36" if connected else "#777777"
        )

    def _update_draw_fps(self):
        if len(self._draw_times) < 2:
            self.draw_fps_var.set("— FPS")
            self.draw_fps_label.configure(foreground="#777777")
            return
        elapsed = self._draw_times[-1] - self._draw_times[0]
        if elapsed <= 0.0:
            return
        fps = (len(self._draw_times) - 1) / elapsed
        self.draw_fps_var.set(f"{fps:.0f} FPS")
        self.draw_fps_label.configure(foreground="#777777")

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
        return {
            str(button): "原始輸入"
            for button in sample.get("buttons", ())
        }

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
        self._cancel_raw_hid_countdown()
        self.raw_hid_probe.stop()
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
