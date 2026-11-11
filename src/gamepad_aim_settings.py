"""Round, appearance, and feedback settings for Gamepad Tester."""

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import secrets
import tkinter as tk
from tkinter import colorchooser, ttk

from config_utils import atomic_write_bytes, config_file_lock
from dialog_layout import center_child_dialog
from gamepad_aim_display import available_resolutions, highest_resolution
from gamepad_aim_model import TIMED_MODES, TRACKING_MODES
from readable_combobox import ReadableCombobox
from tooltip_layout import HoverTip


LIMITS = {
    "target_count": (1, 100),
    "target_seconds": (0, 300),
    "tracking_seconds": (1, 300),
    "timed_shooting_seconds": (1, 300),
    "seed": (0, 2**32 - 1),
    "depth": (0, 100),
    "rumble": (0, 100),
    "horizontal_spread": (25, 180),
    "vertical_spread": (25, 180),
    "reticle_size": (6, 48),
    "target_size_2d": (2, 20),
}
COLORS = {
    "reticle_color": "準心顏色",
    "reticle_on_target_color": "準心對靶色",
    "target_color": "靶顏色",
    "target_on_target_color": "跟槍靶變色",
}
WINDOW_SIZES = ("800x450", "960x540", "1280x720", "1600x900", "1920x1080", "2560x1440", "3840x2160")


def _valid_resolution(value):
    if not isinstance(value, str) or not re.fullmatch(r"[1-9][0-9]{2,4}x[1-9][0-9]{2,4}", value):
        return False
    width, height = map(int, value.split("x"))
    return 640 <= width <= 16384 and 360 <= height <= 16384


@dataclass(frozen=True)
class AimSettings:
    target_count: int = 10
    target_seconds: int = 0
    tracking_seconds: int = 30
    seed: int = 0
    depth: int = 0
    horizontal_spread: int = 100
    vertical_spread: int = 100
    rumble: int = 35
    sound: bool = True
    reticle_size: int = 14
    target_size_2d: int = 7
    reticle_color: str = "#edf4fc"
    target_color: str = "#57dab1"
    window_size: str = ""
    fullscreen: bool = True
    reticle_on_target_color: str = "#ff456f"
    target_on_target_color: str = "#f3d678"
    timed_shooting_seconds: int = 30
    fullscreen_size: str = ""
    depth_shading: bool = True

    @classmethod
    def load(cls, path):
        try:
            with config_file_lock():
                payload = Path(path).read_text(encoding="utf-8")
            data = json.loads(payload)
        except (OSError, ValueError, RecursionError):
            return cls()
        if not isinstance(data, dict):
            return cls()
        values = {}
        for key, (low, high) in LIMITS.items():
            value = data.get(key)
            if type(value) is int and low <= value <= high:
                values[key] = value
        for key in COLORS:
            value = data.get(key)
            if isinstance(value, str) and re.fullmatch(r"#[0-9a-fA-F]{6}", value):
                values[key] = value.lower()
        for key in ("sound", "fullscreen", "depth_shading"):
            if type(data.get(key)) is bool:
                values[key] = data[key]
        if _valid_resolution(data.get("window_size")):
            values["window_size"] = data["window_size"]
        if _valid_resolution(data.get("fullscreen_size")):
            values["fullscreen_size"] = data["fullscreen_size"]
        elif "fullscreen_size" not in data and values.get("fullscreen") and "window_size" in values:
            values["fullscreen_size"] = values["window_size"]
        return cls(**values)

    def save(self, path):
        payload = json.dumps(asdict(self), indent=2).encode("utf-8")
        with config_file_lock():
            atomic_write_bytes(payload, path)

    def basis(self, mode, size):
        shooting = mode in {"shooting", "timed-shooting"}
        timed = mode in TIMED_MODES
        tracking = mode in TRACKING_MODES
        seconds = self.timed_shooting_seconds if timed else self.target_seconds
        if tracking:
            seconds = self.tracking_seconds
        version = 4 if timed else 1
        if mode == "timed-shooting":
            version = 5  # Overlapping balls score by ray intersection depth.
        basis = {
            "version": version,
            "mode": mode,
            "targets": 0 if timed or tracking else self.target_count,
            "seconds": seconds,
            "seed": self.seed,
            "depth": self.depth if shooting else 0,
            "viewport": list(size),
            "reticle": "" if mode.endswith("cursor-mouse") else self.reticle_color,
            "target_color": self.target_color,
            "rumble": self.rumble,
            "sound": self.sound,
        }
        if shooting:
            for key in ("horizontal_spread", "vertical_spread"):
                value = getattr(self, key)
                if value != 100:
                    basis[key] = value
        if not mode.endswith("cursor-mouse") and self.reticle_size != 14:
            basis["reticle_size"] = self.reticle_size
        if mode.startswith(("cursor-", "timed-cursor")) and self.target_size_2d != 7:
            basis["target_size_2d"] = self.target_size_2d
        return basis


