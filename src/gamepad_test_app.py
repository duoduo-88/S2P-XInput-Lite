"""Independent process entry point for the non-modal gamepad test window."""

from __future__ import annotations

import ctypes
import sys
import threading
import time
import tkinter as tk

from config_utils import CONFIG_PATH, load_config
from gamepad_test_window import GamepadTestWindow, TEST_ICON_PATH
from localization import translate_text
from version import VERSION


GAMEPAD_TEST_APP_ID = "S2P-XInput-Lite.GamepadTest"
SUPPORTED_LANGUAGES = frozenset(("zh", "en"))
GAMEPAD_TEST_STARTUP_IMAGE = (
    TEST_ICON_PATH.parent / "S2P-XInput-Lite-600x340.png"
)
GAMEPAD_TEST_SPLASH_MIN_SECONDS = 0.75


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


class GamepadTestStartupOverlay:
    """Show the product banner while the direct tester is constructed."""

    DOT_INTERVAL_MS = 320

    def __init__(self, root, language="zh"):
        self.root = root
        self.language = (
            language if language in SUPPORTED_LANGUAGES else "zh"
        )
        self._dot_count = 3
        self._animation_job = None
        self.window = tk.Toplevel(root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.image = tk.PhotoImage(
            master=self.window,
            file=str(GAMEPAD_TEST_STARTUP_IMAGE),
        )
        width = int(self.image.width())
        height = int(self.image.height())
        self.canvas = tk.Canvas(
            self.window,
            width=width,
            height=height,
            borderwidth=0,
            highlightthickness=0,
        )
        self.canvas.pack()
        self.canvas.create_image(
            0, 0, anchor="nw", image=self.image
        )
        self._text_item = self.canvas.create_text(
            width - 12,
            height - 8,
            anchor="se",
            text=self._loading_text(),
            font=("Segoe UI", 9),
            fill="#4a4a4a",
        )
        x = max(0, (int(root.winfo_screenwidth()) - width) // 2)
        y = max(0, (int(root.winfo_screenheight()) - height) // 2)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.update_idletasks()
        self.window.deiconify()
        self.window.lift()
        self._schedule_animation()
        root.update()

    def _loading_text(self):
        label = translate_text(
            "手把測試程式啟動中", self.language
        )
        return f"v{VERSION} {label}{'.' * self._dot_count}"

    def _schedule_animation(self):
        try:
            self._animation_job = self.window.after(
                self.DOT_INTERVAL_MS,
                self._animate,
            )
        except (AttributeError, tk.TclError):
            self._animation_job = None

    def _animate(self):
        self._animation_job = None
        try:
            if not self.window.winfo_exists():
                return
            self._dot_count = (self._dot_count % 3) + 1
            self.canvas.itemconfigure(
                self._text_item,
                text=self._loading_text(),
            )
            self.window.update_idletasks()
        except (AttributeError, tk.TclError):
            return
        self._schedule_animation()

    def destroy(self):
        job = self._animation_job
        self._animation_job = None
        if job is not None:
            try:
                self.window.after_cancel(job)
            except (AttributeError, tk.TclError):
                pass
        try:
            self.window.destroy()
        except (AttributeError, tk.TclError):
            pass


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

    def __init__(
        self,
        root,
        language=None,
        allow_automatic_update_prompt=True,
    ):
        self.root = root
        self.allow_automatic_update_prompt = bool(
            allow_automatic_update_prompt
        )
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
        allow_automatic_update_prompt="--parent-pipe" not in sys.argv,
    )
    splash = None
    splash_started_at = time.monotonic()
    if "--parent-pipe" not in sys.argv:
        try:
            splash = GamepadTestStartupOverlay(
                root, language=host.language
            )
        except (OSError, tk.TclError):
            splash = None
    tester = GamepadTestWindow(host)
    tester.open()
    if splash is not None:
        delay_ms = max(
            0,
            int(round(
                (
                    GAMEPAD_TEST_SPLASH_MIN_SECONDS
                    - (time.monotonic() - splash_started_at)
                )
                * 1000.0
            )),
        )
        root.after(delay_ms, splash.destroy)

    closing = False

    def close_all():
        nonlocal closing
        if closing:
            return
        closing = True
        if splash is not None:
            splash.destroy()
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
