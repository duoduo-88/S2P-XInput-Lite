"""Lightweight cursor and 3D aiming tests for Gamepad Tester."""

from __future__ import annotations

import logging
import math
import time
import tkinter as tk
from tkinter import font as tkfont, messagebox, ttk

from gamepad_aim_display import (
    activate_fullscreen_mode,
    available_resolutions,
    fullscreen_monitor_bounds,
    highest_resolution,
)
from gamepad_aim_gpu import TARGET_OUTLINE_WIDTH, target_outline_color
from gamepad_aim_gpu_overlay import capture_overlay
from gamepad_aim_history import AimHistory, METRIC_LABELS, format_change
from gamepad_aim_model import (
    FOCAL_SCALE,
    MAX_TIMED_TARGETS,
    ROOM_LINES,
    CursorTest,
    CursorTrackingTest,
    ShootingTest,
    TimedCursorTest,
    TimedShootingTest,
    TrackingTest,
    clip_near,
    clip_viewport,
    project_ball,
    room_redraw_interval,
)
from gamepad_aim_settings import AimSettings, show_aim_settings
from tooltip_layout import HoverTip


FRAME_INTERVAL = 1.0 / 500.0
MOUSE_PRIORITY_SECONDS = 0.05
COUNTDOWN_SECONDS = 3
ACTION_BUTTON_WIDTH = 18
TOOLBAR_BUTTON_WIDTH = 11
VIEW_MARGIN = 8
VIEW_BUTTON_GAP = 6
HUD_LINE_GAP = 4
VIEW_BACKGROUND = "#101825"
RETICLE_COLOR = "#edf4fc"
TARGET_COLOR = "#57dab1"
HIT_DURATION = 0.15
HIT_DIRECTIONS = ((1, 0.2), (0.4, -1), (-0.7, -0.8), (-1, 0.4), (0.3, 1))
# One RGBA pixel of dark blue at 72% opacity, scaled once per HUD panel.
HUD_PIXEL = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGPgFVfZAQABfgEB8X2Y/"
    "AAAAABJRU5ErkJggg=="
)


def _scene_button_style(window):
    style = ttk.Style(window)
    if "AimButton.border" not in style.element_names():
        style.element_create("AimButton.border", "from", "clam", "Button.border")
        style.layout("Aim.TButton", [("AimButton.border", {
            "sticky": "nsew", "border": 1,
            "children": [
                ("fill", {"sticky": "nsew"}),
                ("Button.padding", {"sticky": "nsew", "children": [
                    ("Button.label", {"sticky": "nsew"}),
                ]}),
            ],
        })])
        style.configure(
            "Aim.TButton", background=VIEW_BACKGROUND, foreground=RETICLE_COLOR,
            bordercolor="#52647a", borderwidth=1, relief="solid", padding=(7, 3),
        )
        style.map(
            "Aim.TButton",
            background=[("pressed", "#376d91"), ("active", "#274863")],
            foreground=[("disabled", "#91a1b4")],
        )
    return "Aim.TButton"


