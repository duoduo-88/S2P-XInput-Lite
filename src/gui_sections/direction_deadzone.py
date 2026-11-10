"""Small and editable radar views for four- and eight-way stick mapping."""

import math
import tkinter as tk
from tkinter import messagebox, ttk

from dialog_layout import center_child_dialog
from direction_deadzones import (
    CUSTOM_DEADZONE_KEY,
    DIRECTION_SECTORS,
    MAX_ZONE_PERCENT,
    clamp_zone,
    format_custom_zones,
    parse_custom_zones,
    sector_step,
    uniform_zones,
    zone_edges,
)
from settings_schema import default_section_values


_ARROWS = dict(zip(DIRECTION_SECTORS["8WAY"], "→↗↑↖←↙↓↘"))
_PARAMETERS = (
    ("direction_deadzone", "統一死區", "°", 1, 0, 20),
    ("trigger_threshold", "觸發", "%", 100, 10, 100),
    ("release_threshold", "放開", "%", 100, 0, 97),
)


def _number(value, fallback):
    try:
        value = float(value.get() if isinstance(value, tk.Variable) else value)
        return value if math.isfinite(value) else fallback
    except (TypeError, ValueError, tk.TclError):
        return fallback


def _point(center, radius, angle, shape=0):
    radians = math.radians(angle)
    x, y = math.cos(radians), math.sin(radians)
    scale = 1 - shape + shape / max(abs(x), abs(y))
    return center[0] + radius * x * scale, center[1] - radius * y * scale


def draw_radar(canvas, center, radius, mode, zones, trigger, release, shape, *, editor=False, active=None):
    canvas.delete("radar")
    tags = "radar"
    step = sector_step(mode)
    for index, (start, end) in enumerate(zone_edges(mode, zones)):
        angle = index * step + start
        if start < end:
            points = [*center]
            for sample in range(13):
                points.extend(_point(center, radius, angle + (end - start) * sample / 12, shape))
            canvas.create_polygon(*points, fill="#e99d9d" if active == index else "#f3caca", tags=tags)
        elif editor:
            canvas.create_line(*center, *_point(center, radius, angle, shape), fill="#b76060", dash=(3, 3), tags=tags)
    x, y = center
    canvas.create_line(x - radius, y, x + radius, y, fill="#b0b0b0", tags=tags)
    canvas.create_line(x, y - radius, x, y + radius, fill="#b0b0b0", tags=tags)
    for value, color, dash in ((trigger, "#2878c8", ()), (release, "#666666", (3, 2))):
        ring = radius * value
        canvas.create_oval(x - ring, y - ring, x + ring, y + ring, outline=color, width=2, dash=dash, tags=tags)
    outline = [coordinate for index in range(121) for coordinate in _point(center, radius, index * 3, shape)]
    canvas.create_line(*outline, fill="#333333", width=2, tags=tags)
    canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill="#333333", outline="", tags=tags)
    if editor:
        for index, direction in enumerate(DIRECTION_SECTORS[mode]):
            canvas.create_text(*_point(center, radius + 13, index * step, shape), text=_ARROWS[direction], tags=tags)


