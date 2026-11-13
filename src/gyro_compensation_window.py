"""Dialogs for testing, calculating, and applying gyro output compensation."""

import time
import tkinter as tk
from tkinter import ttk

from dialog_layout import center_child_dialog
from gamepad_aim_settings import LIMITS, TURN_SPEED_FIELDS
from gyro_compensation import suggested_axis_ratio
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


def _suggestion(tr, axis, ratio):
    if GYRO_RATIO_MIN <= ratio <= GYRO_RATIO_MAX:
        return tr("建議 {axis} 補償倍率 {value:.2f}").format(axis=axis, value=ratio)
    return tr("建議 {axis} 補償倍率 {value:.2f}，超出 {low:.2f}–{high:.2f} 的設定範圍。").format(
        axis=axis, value=ratio, low=GYRO_RATIO_MIN, high=GYRO_RATIO_MAX,
    )


def _multiplier_range_message(tr):
    return tr("補償倍率須介於 {low:.2f} 和 {high:.2f}。").format(low=GYRO_RATIO_MIN, high=GYRO_RATIO_MAX)


class GyroCompensationCalculator:
    def __init__(self, owner, tr, open_gyro_settings=None, *, saved_inputs=None):
        self.window, body = _dialog(owner, tr("陀螺儀補償計算"))
        self.window.withdraw()
        selected_axis, axis_inputs = saved_inputs if saved_inputs is not None else ("X", {})
        self.axis = tk.StringVar(self.window, selected_axis)
        self.inputs = {
            axis: tuple(tk.StringVar(self.window, value) for value in axis_inputs.get(axis, ("1.00", "", "")))
            for axis, _label in AXES
        }
        axes = ttk.Frame(body)
        axes.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))
        for axis, label in AXES:
            ttk.Radiobutton(axes, text=tr(label), value=axis, variable=self.axis).pack(side="left", padx=(0, 10))
        self.entries = []
        for row, label in enumerate(("原補償倍率", "舊設定耗時（秒）", "新設定耗時（秒）"), 1):
            ttk.Label(body, text=tr(label)).grid(row=row, column=0, sticky="w", pady=3)
            entry = ttk.Entry(body, width=9)
            entry.grid(row=row, column=1, sticky="w", padx=(8, 0), pady=3)
            self.entries.append(entry)
        ttk.Label(body, text=tr(
            "1. 選 X 或 Y，填入原補償倍率。首次調整用 1.00。\n"
            "2. 改遊戲視角速度前後，用相同搖桿推幅\n"
            "   從同一地標轉到另一地標，錄影並填入兩次耗時。\n"
            "3. 到 S2P 的「補償設定」輸入建議倍率。"
        ), justify="left").grid(row=4, column=0, columnspan=2, sticky="w", pady=(8, 4))
        self.result = tk.StringVar(self.window)
        feedback = ttk.Label(body, textvariable=self.result, wraplength=self.window.winfo_pixels("210p"))

        def refresh(*_args):
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
            if text:
                feedback.grid(row=5, column=0, columnspan=2, sticky="w")
            else:
                feedback.grid_remove()

        def switch_axis(*_args):
            for entry, value in zip(self.entries, self.inputs[self.axis.get()]):
                entry.configure(textvariable=value)
            refresh()

        for values in self.inputs.values():
            for value in values:
                value.trace_add("write", refresh)
        self.axis.trace_add("write", switch_axis)
        switch_axis()
        buttons = ttk.Frame(body)
        buttons.grid(row=6, column=0, columnspan=2, sticky="e", pady=(8, 0))
        self.settings_button = ttk.Button(buttons, text=tr("開啟 S2P 補償設定"), command=open_gyro_settings)
        self.settings_button.pack(side="left", padx=(0, 6))
        if open_gyro_settings is None:
            self.settings_button.state(["disabled"])
        ttk.Button(buttons, text=tr("關閉"), command=self.window.destroy).pack(side="left")
        center_child_dialog(self.window, owner.winfo_toplevel())

    def snapshot_inputs(self):
        return self.axis.get(), {
            axis: tuple(value.get() for value in values) for axis, values in self.inputs.items()
        }


