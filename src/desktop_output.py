"""Reference-counted Windows keyboard and mouse output management.

This module owns only desktop-output state.  It deliberately has no knowledge
of controller reports, ViGEm, mapping layers, transport threads, or gyro state.
"""

from __future__ import annotations

import ctypes
import math


_DEFAULT_USER32 = getattr(getattr(ctypes, "windll", None), "user32", None)


class _UnavailableUser32:
    def keybd_event(self, *_args):
        raise OSError("Windows keyboard output is unavailable on this platform")

    def mouse_event(self, *_args):
        raise OSError("Windows mouse output is unavailable on this platform")


_UNAVAILABLE_USER32 = _UnavailableUser32()


def _resolve_user32(backend=None):
    return backend or _DEFAULT_USER32 or _UNAVAILABLE_USER32


KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_EXTENDEDKEY = 0x0001
MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000

MOUSE_BUTTON_FLAGS = {
    "LEFT": (MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP),
    "RIGHT": (MOUSEEVENTF_RIGHTDOWN, MOUSEEVENTF_RIGHTUP),
    "MIDDLE": (MOUSEEVENTF_MIDDLEDOWN, MOUSEEVENTF_MIDDLEUP),
}

VK_KEYS = {
    "CTRL": 0x11,
    "SHIFT": 0x10,
    "ALT": 0x12,
    "WIN": 0x5B,
    "ENTER": 0x0D,
    "ESC": 0x1B,
    "SPACE": 0x20,
    "TAB": 0x09,
    "BACKSPACE": 0x08,
    "DELETE": 0x2E,
    "INSERT": 0x2D,
    "HOME": 0x24,
    "END": 0x23,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    **dict.fromkeys(("VOLUME_MUTE", "VK_VOLUME_MUTE", "XF86AUDIOMUTE"), 0xAD),
    **dict.fromkeys((
        "VOLUME_DOWN", "VK_VOLUME_DOWN", "XF86AUDIOLOWERVOLUME"
    ), 0xAE),
    **dict.fromkeys((
        "VOLUME_UP", "VK_VOLUME_UP", "XF86AUDIORAISEVOLUME"
    ), 0xAF),
    **dict.fromkeys((
        "MEDIA_NEXT", "MEDIA_NEXT_TRACK", "VK_MEDIA_NEXT_TRACK",
        "XF86AUDIONEXT",
    ), 0xB0),
    **dict.fromkeys((
        "MEDIA_PREV", "MEDIA_PREV_TRACK", "VK_MEDIA_PREV_TRACK",
        "XF86AUDIOPREV",
    ), 0xB1),
    **dict.fromkeys(("MEDIA_STOP", "VK_MEDIA_STOP", "XF86AUDIOSTOP"), 0xB2),
    **dict.fromkeys((
        "MEDIA_PLAY_PAUSE", "VK_MEDIA_PLAY_PAUSE", "XF86AUDIOPLAY",
        "XF86AUDIOPAUSE",
    ), 0xB3),
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    **{f"F{i}": 0x6F + i for i in range(1, 13)},
    **{str(i): 0x30 + i for i in range(10)},
    **{chr(code): code for code in range(ord("A"), ord("Z") + 1)},
}

EXTENDED_VK_KEYS = (
    set(range(0x21, 0x29))
    | {0x2D, 0x2E, 0x5B, 0x5C}
    | set(range(0xAD, 0xB4))
)


def parse_keyboard_combo(combo_text):
    """Convert a ``CTRL+SHIFT+S`` mapping to Windows virtual-key codes."""
    keys = []
    for key_name in str(combo_text or "").split("+"):
        key_name = key_name.strip().upper()
        if not key_name:
            continue
        vk_code = VK_KEYS.get(key_name)
        if vk_code is not None:
            keys.append(vk_code)
    return keys


def press_keyboard_combo(combo_text, backend=None):
    """Press one keyboard combination without source tracking."""
    backend = _resolve_user32(backend)
    for vk_code in parse_keyboard_combo(combo_text):
        backend.keybd_event(
            vk_code,
            0,
            KEYEVENTF_EXTENDEDKEY if vk_code in EXTENDED_VK_KEYS else 0,
            0,
        )


def release_keyboard_combo(combo_text, backend=None):
    """Release one keyboard combination in reverse key order."""
    backend = _resolve_user32(backend)
    for vk_code in reversed(parse_keyboard_combo(combo_text)):
        backend.keybd_event(
            vk_code,
            0,
            KEYEVENTF_KEYUP
            | (KEYEVENTF_EXTENDEDKEY if vk_code in EXTENDED_VK_KEYS else 0),
            0,
        )


def accumulate_wheel_detents(residual, x, y, rate, dt):
    """Integrate two-axis wheel speed and return bounded whole detents."""
    residual[0] += x * rate * dt
    residual[1] += y * rate * dt

    def whole_detents(value):
        if value >= 0.0:
            return math.floor(value + 1e-9)
        return math.ceil(value - 1e-9)

    horizontal = whole_detents(residual[0])
    vertical = whole_detents(residual[1])
    residual[0] -= horizontal
    residual[1] -= vertical
    return (
        max(-4, min(4, horizontal)),
        max(-4, min(4, vertical)),
    )


