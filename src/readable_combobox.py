"""Overflow help for selectors and compact labels for controller mappings."""

import tkinter as tk
from tkinter import ttk

from gamepad_aim_display import fullscreen_monitor_bounds


class _PopdownTooltip:
    def __init__(self, widget, popup, listbox):
        self.widget = widget
        self.popup = popup
        self.listbox = listbox
        self.window = None
        self.row = None
        call = widget.tk.call
        self.inset = sum(
            call("winfo", "pixels", listbox, call(listbox, "cget", option))
            for option in ("-borderwidth", "-highlightthickness")
        )
        hover = widget.register(self.hover)
        hide = widget.register(self.hide)
        fit = widget.register(self.fit_width)
        call("bind", listbox, "<Map>", f"+{fit}")
        for event in ("<Enter>", "<Motion>"):
            call("bind", listbox, event, f"+{hover} %y")
        for event in ("<Leave>", "<MouseWheel>", "<KeyPress>", "<ButtonPress>"):
            call("bind", listbox, event, f"+{hide}")
        call("bind", popup, "<Unmap>", f"+{hide}")

    def _monitor_bounds(self):
        call = self.widget.tk.call
        try:
            return fullscreen_monitor_bounds(int(call("winfo", "id", self.popup), 0))
        except OSError:
            return (0, 0, self.widget.winfo_screenwidth(), self.widget.winfo_screenheight())

    def fit_width(self):
        """Fit the native list after ttk has populated it and placed its scrollbar."""
        call = self.widget.tk.call
        values = self.widget.tk.splitlist(call(self.listbox, "get", 0, "end"))
        font = call(self.listbox, "cget", "-font")
        text_width = max((call("font", "measure", font, value) for value in values), default=0)
        current_width = call("winfo", "width", self.popup)
        list_width = call("winfo", "width", self.listbox)
        padding = current_width - list_width + 2 * (self.inset + 1)
        left, _top, right, _bottom = self._monitor_bounds()
        width = min(right - left, max(current_width, text_width + padding))
        current_x = call("winfo", "rootx", self.popup)
        x = max(left, min(current_x, right - width))
        if width != current_width or x != current_x:
            y = call("winfo", "rooty", self.popup)
            height = call("winfo", "height", self.popup)
            call("wm", "geometry", self.popup, f"{width}x{height}+{x}+{y}")

    def hover(self, y):
        call = self.widget.tk.call
        try:
            y = int(y)
            index = call(self.listbox, "nearest", y)
            bbox = call(self.listbox, "bbox", index)
            if bbox:
                x, row_y, width, height = bbox
                right = call("winfo", "width", self.listbox) - self.inset
                clipped = x < self.inset or x + width > right
                if clipped and row_y <= y < row_y + height:
                    text = str(call(self.listbox, "get", index))
                    if self.row != (index, text):
                        self.hide()
                        self.show(row_y, text)
                        self.row = (index, text)
                    return
        except tk.TclError:
            pass
        self.hide()

    def show(self, row_y, text):
        call = self.widget.tk.call
        left, top, right, bottom = self._monitor_bounds()
        popup_x = call("winfo", "rootx", self.popup)
        popup_right = popup_x + call("winfo", "width", self.popup)
        popup_y = call("winfo", "rooty", self.popup)
        popup_bottom = popup_y + call("winfo", "height", self.popup)
        row_top = call("winfo", "rooty", self.listbox) + row_y
        space_left, space_right = popup_x - left, right - popup_right
        side_space = max(space_left, space_right)
        wrap_space = side_space if side_space >= 160 else right - left

        tip = self.window = tk.Toplevel(self.widget)
        tip.withdraw()
        tip.overrideredirect(True)
        call("wm", "transient", str(tip), self.popup)
        label = tk.Label(
            tip, text=text, justify="left", relief="solid", borderwidth=1,
            padx=8, pady=6, wraplength=max(1, min(420, wrap_space - 28)),
        )
        label.pack()
        width, height = label.winfo_reqwidth(), label.winfo_reqheight()
        y = row_top
        if space_right >= width + 6:
            x = popup_right + 6
        elif space_left >= width + 6:
            x = popup_x - width - 6
        else:
            x = max(left, min(popup_x, right - width))
            y = popup_bottom + 6 if bottom - popup_bottom >= height + 6 else popup_y - height - 6
        y = max(top, min(y, bottom - height))
        tip.geometry(f"+{x}+{y}")
        tip.deiconify()

    def hide(self):
        if self.window is not None:
            try:
                self.window.destroy()
            except tk.TclError:
                pass
            self.window = None
        self.row = None