class GyroCompensationSettings:
    def __init__(self, owner, tr, current, apply):
        self.window, body = _dialog(owner, tr("陀螺儀補償設定"))
        self.window.withdraw()
        self.compensation = {axis: tk.StringVar(self.window, current[axis]) for axis, _label in AXES}
        for row, (axis, label) in enumerate(AXES):
            ttk.Label(body, text=tr(label)).grid(row=row, column=0, sticky="w", pady=3)
            entry = ttk.Spinbox(body, from_=GYRO_RATIO_MIN, to=GYRO_RATIO_MAX,
                                increment=0.01, format="%.2f", width=9, textvariable=self.compensation[axis])
            entry.grid(row=row, column=1, padx=(8, 0), pady=3)
        ttk.Label(body, text=tr(
            "調高遊戲視角速度後，如果陀螺儀微瞄變得太敏感，\n"
            "可降低對應的倍率。X 調水平，Y 調垂直。\n"
            "維持 1.00 會保留原本的陀螺儀輸出。\n"
            "例如 X 設為 0.50，水平輸出在反死區前會減半。\n"
            "倍率只作用於陀螺儀，實體搖桿維持原本的輸出。"
        ), justify="left").grid(row=2, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self.status = tk.StringVar(self.window)
        feedback = ttk.Label(body, textvariable=self.status, wraplength=self.window.winfo_pixels("190p"))

        def clear_feedback(*_args):
            self.status.set("")
            feedback.grid_remove()

        for value in self.compensation.values():
            value.trace_add("write", clear_feedback)

        def save(_event=None):
            try:
                values = {axis: float(value.get()) for axis, value in self.compensation.items()}
                if not all(GYRO_RATIO_MIN <= value <= GYRO_RATIO_MAX for value in values.values()):
                    raise ValueError
            except ValueError:
                text = _multiplier_range_message(tr)
            else:
                formatted = {axis: f"{value:.2f}" for axis, value in values.items()}
                apply(formatted)
                for axis, value in formatted.items():
                    self.compensation[axis].set(value)
                text = tr("補償倍率已填入，請按主視窗的儲存／套用。")
            self.status.set(text)
            feedback.grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 0))

        buttons = ttk.Frame(body)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text=tr("關閉"), command=self.close).pack(side="left", padx=(0, 6))
        self.apply_button = ttk.Button(buttons, text=tr("套用"), command=save)
        self.apply_button.pack(side="left")
        self.window.bind("<Return>", save)
        self.window.minsize(self.window.winfo_pixels("230p"), 1)
        center_child_dialog(self.window, owner.winfo_toplevel())

    def close(self):
        self.window.destroy()


class GyroCompensationPreview:
    def __init__(self, owner, tr, settings, prepare, turn, close, open_gyro_settings):
        self.tr = tr
        self.prepare = prepare
        self.turn = turn
        self.window, body = _dialog(owner, tr("3D 陀螺儀補償預覽"))
        self.window.bind("<Escape>", close)
        self.window.protocol("WM_DELETE_WINDOW", close)
        self.window.bind("<Destroy>", self._destroyed, add="+")
        self.job = None
        self.measurement = None
        self.original = tuple(getattr(settings, key) for key, _label in TURN_SPEED_FIELDS)
        self.adjusted = []
        self.status = tk.StringVar(self.window)
        self.result = tk.StringVar(self.window)
        self._adjusted_speeds = []
        for title, editable in (("原始視角速度", False), ("調整後視角速度", True)):
            frame = ttk.LabelFrame(body, text=tr(title), padding=(8, 4))
            frame.pack(fill="x", pady=(0, 8))
            for row, (key, label) in enumerate(TURN_SPEED_FIELDS):
                ttk.Label(frame, text=tr(label)).grid(row=row, column=0, sticky="w", pady=3)
                value = self.original[row]
                if editable:
                    low, high = LIMITS[key]
                    speed = tk.StringVar(self.window, str(value))
                    speed.trace_add("write", self._clear_feedback)
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
            variable.trace_add("write", refresh_feedback)
        refresh_feedback()
        self.measure_button = ttk.Button(buttons, text=tr("量測"), command=self.start)
        self.measure_button.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        self.cancel_button = ttk.Button(buttons, text=tr("取消量測"), command=self.cancel)
        self.cancel_button.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        self.settings_button = ttk.Button(buttons, text=tr("開啟 S2P 陀螺儀設定"), command=open_gyro_settings)
        self.settings_button.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(6, 0))
        if open_gyro_settings is None:
            self.settings_button.state(["disabled"])

    def _clear_feedback(self, *_args):
        self.status.set("")
        self.result.set("")

    def start(self):
        if self.measurement is not None:
            return
        adjusted = []
        for control, (key, label) in zip(self.adjusted, TURN_SPEED_FIELDS):
            low, high = LIMITS[key]
            try:
                speed = int(control.get())
            except ValueError:
                speed = low - 1
            if not low <= speed <= high:
                self.result.set("")
                self.status.set(self.tr("{label}請輸入 {low} 到 {high} 的整數。").format(
                    label=self.tr(label), low=low, high=high,
                ))
                return
            adjusted.append(speed)
        self.result.set("")
        self.angles = []
        self.measurement = tuple(zip(("X", "Y", "X", "Y"), (*self.original, *adjusted)))
        self._set_running(True)
        self._start_turn()

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
        self.status.set(self.tr("量測完成。"))
        suggestions = [
            _suggestion(self.tr, axis, suggested_axis_ratio(1.0, old, new))
            for (axis, _label), old, new in zip(AXES, self.angles[:2], self.angles[2:])
        ]
        suggestions.append(self.tr("以上以原補償倍率 1.00 試算。"))
        self.result.set("\n".join(suggestions))

    def _set_running(self, running):
        for widget in (*self.adjusted, self.measure_button):
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