class DirectionDeadzonePreview(tk.Canvas):
    def __init__(self, parent, gui, side, settings, shape_var, *, saved_settings=None):
        super().__init__(
            parent, width=86, height=86, background="#ffffff", relief="raised", borderwidth=1,
            highlightthickness=1, highlightbackground="#a8a8a8", highlightcolor="#2878c8",
            cursor="hand2", takefocus=True,
        )
        self.gui, self.side, self.settings, self.shape_var = gui, side, settings, shape_var
        self._saved_settings = saved_settings
        self._pressed = False
        self._scale = self._value = None
        self._dialog = None
        self._tip = gui.bind_help_tooltip(self, "點擊放大方向死區雷達圖。")
        self.bind("<Enter>", lambda _: self.configure(highlightbackground="#2878c8"), add="+")
        self.bind("<Leave>", lambda _: self.configure(highlightbackground="#a8a8a8"), add="+")
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Return>", lambda _: self.open_editor())
        self.bind("<space>", lambda _: self.open_editor())
        self.bind("<Configure>", lambda _: self.redraw())

    def _press(self, _event):
        self._pressed = True
        self._tip.hide()
        self.configure(relief="sunken", background="#e9f0f8")
        self.move("radar", 1, 1)
        return "break"

    def _release(self, event):
        pressed, self._pressed = self._pressed, False
        self.configure(relief="raised", background="#ffffff")
        self.redraw()
        if pressed and 0 <= event.x < self.winfo_width() and 0 <= event.y < self.winfo_height():
            self.open_editor()
        return "break"

    def mode(self):
        return self.settings["mode"].get().strip()

    def saved_value(self, key):
        if self._saved_settings is not None:
            return self._saved_settings.get(key)
        variable = self.settings[key]
        binding = getattr(self.gui, "parameter_saved_registry", {}).get(str(variable))
        return binding[1] if binding else variable.get()

    def custom_zones(self, *, saved=False):
        value = self.saved_value(CUSTOM_DEADZONE_KEY) if saved else self.settings[CUSTOM_DEADZONE_KEY].get()
        try:
            return parse_custom_zones(value)
        except (ValueError, TypeError, OverflowError):
            return {}

    def attach_slider(self, scale, value):
        self._scale, self._value = scale, value

        def open_if_custom(_event):
            if scale.instate(["disabled"]):
                self.open_editor()
                return "break"

        scale.bind("<ButtonPress-1>", open_if_custom)

    def redraw(self):
        mode = self.mode()
        if mode not in DIRECTION_SECTORS:
            return
        width = max(self.winfo_width(), self.winfo_reqwidth())
        height = max(self.winfo_height(), self.winfo_reqheight())
        center = width / 2 + self._pressed, height / 2 + self._pressed
        custom = self.custom_zones().get(mode)
        uniform = max(0, min(20, _number(self.settings["direction_deadzone"], 5)))
        trigger = max(0, min(1, _number(self.settings["trigger_threshold"], 0.60)))
        release = max(0, min(trigger, _number(self.settings["release_threshold"], 0.50)))
        draw_radar(
            self, center, min(width, height) / 2 - 8, mode,
            custom or uniform_zones(mode, uniform), trigger, release, self.shape(),
        )
        if self._scale is not None:
            state = "disabled" if custom else "!disabled"
            self._scale.state([state])
            self._value.state([state])
            self._scale.configure(cursor="hand2" if custom else "")
            self._value.configure(
                text="SET" if custom else f"{uniform:.0f}°",
                cursor="hand2" if custom else "sb_h_double_arrow",
            )

    def shape(self):
        return max(0, min(10, _number(self.shape_var, 0))) / 10

    def open_editor(self):
        self._tip.hide()
        if self._dialog is not None and self._dialog.winfo_exists():
            self._dialog.lift()
        elif self.mode() in DIRECTION_SECTORS:
            editor = DirectionDeadzoneEditor(self)
            self._dialog = editor.window
            center_child_dialog(editor.window, self.winfo_toplevel())
            self.gui._bring_child_to_front(editor.window)
        return "break"


