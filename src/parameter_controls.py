"""Drag-to-adjust values and right-click restore menus.

The settings window, Gamepad Tester, Aim Test settings, and compensation
dialogs share these helpers so every numeric parameter behaves the same way:
drag its text to change it, and right-click to restore the saved or default value.
"""

import math
import tkinter as tk


PIXELS_PER_STEP = 8


def scrub_numeric_value(
    start_value,
    step_count,
    minimum,
    maximum,
    step=None,
    allowed_values=None,
):
    """Move a numeric value by normal or discrete slider steps."""
    minimum = float(minimum)
    maximum = float(maximum)
    start_value = float(start_value)
    if not math.isfinite(start_value):
        start_value = minimum
    start_value = max(minimum, min(maximum, start_value))
    step_count = int(step_count)

    if allowed_values:
        choices = tuple(float(item) for item in allowed_values)
        if not choices:
            raise ValueError("allowed_values must not be empty")
        start_index = min(
            range(len(choices)),
            key=lambda index: (abs(choices[index] - start_value), index),
        )
        target_index = max(
            0,
            min(len(choices) - 1, start_index + step_count),
        )
        numeric = choices[target_index]
    elif step is not None:
        step = float(step)
        if step <= 0:
            raise ValueError("step must be positive")
        numeric = start_value + step_count * step
    else:
        numeric = start_value

    return max(minimum, min(maximum, numeric))


def widget_is_disabled(widget):
    """Return whether a Tk/ttk parameter control rejects user edits."""
    try:
        if widget.instate(["disabled"]):
            return True
    except (AttributeError, tk.TclError):
        pass
    try:
        return str(widget.cget("state")).strip().lower() == "disabled"
    except (AttributeError, tk.TclError):
        return False


def bind_drag_adjust(
    widget,
    variable,
    minimum,
    maximum,
    apply,
    *,
    step=None,
    allowed_values=None,
    state_widget=None,
    on_disabled_click=None,
    click_command=None,
    text_entry=False,
):
    """Change ``variable`` by dragging ``widget``; ``apply`` receives each new number.

    Dragging right or up raises the value by one step every 8 pixels. A click
    without dragging runs ``click_command``. Text entries keep their native
    click handling and only react to horizontal movement.
    """
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
        state["enabled"] = not widget_is_disabled(state_widget)
        if not state["enabled"]:
            if on_disabled_click is not None:
                on_disabled_click()
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
        return None if text_entry else "break"

    def drag(event):
        if not state["enabled"]:
            return "break"
        delta_x = event.x_root - state["start_x"]
        delta_y = event.y_root - state["start_y"]
        distance = delta_x if text_entry or abs(delta_x) >= abs(delta_y) else -delta_y
        step_count = int(distance / PIXELS_PER_STEP)
        if step_count == 0 and not text_entry:
            return "break"
        if step_count == state["last_steps"]:
            return "break"
        state["dragged"] = True
        state["last_steps"] = step_count
        apply(scrub_numeric_value(
            state["start_value"],
            step_count,
            minimum,
            maximum,
            step=step,
            allowed_values=allowed_values,
        ))
        return "break"

    def finish(_event):
        if not state["enabled"]:
            return "break"
        if not state["dragged"] and click_command is not None:
            click_command()
        return None if text_entry else "break"

    widget.bind("<ButtonPress-1>", begin)
    widget.bind("<B1-Motion>", drag)
    widget.bind("<ButtonRelease-1>", finish)


def show_restore_menu(event, tr, apply, saved_value, default_value, *, state_widget=None):
    """Pop up "Restore Saved" / "Restore Default" for one parameter.

    ``saved_value`` of ``None`` means the parameter is never saved, so that
    item is shown disabled.
    """
    if widget_is_disabled(state_widget or event.widget):
        return "break"
    menu = tk.Menu(event.widget.winfo_toplevel(), tearoff=False)
    menu.add_command(
        label=tr("還原上次儲存"),
        command=lambda: apply(saved_value),
        state="disabled" if saved_value is None else "normal",
    )
    menu.add_separator()
    menu.add_command(
        label=tr("還原系統預設"),
        command=lambda: apply(default_value),
    )
    try:
        menu.tk_popup(event.x_root, event.y_root)
    finally:
        menu.grab_release()
    return "break"


def bind_restore_menu(widgets, tr, apply, saved, default, *, state_widget=None):
    """Bind the restore menu to every widget of one parameter row.

    ``saved`` and ``default`` may be values or callables, so a row can follow a
    saved value that changes after the menu is bound.
    """
    def resolve(value):
        return value() if callable(value) else value

    def popup(event):
        return show_restore_menu(
            event, tr, apply, resolve(saved), resolve(default), state_widget=state_widget,
        )

    for widget in widgets:
        if widget is not None:
            # Widget bindings run before ttk class bindings, so returning
            # "break" also keeps a right-click from moving a Scale.
            widget.bind("<Button-3>", popup, add="+")