def bind_popdown_tooltip(widget):
    """Show the full text of a clipped list row without changing selection."""
    try:
        popup = widget.tk.call("ttk::combobox::PopdownWindow", str(widget))
        listbox = f"{popup}.f.l"
        if widget.tk.call("winfo", "exists", listbox):
            _PopdownTooltip(widget, popup, listbox)
    except (AttributeError, tk.TclError):
        # Tk may use a native popup without an accessible listbox.
        pass


class ReadableCombobox(ttk.Combobox):
    def __init__(self, parent, **options):
        super().__init__(parent, **options)
        bind_popdown_tooltip(self)


_KEY_LABELS = {
    "CTRL": "Ctrl", "SHIFT": "Shift", "ALT": "Alt", "WIN": "Win",
    "ENTER": "Enter", "ESC": "Esc", "SPACE": "Space", "TAB": "Tab",
    "BACKSPACE": "Bksp", "DELETE": "Del", "INSERT": "Ins",
    "HOME": "Home", "END": "End", "PAGEUP": "PgUp", "PAGEDOWN": "PgDn",
    "UP": "↑", "DOWN": "↓", "LEFT": "←", "RIGHT": "→",
    "VOLUME_MUTE": "Mute", "VOLUME_DOWN": "Vol−", "VOLUME_UP": "Vol+",
    "MEDIA_NEXT": "Next", "MEDIA_NEXT_TRACK": "Next",
    "MEDIA_PREV": "Prev", "MEDIA_PREV_TRACK": "Prev",
    "MEDIA_STOP": "Stop", "MEDIA_PLAY_PAUSE": "Play/Pause",
}
_TARGET_LABELS = {
    "MOUSE:LEFT": "M:Left", "MOUSE:RIGHT": "M:Right",
    "MOUSE:MIDDLE": "M:Mid", "MOUSE:WHEEL_UP": "M:Wheel↑",
    "MOUSE:WHEEL_DOWN": "M:Wheel↓",
    **{
        f"{side}_STICK_{direction}": f"{side}S:{arrow}"
        for side in ("L", "R")
        for direction, arrow in (("UP", "↑"), ("DOWN", "↓"), ("LEFT", "←"), ("RIGHT", "→"))
    },
}


def mapping_target_label(value, translate=lambda text: text):
    """Format display text only; serialized mapping tokens remain unchanged."""
    value = str(value)
    if value == "CUSTOM_KEYBOARD":
        return translate("自訂按鍵")
    if value.startswith("KEYBOARD:"):
        return "KB:" + "+".join(
            _KEY_LABELS.get(key, key) for key in value[len("KEYBOARD:"):].split("+")
        )
    return _TARGET_LABELS.get(value, value)


class MappingCombobox(ttk.Combobox):
    """Keep compact display text separate from the canonical setting variable."""

    def __init__(self, parent, *, textvariable, formatter=mapping_target_label, **options):
        self.variable = textvariable
        self.formatter = formatter
        self.source_values = tuple(options.pop("values", ()))
        self._updating = False
        self.display_var = tk.StringVar(parent)
        super().__init__(
            parent, textvariable=self.display_var, **options
        )
        self._s2p_parameter_variable = textvariable
        self._source_trace = textvariable.trace_add("write", self.refresh)
        self._display_trace = self.display_var.trace_add("write", self._select)
        self.bind("<Destroy>", self._cleanup, add="+")
        self.refresh()

    def refresh(self, *_args):
        if self._updating:
            return
        self._updating = True
        try:
            self._targets_by_label = {
                self.formatter(value): value for value in self.source_values
            }
            self.configure(values=tuple(self._targets_by_label))
            self.display_var.set(self.formatter(self.variable.get()))
        finally:
            self._updating = False

    def _select(self, *_args):
        if not self._updating:
            target = self._targets_by_label.get(self.display_var.get())
            if target is not None:
                self.variable.set(target)

    def full_text(self):
        """Supply the original mapping to the existing overflow tooltip."""
        return self.variable.get()

    def _cleanup(self, event):
        if event.widget is self:
            self.variable.trace_remove("write", self._source_trace)
            self.display_var.trace_remove("write", self._display_trace)