class AimTestHud:
    tag = "aim_hud"
    margin = VIEW_MARGIN
    padding = 8

    def __init__(self, canvas):
        self.canvas = canvas
        self.font = tkfont.nametofont("TkDefaultFont", root=canvas)
        self.line = self.font.metrics("linespace") + HUD_LINE_GAP
        self.pixel = tk.PhotoImage(master=canvas, data=HUD_PIXEL, format="png")
        self.panel_images = {}
        self.labels = ()

    def clear(self):
        self.canvas.delete(self.tag)
        self.panel_images.clear()
        self.labels = ()

    def layout(self, width, height, changes=()):
        self.clear()
        margin, pad, line = self.margin, self.padding, self.line
        footer_width = max(1, min(540, (width - 3 * margin) // 2))
        wrap = max(1, footer_width - 2 * pad)
        self.footer_bottom = height - margin
        fps_y = self.footer_bottom - pad - line
        change_y = fps_y - max(1, len(self._change_rows(changes, wrap))) * line
        stats_x = width - margin - footer_width
        self.status_panel = self.canvas.create_image(margin, 0, anchor="nw", tags=self.tag)
        self.stats_panel = self.canvas.create_image(stats_x, 0, anchor="nw", tags=self.tag)
        self.panel_width = footer_width
        right = width - margin - pad
        self.labels = (
            self._text(margin + pad, self.footer_bottom - pad - HUD_LINE_GAP, anchor="sw", width=wrap),
            self._text(right, change_y - HUD_LINE_GAP, anchor="se", justify="right", width=wrap),
            self._text(right, fps_y, anchor="ne"),
        )
        self.change_origin = (right, change_y, wrap)
        self.change_items = []
        self.previous_changes = None

    def _text(self, x, y, anchor="nw", **options):
        return self.canvas.create_text(
            x, y, anchor=anchor, font=self.font, fill=RETICLE_COLOR, tags=self.tag, **options,
        )

    def _change_rows(self, changes, width):
        rows = []
        used = 0
        for text, color in changes:
            span = self.font.measure(text)
            if not rows or used + span > width:
                rows.append([])
                used = 0
            rows[-1].append((text, color, span))
            used += span + self.padding
        return rows

    def update(self, status, score, fps, changes):
        if not self.labels:
            return
        for item, text in zip(self.labels, (status, score, fps)):
            if self.canvas.itemcget(item, "text") != text:
                self.canvas.itemconfigure(item, text=text)
        self._resize_panel(self.stats_panel, self.labels[1])
        if status:
            self._resize_panel(self.status_panel, self.labels[0])
        status_state = "normal" if status else "hidden"
        if self.canvas.itemcget(self.status_panel, "state") != status_state:
            self.canvas.itemconfigure(self.status_panel, state=status_state)
        if changes != self.previous_changes:
            for item in self.change_items:
                self.canvas.delete(item)
            self.change_items.clear()
            right, y, width = self.change_origin
            for row in self._change_rows(changes, width):
                x = right
                for text, color, span in reversed(row):
                    item = self._text(x, y, anchor="ne", text=text)
                    self.canvas.itemconfigure(item, fill=color or RETICLE_COLOR)
                    self.change_items.append(item)
                    x -= span + self.padding
                y += self.line
            self.previous_changes = changes

    def _resize_panel(self, panel, label):
        top = self.canvas.bbox(label)[1] - self.padding
        x, y = self.canvas.coords(panel)
        if panel not in self.panel_images or y != top:
            image = self.pixel.zoom(self.panel_width, self.footer_bottom - top)
            self.canvas.itemconfigure(panel, image=image)
            self.canvas.coords(panel, x, top)
            self.panel_images[panel] = image

class AimTestPane:
    def __init__(self, parent, translate, read_output, display_hz=lambda _window: 60.0, *,
                 settings_path=None, history=None, feedback=None, gpu_renderer=None):
        self.tr = translate
        self.read_output = read_output
        self.owner = parent.winfo_toplevel()
        self.read_display_hz = display_hz
        self.display_hz = display_hz(self.owner)
        self.window = tk.Toplevel(self.owner, background=VIEW_BACKGROUND)
        self.window.withdraw()
        self.window.transient(self.owner)
        self.window.title(self.tr("瞄準測試"))
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.close_test)
        self._gpu_factory = gpu_renderer
        self._gpu_view = None
        self.gpu = None
        self.gpu_error = None
        self._gpu_overlay = None
        self._gpu_fragments = ()
        self.settings_path = settings_path
        self.settings = AimSettings.load(settings_path) if settings_path else AimSettings()
        self.settings_dialog = None
        self.history = history if history is not None else AimHistory(None)
        self.feedback = feedback
        self.feedback_at = 0.0
        self.round_basis = None
        self.round_started_at = None
        self.change_parts = ()
        self.start_pressed = self.cancel_pressed = True
        self.test_open = False
        self._fullscreen_mode = None
        self._focus_close_job = None
        self.hud = None
        self.dimension = tk.StringVar(parent, "3d")
        self.mode = tk.StringVar(parent, "shooting")
        self.status = tk.StringVar(parent)
        self.score = tk.StringVar(parent)
        self.fps_text = tk.StringVar(parent)
        self.size = (640, 360)
        self.job = None
        self.device_key = None
        self.output_ready = False
        self.trigger_pressed = True
        self.space_pressed = self.space_armed = False
        self.interrupted = False
        self.countdown_until = self.countdown_value = None
        self.last_frame = self.next_frame = self.next_draw = 0.0
        self.fps_started = self.label_update_at = 0.0
        self.frames = self.draws = 0

        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)
        toolbar = ttk.Frame(parent)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        toolbar.columnconfigure(0, weight=1)
        self.dimension_controls = ttk.Frame(toolbar)
        self.dimension_controls.grid(row=0, column=0, sticky="w", pady=(0, 4))
        for value, label in (("2d", "2D 模式"), ("3d", "3D 模式")):
            ttk.Radiobutton(
                self.dimension_controls, text=self.tr(label),
                variable=self.dimension, value=value, command=self._reset,
            ).pack(side="left", padx=(0, 8))
        help_mark = tk.Label(
            self.dimension_controls, text="?", width=2, relief="solid",
            borderwidth=1, cursor="question_arrow",
        )
        help_mark.pack(side="left")
        HoverTip(help_mark, self._help_text(), wraplength=380)
        self.drill_controls = ttk.Frame(toolbar)
        self.drill_controls.grid(row=1, column=0, sticky="w")
        for value, label in (("tracking", "跟槍"), ("shooting", "點靶"), ("timed-shooting", "限時點靶")):
            ttk.Radiobutton(
                self.drill_controls, text=self.tr(label),
                variable=self.mode, value=value, command=self._reset,
            ).pack(side="left", padx=(0, 8))
        actions = ttk.Frame(toolbar)
        actions.grid(row=0, column=1, rowspan=2, sticky="se")
        clear_history = ttk.Button(actions, text=self.tr("清除紀錄"), width=TOOLBAR_BUTTON_WIDTH)
        clear_history.pack(side="left", padx=(0, 6))
        self.settings_button = ttk.Button(
            actions, text=self.tr("測試設定"), width=TOOLBAR_BUTTON_WIDTH, command=self._open_settings,
        )
        self.settings_button.pack(side="left", padx=(0, 6))
        self.start_button = ttk.Button(
            actions, text=self.tr("開始"), width=TOOLBAR_BUTTON_WIDTH, command=self._start,
        )
        self.start_button.pack(side="left")
        self.start_button.bind("<KeyPress-space>", lambda _event: "break")
        self.start_button.bind("<KeyRelease-space>", self._start_with_space)

        self.history_view = self.history.create_view(
            parent, self.tr, on_clear=lambda: self._show_changes(()), clear_button=clear_history,
        )
        self.history_view.grid(row=1, column=0, sticky="nsew")
        scene_style = _scene_button_style(self.window)
        self.restart_button = ttk.Button(
            self.window, width=ACTION_BUTTON_WIDTH, command=self._start, takefocus=False, style=scene_style,
        )
        self.close_button = ttk.Button(
            self.window, text=self.tr("關閉 (Esc / B)"), width=ACTION_BUTTON_WIDTH,
            command=self.close_test, takefocus=False, style=scene_style,
        )
        self.canvas = tk.Canvas(self.window, background=VIEW_BACKGROUND, highlightthickness=0)
        self._view = self.canvas
        self._place_view()
        self.grid_lines = [
            self.canvas.create_line(-20, -20, -20, -20, fill=color, tags="scene")
            for color, _start, _end in ROOM_LINES
        ]
        self.grid_bounds = [None] * len(ROOM_LINES)
        self.grid_pose = None
        self.next_room_draw = 0.0
        self._ball_items = tuple(
            self.canvas.create_oval(
                -20, -20, -20, -20, fill=TARGET_COLOR,
                outline=target_outline_color(TARGET_COLOR), width=TARGET_OUTLINE_WIDTH,
            )
            for _ in range(MAX_TIMED_TARGETS)
        )
        self._ball_bounds = [None] * len(self._ball_items)
        self.hit_effect = None
        self.hit_particles = [
            self.canvas.create_polygon(0, 0, 0, 0, 0, 0, state="hidden", tags="hit_effect")
            for _ in HIT_DIRECTIONS
        ]
        self.reticle = [
            self.canvas.create_line(-20, -20, -20, -20, fill=RETICLE_COLOR, width=2)
            for _ in range(2)
        ]
        self.reticle_geometry = None
        self.reticle_color = RETICLE_COLOR
        self.countdown_item = self.canvas.create_text(
            self.size[0] / 2, self.size[1] / 2, text="",
            fill=RETICLE_COLOR, font=("Segoe UI", 56, "bold"),
        )
        self.finish_hint_item = self.canvas.create_text(
            0, 0, text="", anchor="n", fill="#d0dcec", font=("Segoe UI", 12),
        )
        self.ball_color = TARGET_COLOR
        self._bind_view(self.canvas)
        self.canvas.bind("<Destroy>", self._canvas_destroyed)
        # Start on release so the Windows Enter auto-repeat cannot restart a round.
        self.owner_start_binding = self.owner.bind("<KeyRelease-Return>", self._start_shortcut, add="+")
        self.window.bind("<KeyRelease-Return>", self._start_shortcut)
        self.window.bind("<Escape>", self.close_test)
        self.window.bind("<Configure>", self._refresh_display_rate)
        parent.bind("<Map>", lambda _event: self.set_visible(True), add="+")
        parent.bind("<Unmap>", lambda _event: self.set_visible(False), add="+")
        parent.bind("<Destroy>", lambda event: self._destroy(event, parent), add="+")
        self.connection_hint = ttk.Label(parent, text=self.tr("請選擇有搖桿輸出的手把。"))
        self.connection_hint.grid(row=2, column=0, sticky="w", pady=(4, 0))
        self._reset()

    def _bind_view(self, view):
        view.bind("<Configure>", self._resize)
        view.bind("<Button-1>", self._mouse_click)
        view.bind("<Motion>", self._mouse_move)
        view.bind("<KeyPress-space>", self._space_down)
        view.bind("<KeyRelease-space>", self._space_shot)
        view.bind("<Tab>", lambda _event: "break")
        view.bind("<Shift-Tab>", lambda _event: "break")
        view.bind("<FocusOut>", self._view_lost_focus)

    def _view_lost_focus(self, event):
        if event.widget is not self._view or self.window.focus_displayof() is self._view:
            return
        self._interrupt()
        if self.test_open and self.settings.fullscreen and self._focus_close_job is None:
            self._focus_close_job = self.owner.after_idle(self._close_inactive_fullscreen)

    def _close_inactive_fullscreen(self):
        self._focus_close_job = None
        if not self.test_open or not self.settings.fullscreen:
            return
        focus = self.window.focus_displayof()
        if focus is None or focus.winfo_toplevel() is not self.window:
            self.close_test()

    def _place_view(self):
        self._view.place(x=0, y=0, relwidth=1, relheight=1)
        self._view.tk.call("raise", self._view._w)
        self.restart_button.lift()
        self.close_button.lift()

    def _sync_gpu(self):
        if not self.test_open:
            self._stop_gpu()
            return
        if self.gpu is not None or self._gpu_factory is None or self.gpu_error is not None:
            return
        if self._gpu_view is None:
            self._gpu_view = tk.Frame(self.window, background=VIEW_BACKGROUND, borderwidth=0)
            self._bind_view(self._gpu_view)
        try:
            self.gpu = self._gpu_factory(self._gpu_view.winfo_id(), *self.size)
        except (OSError, RuntimeError, tk.TclError) as error:
            self.gpu_error = str(error)
            return
        had_focus = self.canvas.focus_displayof() is self.canvas
        self._view = self._gpu_view
        # Keep Tk's display list for layout, but unmap it to skip raster painting.
        self.canvas.place_forget()
        self._place_view()
        if had_focus:
            self._view.focus_set()
        self._gpu_overlay = None
        self.grid_pose = None
        self.next_draw = 0.0

    def _stop_gpu(self, *, restore=True):
        if self.gpu is None:
            return
        renderer, self.gpu = self.gpu, None
        had_focus = restore and self._view.focus_displayof() is self._view
        self._view = self.canvas
        renderer.close()
        if self._gpu_view.winfo_exists():
            self._gpu_view.place_forget()
        if restore:
            for item, bounds in zip(self.grid_lines, self.grid_bounds):
                self.canvas.coords(item, *(bounds or (-20, -20, -20, -20)))
            self._place_view()
            if had_focus:
                self._view.focus_set()
        self._gpu_overlay = None
        self.grid_pose = None
        self.next_draw = 0.0

    def _canvas_destroyed(self, _event):
        self.test_open = False
        try:
            self._stop_gpu(restore=False)
        finally:
            self._restore_display_mode()
        if self.job is not None:
            self.owner.after_cancel(self.job)
            self.job = None

    def _destroy(self, event, parent):
        if event.widget is parent:
            self.set_visible(False)
            if self.feedback is not None:
                self.feedback.close()
            self.owner.unbind("<KeyRelease-Return>", self.owner_start_binding)
            if self.window.winfo_exists():
                self.window.destroy()

    def set_visible(self, visible):
        if visible:
            if self.job is None:
                self.start_pressed = self.cancel_pressed = True
                now = time.perf_counter()
                self.last_frame = self.next_frame = self.next_draw = self.fps_started = now
                self.frames = self.draws = 0
                self.job = self.owner.after(1, self._tick)
                self._sync_gpu()
            return
        if self.job is not None:
            self.owner.after_cancel(self.job)
            self.job = None
        self.close_test()
        self.fps_text.set("")

    def _reset(self):
        self._check_finish(skip_animation=True)
        if self.feedback is not None:
            self.feedback.stop()
        self.feedback_at = 0.0
        self.round_basis = None
        self.round_started_at = None
        self.finished = False
        self.next_room_draw = 0.0
        self.history_error = False
        self._show_changes(())
        self.mode_is_3d = self.dimension.get() == "3d"
        self.mode_is_tracking = self.mode.get() == "tracking"
        self.mode_is_shooting = self.mode_is_3d and not self.mode_is_tracking
        self.mode_is_timed = self.mode.get() == "timed-shooting"
        self._mouse_priority_until = 0.0
        settings = self.settings
        if self.mode_is_tracking:
            self.cursor = CursorTrackingTest(settings.tracking_seconds, settings.seed, target_size=settings.target_size_2d)
        elif self.mode_is_timed:
            self.cursor = TimedCursorTest(settings.timed_shooting_seconds, settings.seed, target_size=settings.target_size_2d)
        else:
            self.cursor = CursorTest(
                settings.target_count, settings.target_seconds, settings.seed, target_size=settings.target_size_2d,
            )
        self.tracking = TrackingTest(settings.tracking_seconds, settings.seed)
        spread = {"horizontal_spread": settings.horizontal_spread, "vertical_spread": settings.vertical_spread}
        if self.mode_is_timed:
            self.shooting = TimedShootingTest(settings.timed_shooting_seconds, settings.seed, settings.depth, **spread)
        else:
            self.shooting = ShootingTest(settings.target_count, settings.target_seconds, settings.seed, settings.depth, **spread)
        self.scene = self.shooting if self.mode_is_shooting else self.tracking
        self.trigger_pressed = True
        self.space_pressed = self.space_armed = False
        self.interrupted = False
        self.countdown_until = None
        self._display_countdown(None)
        self.canvas.itemconfigure(self.countdown_item, font=("Segoe UI", 56, "bold"))
        self.canvas.itemconfigure(self.finish_hint_item, text="")
        self.canvas.delete("finish_outline")
        self._gpu_overlay = None
        self.hit_effect = None
        self.canvas.itemconfigure("hit_effect", state="hidden")
        self.canvas.itemconfigure("scene", state="normal" if self.mode_is_3d else "hidden")
        self._sync_gpu()
        self._draw()
        self._layout_hud()
        self._update_labels()

    def _start(self):
        if (self.countdown_until is not None or self._awaiting_finish()
                or (self.mode_is_3d and not self.output_ready)):
            return
        self._reset()
        if not self.test_open:
            try:
                if not self._open_test_window():
                    return
            except OSError:
                logging.getLogger(__name__).exception("Could not open the selected fullscreen display mode")
                messagebox.showerror(
                    self.tr("瞄準測試"),
                    self.tr("無法以 {size} 開啟全螢幕測試。請在測試設定選擇其他解析度。").format(
                        size=self.settings.fullscreen_size or self.tr("自動（最高）"),
                    ),
                    parent=self.owner,
                )
                return
        self._view.focus_set()
        self.last_frame = time.perf_counter()
        self.countdown_until = self.last_frame + COUNTDOWN_SECONDS
        self._display_countdown(COUNTDOWN_SECONDS)
        self._draw()
        self._update_labels()

    def _start_shortcut(self, _event=None):
        if self.job is not None and self.owner.grab_current() is None:
            focus = self.owner.focus_displayof()
            if focus is not None and focus.winfo_toplevel() in (self.owner, self.window):
                self.start_button.invoke()
                return "break"

    def _start_with_space(self, _event):
        self.start_button.invoke()
        return "break"

    def _display_countdown(self, value):
        if value != self.countdown_value:
            self.countdown_value = value
            self._gpu_overlay = None
            if self.canvas.winfo_exists():
                self.canvas.itemconfigure(self.countdown_item, text="" if value is None else str(value))

    def _advance_countdown(self, now):
        if self.countdown_until is None:
            return
        remaining = min(COUNTDOWN_SECONDS, math.ceil(self.countdown_until - now))
        if remaining > 0:
            self._display_countdown(remaining)
            return
        self.countdown_until = None
        self._display_countdown(None)
        self.trigger_pressed = True
        self.round_basis = self.settings.basis(self._mode_key(), self.size)
        if not self.mode_is_3d:
            self.round_basis["target_top"] = self._cursor_top()
        self.round_started_at = now
        self._round().start()
        if not self.mode_is_3d:
            self.cursor.x, self.cursor.y = self.size[0] / 2, self.size[1] / 2
        self._draw()
        self._update_labels()

    def _interrupt(self):
        self.space_pressed = self.space_armed = False
        if self.countdown_until is not None or self.scene.running or self.cursor.running:
            self.countdown_until = None
            self._display_countdown(None)
            self.scene.running = self.cursor.running = False
            self.interrupted = True
            self.round_basis = None
            self.round_started_at = None
        if self.feedback is not None:
            self.feedback.stop()

    def _open_test_window(self):
        if not self._restore_display_mode():
            raise OSError("Could not restore the previous display mode")
        hwnd = self.owner.winfo_id()
        fullscreen = self.settings.fullscreen
        resolution = self.settings.fullscreen_size if fullscreen else self.settings.window_size
        maximize = not fullscreen and not resolution and self.window.tk.call("tk", "windowingsystem") == "win32"
        owner_width, owner_height = self.owner.winfo_width(), self.owner.winfo_height()
        if fullscreen and not resolution:
            resolution = highest_resolution(available_resolutions(hwnd))
            if not resolution:
                raise OSError("No available fullscreen resolution")
        if resolution:
            width, height = map(int, resolution.split("x"))
        elif maximize:
            # The initial rectangle chooses the monitor for the native maximize.
            width, height = owner_width, owner_height
        else:
            width, height = self.owner.winfo_screenwidth(), self.owner.winfo_screenheight()
        owner_x, owner_y = self.owner.winfo_rootx(), self.owner.winfo_rooty()
        x = max(min(0, owner_x), owner_x + (owner_width - width) // 2)
        y = max(min(0, owner_y), owner_y + (owner_height - height) // 2)
        if fullscreen:
            self._fullscreen_mode = activate_fullscreen_mode(hwnd, (width, height))
            x, y, right, bottom = (
                self._fullscreen_mode.bounds if self._fullscreen_mode is not None
                else fullscreen_monitor_bounds(hwnd)
            )
            width, height = right - x, bottom - y
        self.test_open = True
        try:
            # Tk 8.6 fullscreen forces Windows windows to the primary screen.
            # A transient override window becomes a native child and cannot take focus.
            self.window.transient("" if fullscreen else self.owner)
            self.window.overrideredirect(fullscreen)
            self.window.attributes("-topmost", fullscreen)
            self.window.geometry(f"{width}x{height}+{x}+{y}")
            offset = -VIEW_MARGIN
            for button in (self.close_button, self.restart_button):
                button.place(relx=1, x=offset, y=VIEW_MARGIN, anchor="ne")
                offset -= button.winfo_reqwidth() + VIEW_BUTTON_GAP
            self._place_view()
            self.window.deiconify()
            if maximize:
                self.window.state("zoomed")
            self.window.lift()
            self.window.update_idletasks()
            if not self.test_open:
                return False
            self.display_hz = self.read_display_hz(self.window)
            self.gpu_error = None
            self._sync_gpu()
            # The native GPU child must be mapped before it can receive focus.
            self.window.update_idletasks()
            if not self.test_open:
                return False
            self._view.focus_force()
            self.frames = self.draws = 0
            self.fps_text.set("")
            self.fps_started = self.next_draw = time.perf_counter()
            self._layout_hud()
        except BaseException:
            self.close_test()
            raise
        return True

    def close_test(self, _event=None):
        try:
            if self._focus_close_job is not None:
                self.owner.after_cancel(self._focus_close_job)
                self._focus_close_job = None
            self._interrupt()
            if self.test_open:
                self._check_finish(skip_animation=True)
                focus = self.window.focus_displayof()
                self.test_open = False
                self.window.withdraw()
                self._stop_gpu()
                self.window.attributes("-topmost", False)
                self.window.overrideredirect(False)
                if (focus is not None and focus.winfo_toplevel() is self.window
                        and self.start_button.winfo_exists() and self.start_button.winfo_viewable()):
                    self.start_button.focus_set()
        finally:
            if not self._restore_display_mode() and self.owner.winfo_exists():
                self.owner.after(100, self._retry_display_restore, self._fullscreen_mode)
        return "break"

    def _retry_display_restore(self, mode):
        if self._fullscreen_mode is mode and not self.test_open:
            self._restore_display_mode()

    def _restore_display_mode(self):
        if self._fullscreen_mode is not None:
            try:
                self._fullscreen_mode.close()
            except OSError:
                logging.getLogger(__name__).exception("Could not restore the original display mode")
                return False
            self._fullscreen_mode = None
        return True

    def _refresh_display_rate(self, event):
        if self.test_open and event.widget is self.window:
            self.display_hz = self.read_display_hz(self.window)

    def _layout_hud(self):
        if not self.test_open:
            return
        if self.hud is None:
            self.hud = AimTestHud(self.canvas)
        self.hud.layout(*self.size, changes=self.change_parts)
        self._update_hud()

    def _update_hud(self):
        self._gpu_overlay = None
        if self.test_open and self.hud is not None:
            changes = tuple((text, "#79e8a4" if better else "#ff9a9a" if better is False else None)
                            for text, better in self.change_parts)
            self.hud.update(self.status.get(), self.score.get(), self.fps_text.get(), changes)

    def _mode_key(self):
        if self.mode_is_3d:
            return self.mode.get()
        return "timed-cursor" if self.mode_is_timed else "cursor-" + self.mode.get()

    def _round(self):
        return self.scene if self.mode_is_3d else self.cursor

    def _open_settings(self):
        self.close_test()
        if self.settings_dialog is not None and self.settings_dialog.winfo_exists():
            self.settings_dialog.lift()
            return
        self.settings_dialog = show_aim_settings(self.owner, self.tr, self.settings, self._save_settings)

    def _save_settings(self, settings):
        if self.settings_path is not None:
            settings.save(self.settings_path)
        self.settings = settings
        self._reset()

    def _help_text(self):
        notes = (
            "瞄準測試說明",
            "2D 模式可直接用滑鼠或右搖桿移動準心，依輸入自動接手。按左鍵、RT 或空白鍵點靶。",
            "跟槍模式用右搖桿輸出轉動視角，盡量讓準心留在移動的靶上。"
            "跟靶率是準心在靶上的時間比例。",
            "點靶模式用右搖桿輸出瞄準，按 RT 或空白鍵射擊。"
            "按 Enter 或手把 START 可開始或重測。",
            "2D 和 3D 都有「限時點靶」。開場就有兩顆靶。"
            "靶會留到被擊中，命中後補新靶。倒數歸零時結算命中數，並記錄命中率與平均偏差。",
            "同設定的紀錄須使用相同的模式、畫面尺寸、出靶種子、震動與音效。"
            "也會比對該模式的靶數、秒數、靶心大小、遠近與出靶範圍。"
            "靶色、準心顏色與大小不影響比對。",
            "成績與同設定的歷史最佳回合比較。點靶依序比命中數、命中率、完成耗時與平均偏差。"
            "跟槍依序比跟靶率與平均偏差。",
            "↑ 和 ↓ 表示數值上升或下降，綠色代表進步，紅色代表退步。"
            "命中率和跟靶率的差異以百分點表示，例如從 80% 降到 65.6% 是下降 14.4 百分點。",
            "命中率是命中數除以射擊次數。兩次都打完所有靶才比較耗時。"
            "平均偏差是準心與靶心的平均距離，2D 用像素（px），3D 用角度（°）。"
            "耗時和平均偏差越小越好。",
        )
        return "\n\n".join(map(self.tr, notes))

    def _show_changes(self, parts):
        needs_layout = parts != self.change_parts
        self.change_parts = parts
        if needs_layout and self.test_open:
            self._layout_hud()
        else:
            self._update_hud()

    def _awaiting_finish(self):
        return self.round_basis is not None and self._round().completed

    def _check_finish(self, *, skip_animation=False):
        if not self._awaiting_finish() or (self.hit_effect is not None and not skip_animation):
            return
        test = self._round()
        basis, self.round_basis = self.round_basis, None
        self.round_started_at = None
        self.finished = True
        self.canvas.itemconfigure(self.countdown_item, font=("Segoe UI", 32, "bold"))
        self._display_countdown(self.tr("測試完成"))
        self.canvas.itemconfigure(self.finish_hint_item, text=self.tr("按 Enter 或手把 START 再測一次。"))
        self._position_finish_hint()
        self._draw()
        metrics = {"seconds": test.elapsed}
        if self.mode_is_tracking:
            metrics.update(tracking=test.on_target_percent, error=test.mean_error)
        else:
            metrics["hits"] = test.hits
            if test.shots:
                metrics["accuracy"] = test.accuracy
            if test.has_error_samples:
                metrics["error"] = test.mean_error
        try:
            changes, previous = self.history.add_with_comparison(basis, metrics)
        except OSError:
            self.history_error = True
            self._update_labels()
            return
        parts = tuple((f"{self.tr(METRIC_LABELS[key])} "
                       f"{format_change(key, value, self._metric_unit(key))}", improved)
                      for key, value, improved in changes)
        if not parts:
            parts = ((self.tr("同基準無變化" if previous else "同基準尚無紀錄"), None),)
        self._show_changes(parts)
        self._update_labels()

    def _metric_unit(self, key):
        if key == "error":
            return "°" if self.mode_is_3d else " px"
        if key in {"accuracy", "tracking"}:
            return " " + self.tr("百分點")
        if key == "seconds":
            return " " + self.tr("秒")
        return None

    def _resize(self, event):
        if getattr(event, "widget", self._view) is not self._view:
            return
        size = event.width, event.height
        if size != self.size:
            if self.countdown_until is None and not self.finished:
                self._interrupt()
            self.size = size
            self._gpu_overlay = None
            self.next_draw = 0.0
            self.cursor.x = self.cursor.y = None
            self.canvas.coords(self.countdown_item, size[0] / 2, size[1] / 2)
            if self.finished:
                self._position_finish_hint()
            self._draw()
            self._layout_hud()

    def _mouse_move(self, event, now=None):
        if self.mode_is_3d or not self.cursor.running:
            return False
        width, height = self.size
        if not (0 <= event.x < width and 0 <= event.y < height):
            return False
        self.cursor.x, self.cursor.y = event.x, event.y
        now = time.perf_counter() if now is None else now
        self._mouse_priority_until = now + MOUSE_PRIORITY_SECONDS
        return True

    def _mouse_click(self, event):
        now = time.perf_counter()
        if self._mouse_move(event, now) and not self.mode_is_tracking:
            self._shoot(now)
            self._draw()
            self._draw_hit_effect(now)
            self._update_labels()

    def _sync_pointer(self):
        active = self.countdown_until is not None or self._round().running
        cursor = "none" if active else ""
        if self._view.cget("cursor") != cursor:
            self._view.configure(cursor=cursor)

    def _position_finish_hint(self):
        heading = self.canvas.bbox(self.countdown_item)
        if heading is not None:
            self.canvas.coords(self.finish_hint_item, self.size[0] / 2, heading[3] + 7)
            self.canvas.delete("finish_outline")
            background = self.canvas.cget("background")
            for item, radius in ((self.countdown_item, 2), (self.finish_hint_item, 1)):
                x, y = self.canvas.coords(item)
                options = {
                    key: self.canvas.itemcget(item, key)
                    for key in ("text", "font", "anchor")
                }
                for dx, dy in ((-1, -1), (0, -1), (1, -1), (-1, 0), (1, 0),
                               (-1, 1), (0, 1), (1, 1)):
                    self.canvas.create_text(
                        x + radius * dx, y + radius * dy, fill=background,
                        tags="finish_outline", **options,
                    )
            self.canvas.tag_raise(self.countdown_item)
            self.canvas.tag_raise(self.finish_hint_item)
            self._gpu_overlay = None

    def _space_down(self, _event):
        if not self.space_pressed:
            self.space_pressed = True
            self.space_armed = self.countdown_until is None

    def _space_shot(self, _event):
        armed = self.space_armed
        self.space_pressed = self.space_armed = False
        if not armed or not self._round().running:
            return
        if self.mode_is_3d:
            output = self.read_output()
            self.output_ready = output is not None
            if output is None or output[0] != self.device_key:
                self._interrupt()
                self._update_labels()
                return
        self._shoot()

    def _shoot(self, now=None):
        if self.mode_is_tracking:
            return
        now = time.perf_counter() if now is None else now
        self._advance_point_clock(now)
        test = self._round()
        hits, shots = test.hits, test.shots
        if self.mode_is_shooting:
            impact = self.shooting.shoot()
        else:
            impact = None
            if self.cursor.x is not None:
                impact = self.cursor.click(self.cursor.x, self.cursor.y, *self.size, self._cursor_top())
        if self.feedback is not None and test.shots > shots and test.hits == hits:
            self.feedback.miss(self.settings.sound)
        self._after_shot(hits, now, impact)

    def _advance_point_clock(self, now):
        if self.round_started_at is not None:
            test = self._round()
            test.advance_time(max(0.0, now - self.round_started_at - test.elapsed))

    def _after_shot(self, hits, now, impact):
        if self._round().hits > hits:
            if self.feedback is not None:
                self.feedback.hit(self.settings.rumble / 100, self.settings.sound)
            if impact is not None:
                self.hit_effect = (now, impact)
                self.canvas.itemconfigure("hit_effect", fill=self.settings.target_color)
        self._check_finish()

    def _update_feedback(self, now):
        if self.feedback is None or not self.mode_is_tracking or now < self.feedback_at:
            return
        self.feedback_at = now + 1 / 125
        on_target = (
            self.tracking.crosshair_on_target() if self.mode_is_3d
            else self.cursor.crosshair_on_target(*self.size, self._cursor_top())
        )
        self.feedback.contact(
            on_target, self.settings.rumble / 100, self.settings.sound, now,
        )

    def _draw_hit_effect(self, now):
        self._gpu_fragments = ()
        if self.hit_effect is None:
            return
        started, impact = self.hit_effect
        progress = max(0, (now - started) / HIT_DURATION)
        if progress >= 1:
            self.canvas.itemconfigure("hit_effect", state="hidden")
            self.hit_effect = None
            self._check_finish()
            return
        bounds = impact
        if self.mode_is_3d:
            bounds = project_ball(self.scene.camera_transform()(impact), *self.size)
        if bounds is None:
            self.canvas.itemconfigure("hit_effect", state="hidden")
            return
        self.canvas.itemconfigure("hit_effect", state="normal")
        x, y, radius = bounds
        distance = min(30, radius) * (0.45 + 1.5 * progress)
        fragment = max(1, min(6, radius / 4) * (1 - progress))
        points = tuple((x + dx * distance, y + dy * distance) for dx, dy in HIT_DIRECTIONS)
        if self.gpu is not None:
            self._gpu_fragments = tuple((*point, fragment, self.settings.target_color) for point in points)
        else:
            for item, (px, py) in zip(self.hit_particles, points):
                self.canvas.coords(item, px - fragment, py + fragment, px + fragment, py, px, py - fragment)

    def _cursor_top(self):
        if self.test_open:
            return min(self.size[1] / 2, VIEW_MARGIN + self.close_button.winfo_reqheight())
        return 0

    def _tick(self):
        now = time.perf_counter()
        if now + 0.0003 < self.next_frame:
            self._schedule(now)
            return
        seconds = max(0, now - self.last_frame)
        self.last_frame = now
        if seconds > 0.25:
            self._interrupt()
            seconds = 0
        output = self.read_output()
        self.output_ready = output is not None
        key, state = output if output is not None else (None, None)
        if key != self.device_key:
            self.device_key = key
            self.trigger_pressed = self.start_pressed = self.cancel_pressed = True
            if self.mode_is_3d:
                self._interrupt()
                seconds = 0
            elif self.feedback is not None:
                self.feedback.stop()
        if state is not None:
            start = "START" in state.buttons
            cancel = "B" in state.buttons
            if cancel and not self.cancel_pressed and self.test_open:
                focus = self.window.focus_displayof()
                if focus is not None and focus.winfo_toplevel() is self.window:
                    self.close_test()
            elif start and not self.start_pressed:
                job = self.job
                self._start_shortcut()
                if self.job != job:
                    return
            self.start_pressed = start
            self.cancel_pressed = cancel
        if self.countdown_until is not None:
            self._advance_countdown(now)
            seconds = 0
        if self._round().running and (state is not None or not self.mode_is_3d):
            if self.mode_is_3d:
                if self.mode_is_shooting:
                    self.scene.turn(state.right, seconds)
                else:
                    self.scene.advance(state.right, seconds)
            else:
                if state is not None and now >= self._mouse_priority_until:
                    self.cursor.move(state.right, seconds, *self.size)
                if self.mode_is_tracking:
                    self.cursor.advance(seconds, *self.size, self._cursor_top())
            if not self.mode_is_tracking:
                self._advance_point_clock(now)
                if state is not None:
                    pressed = (state.right_trigger or 0) >= 0.5
                    if pressed and not self.trigger_pressed:
                        self._shoot(now)
                    self.trigger_pressed = pressed
            self._check_finish()
            self._update_feedback(now)
        if self.test_open:
            if output is not None:
                self.frames += 1
            if not self.mode_is_3d or output is not None or self.gpu is not None:
                self._draw_if_due(now)
            else:
                self._draw_hit_effect(now)
        if now >= self.label_update_at:
            self.label_update_at = now + 0.1
            self._update_labels()
        if self.test_open and now - self.fps_started >= 0.5:
            span = now - self.fps_started
            renderer = "GPU · " if self.gpu is not None else ("Tk · " if self._gpu_factory else "")
            rate = (
                "繪圖 {fps:.0f} FPS　更新 {hz:.0f} Hz" if self.output_ready or self.mode_is_3d
                else "繪圖 {fps:.0f} FPS"
            )
            self.fps_text.set(renderer + self.tr(rate).format(fps=self.draws / span, hz=self.frames / span))
            self.fps_started, self.frames, self.draws = now, 0, 0
            self._update_hud()
        if not self.test_open:
            interval = 0.03
        elif not self.mode_is_3d and not self.output_ready:
            interval = max(FRAME_INTERVAL, 1.0 / self.display_hz)
        else:
            interval = FRAME_INTERVAL if self.output_ready else 0.03
        self.next_frame = max(self.next_frame + interval, now + interval / 2)
        self._schedule(time.perf_counter())

    def _schedule(self, now):
        delay = max(1, int((self.next_frame - now) * 1000))
        self.job = self.owner.after(delay, self._tick)

    def _draw_if_due(self, now):
        if now + 0.0003 < self.next_draw:
            return
        interval = max(FRAME_INTERVAL, 1.0 / self.display_hz)
        slots = max(1, int((now - self.next_draw) / interval) + 1)
        self.next_draw += slots * interval
        self._draw()
        self._draw_hit_effect(now)
        if self.gpu is None or self._present_gpu():
            self.draws += 1

    def _present_gpu(self):
        if self._gpu_overlay is None:
            items = self.canvas.find_withtag(self.hud.tag) if self.hud else ()
            outlines = self.canvas.find_withtag("finish_outline") if self.finished else ()
            self._gpu_overlay = capture_overlay(
                self.canvas, (*items, *outlines, self.countdown_item, self.finish_hint_item),
            )
        overlays, labels = self._gpu_overlay
        lines = ()
        if self.mode_is_3d:
            lines = ((*bounds, line[0]) for line, bounds in zip(ROOM_LINES, self.grid_bounds) if bounds is not None)
        balls = tuple((*bounds, self.ball_color) for bounds in self._ball_bounds if bounds is not None)
        reticle = (*self.reticle_geometry, self.reticle_color) if self.reticle_geometry is not None else None
        try:
            self.gpu.draw(
                *self.size, lines=lines, balls=balls, reticle=reticle,
                fragments=self._gpu_fragments, overlays=overlays, labels=labels,
            )
        except (OSError, RuntimeError) as error:
            self.gpu_error = str(error)
            self._stop_gpu()
            return False
        return True

    def _update_labels(self):
        ready = not self.mode_is_3d or self.output_ready
        can_start = ready and self.countdown_until is None and not self._awaiting_finish()
        for button in (self.start_button, self.restart_button):
            button.state(["!disabled" if can_start else "disabled"])
        self._sync_pointer()
        if self.mode_is_timed:
            start_text = "開始限時點靶"
        elif self.mode_is_tracking:
            start_text = "開始跟槍"
        else:
            start_text = "開始點靶"
        if self._round().running or self.finished or self.interrupted:
            restart_text = self.tr("重新開始")
        else:
            restart_text = self.tr(start_text)
        if self.restart_button.cget("text") != restart_text:
            self.restart_button.configure(text=restart_text)
        if self.interrupted:
            start_hint_key = (
                "測試已中斷。按 Enter 或手把 START 重新開始。" if ready
                else "測試已中斷。請選擇有搖桿輸出的手把，再按 Enter 或 START。"
            )
        else:
            start_hint_key = "按 Enter 或手把 START 開始測試。" if ready else "請選擇有搖桿輸出的手把。"
        start_hint = self.tr(start_hint_key)
        if self.connection_hint.cget("text") != start_hint:
            self.connection_hint.configure(text=start_hint)
        if self.history_error:
            instruction = "測試完成。無法儲存這筆紀錄。"
        elif self.finished:
            instruction = ""
        elif self.interrupted:
            instruction = start_hint_key
        elif not ready:
            instruction = "請選擇有搖桿輸出的手把。"
        elif self.mode_is_shooting:
            instruction = "用右搖桿輸出瞄準球靶，按 RT 或空白鍵射擊。"
        elif self.mode_is_3d:
            instruction = "用右搖桿輸出轉動視角，追蹤移動球靶。"
        elif self.mode_is_tracking:
            instruction = "用滑鼠或右搖桿移動準心，追蹤移動球靶。"
        else:
            instruction = "用滑鼠或右搖桿瞄準，按左鍵、RT 或空白鍵點靶。"
        status = self.tr(instruction) if instruction else ""
        if status != self.status.get():
            self.status.set(status)
        if self.mode_is_tracking:
            test = self._round()
            error = f"{test.mean_error:.2f}°" if self.mode_is_3d else f"{test.mean_error:.1f} px"
            self.score.set(self.tr(
                "剩餘 {seconds:.1f} 秒　跟靶率 {percent}　平均偏差 {error}"
            ).format(
                seconds=test.duration - test.elapsed,
                percent=f"{test.on_target_percent:.1f}%" if test.elapsed else "—",
                error=error if test.elapsed else "—",
            ))
        else:
            test = self._round()
            error = f"{test.mean_error:.2f}°" if self.mode_is_3d else f"{test.mean_error:.1f} px"
            summary = (
                "剩餘 {seconds:.1f} 秒　命中 {hits}　命中率 {accuracy}　平均偏差 {error}"
                if self.mode_is_timed
                else "命中 {hits}/{total}　命中率 {accuracy}　耗時 {seconds:.1f}/{limit} 秒　平均偏差 {error}"
            )
            self.score.set(self.tr(summary).format(
                hits=test.hits, total=test.count,
                accuracy=f"{test.accuracy:.1f}%" if test.shots else "—",
                seconds=test.duration - test.elapsed if self.mode_is_timed else test.elapsed,
                limit=test.duration or "∞", error=error if test.has_error_samples else "—",
            ))
        self._update_hud()

    def _draw(self):
        width, height = self.size
        if self.mode_is_3d:
            on_target = self._draw_scene(width, height) and self.mode_is_shooting
            center = width / 2, height / 2
        else:
            top = self._cursor_top()
            center = (self.cursor.x, self.cursor.y) if self.cursor.x is not None else (width / 2, height / 2)
            on_target = self.cursor.on_target(*center, width, height, top)
            self._draw_balls(self.cursor.target_positions(width, height, top), self.mode_is_tracking and on_target)
            on_target = on_target and not self.mode_is_tracking
        color = self.settings.reticle_on_target_color if on_target else self.settings.reticle_color
        if color != self.reticle_color:
            self.reticle_color = color
            for item in self.reticle:
                self.canvas.itemconfigure(item, fill=color)
        if self.countdown_until is not None or self.finished:
            center = None
        geometry = None if center is None else (*center, self.settings.reticle_size / 2)
        if geometry == self.reticle_geometry:
            return
        self.reticle_geometry = geometry
        if geometry is None:
            for item in self.reticle:
                self.canvas.coords(item, -20, -20, -20, -20)
        else:
            x, y, half = geometry
            self.canvas.coords(self.reticle[0], x - half, y, x + half, y)
            self.canvas.coords(self.reticle[1], x, y - half, x, y + half)

    def _draw_balls(self, targets, on_target=False):
        color = self.settings.target_on_target_color if on_target else self.settings.target_color
        for index, item in enumerate(self._ball_items):
            bounds = targets[index] if index < len(targets) else None
            if bounds != self._ball_bounds[index]:
                self._ball_bounds[index] = bounds
                self._position_ball(item, bounds)
            if color != self.ball_color:
                self.canvas.itemconfigure(item, fill=color, outline=target_outline_color(color))
        self.ball_color = color

    def _position_ball(self, item, bounds):
        if bounds is None:
            self.canvas.coords(item, -20, -20, -20, -20)
        else:
            x, y, radius = bounds
            self.canvas.coords(item, x - radius, y - radius, x + radius, y + radius)

    def _draw_scene(self, width, height):
        transform = self.scene.camera_transform()
        cx, cy, focal = width / 2, height / 2, height * FOCAL_SCALE

        def project(point):
            x, y, z = point
            return cx + focal * x / z, cy - focal * y / z

        pose = width, height, self.scene.yaw, self.scene.pitch
        if self.gpu is not None:
            room_changed = pose != self.grid_pose
        else:
            now = time.perf_counter()
            # Limit full-window Tk repaints on the software fallback.
            resized = self.grid_pose is None or pose[:2] != self.grid_pose[:2]
            room_changed = resized
            if not resized and now >= self.next_room_draw:
                _, _, yaw, pitch = self.grid_pose
                yaw_change = math.remainder(self.scene.yaw - yaw, math.tau)
                room_changed = max(abs(yaw_change), abs(self.scene.pitch - pitch)) * focal >= 0.5
            if room_changed:
                self.next_room_draw = now + room_redraw_interval(width, height)
        if room_changed:
            self.grid_pose = pose
            for index, (item, (_color, start, end)) in enumerate(zip(self.grid_lines, ROOM_LINES)):
                clipped = clip_near(transform(start), transform(end))
                bounds = None if clipped is None else clip_viewport(
                    project(clipped[0]), project(clipped[1]), width, height,
                )
                if bounds is not None and self.gpu is None:
                    bounds = tuple(round(value) for value in bounds)
                if bounds != self.grid_bounds[index]:
                    self.grid_bounds[index] = bounds
                    if self.gpu is None:
                        self.canvas.coords(item, *(bounds or (-20, -20, -20, -20)))
        targets = sorted(map(transform, self.scene.target_positions()), key=lambda target: target[2], reverse=True)
        visible = []
        on_target = False
        for target in targets:
            bounds = project_ball(target, width, height)
            if bounds is not None:
                visible.append(bounds)
                on_target = on_target or self.scene.on_target(target)
        self._draw_balls(visible, on_target and not self.mode_is_shooting)
        return on_target
