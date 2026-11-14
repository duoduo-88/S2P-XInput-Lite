"""Dialogs for testing, calculating, and applying gyro output compensation."""

import time
from configparser import Error as ConfigError
import tkinter as tk
from tkinter import font as tkfont, ttk

from dialog_layout import center_child_dialog
from gamepad_aim_model import DEFAULT_STICK_TURN_SPEED
from gamepad_aim_settings import LIMITS, TURN_SPEED_FIELDS
from gyro_compensation import DEFAULT_COMPENSATION, suggested_axis_ratio
from runtime_status import settings_reload_result
from settings_schema import GYRO_RATIO_MAX, GYRO_RATIO_MIN


AXES = (("X", "X（水平）"), ("Y", "Y（垂直）"))
PREVIEW_OUTPUT = 0.20
PREVIEW_SECONDS = 0.35  # At 1080°/s, the turn stays within the camera's ±85° pitch limit.


def _dialog(owner, title):
    window = tk.Toplevel(owner)
    window.title(title)
    window.attributes("-topmost", True)
    window.resizable(False, False)
    window.bind("<Escape>", lambda _event: window.destroy())
    body = ttk.Frame(window, padding=12)
    body.pack(fill="both", expand=True)
    return window, body


def _trace_dialog_variable(window, variable, callback):
    trace = variable.trace_add("write", callback)

    def remove_trace(event):
        # Tcl variables outlive their widgets unless their traces are released.
        if event.widget is window:
            variable.trace_remove("write", trace)

    window.bind("<Destroy>", remove_trace, add="+")


def _suggestion(tr, axis, ratio):
    if GYRO_RATIO_MIN <= ratio <= GYRO_RATIO_MAX:
        return tr("建議 {axis} 補償倍率 {value:.2f}").format(axis=axis, value=ratio)
    return tr("建議 {axis} 補償倍率 {value:.2f}，超出 {low:.2f}–{high:.2f} 的設定範圍。").format(
        axis=axis, value=ratio, low=GYRO_RATIO_MIN, high=GYRO_RATIO_MAX,
    )


def _multiplier_range_message(tr):
    return tr("補償倍率須介於 {low:.2f} 和 {high:.2f}。").format(low=GYRO_RATIO_MIN, high=GYRO_RATIO_MAX)