class DirectionDeadzoneEditor:
    def __init__(self, preview):
        self.preview = preview
        self.mode = preview.mode()
        self.step = sector_step(self.mode)
        self._initial_source = self._source_state()
        self.custom = self._initial_source[-1]
        defaults = default_section_values(f"stick_direction_{preview.side.lower()}")
        default_uniform = float(defaults["direction_deadzone"])
        saved_uniform = _number(preview.saved_value("direction_deadzone"), default_uniform)
        default_zones = uniform_zones(self.mode, default_uniform)
        saved_zones = preview.custom_zones(saved=True).get(self.mode) or uniform_zones(self.mode, saved_uniform)
        self.drag = None
        tr = self.tr = preview.gui.tr
        owner = preview.winfo_toplevel()
        self.window = window = tk.Toplevel(owner)
        window.withdraw()
        window.transient(owner)
        window.title(f"{tr('方向死區')} · {tr('左搖桿' if preview.side == 'LEFT' else '右搖桿')}")
        window.resizable(False, False)
        window.bind("<Escape>", lambda _: window.destroy())
        padding, radar_border = 10, 1
        frame = ttk.Frame(window, padding=padding)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame, text=tr("拖曳紅色死區以旋轉。外圈的 0–90% 是死區佔相鄰方向夾角的比例。"),
            wraplength=360,
        ).pack()
        size = min(460, max(360, round(360 * preview.gui.ui_scale)))
        self.center = size / 2, size / 2
        self.radius = size / 2 - 66
        self.canvas = tk.Canvas(frame, width=size, height=size, background="white", highlightthickness=radar_border)
        self.canvas.pack(pady=6)
        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._move_drag)
        self.canvas.bind("<ButtonRelease-1>", self._stop_drag)
        self.widths = []
        self._shown_widths = []
        self.field_positions = []
        for index in range(len(DIRECTION_SECTORS[self.mode])):
            field = ttk.Frame(self.canvas)
            value = tk.StringVar(field)
            self.widths.append(value)
            self._shown_widths.append(0.0)
            entry = ttk.Entry(field, width=5, justify="right", textvariable=value)
            entry.pack(side="left")
            ttk.Label(field, text="%").pack(side="left")
            entry.bind("<FocusOut>", lambda _, current=index: self._commit_width(current))
            entry.bind("<Return>", lambda _, current=index: self._commit_width(current))
            self._bind_numeric_entry(
                entry, value, 0, MAX_ZONE_PERCENT, lambda percent, current=index: self._set_width(current, percent),
                saved_zones[index][1], default_zones[index][1],
            )
            angle = (index + 0.5) * self.step
            px, py = _point(self.center, self.radius, angle, preview.shape())
            dx, dy = _point((px, py), 34, angle)
            self.field_positions.append((dx, dy))
            self.canvas.create_window(dx, dy, window=field, tags="entries")

        controls = ttk.Frame(frame)
        controls.pack(fill="x", padx=radar_border)
        self.params = {}
        for index, (key, label, unit, factor, minimum, maximum) in enumerate(_PARAMETERS):
            label_column = index * 2
            entry_column = label_column + 1
            controls.columnconfigure(entry_column, weight=1, uniform="deadzone_inputs")
            ttk.Label(controls, text=f"{tr(label)} ({unit})").grid(
                row=0, column=label_column, padx=(8 if index else 0, 3),
            )
            value = tk.StringVar(window, value=f"{_number(preview.settings[key], 0) * factor:g}")
            self.params[key] = value
            entry = ttk.Entry(controls, width=6, justify="right", textvariable=value)
            entry.grid(row=0, column=entry_column, sticky="ew")
            entry.bind("<FocusOut>", lambda _: self._redraw(sync_widths=True))
            entry.bind("<Return>", lambda _: self._redraw(sync_widths=True))
            default = float(defaults[key])
            self._bind_numeric_entry(
                entry, value, minimum, maximum, lambda number, current=key: self._set_parameter(current, number),
                _number(preview.saved_value(key), default) * factor, default * factor,
            )
            if index == 0:
                self.uniform_entry = entry

        actions = ttk.Frame(frame)
        actions.pack(fill="x", padx=radar_border, pady=(9, 0))
        self.uniform_button = ttk.Button(actions, text=tr("使用統一死區"), command=self._use_uniform)
        self.uniform_button.pack(side="left")
        ttk.Button(actions, text=tr("取消"), command=window.destroy).pack(side="right")
        ttk.Button(actions, text=tr("確定"), command=self._save).pack(side="right", padx=6)
        self._redraw(sync_widths=True)
        # Long labels can grow the controls' preferred width. Keep the radar
        # as the width reference and let the weighted input columns share the space.
        frame.update_idletasks()
        frame.configure(width=size + 2 * (padding + radar_border), height=frame.winfo_reqheight())
        frame.pack_propagate(False)

    def _bind_numeric_entry(self, entry, value, minimum, maximum, setter, saved, default):
        gui = self.preview.gui
        gui.bind_numeric_scrubber(entry, value, minimum, maximum, step=1, setter=setter, text_entry=True)
        entry.bind("<Button-3>", lambda event: gui.popup_parameter_reset_menu(event, setter, saved, default))

    def _set_parameter(self, key, number):
        if key == "trigger_threshold":
            release = self.params["release_threshold"]
            if _number(release, 0) > number - 3:
                release.set(f"{number - 3:g}")
        elif key == "release_threshold":
            trigger = _number(self.params["trigger_threshold"], 60)
            number = max(0, min(number, trigger - 3))
        self.params[key].set(f"{number:g}")
        self._redraw(sync_widths=key == "direction_deadzone")

    def _zones(self):
        uniform = max(0, min(20, _number(self.params["direction_deadzone"], 5)))
        return self.custom or uniform_zones(self.mode, uniform)

    def _source_state(self):
        preview = self.preview
        return (
            getattr(preview.gui, "active_profile", None),
            preview.mode(),
            tuple(preview.settings[key].get() for key, *_ in _PARAMETERS),
            tuple(_number(preview.saved_value(key), None) for key, *_ in _PARAMETERS),
            preview.custom_zones(saved=True).get(self.mode),
            preview.custom_zones().get(self.mode),
        )

    def _redraw(self, *, sync_widths=False):
        zones = self._zones()
        trigger = max(0, min(1, _number(self.params["trigger_threshold"], 60) / 100))
        release = max(0, min(trigger, _number(self.params["release_threshold"], 50) / 100))
        draw_radar(
            self.canvas, self.center, self.radius, self.mode, zones, trigger, release,
            self.preview.shape(), editor=True, active=self.drag[0] if self.drag else None,
        )
        for index, ((offset, _), field) in enumerate(zip(zones, self.field_positions)):
            angle = (index + 0.5) * self.step + offset
            edge = _point(self.center, self.radius, angle, self.preview.shape())
            self.canvas.create_line(*edge, *field, fill="#b76060", tags="radar")
        self.canvas.tag_raise("entries")
        if sync_widths:
            for index, (_, percent) in enumerate(zones):
                self._display_width(index, percent)
        self.uniform_entry.state(["disabled" if self.custom else "!disabled"])
        self.uniform_entry.configure(cursor="arrow" if self.custom else "sb_h_double_arrow")
        self.uniform_button.state(["!disabled" if self.custom else "disabled"])

    def _angle(self, event):
        dx, dy = event.x - self.center[0], self.center[1] - event.y
        return math.degrees(math.atan2(dy, dx)) % 360

    def _start_drag(self, event):
        angle = self._angle(event)
        index = int(angle // self.step) % len(DIRECTION_SECTORS[self.mode])
        zones = self._zones()
        start, end = zone_edges(self.mode, zones)[index]
        position = angle - index * self.step
        distance = math.hypot(event.x - self.center[0], event.y - self.center[1])
        edge = _point(self.center, self.radius, angle, self.preview.shape())
        if 9 < distance <= math.dist(self.center, edge) + 3 and start - 2 <= position <= end + 2:
            self.canvas.focus_set()
            self.drag = index, angle, zones[index][0]
            self._redraw()

    def _move_drag(self, event):
        if self.drag is None or math.dist(self.center, (event.x, event.y)) <= 9:
            return
        index, start, initial_offset = self.drag
        delta = math.remainder(self._angle(event) - start, 360)
        _, percent = self._zones()[index]
        self._change_zone(index, initial_offset + delta, percent)

    def _stop_drag(self, _event):
        self.drag = None
        self._redraw()

    def _change_zone(self, index, offset, percent):
        zones = list(self._zones())
        zone = clamp_zone(self.mode, offset, percent)
        if zone != zones[index]:
            zones[index] = zone
            self.custom = tuple(zones)
            self._redraw()

    def _commit_width(self, index):
        shown = self._shown_widths[index]
        percent = max(0, min(MAX_ZONE_PERCENT, _number(self.widths[index], shown)))
        if percent != shown:
            self._set_width(index, percent)
        else:
            self._display_width(index, self._zones()[index][1])

    def _set_width(self, index, percent):
        offset, _ = self._zones()[index]
        self._change_zone(index, offset, percent)
        self._display_width(index, self._zones()[index][1])

    def _display_width(self, index, percent):
        self._shown_widths[index] = round(percent, 2)
        self.widths[index].set(f"{percent:.2f}".rstrip("0").rstrip("."))

    def _use_uniform(self):
        self.custom = None
        self._redraw(sync_widths=True)

    def _save(self):
        if self._source_state() != self._initial_source:
            messagebox.showwarning(
                self.tr("方向死區"),
                self.tr("方向死區設定已變更。請重新開啟雷達圖再編輯。"),
                parent=self.window,
            )
            self.window.destroy()
            return
        for index in range(len(self.widths)):
            self._commit_width(index)
        uniform = _number(self.params["direction_deadzone"], -1)
        trigger = _number(self.params["trigger_threshold"], -1)
        release = _number(self.params["release_threshold"], -1)
        if not (0 <= uniform <= 20 and 10 <= trigger <= 100 and 0 <= release <= trigger - 3 + 1e-9):
            messagebox.showwarning(
                self.tr("方向死區"), self.tr("統一死區須為 0–20°。觸發須為 10–100%，放開須至少比觸發低 3 個百分點。"),
                parent=self.window,
            )
            return
        settings = self.preview.settings
        settings["direction_deadzone"].set(f"{uniform:g}")
        trigger_percent = math.floor(trigger + 0.5)
        release_percent = min(trigger_percent - 3, math.floor(release + 0.5))
        settings["trigger_threshold"].set(f"{trigger_percent / 100:.2f}")
        settings["release_threshold"].set(f"{release_percent / 100:.2f}")
        custom = self.preview.custom_zones()
        if self.custom:
            custom[self.mode] = self.custom
        else:
            custom.pop(self.mode, None)
        settings[CUSTOM_DEADZONE_KEY].set(format_custom_zones(custom))
        self.preview.redraw()
        self.window.destroy()
