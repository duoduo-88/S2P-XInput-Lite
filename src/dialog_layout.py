"""Shared Tk window positioning."""

from gamepad_aim_display import fullscreen_monitor_bounds


def startup_banner_geometry(root, width, height, *, center_rect=None, bounds=None):
    """Center the banner on the root's display or over an explicit owner rect."""
    if bounds is None:
        try:
            left, top, right, bottom = fullscreen_monitor_bounds(root.winfo_id())
            bounds = left, top, right - left, bottom - top
        except OSError:
            bounds = 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()
    bounds_x, bounds_y, bounds_width, bounds_height = bounds
    center_x, center_y, center_width, center_height = bounds if center_rect is None else center_rect
    x = center_x + (center_width - width) // 2
    y = center_y + (center_height - height) // 2
    max_x = bounds_x + max(0, bounds_width - width)
    max_y = bounds_y + max(0, bounds_height - height)
    x = min(max(bounds_x, x), max_x)
    y = min(max(bounds_y, y), max_y)
    return f"{width}x{height}+{x}+{y}"


def center_child_dialog(dialog, owner, *, bounds=None):
    """Open a child centered over its owner's client area."""
    dialog.update_idletasks()
    min_width, min_height = dialog.minsize()
    width = max(min_width, dialog.winfo_reqwidth())
    height = max(min_height, dialog.winfo_reqheight())
    owner_width = owner.winfo_width()
    owner_height = owner.winfo_height()
    x = owner.winfo_x() + (owner_width - width) // 2
    y = owner.winfo_y() + (owner_height - height) // 2
    dialog.geometry(f"+{x}+{y}")
    dialog.deiconify()
    dialog.update_idletasks()
    # A fullscreen owner has no frame; the dialog still has a title bar.
    dx = owner.winfo_rootx() - dialog.winfo_rootx() + (owner_width - dialog.winfo_width()) // 2
    dy = owner.winfo_rooty() - dialog.winfo_rooty() + (owner_height - dialog.winfo_height()) // 2
    frame_x, frame_y = dialog.winfo_x(), dialog.winfo_y()
    x, y = frame_x + dx, frame_y + dy
    if bounds is not None:
        left, top, right, bottom = bounds
        border = max(0, dialog.winfo_rootx() - frame_x)
        title = max(0, dialog.winfo_rooty() - frame_y)
        x = max(left, min(x, right - dialog.winfo_width() - 2 * border))
        y = max(top, min(y, bottom - dialog.winfo_height() - title - border))
    if (x, y) != (frame_x, frame_y):
        dialog.geometry(f"+{x}+{y}")