class GyroCompensationCalculator:
    def __init__(self, owner, tr, open_gyro_settings=None, *, multipliers=None, saved_inputs=None):
        self.window, body = _dialog(owner, tr("陀螺儀補償計算"))
        self.window.withdraw()
        selected_axis, axis_inputs = saved_inputs if saved_inputs is not None else ("X", {})
        multipliers = DEFAULT_COMPENSATION if multipliers is None else multipliers
        self.axis = tk.StringVar(self.window, selected_axis)
        self.inputs = {
            axis: tuple(tk.StringVar(self.window, value) for value in (
                f"{multipliers[axis]:.2f}", *axis_inputs.get(axis, ("", "")),
            ))
            for axis, _label in AXES
        }
        axes = ttk.Frame(body)
        axes.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        for axis, label in AXES:
            ttk.Radiobutton(axes, text=tr(label), value=axis, variable=self.axis).pack(side="left", padx=(0, 10))
        ttk.Label(body, text=tr("目前補償倍率")).grid(row=1, column=0, sticky="w", pady=3)
        multiplier = ttk.Entry(body, width=9, state="readonly")
        multiplier.grid(row=1, column=1, sticky="e", pady=3)
        self.entries = [multiplier]
        for row, title in enumerate(("調整視角速度前", "調整視角速度後"), 2):
            section = ttk.LabelFrame(body, text=tr(title), padding=(8, 4))
            section.grid(row=row, column=0, columnspan=2, sticky="ew", pady=(4, 0))
            ttk.Label(section, text=tr("耗時（秒）")).pack(side="left")
            entry = ttk.Entry(section, width=9)
            entry.pack(side="right", padx=(8, 0))
            self.entries.append(entry)
        ttk.Label(body, text=tr(
            "1. 選 X 或 Y，確認自動帶入的補償倍率。\n"
            "2. 改遊戲視角速度前後，用相同搖桿推幅\n"
            "   從同一地標轉到另一地標，錄影並填入兩次耗時。\n"
            "3. 到 S2P 的「補償設定」輸入建議倍率。"
        ), justify="left").grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 4))
        self.result = tk.StringVar(self.window)
        self.invalidated = False
        feedback = ttk.Label(body, textvariable=self.result, wraplength=self.window.winfo_pixels("210p"))

        def show_feedback(*_args):
            if self.result.get():
                feedback.grid(row=5, column=0, columnspan=2, sticky="w")
            else:
                feedback.grid_remove()

        def refresh(*_args):
            if self.invalidated:
                return
            axis = self.axis.get()
            original, old, new = (item.get() for item in self.inputs[axis])
            try:
                multiplier = float(original)
                if not GYRO_RATIO_MIN <= multiplier <= GYRO_RATIO_MAX:
                    text = _multiplier_range_message(tr)
                else:
                    ratio = suggested_axis_ratio(multiplier, float(old), float(new), elapsed_time=True)
                    text = _suggestion(tr, axis, ratio)
            except ValueError:
                text = tr("請輸入大於 0 的倍率及耗時。") if all((original, old, new)) else ""
            self.result.set(text)

        def switch_axis(*_args):
            for entry, value in zip(self.entries, self.inputs[self.axis.get()]):
                entry.configure(textvariable=value)
            refresh()

        for values in self.inputs.values():
            for value in values:
                _trace_dialog_variable(self.window, value, refresh)
        _trace_dialog_variable(self.window, self.result, show_feedback)
        _trace_dialog_variable(self.window, self.axis, switch_axis)
        switch_axis()
        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e", pady=(8, 0))
        self.settings_button = ttk.Button(buttons, text=tr("開啟 S2P 補償設定"), command=open_gyro_settings)
        self.settings_button.pack(side="left", padx=(0, 6))
        if open_gyro_settings is None:
            self.settings_button.state(["disabled"])
        ttk.Button(buttons, text=tr("關閉"), command=self.window.destroy).pack(side="left")
        center_child_dialog(self.window, owner.winfo_toplevel())

    def invalidate(self, message):
        self.invalidated = True
        self.result.set(message)
        self.settings_button.state(["disabled"])

    def snapshot_inputs(self):
        return self.axis.get(), {
            axis: tuple(value.get() for value in values[1:]) for axis, values in self.inputs.items()
        }