class DesktopOutputManager:
    """Track shared keyboard/mouse sources and emit each physical edge once."""

    def __init__(self, backend=None):
        self._user32 = _resolve_user32(backend)
        self.keyboard_combo_sources = {}
        self.keyboard_key_sources = {}
        self.mouse_button_sources = {
            button: set() for button in MOUSE_BUTTON_FLAGS
        }
        self.active_keyboard_buttons = set()
        self.active_mouse_buttons = {}
        self.active_stick_keyboard = {"LEFT": None, "RIGHT": None}
        self.active_stick_mouse_action = {"LEFT": None, "RIGHT": None}
        self.mouse_residual = [0.0, 0.0]
        self.wheel_residual = {
            "LEFT": [0.0, 0.0],
            "RIGHT": [0.0, 0.0],
        }

    def acquire_keyboard_combo(self, combo_text, source):
        combo_text = str(combo_text or "").strip().upper()
        if not combo_text:
            return
        keys = parse_keyboard_combo(combo_text)
        if not keys:
            return

        sources = self.keyboard_combo_sources.setdefault(combo_text, set())
        if source in sources:
            return

        source_token = (combo_text, source)
        for vk_code in keys:
            key_sources = self.keyboard_key_sources.setdefault(vk_code, set())
            if source_token in key_sources:
                continue
            if not key_sources:
                self._user32.keybd_event(
                    vk_code,
                    0,
                    KEYEVENTF_EXTENDEDKEY
                    if vk_code in EXTENDED_VK_KEYS else 0,
                    0,
                )
            key_sources.add(source_token)
        sources.add(source)

    def release_keyboard_combo_source(self, combo_text, source):
        combo_text = str(combo_text or "").strip().upper()
        if not combo_text:
            return
        sources = self.keyboard_combo_sources.get(combo_text)
        if not sources or source not in sources:
            return

        source_token = (combo_text, source)
        for vk_code in reversed(parse_keyboard_combo(combo_text)):
            key_sources = self.keyboard_key_sources.get(vk_code)
            if not key_sources:
                continue
            key_sources.discard(source_token)
            if key_sources:
                continue
            self._user32.keybd_event(
                vk_code,
                0,
                KEYEVENTF_KEYUP
                | (KEYEVENTF_EXTENDEDKEY
                   if vk_code in EXTENDED_VK_KEYS else 0),
                0,
            )
            self.keyboard_key_sources.pop(vk_code, None)

        sources.discard(source)
        if not sources:
            self.keyboard_combo_sources.pop(combo_text, None)

    def release_all_keyboard_buttons(self):
        for vk_code in reversed(list(self.keyboard_key_sources.keys())):
            self._user32.keybd_event(
                vk_code,
                0,
                KEYEVENTF_KEYUP
                | (KEYEVENTF_EXTENDEDKEY
                   if vk_code in EXTENDED_VK_KEYS else 0),
                0,
            )
        self.keyboard_key_sources.clear()
        self.keyboard_combo_sources.clear()
        self.active_keyboard_buttons.clear()
        for side in self.active_stick_keyboard:
            self.active_stick_keyboard[side] = None

    def acquire_mouse_button(self, button, source):
        button = str(button or "").strip().upper()
        flags = MOUSE_BUTTON_FLAGS.get(button)
        if flags is None:
            return
        sources = self.mouse_button_sources[button]
        if source in sources:
            return
        if not sources:
            self._user32.mouse_event(flags[0], 0, 0, 0, 0)
        sources.add(source)

    def release_mouse_button(self, button, source):
        button = str(button or "").strip().upper()
        flags = MOUSE_BUTTON_FLAGS.get(button)
        if flags is None:
            return
        sources = self.mouse_button_sources[button]
        if source not in sources:
            return
        sources.discard(source)
        if not sources:
            self._user32.mouse_event(flags[1], 0, 0, 0, 0)

    def emit_mouse_wheel(self, direction):
        horizontal = direction in ("WHEEL_LEFT", "WHEEL_RIGHT")
        positive = direction in ("WHEEL_UP", "WHEEL_RIGHT")
        delta = 120 if positive else -120
        flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
        self._user32.mouse_event(flag, 0, 0, ctypes.c_long(delta), 0)

    def release_all_mouse_buttons(self):
        for button, sources in self.mouse_button_sources.items():
            if sources:
                self._user32.mouse_event(
                    MOUSE_BUTTON_FLAGS[button][1], 0, 0, 0, 0
                )
            sources.clear()
        self.active_mouse_buttons.clear()
        for side in self.active_stick_mouse_action:
            self.active_stick_mouse_action[side] = None

    def emit_mouse_move(self, delta_x, delta_y):
        self.mouse_residual[0] += delta_x
        self.mouse_residual[1] += delta_y
        move_x = int(self.mouse_residual[0])
        move_y = int(self.mouse_residual[1])
        self.mouse_residual[0] -= move_x
        self.mouse_residual[1] -= move_y
        if move_x or move_y:
            self._user32.mouse_event(
                MOUSEEVENTF_MOVE,
                ctypes.c_long(move_x),
                ctypes.c_long(move_y),
                0,
                0,
            )

    def reset_motion_residuals(self):
        self.mouse_residual[:] = (0.0, 0.0)
        self.reset_wheel_residuals()

    def reset_wheel_residuals(self):
        for residual in self.wheel_residual.values():
            residual[:] = (0.0, 0.0)