def show_aim_settings(parent, tr, current, apply):
    dialog = tk.Toplevel(parent)
    dialog.withdraw()
    dialog.title(tr("測試設定"))
    dialog.transient(parent)
    dialog.resizable(False, False)
    body = ttk.Frame(dialog, padding=(10, 12))
    body.pack(fill="both", expand=True)
    body.columnconfigure(1, weight=1)
    values = {}
    inputs = (
        ("target_count", "點靶數量"),
        ("target_seconds", "點靶秒數（0 為不限時）"),
        ("tracking_seconds", "跟槍秒數"),
        ("seed", "出靶種子（0 為固定配置）"),
        ("timed_shooting_seconds", "限時點靶秒數"),
    )
    first = None
    for row, (key, label) in enumerate(inputs):
        ttk.Label(body, text=tr(label)).grid(row=row, column=0, sticky="w", padx=(0, 8), pady=4)
        values[key] = tk.StringVar(dialog, str(getattr(current, key)))
        entry = ttk.Spinbox(body, from_=LIMITS[key][0], to=LIMITS[key][1],
                            textvariable=values[key], width=10)
        entry.grid(row=row, column=1, sticky="ew", pady=4)
        if first is None:
            first = entry
    ttk.Button(
        body, text="↻", width=3,
        command=lambda: values["seed"].set(str(secrets.randbelow(LIMITS["seed"][1]) + 1)),
    ).grid(row=3, column=2, padx=(6, 0))

    sliders = (
        ("depth", "3D 點靶遠近差異"),
        ("horizontal_spread", "3D 點靶水平範圍"),
        ("vertical_spread", "3D 點靶垂直範圍"),
        ("reticle_size", "準心大小"),
        ("target_size_2d", "2D 靶心大小（畫面短邊）"),
        ("rumble", "震動強度"),
    )
    slider_texts = {}
    for row, (key, label) in enumerate(sliders, start=len(inputs)):
        ttk.Label(body, text=tr(label)).grid(row=row, column=0, sticky="w", pady=4)
        values[key] = tk.DoubleVar(dialog, getattr(current, key))
        suffix = " px" if key == "reticle_size" else "%"
        reading = tk.StringVar(dialog, f"{int(values[key].get())}{suffix}")
        slider_texts[key] = reading, suffix
        ttk.Scale(
            body, from_=LIMITS[key][0], to=LIMITS[key][1], variable=values[key],
            command=lambda value, text=reading, unit=suffix: text.set(f"{round(float(value))}{unit}"),
        ).grid(row=row, column=1, sticky="ew", pady=4)
        ttk.Label(body, textvariable=reading, width=6).grid(row=row, column=2, sticky="w", padx=(6, 0))

    def set_color(key, button, color):
        values[key].set(color.lower())
        button.configure(background=color, activebackground=color)

    def choose_color(key, button):
        _rgb, color = colorchooser.askcolor(values[key].get(), parent=dialog)
        if color:
            set_color(key, button, color)

    swatches = {}
    colors_row = len(inputs) + len(sliders)
    for row, (key, label) in enumerate(COLORS.items(), start=colors_row):
        ttk.Label(body, text=tr(label)).grid(row=row, column=0, sticky="w", pady=4)
        color = getattr(current, key)
        values[key] = tk.StringVar(dialog, color)
        swatch = tk.Button(body, textvariable=values[key], background=color,
                           activebackground=color, width=10)
        swatches[key] = swatch
        swatch.configure(command=lambda field=key, widget=swatch: choose_color(field, widget))
        swatch.grid(row=row, column=1, sticky="ew", pady=4)

    display = ttk.Frame(body)
    display_row = colors_row + len(COLORS)
    display.grid(row=display_row, column=0, columnspan=3, sticky="w", pady=4)
    ttk.Label(display, text=tr("解析度")).pack(side="left", padx=(0, 8))
    auto_size = tr("自動（最高）")
    window_size = tk.StringVar(dialog, current.window_size or auto_size)
    fullscreen_size = tk.StringVar(dialog, current.fullscreen_size or auto_size)
    fullscreen = tk.BooleanVar(dialog, current.fullscreen)
    size_picker = ReadableCombobox(display, textvariable=window_size, width=11, state="readonly")
    size_picker.pack(side="left")
    try:
        fullscreen_sizes = tuple(size for size in available_resolutions(parent.winfo_id()) if _valid_resolution(size))
    except OSError:
        fullscreen_sizes = ()
    upper_size = highest_resolution(fullscreen_sizes)
    if upper_size:
        max_width, max_height = map(int, upper_size.split("x"))
    else:
        max_width, max_height = parent.winfo_screenwidth(), parent.winfo_screenheight()
    windowed_sizes = []
    for size in dict.fromkeys((*WINDOW_SIZES, current.window_size)):
        if _valid_resolution(size):
            width, height = map(int, size.split("x"))
            if width <= max_width and height <= max_height:
                windowed_sizes.append(size)

    def sync_size_picker():
        if fullscreen.get():
            size_picker.configure(
                textvariable=fullscreen_size, values=(auto_size, *fullscreen_sizes),
            )
        else:
            size_picker.configure(textvariable=window_size, values=(auto_size, *windowed_sizes))

    fullscreen_option = ttk.Checkbutton(
        display, text=tr("全螢幕"), variable=fullscreen, command=sync_size_picker,
    )
    fullscreen_option.pack(side="left", padx=(8, 0))
    HoverTip(fullscreen_option, tr("全螢幕會暫時切換螢幕解析度。關閉測試後會還原。"))
    sync_size_picker()

    sound = tk.BooleanVar(dialog, current.sound)
    ttk.Checkbutton(body, text=tr("命中與跟靶音效"), variable=sound).grid(
        row=display_row + 1, column=0, sticky="w", pady=6,
    )
    depth_shading = tk.BooleanVar(dialog, current.depth_shading)
    ttk.Checkbutton(body, text=tr("加深遠近效果"), variable=depth_shading).grid(
        row=display_row + 1, column=1, columnspan=2, sticky="w", pady=6,
    )
    error = tk.StringVar(dialog)
    ttk.Label(body, textvariable=error, foreground="#b91c1c", wraplength=300).grid(
        row=display_row + 2, column=0, columnspan=3, sticky="w",
    )

    def restore_defaults():
        defaults = AimSettings()
        for key in LIMITS:
            values[key].set(getattr(defaults, key))
        for key, (reading, suffix) in slider_texts.items():
            reading.set(f"{getattr(defaults, key)}{suffix}")
        for key, button in swatches.items():
            set_color(key, button, getattr(defaults, key))
        sound.set(defaults.sound)
        depth_shading.set(defaults.depth_shading)
        window_size.set(defaults.window_size or auto_size)
        fullscreen_size.set(defaults.fullscreen_size or auto_size)
        fullscreen.set(defaults.fullscreen)
        sync_size_picker()
        error.set("")

    def save(_event=None):
        selected_window_size = window_size.get()
        if selected_window_size == auto_size:
            selected_window_size = ""
        selected_fullscreen_size = fullscreen_size.get()
        if selected_fullscreen_size == auto_size:
            selected_fullscreen_size = ""
        if (fullscreen.get() and selected_fullscreen_size
                and selected_fullscreen_size not in fullscreen_sizes):
            error.set(tr("這台螢幕不支援 {size} 的全螢幕解析度。").format(size=selected_fullscreen_size))
            return
        options = {key: values[key].get() for key in COLORS}
        for key, (low, high) in LIMITS.items():
            try:
                value = round(values[key].get()) if key in slider_texts else int(values[key].get())
            except (ValueError, OverflowError, tk.TclError):
                value = low - 1
            if not low <= value <= high:
                label = dict(inputs + sliders)[key]
                error.set(tr("{label}請輸入 {low} 到 {high} 的整數。").format(
                    label=tr(label), low=low, high=high,
                ))
                return
            options[key] = value
        try:
            apply(AimSettings(
                **options, sound=sound.get(), window_size=selected_window_size, fullscreen=fullscreen.get(),
                fullscreen_size=selected_fullscreen_size, depth_shading=depth_shading.get(),
            ))
        except OSError:
            error.set(tr("無法儲存測試設定。"))
            return
        dialog.destroy()

    footer = ttk.Frame(body)
    footer.grid(row=display_row + 3, column=0, columnspan=3, sticky="ew", pady=(8, 0))
    ttk.Button(footer, text=tr("恢復預設"), command=restore_defaults).pack(side="left")
    ttk.Button(footer, text=tr("儲存"), command=save).pack(side="right")
    ttk.Button(footer, text=tr("取消"), command=dialog.destroy).pack(side="right", padx=4)
    dialog.bind("<Return>", save)
    dialog.bind("<Escape>", lambda _event: dialog.destroy())
    center_child_dialog(dialog, parent.winfo_toplevel())
    dialog.grab_set()
    first.focus_set()
    return dialog
