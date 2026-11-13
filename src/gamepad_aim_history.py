"""Local results for completed aim-test rounds."""

import json
import math
import tkinter as tk
from contextlib import nullcontext
from datetime import datetime
from pathlib import Path
from tkinter import font, messagebox, ttk

from config_utils import atomic_write_bytes, config_file_lock
from dialog_layout import center_child_dialog
from gamepad_aim_model import DEFAULT_STICK_TURN_SPEED, TIMED_MODES, TRACKING_MODES


METRIC_LABELS = {
    "hits": "命中",
    "accuracy": "命中率",
    "seconds": "耗時",
    "error": "平均偏差",
    "tracking": "跟靶率",
}
CHANGE_COLORS = {True: "#15803d", False: "#b91c1c"}
_MODE_LABELS = {
    "cursor-stick": ("2D", "模擬遊標"),
    "cursor-mouse": ("2D", "Windows 游標"),
    "cursor-tracking": ("2D", "跟槍"),
    "cursor-shooting": ("2D", "點靶"),
    "timed-cursor": ("2D", "限時點靶"),
    "tracking": ("3D", "跟槍"),
    "shooting": ("3D", "點靶"),
    "timed-shooting": ("3D", "限時點靶"),
    "timed-cursor-stick": ("2D", "限時 · 模擬遊標"),
    "timed-cursor-mouse": ("2D", "限時 · Windows 游標"),
}
_METRIC_UNITS = {"hits": "", "accuracy": "%", "seconds": " s", "tracking": "%"}
_COMPARISON_IGNORED_KEYS = {"reticle", "reticle_size", "target_color", "mouse_sensitivity"}


def _precision(key, unit):
    if key == "hits":
        return 0
    if key == "error" and unit == "°":
        return 2
    return 1


def format_change(key, difference, unit=None):
    """Format a signed delta at the precision used for its displayed metric."""
    if unit is None:
        unit = {
            "accuracy": " pp", "tracking": " pp", "seconds": " s", "error": " px",
        }.get(key, "")
    arrow = "↑" if difference > 0 else "↓"
    return f"{arrow} {difference:+.{_precision(key, unit)}f}{unit}"


def _metric_unit(key, mode):
    if key == "error":
        return " px" if _MODE_LABELS[mode][0] == "2D" else "°"
    return _METRIC_UNITS[key]


def _comparison_basis(basis):
    return {key: value for key, value in basis.items() if key not in _COMPARISON_IGNORED_KEYS}


def _matching_records(records, basis):
    comparison = _comparison_basis(basis)
    return (record for record in reversed(records) if _comparison_basis(record["basis"]) == comparison)


def _record_rank(record):
    basis, metrics = record["basis"], record["metrics"]
    mode = basis["mode"]
    error = -round(metrics.get("error", math.inf), _precision("error", _metric_unit("error", mode)))
    if mode in TRACKING_MODES:
        return round(metrics.get("tracking", -math.inf), 1), error
    hits = metrics.get("hits", -math.inf)
    accuracy = round(metrics.get("accuracy", -math.inf), 1)
    if mode in TIMED_MODES:
        return hits, accuracy, error
    seconds = metrics.get("seconds", math.inf) if hits >= basis["targets"] else math.inf
    return hits, accuracy, -round(seconds, 1), error


def _best_record(records, basis):
    return max(
        _matching_records(records, basis), key=_record_rank, default=None,
    )


def _changes(basis, metrics, baseline):
    changes = []
    for key in METRIC_LABELS:
        if key not in metrics or key not in baseline:
            continue
        if key == "seconds":
            targets = basis["targets"]
            completed = targets and min(metrics.get("hits", 0), baseline.get("hits", 0)) >= targets
            if not completed or basis["mode"] in TIMED_MODES:
                continue
        precision = _precision(key, _metric_unit(key, basis["mode"]))
        current_value = round(metrics[key], precision)
        baseline_value = round(baseline[key], precision)
        difference = round(current_value - baseline_value, precision)
        if difference:
            improved = difference < 0 if key in ("error", "seconds") else difference > 0
            changes.append((key, float(difference), improved))
    return tuple(changes)


