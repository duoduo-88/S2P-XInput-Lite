"""Compact mapping labels without changing serialized controller targets."""

import tkinter as tk
from tkinter import ttk


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
