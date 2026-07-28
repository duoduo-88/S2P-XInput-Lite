"""Independent process entry point for the non-modal gamepad test window."""

from __future__ import annotations

import ctypes
import sys
import threading
import tkinter as tk

from config_utils import CONFIG_PATH, load_config
from gamepad_test_window import GamepadTestWindow, TEST_ICON_PATH
from localization import translate_text


GAMEPAD_TEST_APP_ID = "S2P-XInput-Lite.GamepadTest"
SUPPORTED_LANGUAGES = frozenset(("zh", "en"))


def command_line_language(arguments):
    """Return an explicit supported language from child-process arguments."""
    arguments = tuple(arguments)
    for index, argument in enumerate(arguments):
        if argument == "--language" and index + 1 < len(arguments):
            language = str(arguments[index + 1]).strip().lower()
            return language if language in SUPPORTED_LANGUAGES else None
        if argument.startswith("--language="):
            language = argument.partition("=")[2].strip().lower()
            return language if language in SUPPORTED_LANGUAGES else None
    return None


def configure_windows_taskbar_identity():
    """Keep the tester in its own Windows taskbar group."""
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            GAMEPAD_TEST_APP_ID
        )
    except (AttributeError, OSError):
        pass


def apply_root_icon(root):
    """Set the icon Windows reads from Tk's hidden application root."""
    if not TEST_ICON_PATH.is_file():
        return None
    try:
        icon = tk.PhotoImage(master=root, file=str(TEST_ICON_PATH))
        root.iconphoto(True, icon)
        return icon
    except tk.TclError:
        return None


def notify_parent(message, stream=None):
    """Send one lifecycle message to the settings process."""
    if stream is None:
        stream = getattr(sys.stdout, "buffer", sys.stdout)
    try:
        stream.write(f"{message}\n".encode("ascii"))
        stream.flush()
        return True
    except (
        AttributeError,
        BrokenPipeError,
        OSError,
        UnicodeError,
        TypeError,
        ValueError,
    ):
        return False


def notify_parent_ready(stream=None):
    """Tell the settings process that the tester window is ready."""
    return notify_parent("ready", stream)


def notify_parent_closing(stream=None):
    """Tell the settings process that the tester has begun closing."""
    return notify_parent("closing", stream)


def watch_parent_commands(stream, shutdown_requested, show_requested):
    """Receive all parent commands without ever calling Tk from this thread."""
    while True:
        try:
            command = stream.readline()
        except (AttributeError, OSError, ValueError):
            shutdown_requested.set()
            return
        command = command.strip().lower()
        if command in {b"close", b"quit", b""}:
            shutdown_requested.set()
            return
        if command == b"show":
            show_requested.set()


class GamepadTestHost:
    """The small subset of ConfigGUI needed by GamepadTestWindow.

    This deliberately does not import ConfigGUI. A separate Tcl/Tk
    interpreter keeps the high-refresh tester independent from settings input.
    """

    def __init__(self, root, language=None):
        self.root = root
        requested = str(language or "").strip().lower()
        if requested not in SUPPORTED_LANGUAGES:
            config = load_config(CONFIG_PATH)
            requested = config.get(
                "gui", "language", fallback="zh"
            ).strip().lower()
        self.language = (
            requested if requested in SUPPORTED_LANGUAGES else "zh"
        )

    def tr(self, text):
        return translate_text(text, self.language)


def main():
    # AppUserModelID must be set before the first top-level window is created.
    configure_windows_taskbar_identity()
    root = tk.Tk()
    root_icon = apply_root_icon(root)
    # Keep the PhotoImage alive for the complete Tk interpreter lifetime.
    root._gamepad_test_icon = root_icon
    root.withdraw()
    host = GamepadTestHost(
        root,
        language=command_line_language(sys.argv[1:]),
    )
    tester = GamepadTestWindow(host)
    tester.open()

    closing = False

    def close_all():
        nonlocal closing
        if closing:
            return
        closing = True
        if "--parent-pipe" in sys.argv:
            notify_parent_closing()
        tester.close()
        root.quit()
        try:
            root.destroy()
        except tk.TclError:
            pass

    tester.window.protocol("WM_DELETE_WINDOW", close_all)
    if "--parent-pipe" in sys.argv:
        notify_parent_ready()

    if "--parent-pipe" in sys.argv:
        shutdown_requested = threading.Event()
        show_requested = threading.Event()

        def poll_parent_request():
            if shutdown_requested.is_set():
                close_all()
            elif not closing:
                if show_requested.is_set():
                    show_requested.clear()
                    tester.open()
                root.after(50, poll_parent_request)

        threading.Thread(
            target=watch_parent_commands,
            args=(
                getattr(sys.stdin, "buffer", sys.stdin),
                shutdown_requested,
                show_requested,
            ),
            daemon=True,
            name="GamepadTestParentWatch",
        ).start()
        root.after(50, poll_parent_request)
    root.mainloop()


if __name__ == "__main__":
    main()