def _numeric_metrics(metrics, basis):
    clean = {}
    targets = basis.get("targets", 0)
    for key, value in metrics.items():
        if key not in METRIC_LABELS or type(value) not in (int, float):
            continue
        try:
            if not math.isfinite(value) or value < 0:
                continue
        except OverflowError:
            continue
        if key in ("accuracy", "tracking") and round(value, 10) > 100:
            continue
        if key == "hits" and (value % 1 or (targets > 0 and value > targets)):
            continue
        clean[key] = value
    return clean


def _valid_basis(basis):
    if not isinstance(basis, dict):
        return False
    mode = basis.get("mode")
    if not isinstance(mode, str) or mode not in _MODE_LABELS:
        return False
    if any(type(basis.get(key)) is not int for key in ("targets", "seconds", "seed", "depth")):
        return False
    viewport = basis.get("viewport")
    return (
        isinstance(viewport, list) and len(viewport) == 2
        and all(type(dimension) is int and dimension > 0 for dimension in viewport)
    )


class AimHistory:
    def __init__(self, path: Path | None):
        self.path = path
        self._records = []
        self._popup = None
        self._views = set()
        self._reload()

    def _reload(self):
        if self.path is not None:
            try:
                with config_file_lock():
                    self._records = self._read()
            except OSError:
                pass

    @property
    def records(self):
        return tuple(self._records)

    def has_matching_record(self, basis):
        return next(_matching_records(self._records, basis), None) is not None

    def _read(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError, RecursionError):
            return []
        if not isinstance(data, list):
            return []
        records = []
        for record in data:
            if not isinstance(record, dict):
                continue
            basis, metrics, at = (record.get(key) for key in ("basis", "metrics", "at"))
            if not _valid_basis(basis) or not isinstance(metrics, dict) or not isinstance(at, str):
                continue
            try:
                datetime.fromisoformat(at)
                json.dumps(basis, allow_nan=False)
            except (ValueError, RecursionError):
                continue
            metrics = _numeric_metrics(metrics, basis)
            if metrics:
                records.append({"basis": basis, "metrics": metrics, "at": at})
        return records

    def _replace(self, records):
        if self.path is not None:
            data = json.dumps(records, allow_nan=False)
            atomic_write_bytes(data.encode("utf-8"), self.path)
        self._records = records

    def add(self, basis: dict, metrics: dict) -> tuple[tuple[str, float, bool], ...]:
        return self.add_with_comparison(basis, metrics)[0]

    def add_with_comparison(
        self, basis: dict, metrics: dict,
    ) -> tuple[tuple[tuple[str, float, bool], ...], bool]:
        """Save a result and report changes and match status from the same snapshot."""
        basis = json.loads(json.dumps(basis, allow_nan=False))
        metrics = _numeric_metrics(metrics, basis)
        record = {
            "basis": basis, "metrics": metrics,
            "at": datetime.now().astimezone().isoformat(timespec="seconds"),
        }
        with config_file_lock() if self.path is not None else nullcontext():
            records = self._read() if self.path is not None else self._records
            best = _best_record(records, basis)
            changes = _changes(basis, metrics, best["metrics"]) if best else ()
            self._replace([*records, record])
        self._refresh_views()
        return changes, best is not None

    def clear(self):
        with config_file_lock() if self.path is not None else nullcontext():
            self._replace([])
        self._refresh_views()

    def _refresh_views(self):
        for view in self._views:
            view.refresh()

    def create_view(self, parent, translate, on_clear=lambda: None, *, clear_button=None) -> ttk.Frame:
        """Create a live history view for the caller to place in its layout."""
        self._reload()
        view = _HistoryView(parent, self, translate, on_clear, clear_button)
        self._views.add(view)
        view.bind("<Destroy>", lambda _event: self._views.discard(view), add=True)
        self._refresh_views()
        return view

    def show(self, parent, translate, on_clear=lambda: None):
        if self._popup is None or not self._popup.winfo_exists():
            self._popup = _HistoryPopup(parent, self, translate, on_clear)
            center_child_dialog(self._popup, parent.winfo_toplevel())
        else:
            self._reload()
            self._refresh_views()
            self._popup.deiconify()
        self._popup.lift()
        self._popup.update_idletasks()
        self._popup.view.tree.focus_force()
        return self._popup


class _HistoryPopup(tk.Toplevel):
    def __init__(self, parent, history, translate, on_clear):
        super().__init__(parent)
        self.withdraw()
        self.title(translate("瞄準測試紀錄"))
        self.transient(parent.winfo_toplevel())
        self.bind("<Escape>", lambda _event: self.destroy())
        self.view = history.create_view(self, translate, on_clear)
        self.view.configure(padding=12)
        self.view.pack(fill="both", expand=True)
        ttk.Button(
            self.view.buttons, text=translate("關閉"), command=self.destroy,
        ).pack(side="right")
        self.minsize(self.view.minimum_width + 24, 1)