class GyroCompensationSettings:
    def __init__(self, owner, tr, current, apply, *, sync_saved=None):
        self.window, body = _dialog(owner, tr("陀螺儀補償設定"))
        self.window.withdraw()
        self.compensation = {axis: tk.StringVar(self.window, current[axis]) for axis, _label in AXES}
        self.sliders = {}
        body.columnconfigure(1, weight=1)
        for row, (axis, label) in enumerate(AXES):
            value = self.compensation[axis]
            slider_value = tk.DoubleVar(self.window, GYRO_RATIO_MIN)
            ttk.Label(body, text=tr(label)).grid(row=row, column=0, sticky="w", pady=3)
            slider = self.sliders[axis] = ttk.Scale(
                body, from_=GYRO_RATIO_MIN, to=GYRO_RATIO_MAX, variable=slider_value,
                command=lambda position, field=value: field.set(f"{float(position):.2f}"),
            )
            slider.grid(row=row, column=1, sticky="ew", padx=(12, 4))
            entry = ttk.Spinbox(body, from_=GYRO_RATIO_MIN, to=GYRO_RATIO_MAX,
                                increment=0.01, format="%.2f", width=9, textvariable=value)
            entry.grid(row=row, column=2, padx=(8, 0), pady=3)

            def update_slider(*_args, field=value, position_var=slider_value):
                try:
                    position = float(field.get())
                except ValueError:
                    return
                if GYRO_RATIO_MIN <= position <= GYRO_RATIO_MAX:
                    position_var.set(position)

            _trace_dialog_variable(self.window, value, update_slider)
            update_slider()
        ttk.Label(body, text=tr(
            "調高遊戲視角速度後，如果陀螺儀微瞄變得太敏感，\n"
            "可降低對應的倍率。X 調水平，Y 調垂直。\n"
            "維持 1.00 會保留原本的陀螺儀輸出。\n"
            "例如 X 設為 0.50，水平輸出在反死區前會減半。\n"
            "倍率只作用於陀螺儀，實體搖桿維持原本的輸出。"
        ), justify="left").grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        ttk.Label(body, text=tr("按確定後，請在主視窗按儲存／套用。"), justify="left").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(6, 0),
        )
        self.status = tk.StringVar(self.window)
        feedback = ttk.Label(body, textvariable=self.status, wraplength=self.window.winfo_pixels("190p"))

        def clear_feedback(*_args):
            self.status.set("")
            feedback.grid_remove()

        for value in self.compensation.values():
            _trace_dialog_variable(self.window, value, clear_feedback)

        def save(_event=None):
            if sync_saved is not None:
                sync_saved()
            try:
                values = {axis: float(value.get()) for axis, value in self.compensation.items()}
                if not all(GYRO_RATIO_MIN <= value <= GYRO_RATIO_MAX for value in values.values()):
                    raise ValueError
            except ValueError:
                self.status.set(_multiplier_range_message(tr))
                feedback.grid(row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
                return
            apply({axis: f"{value:.2f}" for axis, value in values.items()})
            self.close()

        def restore_defaults():
            for value in self.compensation.values():
                value.set("1.00")

        buttons = ttk.Frame(body)
        buttons.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        buttons.columnconfigure(1, weight=1)
        self.defaults_button = ttk.Button(buttons, text=tr("還原預設"), command=restore_defaults)
        self.defaults_button.grid(row=0, column=0, sticky="w")
        self.apply_button = ttk.Button(buttons, text=tr("確定"), command=save)
        self.apply_button.grid(row=0, column=2, padx=(6, 0))
        self.cancel_button = ttk.Button(buttons, text=tr("取消"), command=self.close)
        self.cancel_button.grid(row=0, column=3, padx=(6, 0))
        self.window.bind("<Return>", save)
        self.window.minsize(self.window.winfo_pixels("230p"), 1)
        center_child_dialog(self.window, owner.winfo_toplevel())

    def close(self):
        self.window.destroy()


class GyroCompensationPreview:
    def __init__(self, owner, tr, settings, prepare, turn, close, open_gyro_settings, *, multipliers=None,
                 on_speed_changed, apply_measurement=None, read_reload_status=None):
        self.tr = tr
        self.prepare = prepare
        self.turn = turn
        self.window, body = _dialog(owner, tr("3D 陀螺儀補償預覽"))
        self.window.bind("<Escape>", close)
        self.window.protocol("WM_DELETE_WINDOW", close)
        self.window.bind("<Destroy>", self._destroyed, add="+")
        self.job = None
        self.reload_job = None
        self._reload_feedback = ""
        self.measurement = None
        self.original = DEFAULT_STICK_TURN_SPEED
        self.original_multipliers = dict(DEFAULT_COMPENSATION if multipliers is None else multipliers)
        self.speed_source = tk.StringVar(self.window, "original")
        self.on_speed_changed = on_speed_changed
        self.apply_measurement = apply_measurement
        self.read_reload_status = read_reload_status
        self.adjusted = []
        self._adjusted_speeds = []
        self.speed_options = []
        self.status = tk.StringVar(self.window)
        self.result = tk.StringVar(self.window)
        title_font = tkfont.nametofont("TkDefaultFont", root=self.window)
        self.window.minsize(title_font.measure(self.window.title()) + self.window.winfo_pixels("110p"), 1)
        saved = ttk.LabelFrame(body, text=tr("已儲存的補償倍率"), padding=(8, 4))
        saved.pack(fill="x", pady=(0, 8))
        self.current_multiplier_text = {axis: tk.StringVar(self.window) for axis, _ in AXES}
        self._show_multipliers(self.original_multipliers)
        for variable in self.current_multiplier_text.values():
            ttk.Label(saved, textvariable=variable).pack(side="left", padx=(0, 12))
        for title, editable in (("原始視角速度", False), ("調整後視角速度", True)):
            frame = ttk.LabelFrame(body, padding=(8, 4))
            option = ttk.Radiobutton(
                frame, text=tr(title), variable=self.speed_source,
                value="adjusted" if editable else "original",
            )
            frame.configure(labelwidget=option)
            self.speed_options.append(option)
            frame.pack(fill="x", pady=(0, 8))
            for row, (key, label) in enumerate(TURN_SPEED_FIELDS):
                ttk.Label(frame, text=tr(label)).grid(row=row, column=0, sticky="w", pady=3)
                value = self.original[row]
                if editable:
                    low, high = LIMITS[key]
                    speed = tk.StringVar(self.window, str(getattr(settings, key)))
                    _trace_dialog_variable(self.window, speed, self._adjusted_speed_changed)
                    self._adjusted_speeds.append(speed)
                    control = ttk.Spinbox(frame, from_=low, to=high, width=7, textvariable=speed)
                    self.adjusted.append(control)
                else:
                    control = ttk.Label(frame, text=str(value))
                control.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
        buttons = ttk.Frame(body)
        buttons.columnconfigure((0, 1), weight=1, uniform="measurement")
        status_label = ttk.Label(body, textvariable=self.status, justify="left",
                                 wraplength=self.window.winfo_pixels("220p"))
        result_label = ttk.Label(body, textvariable=self.result, justify="left",
                                 wraplength=self.window.winfo_pixels("220p"))
        status_label.pack(anchor="w")
        result_label.pack(anchor="w")
        buttons.pack(fill="x", pady=(8, 0))

        def refresh_feedback(*_args):
            for label, variable in ((status_label, self.status), (result_label, self.result)):
                if variable.get():
                    label.pack(before=buttons, anchor="w", pady=(6, 0))
                else:
                    label.pack_forget()

        for variable in (self.status, self.result):
            _trace_dialog_variable(self.window, variable, refresh_feedback)
        refresh_feedback()
        self.measure_button = ttk.Button(buttons, text=tr("量測"), command=self.start)
        self.measure_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.close_button = ttk.Button(buttons, text=tr("關閉"), command=close)
        self.close_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        self.settings_button = ttk.Button(buttons, text=tr("開啟 S2P 陀螺儀設定"), command=open_gyro_settings)
        self.settings_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        if open_gyro_settings is None:
            self.settings_button.state(["disabled"])
        _trace_dialog_variable(self.window, self.speed_source, self.apply_selected_speeds)

    def _adjusted_speed_changed(self, *_args):
        if self.reload_job is None:
            self.status.set(self._reload_feedback)
        self.result.set("")
        self.apply_selected_speeds()

    def apply_selected_speeds(self, *_args):
        speeds = self.original if self.speed_source.get() == "original" else self._read_adjusted_speeds(show_error=False)
        if speeds is not None:
            self.on_speed_changed(speeds)

    def start(self):
        if self.measurement is not None or self.reload_job is not None:
            return
        adjusted = self._read_adjusted_speeds()
        if adjusted is None:
            return
        self._reload_feedback = ""
        self.result.set("")
        self.angles = []
        self.measurement = tuple(zip(("X", "Y", "X", "Y"), (*self.original, *adjusted)))
        self._set_running(True)
        self._start_turn()

    def _show_multipliers(self, ratios):
        for axis, label in AXES:
            self.current_multiplier_text[axis].set(f"{self.tr(label)}  {ratios[axis]:.2f}")

    def _read_adjusted_speeds(self, *, show_error=True):
        adjusted = []
        for value, (key, label) in zip(self._adjusted_speeds, TURN_SPEED_FIELDS):
            low, high = LIMITS[key]
            try:
                speed = int(value.get())
            except ValueError:
                speed = low - 1
            if not low <= speed <= high:
                if show_error:
                    self.result.set("")
                    self.status.set(self.tr("{label}請輸入 {low} 到 {high} 的整數。").format(
                        label=self.tr(label), low=low, high=high,
                    ))
                return
            adjusted.append(speed)
        return tuple(adjusted)

    def _start_turn(self):
        self.job = None
        if self.measurement is None:
            return
        if not self.prepare():
            self.cancel()
            return
        if self.measurement is None:
            return
        index = len(self.angles)
        axis, _speed = self.measurement[index]
        label = self.tr("量測原始 {axis}…" if index < 2 else "量測調整後 {axis}…")
        self.status.set(label.format(axis=axis))
        self.angle = 0.0
        self.previous_at = time.perf_counter()
        self.end_at = self.previous_at + PREVIEW_SECONDS
        self.job = self.window.after(8, self._tick)

    def _tick(self):
        self.job = None
        axis, speed = self.measurement[len(self.angles)]
        now = min(time.perf_counter(), self.end_at)
        angle = self.turn(axis, speed * PREVIEW_OUTPUT * (now - self.previous_at))
        if angle is None:
            self.cancel()
            return
        self.angle += angle
        self.previous_at = now
        if now < self.end_at:
            self.job = self.window.after(8, self._tick)
            return
        self.angles.append(self.angle)
        if len(self.angles) < len(self.measurement):
            self.job = self.window.after(250, self._start_turn)
            return
        self.measurement = None
        self._set_running(False)
        ratios = {
            axis: suggested_axis_ratio(self.original_multipliers[axis], old, new)
            for (axis, _label), old, new in zip(AXES, self.angles[:2], self.angles[2:])
        }
        self.result.set("\n".join(_suggestion(self.tr, axis, ratio) for axis, ratio in ratios.items()))
        if self.apply_measurement is None:
            self.status.set(self.tr("量測完成。"))
        elif not all(GYRO_RATIO_MIN <= ratio <= GYRO_RATIO_MAX for ratio in ratios.values()):
            self.status.set(self.tr("建議倍率超出設定範圍，未寫入。"))
        else:
            try:
                request_id = self.apply_measurement(ratios)
            except (OSError, ValueError, ConfigError) as exc:
                self.status.set(self.tr("無法寫入補償倍率。") + f"\n{exc}")
            else:
                self._show_multipliers(ratios)
                if request_id and self.read_reload_status is not None:
                    self.status.set(self.tr("補償倍率已儲存，等待連接程式套用。"))
                    self.measure_button.state(["disabled"])
                    self.reload_deadline = time.perf_counter() + 5.0
                    self.reload_job = self.window.after(100, self._poll_reload, request_id)
                else:
                    self.status.set(self.tr("量測完成。補償倍率已寫入 S2P 設定。"))

    def _poll_reload(self, request_id):
        self.reload_job = None
        result = settings_reload_result(self.read_reload_status(), request_id) or {}
        state = result.get("state")
        if state == "success":
            text = self.tr("補償倍率已儲存並套用至目前連線。")
        elif state == "failed":
            text = self.tr("補償倍率即時套用失敗。")
            detail = result.get("message")
            if detail:
                text += f"\n{detail}"
        elif time.perf_counter() >= self.reload_deadline:
            text = self.tr("補償倍率已儲存，尚未確認即時套用結果。")
        else:
            self.reload_job = self.window.after(100, self._poll_reload, request_id)
            return
        self._reload_feedback = text
        self.status.set(text)
        self.measure_button.state(["!disabled"])

    def _set_running(self, running):
        for widget in (*self.adjusted, *self.speed_options, self.measure_button):
            widget.state(["disabled" if running else "!disabled"])

    def cancel(self):
        if self.measurement is not None:
            self._cancel_job()
            self._set_running(False)
            self.status.set(self.tr("量測已取消。"))

    def _cancel_job(self):
        if self.job is not None:
            self.window.after_cancel(self.job)
            self.job = None
        self.measurement = None

    def close(self):
        self.window.destroy()

    def _destroyed(self, event):
        if event.widget is self.window:
            self._cancel_job()
            if self.reload_job is not None:
                self.window.after_cancel(self.reload_job)
                self.reload_job = None
