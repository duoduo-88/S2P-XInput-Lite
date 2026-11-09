"""Tk dialog positioning shared by the tester's child windows."""


def center_child_dialog(dialog, owner):
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
    if dx or dy:
        dialog.geometry(f"+{dialog.winfo_x() + dx}+{dialog.winfo_y() + dy}")