class _HistoryView(ttk.Frame):
    def __init__(self, parent, history, translate, on_clear, clear_button):
        super().__init__(parent)
        self.history = history
        self.tr = translate
        self.on_clear = on_clear

        listing = ttk.Frame(self)
        listing.pack(fill="both", expand=True)
        listing.columnconfigure(0, weight=1)
        listing.rowconfigure(0, weight=1)
        headings = {"at": "日期與時間", "mode": "模式", **METRIC_LABELS}
        self.tree = ttk.Treeview(
            listing, columns=tuple(headings), show="headings", height=8, selectmode="browse",
        )
        text_font = font.nametofont("TkDefaultFont", root=self)
        samples = {
            "at": ("2026-11-08 23:59:59",),
            "mode": tuple(self._mode_text(mode) for mode in _MODE_LABELS),
        }
        total_width = 0
        for key, heading in headings.items():
            label = self.tr(heading)
            column_texts = (label, *samples.get(key, ("100.00°",)))
            width = max(map(text_font.measure, column_texts)) + 18
            anchor = "w" if key in ("at", "mode") else "center"
            self.tree.heading(key, text=label, anchor=anchor)
            self.tree.column(key, width=width, anchor=anchor, stretch=key == "mode")
            total_width += width
        self.scrollbar = ttk.Scrollbar(listing, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=self._set_scroll)
        self.tree.grid(row=0, column=0, sticky="nsew")
        self.scrollbar.grid(row=0, column=1, sticky="ns")
        self.tree.bind("<<TreeviewSelect>>", self._show_selection)

        self._parameter_font = text_font
        self._parameter_width = total_width
        self._parameters = ()
        self.basis_label = ttk.Label(self, width=1, font=text_font, justify="left")
        self.basis_label.pack(fill="x", pady=(6, 0))
        self.basis_label.bind("<Configure>", self._fit_parameters)
        self.comparison = ttk.Frame(self)

        self.buttons = None
        if clear_button is None:
            self.buttons = ttk.Frame(self)
            self.buttons.pack(fill="x", pady=(6, 0))
            clear_button = ttk.Button(self.buttons, text=self.tr("清除紀錄"))
            clear_button.pack(side="left")
        self.clear_button = clear_button
        self.clear_button.configure(command=self._clear)
        self.minimum_width = total_width + self.scrollbar.winfo_reqwidth() + 4

    def _set_scroll(self, first, last):
        self.scrollbar.set(first, last)
        if float(first) > 0 or float(last) < 1:
            self.scrollbar.grid()
        else:
            self.scrollbar.grid_remove()

    def _fit_parameters(self, event=None):
        if event is not None:
            self._parameter_width = event.width
        separator = " · "
        first, second = separator.join(self._parameters), ""
        measure = self._parameter_font.measure
        width = self._parameter_width - 4
        if len(self._parameters) > 1 and measure(first) > width:
            # Pick a boundary between parameters, keeping both rows in view where possible.
            splits = [
                (separator.join(self._parameters[:index]), separator.join(self._parameters[index:]))
                for index in range(1, len(self._parameters))
            ]
            fitting = [pair for pair in splits if max(map(measure, pair)) <= width]
            first, second = fitting[-1] if fitting else min(splits, key=lambda pair: max(map(measure, pair)))
        self.basis_label.configure(text=f"{first}\n{second}")

    def _set_parameters(self, *items):
        self._parameters = items
        self._fit_parameters()

    def _mode_text(self, mode):
        dimension, label = _MODE_LABELS[mode]
        return f"{dimension} · {self.tr(label)}"

    def _metric_text(self, key, basis, metrics):
        value = metrics.get(key)
        if value is None:
            return "—"
        if key == "hits":
            return f"{value:.0f}/{basis['targets']}" if basis["targets"] else f"{value:.0f}"
        unit = _metric_unit(key, basis["mode"])
        return f"{value:.{_precision(key, unit)}f}{unit}"

    def refresh(self):
        self.records = self.history.records
        self.tree.delete(*self.tree.get_children())
        for index in reversed(range(len(self.records))):
            record = self.records[index]
            basis, metrics = record["basis"], record["metrics"]
            date = datetime.fromisoformat(record["at"]).strftime("%Y-%m-%d %H:%M:%S")
            values = tuple(self._metric_text(key, basis, metrics) for key in METRIC_LABELS)
            self.tree.insert(
                "", "end", iid=str(index), values=(date, self._mode_text(basis["mode"]), *values),
            )
        self.clear_button.state(["!disabled" if self.records else "disabled"])
        if self.records:
            newest = str(len(self.records) - 1)
            self.tree.selection_set(newest)
            self.tree.focus(newest)
            self.tree.see(newest)
        self._show_selection()

    def _show_selection(self, _event=None):
        for child in self.comparison.winfo_children():
            child.destroy()
        selection = self.tree.selection()
        if not selection:
            self._set_parameters(self.tr("尚無瞄準測試紀錄。"))
            self.comparison.pack_forget()
            return
        self.comparison.pack(fill="x", pady=(3, 0), before=self.buttons)
        index = int(selection[0])
        record = self.records[index]
        basis = record["basis"]
        self._set_parameters(*self._round_options(basis))
        best = _best_record(self.records[:index], basis)
        changes = _changes(basis, record["metrics"], best["metrics"]) if best else ()
        if changes:
            ttk.Label(self.comparison, text=self.tr("與同設定最佳成績比較")).pack(side="left")
            for key, difference, improved in changes:
                unit = _metric_unit(key, basis["mode"]) if key == "error" else None
                delta = format_change(key, difference, unit)
                ttk.Label(
                    self.comparison, text=f"{self.tr(METRIC_LABELS[key])} {delta}",
                    foreground=CHANGE_COLORS[improved],
                ).pack(side="left", padx=(10, 0))
        else:
            summary = "沒有可顯示的差異。" if best else "尚無相同設定的測試紀錄。"
            ttk.Label(self.comparison, text=self.tr(summary)).pack(side="left")

    def _round_options(self, basis):
        mode = basis["mode"]
        duration = (
            self.tr("時限 {seconds} s").format(seconds=basis["seconds"])
            if basis["seconds"] else self.tr("不限時")
        )
        seed = self.tr("種子 {seed}").format(seed=basis["seed"])
        options = [seed, duration] if mode in TIMED_MODES else [duration, seed]

        if mode == "tracking":
            arc = basis.get("tracking_arc", 0)
            speed = basis.get("tracking_speed", 100)
            if type(arc) is int and type(speed) is int and (arc or speed != 100):
                route = self.tr("環繞 {angle}°").format(angle=arc) if arc else self.tr("原本路線")
                options.extend((route, self.tr("靶速 {value}%").format(value=speed)))
        if mode in ("shooting", "timed-shooting"):
            options.append(self.tr("遠近差異 {depth}%").format(depth=basis["depth"]))
            if "horizontal_spread" in basis or "vertical_spread" in basis:
                options.append(self.tr("水平 {horizontal}%／垂直 {vertical}%").format(
                    horizontal=basis.get("horizontal_spread", 100),
                    vertical=basis.get("vertical_spread", 100),
                ))
        elif "target_size_2d" in basis:
            options.append(self.tr("靶心 {size}%").format(size=basis["target_size_2d"]))

        if _MODE_LABELS[mode][0] == "3D":
            horizontal = basis.get("stick_horizontal_speed", DEFAULT_STICK_TURN_SPEED[0])
            vertical = basis.get("stick_vertical_speed", DEFAULT_STICK_TURN_SPEED[1])
            if type(horizontal) is int and type(vertical) is int:
                options.append(self.tr("手把視角速度 {horizontal}／{vertical}°/秒").format(
                    horizontal=horizontal, vertical=vertical,
                ))
            sensitivity = basis.get("mouse_sensitivity", 100)
            if type(sensitivity) is int:
                options.append(self.tr("滑鼠感度 {value}%").format(value=sensitivity))
        width, height = basis["viewport"]
        options.append(f"{width}×{height}")
        return options

    def _clear(self):
        if not messagebox.askyesno(
            self.tr("清除紀錄"), self.tr("要清除所有瞄準測試紀錄嗎？"), parent=self,
        ):
            return
        try:
            self.history.clear()
        except OSError as error:
            messagebox.showerror(self.tr("無法清除瞄準測試紀錄"), str(error), parent=self)
            return
        self.on_clear()
