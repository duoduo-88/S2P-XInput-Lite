import threading
import time
import ctypes
import math
import imufusion
import numpy as np
import vgamepad as vg
from console_i18n import current_language
from console_i18n import localized_print as print

from imu_calibration import (
    fit_accelerometer_ellipsoid,
    fit_magnetometer_ellipsoid,
    gyro_calibration_quality,
)
from switch2_input import SWITCH_BUTTONS
from config_utils import parse_output_shape_steps
from mapping_layers import LayerSelector, load_layers
from stick_curve import apply_stick_curve


def _tr(zh, en):
    return en if current_language() == "en" else zh


XB_BUTTONS = {
    "UP": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP,
    "DOWN": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN,
    "LEFT": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT,
    "RIGHT": vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT,
    "START": vg.XUSB_BUTTON.XUSB_GAMEPAD_START,
    "BACK": vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK,
    "L_STK": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB,
    "R_STK": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB,
    "LB": vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER,
    "RB": vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER,
    "GUIDE": vg.XUSB_BUTTON.XUSB_GAMEPAD_GUIDE,
    "A": vg.XUSB_BUTTON.XUSB_GAMEPAD_A,
    "B": vg.XUSB_BUTTON.XUSB_GAMEPAD_B,
    "X": vg.XUSB_BUTTON.XUSB_GAMEPAD_X,
    "Y": vg.XUSB_BUTTON.XUSB_GAMEPAD_Y,
}

_MAP_NONE = 0
_MAP_MOUSE = 1
_MAP_KEYBOARD = 2
_MAP_LX = 3
_MAP_LY = 4
_MAP_RX = 5
_MAP_RY = 6
_MAP_LT = 7
_MAP_RT = 8
_MAP_BUTTON = 9


def _compile_button_mapping(mapping):
    """Compile config strings into branch-friendly hot-path operations."""
    compiled = []
    axis_targets = {
        "L_STICK_LEFT": (_MAP_LX, -1.0),
        "L_STICK_RIGHT": (_MAP_LX, 1.0),
        "L_STICK_DOWN": (_MAP_LY, -1.0),
        "L_STICK_UP": (_MAP_LY, 1.0),
        "R_STICK_LEFT": (_MAP_RX, -1.0),
        "R_STICK_RIGHT": (_MAP_RX, 1.0),
        "R_STICK_DOWN": (_MAP_RY, -1.0),
        "R_STICK_UP": (_MAP_RY, 1.0),
    }
    for name, target in mapping.items():
        kind = _MAP_NONE
        value = None
        if target.startswith("MOUSE:"):
            kind = _MAP_MOUSE
            candidate = target[len("MOUSE:"):]
            value = candidate if candidate in MOUSE_BUTTON_FLAGS else None
        elif target.startswith("KEYBOARD:"):
            kind = _MAP_KEYBOARD
            value = target[len("KEYBOARD:"):].strip()
        elif target in axis_targets:
            kind, value = axis_targets[target]
        elif target == "LT":
            kind = _MAP_LT
        elif target == "RT":
            kind = _MAP_RT
        elif target in XB_BUTTONS:
            kind = _MAP_BUTTON
            value = int(XB_BUTTONS[target])
        compiled.append(
            (name, SWITCH_BUTTONS.get(name), kind, value, "BUTTON:" + name)
        )
    return tuple(compiled)


_STICK_DIRECTION_NAMES = (
    "UP", "UP_RIGHT", "RIGHT", "DOWN_RIGHT",
    "DOWN", "DOWN_LEFT", "LEFT", "UP_LEFT",
)


def _compile_stick_direction_settings(raw_sides):
    """Normalize serialized left/right direction settings for the hot path."""
    result = {}
    for side in ("LEFT", "RIGHT"):
        raw = raw_sides[side.lower()]
        mode = str(raw.get("mode", "4WAY")).strip().upper()
        if mode not in (
            "4WAY", "8WAY", "映射為右搖桿", "映射為左搖桿", "映射為滑鼠",
            "MOUSE_WHEEL_LINEAR", "XINPUT_LT_LINEAR", "XINPUT_RT_LINEAR",
        ):
            mode = "4WAY"
        trigger = max(0.0, min(1.0, float(raw.get("trigger_threshold", 0.60))))
        release = max(
            0.0,
            min(trigger, float(raw.get("release_threshold", 0.50))),
        )
        result[side] = {
            "mode": mode,
            "mappings": {
                direction: str(raw.get(direction.lower(), "NONE")).strip().upper()
                for direction in _STICK_DIRECTION_NAMES
            },
            "trigger_threshold": trigger,
            "release_threshold": release,
            "direction_deadzone": max(
                0.0, min(20.0, float(raw.get("direction_deadzone", 5.0)))
            ),
            "mouse_speed": max(
                100.0, min(3000.0, float(raw.get("mouse_speed", 900.0)))
            ),
            "mouse_deadzone": max(
                0.0, min(0.30, float(raw.get("mouse_deadzone", 0.05)))
            ),
            "analog_direction": str(
                raw.get("analog_direction", "UP")
            ).strip().upper()
            if str(raw.get("analog_direction", "UP")).strip().upper()
            in ("UP", "DOWN", "LEFT", "RIGHT") else "UP",
        }
    return result


def _directional_axis_value(x, y, direction):
    """Return the positive curved stick amount for one cardinal direction."""
    return max(0.0, min(1.0, {
        "UP": y,
        "DOWN": -y,
        "LEFT": -x,
        "RIGHT": x,
    }.get(direction, y)))


def _apply_compressed_radial_deadzone(x, y, deadzone):
    """Remove center drift while preserving the full output range."""
    deadzone = max(0.0, min(0.30, float(deadzone)))
    magnitude = math.hypot(x, y)
    if magnitude <= deadzone or magnitude <= 1e-9:
        return 0.0, 0.0
    clamped_magnitude = min(1.0, magnitude)
    scaled_magnitude = (clamped_magnitude - deadzone) / (1.0 - deadzone)
    scale = scaled_magnitude / magnitude
    return x * scale, y * scale


def _accumulate_wheel_detents(residual, x, y, rate, dt):
    """Integrate a two-axis wheel rate and return bounded new detents."""
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


def _soft_mix(first, second, maximum):
    """Combine amplitudes without the harsh clipping of plain addition."""
    if maximum <= 0:
        return 0
    first = max(0.0, min(1.0, first / maximum))
    second = max(0.0, min(1.0, second / maximum))
    return int(maximum * (1.0 - (1.0 - first) * (1.0 - second)))


def _update_rumble_tail(
    current, target, strength, decay_factor, target_changed=False,
):
    """Apply an immediate rise and a configurable decaying fall."""
    target = max(0.0, float(target))
    strength = max(0.0, min(1.0, float(strength)))
    if strength <= 0.0:
        return target
    current = max(0.0, float(current))
    if target >= current:
        return target
    if target_changed:
        current = target + (current - target) * strength
    return max(target, current * max(0.0, min(1.0, decay_factor)))


def _adaptive_gyro_deadzone(base_deadzone, motion_envelope, strength):
    """Reduce center-mode deadzone smoothly once motion is intentional."""
    base = max(0.0, float(base_deadzone))
    amount = max(0.0, min(1.0, float(strength)))
    motion = max(0.0, min(1.0, float(motion_envelope) / 6.0))
    return base * (1.0 - amount * motion)


def _adaptive_gyro_smoothing_ms(base_ms, angular_speed):
    """Use stronger smoothing for micro-aim and about 5 ms for fast turns."""
    base = max(0.0, float(base_ms))
    if base <= 0.0:
        return 0.0
    minimum = min(base, 5.0)
    ratio = max(0.0, min(1.0, (float(angular_speed) - 5.0) / 75.0))
    ratio = ratio * ratio * (3.0 - 2.0 * ratio)
    return base + (minimum - base) * ratio

def _soft_deadzone(value, deadzone):
    magnitude = abs(value)
    if magnitude <= deadzone:
        return 0.0
    return math.copysign(magnitude - deadzone, value)


def _apply_gyro_stick_anti_deadzone(x, y, anti_deadzone, existing_magnitude=0.0):
    """Radially cross a game's stick DZ without altering the physical stick."""
    magnitude = math.hypot(float(x), float(y))
    if magnitude <= 1e-12:
        return 0.0, 0.0
    anti = max(0.0, min(0.95, float(anti_deadzone)))
    existing = max(0.0, min(1.0, float(existing_magnitude)))
    remaining = max(0.0, anti - existing)
    if remaining <= 0.0 or magnitude >= 1.0:
        return float(x), float(y)
    mapped_magnitude = remaining + (1.0 - remaining) * magnitude
    scale = mapped_magnitude / magnitude
    return float(x) * scale, float(y) * scale

def _apply_gyro_response_curve(x, y, curve_mode, strength):
    """Apply a direction-preserving S-curve to Center stick output."""
    magnitude = math.hypot(float(x), float(y))
    if magnitude <= 1e-12 or magnitude >= 1.0:
        return float(x), float(y)
    mode = str(curve_mode).strip().upper()
    if mode == "DYNAMIC":
        mode = "LATE"
    if mode not in ("LATE", "EARLY"):
        return float(x), float(y)
    blend = max(0.0, min(1.0, float(strength)))
    if blend <= 0.0:
        return float(x), float(y)
    smooth = magnitude * magnitude * (3.0 - 2.0 * magnitude)
    delta = smooth - magnitude
    if mode == "EARLY":
        delta = -delta
    mapped = magnitude + delta * blend
    scale = mapped / magnitude
    return float(x) * scale, float(y) * scale

def _parse_gyro_button_list(raw_value, valid_buttons, fallback=()):
    """Parse a stable, de-duplicated controller-button list."""
    valid = set(valid_buttons)
    result = []
    for item in str(raw_value or "").split(","):
        name = item.strip().upper()
        if name and name != "NONE" and name in valid and name not in result:
            result.append(name)
    if result:
        return tuple(result)
    return tuple(name for name in fallback if name in valid)

def _clamp_vector_to_shape(x, y, shape_blend):
    """Clamp a summed vector to the configured circle/square boundary."""
    magnitude = math.hypot(x, y)
    if magnitude <= 0.0:
        return 0.0, 0.0
    unit_x = x / magnitude
    unit_y = y / magnitude
    square_radius = 1.0 / max(abs(unit_x), abs(unit_y))
    maximum_radius = 1.0 + (square_radius - 1.0) * max(
        0.0, min(1.0, shape_blend)
    )
    if magnitude > maximum_radius:
        scale = maximum_radius / magnitude
        x *= scale
        y *= scale
    return max(-1.0, min(1.0, x)), max(-1.0, min(1.0, y))

# Windows 鍵盤輸入
user32 = ctypes.windll.user32

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
    # 修飾鍵
    "CTRL": 0x11,
    "SHIFT": 0x10,
    "ALT": 0x12,
    "WIN": 0x5B,

    # 常用控制鍵
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

    # Windows multimedia keys and the XF86 keysyms emitted by Tk on Windows.
    # Keeping both spellings makes existing KEYBOARD:XF86... mappings work
    # while also allowing explicit VK_* names in config.ini.
    **dict.fromkeys((
        "VOLUME_MUTE", "VK_VOLUME_MUTE", "XF86AUDIOMUTE"
    ), 0xAD),
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
    **dict.fromkeys((
        "MEDIA_STOP", "VK_MEDIA_STOP", "XF86AUDIOSTOP"
    ), 0xB2),
    **dict.fromkeys((
        "MEDIA_PLAY_PAUSE", "VK_MEDIA_PLAY_PAUSE", "XF86AUDIOPLAY",
        "XF86AUDIOPAUSE",
    ), 0xB3),

    # 方向鍵
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,

    # 功能鍵
    **{
        f"F{i}": 0x6F + i
        for i in range(1, 13)
    },

    # 數字
    **{
        str(i): 0x30 + i
        for i in range(10)
    },

    # 英文字母
    **{
        chr(code): code
        for code in range(
            ord("A"),
            ord("Z") + 1
        )
    },
}

EXTENDED_VK_KEYS = set(range(0xAD, 0xB4))

def parse_keyboard_combo(combo_text):
    """
    將例如 CTRL+SHIFT+S
    轉換成虛擬鍵碼列表。
    """
    keys = []

    for key_name in combo_text.split("+"):
        key_name = key_name.strip().upper()

        if not key_name:
            continue

        vk_code = VK_KEYS.get(key_name)

        if vk_code is not None:
            keys.append(vk_code)

    return keys


def press_keyboard_combo(combo_text):
    """按下鍵盤組合鍵。"""

    for vk_code in parse_keyboard_combo(
        combo_text
    ):
        user32.keybd_event(
            vk_code,
            0,
            KEYEVENTF_EXTENDEDKEY if vk_code in EXTENDED_VK_KEYS else 0,
            0
        )


def release_keyboard_combo(combo_text):
    """放開鍵盤組合鍵，使用相反順序避免修飾鍵卡住。"""

    keys = parse_keyboard_combo(
        combo_text
    )

    for vk_code in reversed(keys):
        user32.keybd_event(
            vk_code,
            0,
            KEYEVENTF_KEYUP
            | (KEYEVENTF_EXTENDEDKEY if vk_code in EXTENDED_VK_KEYS else 0),
            0
        )

def apply_calibration_to_axis(raw_value, center, max_abs, min_abs):
    """Normalize the positive and negative travel around center independently."""
    signed_value = raw_value - center
    if signed_value > 0:
        return min(signed_value / max(1, max_abs), 1.0)
    if signed_value < 0:
        return -min(-signed_value / max(1, min_abs), 1.0)
    return 0.0

def get_stick_curve_slope(
    value,
    curve_points,
    interpolation="LINEAR"
):
    """
    取得目前徑向輸入位置所處曲線區段的斜率。

    曲線包含：
    固定原點 (0, 0)
    5 個可調控制點
    固定終點 (1, 1)

    回傳值：
    0.0 以上的實際區段斜率。

    如果兩個控制點的 X 幾乎重疊，
    視為極端陡峭區段，回傳 float("inf")。
    """

    magnitude = max(
        0.0,
        min(
            1.0,
            abs(value)
        )
    )

    if interpolation.strip().upper() == "SMOOTH":
        epsilon = 1e-4
        low = max(0.0, magnitude - epsilon)
        high = min(1.0, magnitude + epsilon)
        if high <= low:
            return 0.0
        return max(
            0.0,
            (
                apply_stick_curve(high, curve_points, "SMOOTH")
                - apply_stick_curve(low, curve_points, "SMOOTH")
            ) / (high - low),
        )

    # =========================
    # 第一個控制點之前
    # 固定原點 (0, 0)
    # → 第一個控制點
    # =========================

    first_x, first_y = curve_points[0]

    if magnitude <= first_x:
        if first_x > 1e-9:
            return max(
                0.0,
                first_y / first_x
            )

        # X 幾乎重疊，但 Y 有變化：
        # 視為極端陡峭。
        if abs(first_y) > 1e-9:
            return float("inf")

        return 0.0

    # =========================
    # 5 個控制點之間
    # =========================

    for index in range(
        len(curve_points) - 1
    ):
        x1, y1 = curve_points[index]
        x2, y2 = curve_points[
            index + 1
        ]

        if (
            x1
            <= magnitude
            <= x2
        ):
            delta_x = x2 - x1
            delta_y = y2 - y1

            if abs(delta_x) < 1e-9:
                if abs(delta_y) > 1e-9:
                    return float("inf")

                return 0.0

            return max(
                0.0,
                delta_y / delta_x
            )

    # =========================
    # 最後一個控制點之後
    # 最後控制點 → 固定終點 (1, 1)
    # =========================

    last_x, last_y = curve_points[-1]

    delta_x = 1.0 - last_x
    delta_y = 1.0 - last_y

    if abs(delta_x) < 1e-9:
        if abs(delta_y) > 1e-9:
            return float("inf")

        return 0.0

    return max(
        0.0,
        delta_y / delta_x
    )


def apply_output_shape(x, y, shape_blend):
    """Blend continuously from a circular range (0.0) to square (1.0)."""
    magnitude = (x * x + y * y) ** 0.5
    if magnitude <= 0.0:
        return 0.0, 0.0

    circle_magnitude = min(1.0, magnitude)
    x = x / magnitude * circle_magnitude
    y = y / magnitude * circle_magnitude
    shape_blend = max(0.0, min(1.0, float(shape_blend)))
    if shape_blend > 0.0:
        largest_axis = max(abs(x), abs(y))
        if largest_axis > 0.0:
            square_scale = circle_magnitude / largest_axis
            blended_scale = 1.0 + (
                square_scale - 1.0
            ) * shape_blend
            x *= blended_scale
            y *= blended_scale

    return (
        max(-1.0, min(1.0, x)),
        max(-1.0, min(1.0, y)),
    )


class XInputController:
    GYRO_STATIONARY_SETTLE_SECONDS = 0.50
    GYRO_INITIAL_ABS_LIMIT = 80.0
    GYRO_TRACK_RESIDUAL_LIMIT = 12.0
    GYRO_TRACK_MAX_DRIFT = 18.0
    IMPACT_BIAS_HOLD_SECONDS = 0.30
    IMPACT_ACCEL_REJECT_SECONDS = 0.06
    IMPACT_ACCEL_RECOVERY_SECONDS = 0.20
    MAGNETOMETER_TIMEOUT_SECONDS = 0.50
    MAGNETOMETER_RECOVERY_SECONDS = 0.75
    AIM_POSE_SETTLE_SECONDS = 0.08
    AIM_BLEND_SECONDS = 0.25
    ACCEL_MIN_ORIENTATION_BINS = 14
    MAG_MIN_ORIENTATION_BINS = 18

    def __init__(
        self, config, calibration, pad=None, activate_runtime=True,
        layer_state_config_path=None,
    ):
        # Parsing a profile/snapshot is read-only unless the real connector
        # explicitly grants the path used to reconcile missing layer IDs.
        self._layer_state_config_path = layer_state_config_path
        # ``activate_runtime=False`` builds a pure-Python settings/state
        # snapshot for reconfigure().  It must never create a ViGEm target,
        # register a native callback, or start another rumble thread.
        self.pad = (
            pad
            if pad is not None
            else (vg.VX360Gamepad() if activate_runtime else None)
        )
        self.config = config
        self.cal = calibration
        # =========================
        # 左右搖桿死區設定
        # =========================
        # =========================
        # 左搖桿
        # =========================

        self.left_deadzone = max(
            0.0,
            min(
                1.0,
                config.getfloat(
                    "stick_curve_left",
                    "deadzone",
                    fallback=0.03
                )
            )
        )

        self.left_outer_deadzone = max(
            0.0,
            min(
                1.0,
                config.getfloat(
                    "stick_curve_left",
                    "outer_deadzone",
                    fallback=0.03
                )
            )
        )

        self.left_deadzone_compress = (
            config.getboolean(
                "stick_curve_left",
                "deadzone_compress",
                fallback=True
            )
        )

        self.left_outer_deadzone_compress = (
            config.getboolean(
                "stick_curve_left",
                "outer_deadzone_compress",
                fallback=True
            )
        )

        # =========================
        # 右搖桿
        # =========================

        self.right_deadzone = max(
            0.0,
            min(
                1.0,
                config.getfloat(
                    "stick_curve_right",
                    "deadzone",
                    fallback=0.03
                )
            )
        )

        self.right_outer_deadzone = max(
            0.0,
            min(
                1.0,
                config.getfloat(
                    "stick_curve_right",
                    "outer_deadzone",
                    fallback=0.03
                )
            )
        )

        self.right_deadzone_compress = (
            config.getboolean(
                "stick_curve_right",
                "deadzone_compress",
                fallback=True
            )
        )

        self.right_outer_deadzone_compress = (
            config.getboolean(
                "stick_curve_right",
                "outer_deadzone_compress",
                fallback=True
            )
        )  

 
        # Normalize the static mapping once.  update() runs at 125+ Hz and must
        # not repeat string cleanup and SWITCH_BUTTONS dictionary lookups for
        # every report.
        self.mapping = {
            str(name).strip().upper(): str(target).strip().upper()
            for name, target in config.items("buttons")
        }
        self._compiled_mapping = _compile_button_mapping(self.mapping)

        # 左右搖桿的 5 點 XY 反應曲線
        #
        # 每個控制點包含：
        # x = 原始搖桿輸入大小
        # y = 套用曲線後的輸出大小
        #
        # 例如：
        # (0.30, 0.50)
        # 表示原始輸入 30% 時，輸出為 50%
        self.stick_curves = {
            "left": [
                (
                    max(
                        0.0,
                        min(
                            1.0,
                            config.getfloat(
                                "stick_curve_left",
                                f"point_{i}_x",
                                fallback=i * 0.25
                            )
                        )
                    ),
                    max(
                        0.0,
                        min(
                            1.0,
                            config.getfloat(
                                "stick_curve_left",
                                f"point_{i}_y",
                                fallback=config.getfloat(
                                    "stick_curve_left",
                                    f"point_{i}",
                                    fallback=i * 0.25
                                )
                            )
                        )
                    ),
                )
                for i in range(5)
            ],

            "right": [
                (
                    max(
                        0.0,
                        min(
                            1.0,
                            config.getfloat(
                                "stick_curve_right",
                                f"point_{i}_x",
                                fallback=i * 0.25
                            )
                        )
                    ),
                    max(
                        0.0,
                        min(
                            1.0,
                            config.getfloat(
                                "stick_curve_right",
                                f"point_{i}_y",
                                fallback=config.getfloat(
                                    "stick_curve_right",
                                    f"point_{i}",
                                    fallback=i * 0.25
                                )
                            )
                        )
                    ),
                )
                for i in range(5)
            ],
        }
        default_curve = (
            (0.00, 0.00),
            (0.25, 0.25),
            (0.50, 0.50),
            (0.75, 0.75),
            (1.00, 1.00),
        )
        self._stick_curve_is_default = {
            side: all(
                abs(point_x - default_x) < 1e-6
                and abs(point_y - default_y) < 1e-6
                for (point_x, point_y), (default_x, default_y) in zip(
                    self.stick_curves[side], default_curve
                )
            )
            for side in ("left", "right")
        }
        self.stick_curve_interpolation = {}
        for side in ("left", "right"):
            mode = config.get(
                f"stick_curve_{side}",
                "interpolation",
                fallback="LINEAR",
            ).strip().upper()
            self.stick_curve_interpolation[side] = (
                mode if mode in ("LINEAR", "SMOOTH") else "LINEAR"
            )

        # =========================
        # 左右搖桿防抖設定
        # =========================
        #
        # GUI 儲存範圍：0.0 ～ 3.0
        #
        # 0.0：
        # 完全關閉防抖，嚴格旁路濾波。
        #
        # 1.0：
        # 標準補償。
        # 曲線放大幾倍，
        # 就按相同倍率增加防抖。
        #
        # 2.0 ～ 3.0：
        # 進一步加強曲線放大區域的防抖。

        self.stick_smoothing = {
            "left": max(
                0.0,
                min(
                    3.0,
                    config.getfloat(
                        "stick_curve_left",
                        "smoothing",
                        fallback=0.0
                    )
                )
            ),

            "right": max(
                0.0,
                min(
                    3.0,
                    config.getfloat(
                        "stick_curve_right",
                        "smoothing",
                        fallback=0.0
                    )
                )
            ),
        }
        # =========================
        # 防抖上一幀輸出狀態
        # =========================
        #
        # None 表示尚未建立歷史值。
        # 左右搖桿完全獨立。

        self._stick_smoothed_magnitude = {
            "left": None,
            "right": None,
        }
        self._stick_smoothing_time = {
            "left": None,
            "right": None,
        }

        self.stick_output_shape = {}
        for side in ("left", "right"):
            self.stick_output_shape[side] = (
                parse_output_shape_steps(
                    config.get(
                        f"stick_curve_{side}",
                        "output_shape",
                        fallback="CIRCLE",
                    )
                ) / 10.0
            )

        # 記錄目前由 Switch 按鍵保持按下的鍵盤組合
        self._active_keyboard_buttons = set()

        # =========================
        # 搖桿方向映射設定
        # =========================

        # 舊版共用值僅作為左右獨立設定的移植回退。
        legacy_direction_trigger = config.getfloat(
            "stick_direction", "trigger_threshold", fallback=0.60
        )
        legacy_direction_release = config.getfloat(
            "stick_direction", "release_threshold", fallback=0.50
        )
        legacy_direction_deadzone = config.getfloat(
            "stick_direction", "angle_hysteresis", fallback=5.0
        )
        legacy_mouse_speed = config.getfloat(
            "stick_direction", "mouse_speed", fallback=900.0
        )

        # 8 個可能方向
        direction_names = (
            "UP",
            "UP_RIGHT",
            "RIGHT",
            "DOWN_RIGHT",
            "DOWN",
            "DOWN_LEFT",
            "LEFT",
            "UP_LEFT",
        )

        # 左右搖桿的模式與方向映射
        self.stick_direction_config = {}

        for side in (
            "LEFT",
            "RIGHT"
        ):
            section_name = (
                "stick_direction_"
                + side.lower()
            )

            mode = config.get(
                section_name,
                "mode",
                fallback="4WAY"
            ).strip().upper()

            # 防止 config.ini 出現未知值
            if mode not in (
                "4WAY",
                "8WAY",
                "映射為右搖桿",
                "映射為左搖桿",
                "映射為滑鼠",
            ):
                mode = "4WAY"

            mappings = {}

            for direction in direction_names:
                mappings[
                    direction
                ] = config.get(
                    section_name,
                    direction.lower(),
                    fallback="NONE"
                ).strip().upper()

            trigger_threshold = max(
                0.0,
                min(
                    1.0,
                    config.getfloat(
                        section_name,
                        "trigger_threshold",
                        fallback=legacy_direction_trigger,
                    ),
                ),
            )
            release_threshold = max(
                0.0,
                min(
                    trigger_threshold,
                    config.getfloat(
                        section_name,
                        "release_threshold",
                        fallback=legacy_direction_release,
                    ),
                ),
            )
            direction_deadzone = max(
                0.0,
                min(
                    20.0,
                    config.getfloat(
                        section_name,
                        "direction_deadzone",
                        fallback=legacy_direction_deadzone,
                    ),
                ),
            )
            mouse_speed = max(
                100.0,
                min(
                    3000.0,
                    config.getfloat(
                        section_name,
                        "mouse_speed",
                        fallback=legacy_mouse_speed,
                    ),
                ),
            )
            mouse_deadzone = max(
                0.0,
                min(
                    0.30,
                    config.getfloat(
                        section_name,
                        "mouse_deadzone",
                        fallback=0.05,
                    ),
                ),
            )

            self.stick_direction_config[
                side
            ] = {
                "mode": mode,
                "mappings": mappings,
                "trigger_threshold": trigger_threshold,
                "release_threshold": release_threshold,
                "direction_deadzone": direction_deadzone,
                "mouse_speed": mouse_speed,
                "mouse_deadzone": mouse_deadzone,
            }

        self._stick_direction_mapping_enabled = {
            side: self._is_stick_direction_mapping_enabled(side)
            for side in ("LEFT", "RIGHT")
        }

        # Secondary layers are complete snapshots of button and stick-direction
        # mappings. Curves, gyro, rumble, and calibration intentionally remain
        # global. Switching only replaces these precompiled hot-path pointers.
        base_buttons = {
            name.lower(): target for name, target in self.mapping.items()
        }
        base_sticks = {
            side: dict(config.items(f"stick_direction_{side}"))
            for side in ("left", "right")
        }
        self.mapping_layers = load_layers(
            config,
            base_buttons,
            base_sticks,
            SWITCH_BUTTONS,
            config_path=self._layer_state_config_path,
        )
        self._base_mapping_runtime = (
            self._compiled_mapping,
            self.stick_direction_config,
            self._stick_direction_mapping_enabled,
        )
        self._mapping_layer_runtimes = {}
        for layer in self.mapping_layers:
            stick_config = _compile_stick_direction_settings({
                "left": layer["stick_left"],
                "right": layer["stick_right"],
            })
            enabled = {
                side: self._is_stick_direction_config_enabled(
                    stick_config[side]
                )
                for side in ("LEFT", "RIGHT")
            }
            self._mapping_layer_runtimes[layer["id"]] = (
                _compile_button_mapping({
                    name.upper(): target
                    for name, target in layer["buttons"].items()
                }),
                stick_config,
                enabled,
            )
        self._mapping_layer_selector = LayerSelector(
            self.mapping_layers, SWITCH_BUTTONS
        )
        self._active_mapping_layer_id = None
        self._mapping_layer_last_buttons = None
        self._main_mapping_neutral_raw = {"LEFT": None, "RIGHT": None}
        self._mapping_layer_neutral_raw = {"LEFT": None, "RIGHT": None}
        self._main_mapping_neutral_samples = {"LEFT": None, "RIGHT": None}
        self._mapping_layer_neutral_samples = {"LEFT": None, "RIGHT": None}

        # =========================
        # 搖桿方向目前狀態
        # =========================
        #
        # None：
        # 目前沒有任何方向被觸發。
        #
        # 之後只有方向真正改變時，
        # 才執行按下 / 放開。

        self._active_stick_directions = {
            "LEFT": None,
            "RIGHT": None,
        }

        # 記錄由搖桿方向映射保持按下的
        # 鍵盤組合。
        self._active_stick_keyboard = {
            "LEFT": None,
            "RIGHT": None,
        }

        # =========================
        # 陀螺儀映射
        # =========================
        self.gyro_activation_mode = config.get(
            "gyro_mapping", "activation_mode", fallback="OFF"
        ).strip().upper()
        if self.gyro_activation_mode not in ("OFF", "HOLD", "TOGGLE"):
            self.gyro_activation_mode = "OFF"
        legacy_activation_button = config.get(
            "gyro_mapping", "activation_button", fallback="ZL"
        )
        raw_activation_buttons = config.get(
            "gyro_mapping",
            "activation_buttons",
            fallback=legacy_activation_button,
        )
        self.gyro_activation_buttons = _parse_gyro_button_list(
            raw_activation_buttons, SWITCH_BUTTONS, fallback=("ZL",)
        )
        self.gyro_activation_button = self.gyro_activation_buttons[0]
        self.gyro_activation_match = config.get(
            "gyro_mapping", "activation_match", fallback="ANY"
        ).strip().upper()
        if self.gyro_activation_match not in ("ANY", "ALL"):
            self.gyro_activation_match = "ANY"
        self.gyro_target = config.get(
            "gyro_mapping", "target", fallback="RIGHT_STICK"
        ).strip().upper()
        if self.gyro_target not in ("LEFT_STICK", "RIGHT_STICK", "MOUSE"):
            self.gyro_target = "RIGHT_STICK"
        self.gyro_motion_mode = config.get(
            "gyro_mapping", "motion_mode", fallback="CENTER"
        ).strip().upper()
        if self.gyro_motion_mode not in ("CENTER", "TILT"):
            self.gyro_motion_mode = "CENTER"
        # Tilt represents a held stick position and is therefore not meaningful
        # for relative mouse movement.  Keep malformed/manual configs safe.
        if self.gyro_target == "MOUSE" and self.gyro_motion_mode == "TILT":
            self.gyro_motion_mode = "CENTER"
        self.gyro_tilt_axis = config.get(
            "gyro_mapping", "tilt_axis", fallback="HORIZONTAL"
        ).strip().upper()
        if self.gyro_tilt_axis not in ("HORIZONTAL", "DUAL"):
            self.gyro_tilt_axis = "HORIZONTAL"
        self.gyro_tilt_recenter_button = config.get(
            "gyro_mapping", "tilt_recenter_button", fallback="NONE"
        ).strip().upper()
        if (
            self.gyro_tilt_recenter_button != "NONE"
            and self.gyro_tilt_recenter_button not in SWITCH_BUTTONS
        ):
            self.gyro_tilt_recenter_button = "NONE"
        self.gyro_tilt_max_angle = max(
            10.0,
            min(60.0, config.getfloat(
                "gyro_mapping", "tilt_max_angle", fallback=35.0
            )),
        )
        self.gyro_tilt_deadzone = max(
            0.0,
            min(5.0, self.gyro_tilt_max_angle - 1.0, config.getfloat(
                "gyro_mapping", "tilt_deadzone", fallback=0.8
            )),
        )
        self.gyro_tilt_smoothing_ms = max(
            0.0,
            min(150.0, config.getfloat(
                "gyro_mapping", "tilt_smoothing_ms", fallback=8.0
            )),
        )
        self.gyro_stick_sensitivity = max(
            0.1,
            min(
                10.0,
                config.getfloat(
                    "gyro_mapping", "stick_sensitivity", fallback=1.5
                ),
            ),
        )
        self.gyro_response_curve = config.get(
            "gyro_mapping", "response_curve", fallback="LINEAR"
        ).strip().upper()
        if self.gyro_response_curve == "DYNAMIC":
            self.gyro_response_curve = "LATE"
        if self.gyro_response_curve not in ("LINEAR", "LATE", "EARLY"):
            self.gyro_response_curve = "LINEAR"
        self.gyro_curve_strength = max(
            0.0,
            min(1.0, config.getfloat(
                "gyro_mapping", "curve_strength", fallback=5.0
            ) / 10.0),
        )
        self.gyro_mouse_sensitivity = max(
            0.5,
            min(
                30.0,
                config.getfloat(
                    "gyro_mapping", "mouse_sensitivity", fallback=8.0
                ),
            ),
        )
        self.gyro_deadzone = max(
            0.0,
            min(
                5.0,
                config.getfloat("gyro_mapping", "deadzone", fallback=0.6),
            ),
        )
        self.gyro_stick_anti_deadzone = max(
            0.0,
            min(0.30, config.getfloat(
                "gyro_mapping", "stick_anti_deadzone", fallback=0.0
            ) / 100.0),
        )
        self.gyro_smoothing_ms = max(
            0.0,
            min(
                100.0,
                config.getfloat(
                    "gyro_mapping", "smoothing_ms", fallback=5.0
                ),
            ),
        )
        self.gyro_player_space = config.getboolean(
            "gyro_mapping", "player_space", fallback=True
        )
        self.gyro_accel_suppression = max(
            0.0,
            min(1.0, config.getfloat(
                "gyro_mapping", "accel_suppression", fallback=70.0
            ) / 100.0),
        )
        self.gyro_adaptive_deadzone = max(
            0.0,
            min(1.0, config.getfloat(
                "gyro_mapping", "adaptive_deadzone", fallback=85.0
            ) / 100.0),
        )
        self.gyro_button_freeze_ms = max(
            0.0,
            min(120.0, config.getfloat(
                "gyro_mapping", "button_freeze_ms", fallback=5.0
            )),
        )
        legacy_stabilization_button = config.get(
            "gyro_mapping", "stabilization_button", fallback="NONE"
        )
        raw_stabilization_buttons = config.get(
            "gyro_mapping",
            "stabilization_buttons",
            fallback=legacy_stabilization_button,
        )
        self.gyro_stabilization_buttons = _parse_gyro_button_list(
            raw_stabilization_buttons, SWITCH_BUTTONS
        )
        self.gyro_stabilization_button = (
            self.gyro_stabilization_buttons[0]
            if self.gyro_stabilization_buttons else "NONE"
        )
        self.gyro_x_ratio = max(
            0.5,
            min(
                2.0,
                config.getfloat("gyro_mapping", "x_ratio", fallback=1.0),
            ),
        )
        self.gyro_y_ratio = max(
            0.5,
            min(
                2.0,
                config.getfloat("gyro_mapping", "y_ratio", fallback=1.0),
            ),
        )
        self.gyro_invert_x = config.getboolean(
            "gyro_mapping", "invert_x", fallback=False
        )
        self.gyro_invert_y = config.getboolean(
            "gyro_mapping", "invert_y", fallback=False
        )
        self._gyro_toggle_enabled = False
        self._gyro_trigger_was_pressed = False
        self._gyro_trigger_button_state = 0
        self._gyro_bias = [0.0, 0.0, 0.0]
        self._gyro_bias_samples = 0
        self._gyro_stationary_samples = 0
        self._gyro_stationary_started = None
        self._gyro_stationary_accel_reference = None
        self._gyro_stationary_mag_reference = None
        self._gyro_bias_anchor = None
        self._gyro_bias_block_until = 0.0
        self._gyro_last_raw = None
        self._impact_accel_lp = None
        self._impact_gravity_scale = None
        self._impact_last_gyro_raw = None
        self._impact_accel_reject_until = 0.0
        self._impact_accel_recover_until = 0.0
        self._gyro_smoothed = (0.0, 0.0)
        self._gyro_was_active = False
        self._gyro_motion_envelope = 0.0
        self._aim_gravity_sign = None
        self._aim_pose_ready_since = None
        self._aim_player_space_blend = 0.0
        self._gyro_freeze_until = 0.0
        self._gyro_stabilization_button_state = 0
        self._tilt_recenter_was_pressed = False
        self._tilt_orientation = None
        self._tilt_neutral = None
        self._tilt_neutral_quaternion = None
        self._ahrs = imufusion.Ahrs()
        self._ahrs.settings = imufusion.Settings(
            imufusion.CONVENTION_NWU,
            0.1,
            2000.0,
            10.0,
            20.0,
            625,  # 約 5 秒，125 Hz × 5
        )
        # Reuse the exact float64 arrays consumed by imufusion.  At 250 Hz,
        # constructing Gyro/Accel/Mag arrays for every report added avoidable
        # allocator and NumPy dispatch jitter to the motion hot path.
        self._fusion_gyro = np.empty(3, dtype=np.float64)
        self._fusion_accel = np.empty(3, dtype=np.float64)
        self._fusion_mag = np.empty(3, dtype=np.float64)
        self._mag_bias = [0.0, 0.0, 0.0]
        self._mag_scale = [1.0, 1.0, 1.0]
        self._mag_matrix = None
        self._mag_field_reference = None
        self._mag_field_valid = False
        self._accel_bias = None
        self._accel_matrix = None
        self._nine_axis_orientation = None
        self._nine_axis_quaternion = None
        self._nine_axis_has_magnetometer = False
        self._mag_last_valid_time = None
        self._mag_recovery_started = None
        self._mag_recovery_accumulator = 0.0
        self._gyro_calibration_lock = threading.Lock()
        self._gyro_calibration_state = "idle"
        self._gyro_calibration_message = ""
        self._gyro_calibration_started = 0.0
        self._gyro_calibration_stable_started = None
        self._gyro_calibration_last_sample_time = None
        self._gyro_calibration_valid_time = 0.0
        self._gyro_calibration_last_raw = None
        self._gyro_calibration_samples = []
        self._gyro_calibration_result = None
        self._gyro_calibration_quality = None
        self._mag_calibration_state = "idle"
        self._mag_calibration_message = ""
        self._mag_calibration_started = 0.0
        self._mag_calibration_samples = []
        self._mag_orientation_bins = set()
        self._mag_calibration_min = [math.inf, math.inf, math.inf]
        self._mag_calibration_max = [-math.inf, -math.inf, -math.inf]
        self._mag_calibration_result = None
        self._mag_calibration_last_fit = 0.0
        self._mag_calibration_last_error = ""
        self._accel_calibration_state = "idle"
        self._accel_calibration_message = ""
        self._accel_calibration_started = 0.0
        self._accel_calibration_samples = []
        self._accel_calibration_bins = set()
        self._accel_orientation_bins = set()
        self._accel_calibration_last_fit = 0.0
        self._accel_calibration_last_error = ""
        self._accel_calibration_result = None
        self._accel_calibration_quality = None
        self._last_update_time = None
        self._last_report_time = None
        self._last_report_delta = None
        self._mouse_residual = [0.0, 0.0]
        self._wheel_residual = {
            "LEFT": [0.0, 0.0],
            "RIGHT": [0.0, 0.0],
        }

        # =========================
        # 鍵盤輸出來源追蹤
        # =========================
        #
        # 格式：
        # {
        #     "W": {
        #         "BUTTON:A",
        #         "STICK:LEFT",
        #     }
        # }
        #
        # 同一個鍵盤組合可以同時由
        # 多個來源保持。
        #
        # 只有最後一個來源放開時，
        # 才真正送出 KeyUp。

        self._keyboard_combo_sources = {}
        self._mouse_button_sources = {
            button: set() for button in MOUSE_BUTTON_FLAGS
        }
        self._active_mouse_buttons = {}
        self._active_stick_mouse_action = {
            "LEFT": None,
            "RIGHT": None,
        }


        # Rumble settings
        self.lf_strength = max(
            0.0, min(1.0, config.getfloat("rumble", "lf_strength", fallback=0.88))
        )
        self.hf_strength = max(
            0.0, min(1.0, config.getfloat("rumble", "hf_strength", fallback=0.65))
        )
        
        self.lf_curve = max(
            0.1,
            min(
                5.0,
                config.getfloat(
                    "rumble",
                    "lf_curve",
                    fallback=1.10
                )
            )
        )

        self.hf_curve = max(
            0.1,
            min(
                5.0,
                config.getfloat(
                    "rumble",
                    "hf_curve",
                    fallback=1.10
                )
            )
        )       
        
        self.lf_to_hf_compensation = max(
            0.0,
            min(
                1.0,
                config.getfloat(
                    "rumble",
                    "lf_to_hf_compensation",
                    fallback=0.05,
                ),
            ),
        )
 
        self.hf_to_lf_compensation = max(
            0.0,
            min(
                1.0,
                config.getfloat(
                    "rumble",
                    "hf_to_lf_compensation",
                    fallback=0.02,
                ),
            ),
        ) 
                
        self.lf_frequency = max(
            1, min(511, config.getint("rumble", "lf_frequency", fallback=220))
        )
        self.hf_frequency = max(
            1, min(511, config.getint("rumble", "hf_frequency", fallback=325))
        )
        self.max_amplitude = max(
            0, min(1023, config.getint("rumble", "max_amplitude", fallback=640))
        )

        self.audio_haptics_mode = config.get(
            "audio_haptics", "mode", fallback="GAME"
        ).strip().upper()
        if self.audio_haptics_mode not in ("GAME", "AUDIO", "MIX"):
            self.audio_haptics_mode = "GAME"
        self.audio_haptics_mix_ratio = max(
            0.0,
            min(
                1.0,
                config.getfloat(
                    "audio_haptics", "mix_ratio", fallback=0.12
                ),
            ),
        )
        self.final_tail_strength = max(
            0.0,
            min(
                1.0,
                config.getfloat(
                    "audio_haptics", "final_tail_strength", fallback=0.0
                ),
            ),
        )
        self.final_tail_decay_ms = max(
            50.0,
            min(
                2000.0,
                config.getfloat(
                    "audio_haptics", "final_tail_decay_ms", fallback=160.0
                ),
            ),
        )
        self._game_rumble = (0, 0)
        self._audio_rumble = (0, 0)

        self._rumble_sender = None
        self._rumble_sender_supports_priority = False
        self._rumble_condition = threading.Condition()
        self._rumble_state = None
        self._rumble_force_zero = False
        self._rumble_priority = False
        self._rumble_sequence = 0
        self._rumble_running = True
        self._rumble_thread = None
        if activate_runtime:
            self._rumble_thread = threading.Thread(
                target=self._rumble_dispatch_loop,
                daemon=True,
                name="XInputRumbleDispatcher",
            )
            self.pad.register_notification(
                callback_function=self._vibration_callback
            )
            self._rumble_thread.start()

    def reconfigure(self, config, calibration):
        """Apply a complete profile without replacing native resources.

        The ViGEm pad owns a native vibration callback whose lifetime must
        match this Python object.  Rebuilding controllers around the same pad
        leaves stale callback pointers behind and can terminate the process.
        Build an inactive snapshot instead, then transplant its Python state
        while preserving the one pad, callback, condition and worker thread.
        """
        snapshot = type(self)(
            config,
            calibration,
            pad=None,
            activate_runtime=False,
            layer_state_config_path=self._layer_state_config_path,
        )

        # Release outputs under the old mapping before its transient tracking
        # dictionaries are replaced.  This prevents stuck keys/mouse buttons.
        self.reset_output_state()

        condition = self._rumble_condition
        with condition:
            preserved = {
                "pad": self.pad,
                "_rumble_sender": self._rumble_sender,
                "_rumble_sender_supports_priority": (
                    self._rumble_sender_supports_priority
                ),
                "_rumble_condition": condition,
                "_rumble_state": self._rumble_state,
                "_rumble_force_zero": self._rumble_force_zero,
                "_rumble_priority": self._rumble_priority,
                "_rumble_sequence": self._rumble_sequence,
                "_rumble_running": self._rumble_running,
                "_rumble_thread": self._rumble_thread,
            }
            replacement_state = dict(snapshot.__dict__)
            replacement_state.update(preserved)
            # One dict update keeps native vibration callbacks from observing
            # the inactive snapshot's pad/condition between two assignments.
            self.__dict__.update(replacement_state)
            condition.notify_all()

    def _acquire_keyboard_combo(
        self,
        combo_text,
        source
    ):
        """
        讓指定來源開始保持鍵盤組合。

        第一個來源取得時才真正 KeyDown。
        """

        combo_text = (
            combo_text
            .strip()
            .upper()
        )

        if not combo_text:
            return

        sources = (
            self._keyboard_combo_sources
            .setdefault(
                combo_text,
                set()
            )
        )

        # 這個來源已經持有，
        # 不重複 KeyDown。
        if source in sources:
            return

        # 第一個來源：
        # 真正按下鍵盤組合
        if not sources:
            press_keyboard_combo(
                combo_text
            )

        sources.add(
            source
        )


    def _release_keyboard_combo_source(
        self,
        combo_text,
        source
    ):
        """
        讓指定來源停止保持鍵盤組合。

        只有最後一個來源放開時，
        才真正 KeyUp。
        """

        combo_text = (
            combo_text
            .strip()
            .upper()
        )

        if not combo_text:
            return

        sources = (
            self._keyboard_combo_sources
            .get(
                combo_text
            )
        )

        if not sources:
            return

        sources.discard(
            source
        )

        # 還有其他來源保持：
        # 不放開鍵盤
        if sources:
            return

        # 最後一個來源已放開
        release_keyboard_combo(
            combo_text
        )

        self._keyboard_combo_sources.pop(
            combo_text,
            None
        )


    def release_all_keyboard_buttons(self):
        """
        強制釋放所有目前由程式保持的鍵盤組合。

        用於：
        - 控制器斷線
        - 程式停止
        - 重新啟動連接程式

        這裡直接清空所有來源，
        確保不會留下卡住的鍵盤按鍵。
        """

        # =========================
        # 釋放所有目前保持的鍵盤組合
        # =========================
        for combo_text in list(
            self._keyboard_combo_sources.keys()
        ):
            release_keyboard_combo(
                combo_text
            )

        # 清空統一來源追蹤
        self._keyboard_combo_sources.clear()

        # 清空普通 Switch 按鍵狀態
        self._active_keyboard_buttons.clear()

        # 清空左右搖桿鍵盤與方向狀態
        for side in (
            "LEFT",
            "RIGHT"
        ):
            self._active_stick_keyboard[
                side
            ] = None

            self._active_stick_directions[
                side
            ] = None

    def _acquire_mouse_button(self, button, source):
        button = button.strip().upper()
        flags = MOUSE_BUTTON_FLAGS.get(button)
        if flags is None:
            return
        sources = self._mouse_button_sources[button]
        if source in sources:
            return
        if not sources:
            user32.mouse_event(flags[0], 0, 0, 0, 0)
        sources.add(source)

    def _release_mouse_button(self, button, source):
        button = button.strip().upper()
        flags = MOUSE_BUTTON_FLAGS.get(button)
        if flags is None:
            return
        sources = self._mouse_button_sources[button]
        if source not in sources:
            return
        sources.discard(source)
        if not sources:
            user32.mouse_event(flags[1], 0, 0, 0, 0)

    @staticmethod
    def _emit_mouse_wheel(direction):
        horizontal = direction in ("WHEEL_LEFT", "WHEEL_RIGHT")
        positive = direction in ("WHEEL_UP", "WHEEL_RIGHT")
        delta = 120 if positive else -120
        flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
        user32.mouse_event(flag, 0, 0, ctypes.c_long(delta), 0)

    def release_all_mouse_buttons(self):
        for button, sources in self._mouse_button_sources.items():
            if sources:
                user32.mouse_event(MOUSE_BUTTON_FLAGS[button][1], 0, 0, 0, 0)
            sources.clear()
        self._active_mouse_buttons.clear()
        for side in self._active_stick_mouse_action:
            self._active_stick_mouse_action[side] = None

    def reset_output_state(self):
        """Immediately release every virtual gamepad and keyboard output."""
        self.release_all_keyboard_buttons()
        self.release_all_mouse_buttons()
        self._stick_smoothed_magnitude["left"] = None
        self._stick_smoothed_magnitude["right"] = None
        self._stick_smoothing_time["left"] = None
        self._stick_smoothing_time["right"] = None
        self._gyro_toggle_enabled = False
        self._gyro_trigger_was_pressed = False
        self._gyro_trigger_button_state = 0
        self._gyro_smoothed = (0.0, 0.0)
        self._gyro_was_active = False
        self._gyro_motion_envelope = 0.0
        self._aim_gravity_sign = None
        self._aim_pose_ready_since = None
        self._aim_player_space_blend = 0.0
        self._gyro_freeze_until = 0.0
        self._gyro_stabilization_button_state = 0
        self._tilt_recenter_was_pressed = False
        self._tilt_orientation = None
        self._tilt_neutral = None
        self._tilt_neutral_quaternion = None
        self._ahrs.reset()
        self._reset_magnetic_fusion_state()
        self._nine_axis_orientation = None
        self._nine_axis_quaternion = None
        self._nine_axis_has_magnetometer = False
        with self._gyro_calibration_lock:
            if self._gyro_calibration_state == "running":
                self._gyro_calibration_state = "failed"
                self._gyro_calibration_message = "disconnected"
            if self._mag_calibration_state == "running":
                self._mag_calibration_state = "failed"
                self._mag_calibration_message = "disconnected"
            if self._accel_calibration_state == "running":
                self._accel_calibration_state = "failed"
                self._accel_calibration_message = "disconnected"
        self._last_update_time = None
        self._last_report_time = None
        self._last_report_delta = None
        self._mouse_residual = [0.0, 0.0]
        self._wheel_residual = {
            "LEFT": [0.0, 0.0],
            "RIGHT": [0.0, 0.0],
        }

        self._mapping_layer_selector.reset()
        self._active_mapping_layer_id = None
        self._mapping_layer_last_buttons = None
        self._main_mapping_neutral_raw = {"LEFT": None, "RIGHT": None}
        self._mapping_layer_neutral_raw = {"LEFT": None, "RIGHT": None}
        self._main_mapping_neutral_samples = {"LEFT": None, "RIGHT": None}
        self._mapping_layer_neutral_samples = {"LEFT": None, "RIGHT": None}
        (
            self._compiled_mapping,
            self.stick_direction_config,
            self._stick_direction_mapping_enabled,
        ) = self._base_mapping_runtime

        with self._rumble_condition:
            self._game_rumble = (0, 0)
            self._audio_rumble = (0, 0)
        self._queue_rumble(0, 0, force_zero=True)

        self.pad.reset()
        self.pad.update()

    def set_rumble_sender(self, sender, supports_priority=False):
        """Register a rumble sender; ESP32 may accept priority metadata."""
        with self._rumble_condition:
            self._rumble_sender = sender
            self._rumble_sender_supports_priority = bool(supports_priority)
            self._rumble_condition.notify_all()

    def set_audio_rumble(self, lf_level, hf_level):
        """Receive normalized audio levels and publish the selected mix."""
        lf_input = max(0.0, min(1.0, lf_level))
        hf_input = max(0.0, min(1.0, hf_level))
        raw_lf_amp = int(self.max_amplitude * (lf_input ** self.lf_curve))
        raw_hf_amp = int(self.max_amplitude * (hf_input ** self.hf_curve))
        lf_amp = int(raw_lf_amp * self.lf_strength)
        lf_amp += int(raw_hf_amp * self.hf_to_lf_compensation)
        hf_amp = int(raw_hf_amp * self.hf_strength)
        hf_amp += int(raw_lf_amp * self.lf_to_hf_compensation)
        lf_amp = max(0, min(self.max_amplitude, lf_amp))
        hf_amp = max(0, min(self.max_amplitude, hf_amp))
        with self._rumble_condition:
            self._audio_rumble = (lf_amp, hf_amp)
        self._publish_rumble_mix(priority=False)

    def _publish_rumble_mix(self, priority=False, force_zero=False):
        with self._rumble_condition:
            game_lf, game_hf = self._game_rumble
            audio_lf, audio_hf = self._audio_rumble

        if self.audio_haptics_mode == "AUDIO":
            lf_amp, hf_amp = audio_lf, audio_hf
        elif self.audio_haptics_mode == "MIX":
            game_weight = 1.0 - self.audio_haptics_mix_ratio
            audio_weight = self.audio_haptics_mix_ratio
            lf_amp = _soft_mix(
                int(game_lf * game_weight),
                int(audio_lf * audio_weight),
                self.max_amplitude,
            )
            hf_amp = _soft_mix(
                int(game_hf * game_weight),
                int(audio_hf * audio_weight),
                self.max_amplitude,
            )
        else:
            lf_amp, hf_amp = game_lf, game_hf
        # Game-originated updates use the 7.5 ms priority path. Audio-only
        # changes remain at 16.6 ms. A true mixed zero is also prioritized.
        effective_force_zero = bool(force_zero and lf_amp <= 0 and hf_amp <= 0)
        self._queue_rumble(
            lf_amp,
            hf_amp,
            force_zero=effective_force_zero,
            priority=bool(priority or effective_force_zero),
        )

    def set_calibration(self, calibration):
        """Atomically replace calibration when a controller identity is known."""
        normalized = {}
        for side in ("left", "right"):
            values = calibration[side]
            normalized[side] = {
                "center": tuple(int(v) for v in values["center"]),
                "max": tuple(max(1, int(v)) for v in values["max"]),
                "min": tuple(max(1, int(v)) for v in values["min"]),
            }
        self.cal = normalized
        self._stick_smoothed_magnitude["left"] = None
        self._stick_smoothed_magnitude["right"] = None
        self._stick_smoothing_time["left"] = None
        self._stick_smoothing_time["right"] = None

    def _queue_rumble(
        self, lf_amp, hf_amp, force_zero=False, priority=False,
    ):
        with self._rumble_condition:
            self._rumble_state = (
                self.lf_frequency,
                int(lf_amp),
                self.hf_frequency,
                int(hf_amp),
            )
            self._rumble_force_zero = bool(force_zero)
            self._rumble_priority = bool(priority or force_zero)
            self._rumble_sequence += 1
            self._rumble_condition.notify_all()

    def _rumble_dispatch_loop(self):
        """Send rumble and continue any configured final-output tail."""
        last_sequence = -1
        tail_lf = 0.0
        tail_hf = 0.0
        last_time = time.monotonic()
        while True:
            with self._rumble_condition:
                while (
                    self._rumble_running
                    and (
                        self._rumble_sender is None
                        or self._rumble_state is None
                    )
                ):
                    self._rumble_condition.wait()
                if not self._rumble_running:
                    return
                sender = self._rumble_sender
                state = self._rumble_state
                sequence = self._rumble_sequence
                force_zero = self._rumble_force_zero
                priority = self._rumble_priority
                supports_priority = self._rumble_sender_supports_priority

            now = time.monotonic()
            delta_time = max(0.001, min(0.1, now - last_time))
            last_time = now
            target_lf = max(0, int(state[1]))
            target_hf = max(0, int(state[3]))
            target_changed = sequence != last_sequence

            if force_zero:
                tail_lf = 0.0
                tail_hf = 0.0
            else:
                decay_seconds = max(0.05, self.final_tail_decay_ms / 1000.0)
                decay_factor = math.exp(-delta_time / decay_seconds)
                tail_lf = _update_rumble_tail(
                    tail_lf,
                    target_lf,
                    self.final_tail_strength,
                    decay_factor,
                    target_changed,
                )
                tail_hf = _update_rumble_tail(
                    tail_hf,
                    target_hf,
                    self.final_tail_strength,
                    decay_factor,
                    target_changed,
                )

            output_state = (
                state[0],
                max(0, min(self.max_amplitude, int(round(tail_lf)))),
                state[2],
                max(0, min(self.max_amplitude, int(round(tail_hf)))),
            )
            try:
                if supports_priority:
                    sender(
                        *output_state,
                        priority=priority,
                        force_zero=force_zero,
                    )
                else:
                    sender(*output_state)
            except Exception as exc:
                print(f"震動傳送失敗：{exc}")

            last_sequence = sequence
            output_active = output_state[1] > 0 or output_state[3] > 0
            with self._rumble_condition:
                if (
                    self._rumble_running
                    and self._rumble_sequence == sequence
                ):
                    self._rumble_condition.wait(
                        timeout=0.015 if output_active else None
                    )

    def close(self):
        """Stop the dispatcher after sending the final zero frame."""
        self.reset_output_state()
        time.sleep(0.02)
        with self._rumble_condition:
            self._rumble_running = False
            self._rumble_condition.notify_all()
        if (
            self._rumble_thread.is_alive()
            and threading.current_thread() is not self._rumble_thread
        ):
            self._rumble_thread.join(timeout=1.0)

    def _vibration_callback(
        self,
        client,
        target,
        large_motor,
        small_motor,
        led_number,
        user_data,
    ):
        del client, target, led_number, user_data

        if self._rumble_sender is None:
            return

        # LF 與 HF 強度都為 0 時視為完整關閉震動。
        # 必須在補償計算前處理，否則非零的交叉補償仍會產生輸出。
        if self.lf_strength <= 0.0 and self.hf_strength <= 0.0:
            with self._rumble_condition:
                self._game_rumble = (0, 0)
            self._publish_rumble_mix(priority=True)
            return

        # 將 XInput 震動輸入轉換為 0.0 ～ 1.0
        lf_input = max(
            0.0,
            min(1.0, int(large_motor) / 255.0)
        )

        hf_input = max(
            0.0,
            min(1.0, int(small_motor) / 255.0)
        )

        # 套用 LF / HF 獨立震動曲線
        lf_output = lf_input ** self.lf_curve
        hf_output = hf_input ** self.hf_curve

        # 套用最大振幅與強度
        # 曲線處理後、強度調整前的原始振幅
        raw_lf_amp = int(
            self.max_amplitude
            * lf_output
        )

        raw_hf_amp = int(
            self.max_amplitude
            * hf_output
        )

        # 各通道自身的強度調整
        lf_amp = int(
            raw_lf_amp
            * self.lf_strength
        )

        hf_amp = int(
            raw_hf_amp
            * self.hf_strength
        )

        # LF → HF 補償使用強度調整前的 LF 振幅
        hf_amp += int(
            raw_lf_amp
            * self.lf_to_hf_compensation
        )

        # HF → LF 補償使用強度調整前的 HF 振幅
        lf_amp += int(
            raw_hf_amp
            * self.hf_to_lf_compensation
        )

        # 最終限制
        lf_amp = max(
            0,
            min(
                self.max_amplitude,
                lf_amp
            )
        )

        hf_amp = max(
            0,
            min(
                self.max_amplitude,
                hf_amp
            )
        )

        with self._rumble_condition:
            self._game_rumble = (lf_amp, hf_amp)
        self._publish_rumble_mix(priority=True)

    def _axis_pair(
        self,
        raw_xy,
        side,
        minimum_deadzone=0.0,
        compress_minimum_deadzone=False,
        center_override=None,
    ):
        # =========================
        # 選擇目前搖桿的死區設定
        # =========================

        if side == "left":
            deadzone = self.left_deadzone
            outer_deadzone = (
                self.left_outer_deadzone
            )
            deadzone_compress = (
                self.left_deadzone_compress
            )
            outer_deadzone_compress = (
                self.left_outer_deadzone_compress
            )

        elif side == "right":
            deadzone = self.right_deadzone
            outer_deadzone = (
                self.right_outer_deadzone
            )
            deadzone_compress = (
                self.right_deadzone_compress
            )
            outer_deadzone_compress = (
                self.right_outer_deadzone_compress
            )

        else:
            raise ValueError(_tr(
                f"未知的搖桿 side：{side!r}",
                f"Unknown stick side: {side!r}",
            ))

        # Pointer and linear-wheel mapping need their drift guard in physical
        # stick space, before a response curve can amplify a neutral offset.
        # Treat it as a minimum so the normal stick deadzone is never weakened.
        minimum_deadzone = max(0.0, min(0.30, float(minimum_deadzone)))
        if minimum_deadzone > deadzone:
            deadzone = minimum_deadzone
        if compress_minimum_deadzone:
            deadzone_compress = True

        cal = self.cal[side]
        cx, cy = cal["center"]
        if center_override is not None:
            cx, cy = center_override
        max_x, max_y = cal["max"]
        min_x, min_y = cal["min"]

        # Same current processing path used by Tommy:
        # per-axis calibration -> clamp -> 3% radial deadzone.
        x = max(-1.0, min(1.0, apply_calibration_to_axis(
            raw_xy[0], cx, max_x, min_x
        )))
        y = max(-1.0, min(1.0, apply_calibration_to_axis(
            raw_xy[1], cy, max_y, min_y
        )))

        # =========================
        # 死區最高優先級
        # =========================
        #
        # 死區判定永遠使用：
        # 「校正後、套用曲線前」的原始搖桿位置。
        #
        # 因此 XY 曲線無法：
        # 1. 突破中心死區
        # 2. 延後或取消外圍死區

        raw_x = x
        raw_y = y

        raw_magnitude = (
            raw_x * raw_x
            + raw_y * raw_y
        ) ** 0.5

        # =========================
        # 最高優先級 1：中心死區
        # =========================
        if raw_magnitude < deadzone:
            self._stick_smoothed_magnitude[
                side
            ] = 0.0
            self._stick_smoothing_time[side] = None

            return 0.0, 0.0

        # =========================
        # 依照搖桿徑向距離套用曲線
        # =========================
        #
        # 曲線只改變「推動距離」，
        # 不改變原始搖桿方向。
        #
        # 這樣斜方向不會因為 X、Y
        # 分別被放大而超出單位圓。

        curve_points = self.stick_curves[
            side
        ]

        # =========================
        # 檢查是否為預設 1:1 線性曲線
        # =========================
        #
        # 如果曲線完全是預設值，
        # 就完全旁路曲線處理。
        #
        # 這樣可以 100% 保留原本的
        # raw_x / raw_y，不額外圓形化。

        is_default_curve = self._stick_curve_is_default[side]

        # =========================
        # 預設曲線：完全旁路
        # =========================
        if (
            is_default_curve
            and not deadzone_compress
            and not outer_deadzone_compress
        ):
            x = raw_x
            y = raw_y

        # =========================
        # 自定義曲線：徑向比例縮放
        # =========================
        else:
            # 使用校正後、曲線前的徑向距離
            # 作為曲線的輸入值。
            #
            # 如果原始斜方向本來超過 1.0，
            # 曲線輸入最多只取 1.0，
            # 避免破壞原本 raw 的外圍形狀。
            raw_curve_magnitude = min(
                1.0,
                raw_magnitude
            )

            # =========================
            # 取得曲線實際作用範圍
            # =========================

            curve_start = (
                deadzone
                if deadzone_compress
                else 0.0
            )

            curve_end = (
                1.0 - outer_deadzone
                if outer_deadzone_compress
                else 1.0
            )

            # =========================
            # 防止壓縮範圍重疊或反轉
            # =========================
            #
            # 正常情況：
            # curve_start < curve_end
            #
            # 如果設定異常導致有效範圍
            # 為 0 或反轉，則取消本次壓縮，
            # 回到完整 0.0 ～ 1.0 範圍。

            if curve_end <= curve_start:
                curve_start = 0.0
                curve_end = 1.0

            curve_width = (
                curve_end
                - curve_start
            )

            # =========================
            # 實際輸入
            # → 曲線內部 0.0 ～ 1.0
            # =========================

            input_magnitude = (
                (
                    raw_curve_magnitude
                    - curve_start
                )
                / curve_width
            )

            input_magnitude = max(
                0.0,
                min(
                    1.0,
                    input_magnitude
                )
            )

            # 對換算後的徑向距離
            # 套用自定義曲線。
            output_magnitude = apply_stick_curve(
                input_magnitude,
                curve_points,
                self.stick_curve_interpolation[side],
            )

            # 曲線輸出限制在 0.0 ～ 1.0
            output_magnitude = max(
                0.0,
                min(
                    1.0,
                    output_magnitude
                )
            )

            # =========================
            # 曲線斜率動態防抖
            # =========================
            smoothing_strength = (
                self.stick_smoothing[
                    side
                ]
            )

            # =========================
            # 0%：嚴格旁路
            # =========================
            if smoothing_strength <= 0.0:
                # 當前輸出完全不修改。
                #
                # 只同步歷史狀態，
                # 避免之後改成非 0% 時，
                # 使用過期的舊數值。
                self._stick_smoothed_magnitude[
                    side
                ] = output_magnitude

            else:
                curve_slope = (
                    get_stick_curve_slope(
                        input_magnitude,
                        curve_points,
                        self.stick_curve_interpolation[side],
                    )
                )

                # =========================
                # 計入死區壓縮造成的
                # 實際輸入斜率放大
                # =========================
                #
                # 例如曲線被壓縮到
                # 實際輸入 0.10 ～ 0.90：
                #
                # curve_width = 0.80
                #
                # 原本曲線內部的 1× 斜率，
                # 相對實際搖桿輸入會變成：
                #
                # 1.0 / 0.80 = 1.25×

                curve_slope = (
                    curve_slope
                    / curve_width
                )

                # =========================
                # 只處理曲線額外放大的區域
                # =========================
                if curve_slope <= 1.0:
                    base_alpha = 1.0

                else:
                    # 限制極端斜率，
                    # 避免垂直線段產生無限大的防抖倍率。
                    effective_slope = min(
                        curve_slope,
                        10.0
                    )

                    filter_multiplier = (
                        1.0
                        + (
                            effective_slope
                            - 1.0
                        )
                        * smoothing_strength
                    )

                    base_alpha = min(
                        1.0,
                        1.0
                        / filter_multiplier
                    )

                now = time.perf_counter()
                previous_time = self._stick_smoothing_time[side]
                self._stick_smoothing_time[side] = now

                # Preserve the old control response at a nominal 120 Hz while
                # making the result independent of the actual BLE/USB report rate.
                if base_alpha >= 1.0 or previous_time is None:
                    alpha = 1.0
                else:
                    nominal_dt = 1.0 / 120.0
                    time_constant = (
                        -nominal_dt / math.log(1.0 - base_alpha)
                    )
                    delta_time = max(0.0, min(0.1, now - previous_time))
                    alpha = 1.0 - math.exp(
                        -delta_time / time_constant
                    )

                previous_magnitude = (
                    self._stick_smoothed_magnitude[
                        side
                    ]
                )

                # 第一次沒有歷史值：
                # 直接使用目前曲線結果，
                # 不製造啟動跳變。
                if previous_magnitude is None:
                    smoothed_magnitude = (
                        output_magnitude
                    )

                else:
                    smoothed_magnitude = (
                        previous_magnitude
                        + (
                            output_magnitude
                            - previous_magnitude
                        )
                        * alpha
                    )

                output_magnitude = (
                    smoothed_magnitude
                )

                self._stick_smoothed_magnitude[
                    side
                ] = output_magnitude

            # =========================
            # 按相同比例縮放 raw X / Y
            # =========================
            #
            # 不使用 max(|X|, |Y|)，
            # 因此不會額外產生方形／十字形。
            #
            # 不重新建立單位圓座標，
            # 只用相同比例縮放原始 X / Y。

            if raw_magnitude > 0.0:
                scale = (
                    output_magnitude
                    / raw_magnitude
                )

                x = raw_x * scale
                y = raw_y * scale

            else:
                x = 0.0
                y = 0.0

        # =========================
        # 最高優先級 2：外圍死區
        # =========================
        #
        # 外圍死區使用曲線前的
        # raw_magnitude 判定。
        #
        # 與自定義曲線使用相同的
        # 徑向座標系，避免十字軸附近
        # 因不同判定方式產生跳變。

        if outer_deadzone > 0.0:
            outer_threshold = (
                1.0
                - outer_deadzone
            )

            if (
                raw_magnitude
                >= outer_threshold
            ):
                # 使用原始 raw 方向，
                # 強制徑向長度到 1.0。
                if raw_magnitude > 0.0:
                    x = (
                        raw_x
                        / raw_magnitude
                    )

                    y = (
                        raw_y
                        / raw_magnitude
                    )

                    # 外圍死區具有最高優先級，
                    # 實際輸出已直接到達 1.0。
                    #
                    # 同步防抖歷史值，
                    # 避免離開外圍死區後
                    # 使用過期的平滑狀態。
                    self._stick_smoothed_magnitude[
                        side
                    ] = 1.0

        # Apply one explicit output shape after every curve/deadzone path so
        # default and custom curves have identical outer-range behavior.
        return apply_output_shape(
            x,
            y,
            self.stick_output_shape[side],
        )

    def _get_stick_direction(
        self,
        x,
        y,
        side
    ):
        """
        根據搖桿 XY 判斷目前方向。

        支援：
        - 4WAY：上 / 下 / 左 / 右
        - 8WAY：包含四個斜方向

        使用觸發 / 放開雙門檻，
        避免搖桿在門檻附近抖動。
        """

        magnitude = (x * x + y * y) ** 0.5
        current_direction = self._active_stick_directions[side]
        config_data = self.stick_direction_config[side]
        trigger_threshold = config_data["trigger_threshold"]
        release_threshold = config_data["release_threshold"]
        direction_deadzone = config_data["direction_deadzone"]

        # =========================
        # 遲滯判定
        # =========================

        # 目前沒有方向：
        # 必須達到「觸發門檻」才開始判定
        if current_direction is None:
            if (
                magnitude
                < trigger_threshold
            ):
                return None

        # 目前已有方向：
        # 只有低於「放開門檻」才完全放開
        else:
            if (
                magnitude
                <= release_threshold
            ):
                return None

        mode = config_data["mode"]

        angle = math.degrees(math.atan2(y, x)) % 360.0
        if mode == "4WAY":
            sector_names = ("RIGHT", "UP", "LEFT", "DOWN")
            sector_step = 90.0
        else:
            sector_names = (
                "RIGHT", "UP_RIGHT", "UP", "UP_LEFT",
                "LEFT", "DOWN_LEFT", "DOWN", "DOWN_RIGHT",
            )
            sector_step = 45.0

        half_sector = sector_step / 2.0
        sector_index = int((angle + half_sector) // sector_step) % len(
            sector_names
        )
        candidate = sector_names[sector_index]
        candidate_center = sector_index * sector_step
        candidate_distance = abs(
            (angle - candidate_center + 180.0) % 360.0 - 180.0
        )

        # 已觸發時讓原方向穿越整個分界死區，直到明確進入
        # 新扇形才切換，避免輸出在邊界上斷開或快速往復。
        if current_direction in sector_names:
            current_center = sector_names.index(current_direction) * sector_step
            current_distance = abs(
                (angle - current_center + 180.0) % 360.0 - 180.0
            )
            if current_distance <= half_sector + direction_deadzone:
                return current_direction

        # 從未觸發狀態進入分界死區時不輸出方向。已觸發後
        # 若大幅跳到另一個分界，也繼續保留原方向。
        if candidate_distance > half_sector - direction_deadzone:
            return current_direction

        return candidate

    def _is_stick_direction_mapping_enabled(
        self,
        side
    ):
        """
        判斷指定搖桿目前是否啟用方向映射。

        只檢查目前模式實際使用的方向：
        - 4WAY：上下左右
        - 8WAY：全部 8 個方向

        只要其中任何一個方向不是 NONE，
        就視為這支搖桿已切換成方向映射模式。
        """

        config_data = self.stick_direction_config[side]
        return self._is_stick_direction_config_enabled(config_data)

    @staticmethod
    def _is_stick_direction_config_enabled(config_data):
        """Pure variant used while precompiling secondary layer settings."""

        mode = config_data[
            "mode"
        ]

        mappings = config_data[
            "mappings"
        ]

        # 映射為另一邊類比搖桿時，
        # 不屬於方向按鍵映射。
        if mode in (
            "映射為右搖桿",
            "映射為左搖桿",
            "映射為滑鼠",
            "MOUSE_WHEEL_LINEAR",
            "XINPUT_LT_LINEAR",
            "XINPUT_RT_LINEAR",
        ):
            return False

        if mode == "4WAY":
            active_directions = (
                "UP",
                "RIGHT",
                "DOWN",
                "LEFT",
            )

        else:
            active_directions = (
                "UP",
                "UP_RIGHT",
                "RIGHT",
                "DOWN_RIGHT",
                "DOWN",
                "DOWN_LEFT",
                "LEFT",
                "UP_LEFT",
            )

        for direction in active_directions:
            target = mappings.get(
                direction,
                "NONE"
            )

            target = (
                target
                .strip()
                .upper()
            )

            if target not in (
                "",
                "NONE"
            ):
                return True

        return False

    @staticmethod
    def _orientation_bin(values, threshold=0.35):
        """Quantize a non-zero 3D vector into one of 26 cube directions."""
        vector = np.asarray(values, dtype=np.float64)
        magnitude = float(np.linalg.norm(vector))
        if vector.shape != (3,) or not math.isfinite(magnitude) or magnitude <= 1e-9:
            return None
        unit = vector / magnitude
        direction = tuple(
            1 if value >= threshold else -1 if value <= -threshold else 0
            for value in unit
        )
        return direction if direction != (0, 0, 0) else None
    @staticmethod
    def _gyro_accel_is_plausible(accelerometer):
        accel_magnitude = math.sqrt(
            sum(float(value) * float(value) for value in accelerometer)
        )
        return (
            abs(accel_magnitude - 4096.0) < 850.0
            or abs(accel_magnitude - 16384.0) < 3000.0
        )

    def set_gyro_bias(self, bias):
        """Apply a validated persistent three-axis gyro zero bias."""
        if bias is None:
            self._gyro_bias = [0.0, 0.0, 0.0]
            self._gyro_bias_samples = 0
            self._gyro_stationary_samples = 0
            self._gyro_stationary_started = None
            self._gyro_bias_anchor = None
            self._gyro_last_raw = None
            return
        values = tuple(float(value) for value in bias)
        if len(values) != 3 or any(
            not math.isfinite(value) or abs(value) > 4096.0
            for value in values
        ):
            raise ValueError("invalid gyro bias")
        self._gyro_bias = list(values)
        self._gyro_bias_samples = 64
        self._gyro_stationary_samples = 0
        self._gyro_stationary_started = None
        self._gyro_bias_anchor = list(values)
        self._gyro_last_raw = None

    def _reset_magnetic_fusion_state(self):
        self._mag_last_valid_time = None
        self._mag_recovery_started = None
        self._mag_recovery_accumulator = 0.0
        self._nine_axis_has_magnetometer = False
        self._aim_gravity_sign = None
        self._aim_pose_ready_since = None
        self._aim_player_space_blend = 0.0
    def set_accelerometer_calibration(self, bias, matrix=None):
        """Apply a validated per-controller multi-pose ellipsoid calibration."""
        if bias is None or matrix is None:
            self._accel_bias = None
            self._accel_matrix = None
            self._ahrs.reset()
            self._reset_magnetic_fusion_state()
            self._nine_axis_orientation = None
            self._nine_axis_quaternion = None
            return
        bias_values = np.asarray(bias, dtype=np.float64)
        matrix_values = np.asarray(matrix, dtype=np.float64)
        if (
            bias_values.shape != (3,)
            or matrix_values.shape != (3, 3)
            or not np.all(np.isfinite(bias_values))
            or not np.all(np.isfinite(matrix_values))
            or float(np.max(np.abs(matrix_values - matrix_values.T))) > 1e-9
            or float(np.linalg.det(matrix_values)) <= 1e-12
        ):
            raise ValueError("invalid accelerometer calibration")
        self._accel_bias = bias_values
        self._accel_matrix = matrix_values
        self._ahrs.reset()
        self._reset_magnetic_fusion_state()
        self._nine_axis_orientation = None
        self._nine_axis_quaternion = None
        self._nine_axis_has_magnetometer = False

    def _correct_accelerometer(self, accelerometer, output=None):
        try:
            raw_x = float(accelerometer[0])
            raw_y = float(accelerometer[1])
            raw_z = float(accelerometer[2])
        except (IndexError, TypeError, ValueError):
            return None
        if not all(map(math.isfinite, (raw_x, raw_y, raw_z))):
            return None
        corrected = output if output is not None else np.empty(3, dtype=np.float64)
        accel_bias = getattr(self, "_accel_bias", None)
        accel_matrix = getattr(self, "_accel_matrix", None)
        if accel_bias is not None and accel_matrix is not None:
            delta_x = raw_x - float(accel_bias[0])
            delta_y = raw_y - float(accel_bias[1])
            delta_z = raw_z - float(accel_bias[2])
            corrected[0] = (
                float(accel_matrix[0][0]) * delta_x
                + float(accel_matrix[0][1]) * delta_y
                + float(accel_matrix[0][2]) * delta_z
            )
            corrected[1] = (
                float(accel_matrix[1][0]) * delta_x
                + float(accel_matrix[1][1]) * delta_y
                + float(accel_matrix[1][2]) * delta_z
            )
            corrected[2] = (
                float(accel_matrix[2][0]) * delta_x
                + float(accel_matrix[2][1]) * delta_y
                + float(accel_matrix[2][2]) * delta_z
            )
        else:
            magnitude = math.sqrt(
                raw_x * raw_x + raw_y * raw_y + raw_z * raw_z
            )
            if magnitude <= 1e-9:
                return None
            gravity_scale = min(
                (4096.0, 16384.0),
                key=lambda scale: abs(magnitude - scale),
            )
            inverse_scale = 1.0 / gravity_scale
            corrected[0] = raw_x * inverse_scale
            corrected[1] = raw_y * inverse_scale
            corrected[2] = raw_z * inverse_scale
        corrected_magnitude_squared = (
            float(corrected[0]) * float(corrected[0])
            + float(corrected[1]) * float(corrected[1])
            + float(corrected[2]) * float(corrected[2])
        )
        if not 0.0625 <= corrected_magnitude_squared <= 6.25:
            return None
        return corrected

    def set_magnetometer_calibration(self, bias, scale=None, matrix=None):
        """Apply validated per-controller hard/soft-iron calibration."""
        if bias is None:
            self._mag_bias = [0.0, 0.0, 0.0]
            self._mag_scale = [1.0, 1.0, 1.0]
            self._mag_matrix = None
            self._mag_field_reference = None
            self._mag_field_valid = False
            self._ahrs.reset()
            self._reset_magnetic_fusion_state()
            self._nine_axis_orientation = None
            self._nine_axis_quaternion = None
            self._nine_axis_has_magnetometer = False
            return
        bias_values = tuple(float(value) for value in bias)
        scale_values = tuple(
            float(value) for value in (scale or (1.0, 1.0, 1.0))
        )
        if len(bias_values) != 3 or any(
            not math.isfinite(value) or not -32768.0 <= value <= 32767.0
            for value in bias_values
        ):
            raise ValueError("invalid magnetometer bias")
        if len(scale_values) != 3 or any(
            not math.isfinite(value) or not 0.25 <= value <= 4.0
            for value in scale_values
        ):
            raise ValueError("invalid magnetometer scale")
        self._mag_bias = list(bias_values)
        self._mag_scale = list(scale_values)
        if matrix is None:
            self._mag_matrix = None
        else:
            matrix_values = np.asarray(matrix, dtype=np.float64)
            if matrix_values.shape != (3, 3) or not np.all(
                np.isfinite(matrix_values)
            ):
                raise ValueError("invalid magnetometer matrix")
            determinant = float(np.linalg.det(matrix_values))
            if determinant <= 1e-12:
                raise ValueError("invalid magnetometer matrix")
            self._mag_matrix = matrix_values
        self._mag_field_reference = None
        self._mag_field_valid = False
        self._ahrs.reset()
        self._reset_magnetic_fusion_state()
        self._nine_axis_orientation = None
        self._nine_axis_quaternion = None
        self._nine_axis_has_magnetometer = False

    def set_magnetometer_bias(self, bias):
        """Backward-compatible bias-only calibration entry point."""
        self.set_magnetometer_calibration(bias)

    def start_gyro_calibration(self):
        """Begin a one-second stationary zero-bias calibration."""
        with self._gyro_calibration_lock:
            self._gyro_calibration_state = "running"
            self._gyro_calibration_message = "keep_still"
            self._gyro_calibration_started = time.perf_counter()
            self._gyro_calibration_stable_started = None
            self._gyro_calibration_last_sample_time = None
            self._gyro_calibration_valid_time = 0.0
            self._gyro_calibration_last_raw = None
            self._gyro_calibration_samples = []
            self._gyro_calibration_result = None
            self._gyro_calibration_quality = None
        self._gyro_toggle_enabled = False
        self._gyro_smoothed = (0.0, 0.0)
        self._gyro_was_active = False
        self._tilt_recenter_was_pressed = False
        self._tilt_orientation = None
        self._tilt_neutral = None
        self._tilt_neutral_quaternion = None
        self._ahrs.reset()
        self._reset_magnetic_fusion_state()
        self._nine_axis_orientation = None
        self._nine_axis_quaternion = None
        self._nine_axis_has_magnetometer = False

    def get_gyro_calibration_status(self):
        with self._gyro_calibration_lock:
            return {
                "state": self._gyro_calibration_state,
                "message": self._gyro_calibration_message,
                "samples": len(self._gyro_calibration_samples),
                "quality": self._gyro_calibration_quality,
            }

    def consume_gyro_calibration_result(self):
        with self._gyro_calibration_lock:
            result = self._gyro_calibration_result
            self._gyro_calibration_result = None
            return result

    def start_accelerometer_calibration(self):
        """Collect many low-motion gravity directions for ellipsoid fitting."""
        with self._gyro_calibration_lock:
            self._accel_calibration_state = "running"
            self._accel_calibration_message = "move_and_pause"
            self._accel_calibration_started = time.perf_counter()
            self._accel_calibration_samples = []
            self._accel_calibration_bins = set()
            self._accel_orientation_bins = set()
            self._accel_calibration_last_fit = 0.0
            self._accel_calibration_last_error = ""
            self._accel_calibration_result = None
            self._accel_calibration_quality = None
        self._gyro_toggle_enabled = False
        self._gyro_smoothed = (0.0, 0.0)
        self._gyro_was_active = False
        self._tilt_neutral = None
        self._tilt_neutral_quaternion = None

    def get_accelerometer_calibration_status(self):
        with self._gyro_calibration_lock:
            samples = self._accel_calibration_samples
            if samples:
                spans = tuple(
                    max(sample[index] for sample in samples)
                    - min(sample[index] for sample in samples)
                    for index in range(3)
                )
            else:
                spans = (0.0, 0.0, 0.0)
            elapsed = (
                max(0.0, time.perf_counter() - self._accel_calibration_started)
                if self._accel_calibration_state == "running"
                else 0.0
            )
            progress = int(round(95.0 * min(
                1.0,
                elapsed / 18.0,
                len(samples) / 120.0,
                min(spans) / 6000.0,
                len(self._accel_orientation_bins)
                / self.ACCEL_MIN_ORIENTATION_BINS,
            )))
            if self._accel_calibration_state == "success":
                progress = 100
            return {
                "state": self._accel_calibration_state,
                "message": self._accel_calibration_message,
                "samples": len(samples),
                "spans": spans,
                "progress": max(0, min(100, progress)),
                "quality": self._accel_calibration_quality,
                "orientation_bins": tuple(sorted(self._accel_orientation_bins)),
                "orientation_coverage": len(self._accel_orientation_bins) / 26.0,
            }

    def consume_accelerometer_calibration_result(self):
        with self._gyro_calibration_lock:
            result = self._accel_calibration_result
            self._accel_calibration_result = None
            return result

    def _process_accelerometer_calibration(self, accelerometer, gyroscope):
        now = time.perf_counter()
        raw = tuple(float(value) for value in accelerometer)
        with self._gyro_calibration_lock:
            if self._accel_calibration_state != "running":
                return False
            elapsed = now - self._accel_calibration_started
            raw_magnitude = math.sqrt(sum(value * value for value in raw))
            gravity_scale = min(
                (4096.0, 16384.0),
                key=lambda scale: abs(raw_magnitude - scale),
            )
            gyro_speed = math.sqrt(sum(
                ((float(gyroscope[index]) - self._gyro_bias[index]) / 14.285714) ** 2
                for index in range(3)
            ))
            valid_gravity = (
                self._gyro_accel_is_plausible(raw)
                and abs(raw_magnitude - gravity_scale) <= gravity_scale * 0.12
            )
            if valid_gravity and gyro_speed <= 30.0:
                direction = tuple(
                    int(round(value / raw_magnitude * 16.0)) for value in raw
                )
                if direction not in self._accel_calibration_bins:
                    self._accel_calibration_bins.add(direction)
                    self._accel_calibration_samples.append(raw)
                orientation_bin = self._orientation_bin(raw)
                if orientation_bin is not None:
                    self._accel_orientation_bins.add(orientation_bin)

            samples = self._accel_calibration_samples
            if samples:
                spans = tuple(
                    max(sample[index] for sample in samples)
                    - min(sample[index] for sample in samples)
                    for index in range(3)
                )
            else:
                spans = (0.0, 0.0, 0.0)
            ready_to_fit = (
                elapsed >= 18.0
                and len(samples) >= 120
                and min(spans) >= 6000.0
                and len(self._accel_orientation_bins)
                >= self.ACCEL_MIN_ORIENTATION_BINS
                and now - self._accel_calibration_last_fit >= 1.0
            )
            if not ready_to_fit and elapsed < 40.0:
                return True
            if not ready_to_fit:
                self._accel_calibration_state = "failed"
                self._accel_calibration_message = (
                    self._accel_calibration_last_error
                    or "insufficient_accelerometer_coverage"
                )
                return True
            self._accel_calibration_last_fit = now
            try:
                bias, matrix, quality = fit_accelerometer_ellipsoid(samples)
            except ValueError as exc:
                self._accel_calibration_last_error = str(exc)
                self._accel_calibration_message = "more_orientations"
                return True
            self._accel_bias = np.asarray(bias, dtype=np.float64)
            self._accel_matrix = np.asarray(matrix, dtype=np.float64)
            self._accel_calibration_quality = quality
            self._accel_calibration_result = (bias, matrix, quality)
            self._accel_calibration_state = "success"
            self._accel_calibration_message = "ready_to_save"
            self._ahrs.reset()
            self._reset_magnetic_fusion_state()
            self._nine_axis_orientation = None
            self._nine_axis_quaternion = None
            self._nine_axis_has_magnetometer = False
            self._tilt_neutral = None
            self._tilt_neutral_quaternion = None
            return True

    def start_magnetometer_calibration(self):
        """Begin a guided full-orientation magnetometer calibration."""
        with self._gyro_calibration_lock:
            self._mag_calibration_state = "running"
            self._mag_calibration_message = "move_figure_eight"
            self._mag_calibration_started = time.perf_counter()
            self._mag_calibration_samples = []
            self._mag_orientation_bins = set()
            self._mag_calibration_min = [math.inf, math.inf, math.inf]
            self._mag_calibration_max = [-math.inf, -math.inf, -math.inf]
            self._mag_calibration_result = None
            self._mag_calibration_last_fit = 0.0
            self._mag_calibration_last_error = ""
        self._gyro_toggle_enabled = False
        self._gyro_smoothed = (0.0, 0.0)
        self._gyro_was_active = False
        self._tilt_neutral = None
        self._tilt_neutral_quaternion = None

    def get_magnetometer_calibration_status(self):
        with self._gyro_calibration_lock:
            spans = tuple(
                max(0.0, self._mag_calibration_max[index]
                    - self._mag_calibration_min[index])
                for index in range(3)
            )
            elapsed = max(
                0.0, time.perf_counter() - self._mag_calibration_started
            ) if self._mag_calibration_state == "running" else 0.0
            coverage = min(1.0, min(spans) / 60.0) if spans else 0.0
            progress = int(round(95.0 * min(
                coverage,
                elapsed / 12.0,
                len(self._mag_orientation_bins) / self.MAG_MIN_ORIENTATION_BINS,
            )))
            if self._mag_calibration_state == "success":
                progress = 100
            return {
                "state": self._mag_calibration_state,
                "message": self._mag_calibration_message,
                "samples": len(self._mag_calibration_samples),
                "spans": spans,
                "progress": max(0, min(100, progress)),
                "orientation_bins": tuple(sorted(self._mag_orientation_bins)),
                "orientation_coverage": len(self._mag_orientation_bins) / 26.0,
            }

    def consume_magnetometer_calibration_result(self):
        with self._gyro_calibration_lock:
            result = self._mag_calibration_result
            self._mag_calibration_result = None
            return result

    def _process_magnetometer_calibration(self, magnetometer):
        now = time.perf_counter()
        with self._gyro_calibration_lock:
            if self._mag_calibration_state != "running":
                return False
            elapsed = now - self._mag_calibration_started
            if self._magnetometer_is_plausible(magnetometer):
                sample = tuple(float(value) for value in magnetometer)
                self._mag_calibration_samples.append(sample)
                for index in range(3):
                    self._mag_calibration_min[index] = min(
                        self._mag_calibration_min[index], sample[index]
                    )
                    self._mag_calibration_max[index] = max(
                        self._mag_calibration_max[index], sample[index]
                    )
                # Recenter all samples periodically so hard-iron offset does not
                # bias the visual 26-direction coverage. At 125 Hz this refreshes
                # roughly every 64 ms without adding meaningful CPU load.
                if len(self._mag_calibration_samples) % 8 == 0:
                    center = tuple(
                        (self._mag_calibration_min[index]
                         + self._mag_calibration_max[index]) * 0.5
                        for index in range(3)
                    )
                    bins = set()
                    for previous in self._mag_calibration_samples:
                        orientation_bin = self._orientation_bin(tuple(
                            previous[index] - center[index] for index in range(3)
                        ))
                        if orientation_bin is not None:
                            bins.add(orientation_bin)
                    self._mag_orientation_bins = bins

            spans = tuple(
                self._mag_calibration_max[index]
                - self._mag_calibration_min[index]
                for index in range(3)
            )
            ready_to_fit = (
                elapsed >= 12.0
                and len(self._mag_calibration_samples) >= 300
                and min(spans) >= 60.0
                and len(self._mag_orientation_bins)
                >= self.MAG_MIN_ORIENTATION_BINS
                and now - self._mag_calibration_last_fit >= 0.75
            )
            if not ready_to_fit and elapsed < 30.0:
                return True
            if not ready_to_fit:
                self._mag_calibration_state = "failed"
                self._mag_calibration_message = (
                    self._mag_calibration_last_error
                    or "insufficient_3d_coverage"
                )
                return True
            self._mag_calibration_last_fit = now
            try:
                bias, matrix, quality = fit_magnetometer_ellipsoid(
                    self._mag_calibration_samples
                )
            except ValueError as exc:
                self._mag_calibration_last_error = str(exc)
                self._mag_calibration_message = "more_orientations"
                return True

            self._mag_bias = list(bias)
            self._mag_scale = [1.0, 1.0, 1.0]
            self._mag_matrix = np.asarray(matrix, dtype=np.float64)
            self._mag_field_reference = 1.0
            self._mag_field_valid = True
            self._mag_calibration_result = (bias, matrix, quality)
            self._mag_calibration_state = "success"
            self._mag_calibration_message = "ready_to_save"
            self._ahrs.reset()
            self._reset_magnetic_fusion_state()
            self._nine_axis_orientation = None
            self._nine_axis_quaternion = None
            self._nine_axis_has_magnetometer = False
            self._tilt_neutral = None
            self._tilt_neutral_quaternion = None
            return True

    def _process_gyro_calibration(self, gyroscope, accelerometer):
        now = time.perf_counter()
        raw = tuple(float(value) for value in gyroscope)
        with self._gyro_calibration_lock:
            if self._gyro_calibration_state != "running":
                return False
            if now - self._gyro_calibration_started > 10.0:
                self._gyro_calibration_state = "failed"
                self._gyro_calibration_message = "timeout_or_movement"
                return True

            previous = self._gyro_calibration_last_raw
            self._gyro_calibration_last_raw = raw
            stable = (
                self._gyro_accel_is_plausible(accelerometer)
                and previous is not None
                and max(
                    abs(raw[index] - previous[index]) for index in range(3)
                ) <= 35.0
            )
            if not stable:
                self._gyro_calibration_stable_started = None
                self._gyro_calibration_last_sample_time = None
                self._gyro_calibration_valid_time = 0.0
                self._gyro_calibration_samples = []
                self._gyro_calibration_message = "keep_still"
                return True

            if self._gyro_calibration_stable_started is None:
                self._gyro_calibration_stable_started = now
                self._gyro_calibration_last_sample_time = now
            else:
                sample_dt = max(
                    0.0,
                    min(0.05, now - self._gyro_calibration_last_sample_time),
                )
                self._gyro_calibration_valid_time += sample_dt
                self._gyro_calibration_last_sample_time = now
            self._gyro_calibration_samples.append(raw)
            if (
                self._gyro_calibration_valid_time < 5.0
                or len(self._gyro_calibration_samples) < 150
            ):
                return True

            samples = self._gyro_calibration_samples
            axis_ranges = tuple(
                max(sample[index] for sample in samples)
                - min(sample[index] for sample in samples)
                for index in range(3)
            )
            try:
                bias, quality = gyro_calibration_quality(samples)
            except ValueError:
                self._gyro_calibration_state = "failed"
                self._gyro_calibration_message = "unstable"
                return True
            if max(axis_ranges) > 180.0 or any(
                not math.isfinite(value) or abs(value) > 4096.0
                for value in bias
            ):
                self._gyro_calibration_state = "failed"
                self._gyro_calibration_message = "unstable"
                return True

            self._gyro_bias = list(bias)
            self._gyro_bias_anchor = list(bias)
            self._gyro_bias_samples = 64
            self._gyro_stationary_samples = 0
            self._gyro_last_raw = None
            self._gyro_calibration_result = bias
            self._gyro_calibration_quality = quality
            self._gyro_calibration_state = "success"
            self._gyro_calibration_message = "saved"
            return True

    @staticmethod
    def _unit_vector(values):
        vector = tuple(float(value) for value in values)
        magnitude = math.sqrt(sum(value * value for value in vector))
        if not math.isfinite(magnitude) or magnitude <= 1e-9:
            return None
        inverse = 1.0 / magnitude
        return tuple(value * inverse for value in vector)

    def _update_impact_state(self, gyroscope, accelerometer, dt):
        """Latch short impact protection without suppressing gyro output."""
        now = time.perf_counter()
        raw_gyro = tuple(float(value) for value in gyroscope)
        raw_accel = tuple(float(value) for value in accelerometer)
        accel_magnitude = math.sqrt(sum(value * value for value in raw_accel))
        gravity_scale = self._impact_gravity_scale
        if gravity_scale is None:
            gravity_scale = min(
                (4096.0, 16384.0),
                key=lambda scale: abs(accel_magnitude - scale),
            )
            if abs(accel_magnitude - gravity_scale) <= gravity_scale * 0.35:
                self._impact_gravity_scale = gravity_scale
            else:
                self._impact_accel_lp = None
                self._impact_last_gyro_raw = raw_gyro
                return False
        inverse_gravity = 1.0 / gravity_scale
        accel_g = tuple(value * inverse_gravity for value in raw_accel)
        if dt <= 0.0 or dt > 0.10 or not all(map(math.isfinite, accel_g)):
            self._impact_accel_lp = accel_g
            self._impact_last_gyro_raw = raw_gyro
            return False

        previous_accel = self._impact_accel_lp
        alpha = 1.0 - math.exp(-dt / 0.020)
        filtered_accel = accel_g if previous_accel is None else tuple(
            previous_accel[index]
            + (accel_g[index] - previous_accel[index]) * alpha
            for index in range(3)
        )
        previous_gyro = self._impact_last_gyro_raw
        self._impact_accel_lp = filtered_accel
        self._impact_last_gyro_raw = raw_gyro
        if previous_accel is None or previous_gyro is None:
            return False

        jerk = math.sqrt(sum(
            (filtered_accel[index] - previous_accel[index]) ** 2
            for index in range(3)
        )) / dt
        gyro_acceleration = math.sqrt(sum(
            (raw_gyro[index] - previous_gyro[index]) ** 2
            for index in range(3)
        )) / 14.285714 / dt
        magnitude_error = abs(
            math.sqrt(sum(value * value for value in filtered_accel)) - 1.0
        )

        rumble_state = getattr(self, "_rumble_state", None)
        rumble_amplitude = (
            max(float(rumble_state[1]), float(rumble_state[3]))
            if rumble_state is not None and len(rumble_state) >= 4
            else 0.0
        )
        rumble_ratio = min(
            1.0, rumble_amplitude / max(1.0, float(self.max_amplitude))
        )
        threshold_scale = 1.0 + rumble_ratio
        indicators = (
            magnitude_error > 0.35 * threshold_scale,
            jerk > 15.0 * threshold_scale,
            gyro_acceleration > 900.0 * threshold_scale,
        )
        impact = sum(bool(value) for value in indicators) >= 2
        impact = impact or (
            magnitude_error > 0.65 * threshold_scale
            and jerk > 22.0 * threshold_scale
        )
        if impact:
            self._gyro_bias_block_until = max(
                self._gyro_bias_block_until,
                now + self.IMPACT_BIAS_HOLD_SECONDS,
            )
            self._impact_accel_reject_until = max(
                self._impact_accel_reject_until,
                now + self.IMPACT_ACCEL_REJECT_SECONDS,
            )
            self._impact_accel_recover_until = max(
                self._impact_accel_recover_until,
                now + self.IMPACT_ACCEL_RECOVERY_SECONDS,
            )
            self._gyro_stationary_samples = 0
            self._gyro_stationary_started = None
        return impact

    def _update_gyro_bias(self, gyroscope, accelerometer, magnetometer=None):
        """Learn bias only after sustained, orientation-stable stillness."""
        now = time.perf_counter()
        raw = tuple(float(value) for value in gyroscope)
        previous_raw = self._gyro_last_raw
        self._gyro_last_raw = raw
        if now < self._gyro_bias_block_until:
            self._gyro_stationary_samples = 0
            self._gyro_stationary_started = None
            return

        accel_plausible = self._gyro_accel_is_plausible(accelerometer)
        delta_small = (
            previous_raw is not None
            and max(abs(raw[index] - previous_raw[index]) for index in range(3))
            <= 12.0
        )
        residual = max(
            abs(raw[index] - self._gyro_bias[index]) for index in range(3)
        )
        rate_plausible = (
            max(abs(value) for value in raw) <= self.GYRO_INITIAL_ABS_LIMIT
            if self._gyro_bias_samples < 64
            else residual <= self.GYRO_TRACK_RESIDUAL_LIMIT
        )
        accel_direction = self._unit_vector(accelerometer) if accel_plausible else None
        mag_direction = (
            self._unit_vector(magnetometer)
            if magnetometer is not None
            and self._magnetometer_is_plausible(magnetometer)
            else None
        )
        candidate = accel_direction is not None and delta_small and rate_plausible

        if candidate and self._gyro_stationary_started is None:
            self._gyro_stationary_started = now
            self._gyro_stationary_accel_reference = accel_direction
            self._gyro_stationary_mag_reference = mag_direction
            self._gyro_stationary_samples = 1
            return

        if candidate:
            accel_reference = self._gyro_stationary_accel_reference
            accel_stable = (
                accel_reference is not None
                and sum(
                    accel_reference[index] * accel_direction[index]
                    for index in range(3)
                ) >= math.cos(math.radians(1.5))
            )
            mag_reference = self._gyro_stationary_mag_reference
            mag_stable = (
                mag_reference is None
                or (
                    mag_direction is not None
                    and sum(
                        mag_reference[index] * mag_direction[index]
                        for index in range(3)
                    ) >= math.cos(math.radians(2.0))
                )
            )
            candidate = accel_stable and mag_stable

        if not candidate:
            self._gyro_stationary_samples = 0
            self._gyro_stationary_started = None
            self._gyro_stationary_accel_reference = None
            self._gyro_stationary_mag_reference = None
            return

        self._gyro_stationary_samples += 1
        if (
            now - self._gyro_stationary_started
            < self.GYRO_STATIONARY_SETTLE_SECONDS
            or self._gyro_stationary_samples < 8
        ):
            return

        if self._gyro_bias_samples < 64:
            self._gyro_bias_samples += 1
            weight = 1.0 / self._gyro_bias_samples
            for index in range(3):
                self._gyro_bias[index] += (
                    raw[index] - self._gyro_bias[index]
                ) * weight
            if self._gyro_bias_samples == 64:
                self._gyro_bias_anchor = list(self._gyro_bias)
            return

        if self._gyro_bias_anchor is None:
            self._gyro_bias_anchor = list(self._gyro_bias)
        if residual <= self.GYRO_TRACK_RESIDUAL_LIMIT:
            for index in range(3):
                proposed = self._gyro_bias[index] + (
                    raw[index] - self._gyro_bias[index]
                ) * 0.002
                lower = self._gyro_bias_anchor[index] - self.GYRO_TRACK_MAX_DRIFT
                upper = self._gyro_bias_anchor[index] + self.GYRO_TRACK_MAX_DRIFT
                self._gyro_bias[index] = max(lower, min(upper, proposed))
    @staticmethod
    def _wrap_degrees(value):
        """Return the shortest signed representation of an angle."""
        return (float(value) + 180.0) % 360.0 - 180.0

    @staticmethod
    def _quaternion_conjugate(quaternion):
        w, x, y, z = (float(value) for value in quaternion)
        return (w, -x, -y, -z)

    @staticmethod
    def _quaternion_multiply(left, right):
        lw, lx, ly, lz = (float(value) for value in left)
        rw, rx, ry, rz = (float(value) for value in right)
        return (
            lw * rw - lx * rx - ly * ry - lz * rz,
            lw * rx + lx * rw + ly * rz - lz * ry,
            lw * ry - lx * rz + ly * rw + lz * rx,
            lw * rz + lx * ry - ly * rx + lz * rw,
        )

    @classmethod
    def _relative_orientation_from_quaternions(cls, current, neutral):
        """Return relative (yaw, -roll) without subtracting absolute Euler angles."""
        relative = cls._quaternion_multiply(
            cls._quaternion_conjugate(neutral), current
        )
        norm = math.sqrt(sum(value * value for value in relative))
        if norm <= 1e-9 or not math.isfinite(norm):
            return None
        relative = np.asarray(
            [value / norm for value in relative], dtype=np.float64
        )
        try:
            euler = imufusion.Quaternion(relative).to_euler()
        except (TypeError, ValueError, RuntimeError):
            return None
        if len(euler) < 3:
            return None
        return (
            cls._wrap_degrees(float(euler[2])),
            cls._wrap_degrees(-float(euler[0])),
        )

    @staticmethod
    def _tilt_axes_from_orientation(orientation, neutral, dual_axis):
        """Map fused roll/pitch to virtual-stick X/Y axes.

        Steering left/right is controller roll and therefore virtual-stick X.
        Forward/back tilt is controller pitch and is the optional stick Y.
        """
        horizontal = -XInputController._wrap_degrees(
            orientation[0] - neutral[0]
        )
        vertical = (
            -XInputController._wrap_degrees(
                orientation[1] - neutral[1]
            )
            if dual_axis
            else 0.0
        )
        return horizontal, vertical

    @classmethod
    def _tilt_orientation_from_accel(cls, accelerometer):
        """Return absolute roll/pitch from gravity, or None if unreliable."""
        if not cls._gyro_accel_is_plausible(accelerometer):
            return None
        return cls._orientation_from_gravity_values(accelerometer)

    @classmethod
    def _orientation_from_gravity_values(cls, accelerometer):
        accel_x, accel_y, accel_z = (
            float(value) for value in accelerometer
        )
        return (
            cls._wrap_degrees(
                math.degrees(math.atan2(accel_z, -accel_y))
            ),
            cls._wrap_degrees(
                math.degrees(
                    math.atan2(
                        -accel_x,
                        math.hypot(accel_z, accel_y),
                    )
                )
            ),
        )

    @staticmethod
    def _magnetometer_is_plausible(magnetometer):
        try:
            values = tuple(float(value) for value in magnetometer)
        except (TypeError, ValueError):
            return False
        if len(values) != 3 or not all(math.isfinite(value) for value in values):
            return False
        magnitude = math.sqrt(sum(value * value for value in values))
        return 1.0 <= magnitude <= 100000.0

    def _correct_magnetometer(self, magnetometer):
        """Apply full ellipsoid calibration or a legacy diagonal profile."""
        if not self._magnetometer_is_plausible(magnetometer):
            return None
        raw_x = float(magnetometer[0]) - self._mag_bias[0]
        raw_y = float(magnetometer[1]) - self._mag_bias[1]
        raw_z = float(magnetometer[2]) - self._mag_bias[2]
        if getattr(self, "_mag_matrix", None) is not None:
            matrix = self._mag_matrix
            corrected = (
                float(matrix[0][0]) * raw_x
                + float(matrix[0][1]) * raw_y
                + float(matrix[0][2]) * raw_z,
                float(matrix[1][0]) * raw_x
                + float(matrix[1][1]) * raw_y
                + float(matrix[1][2]) * raw_z,
                float(matrix[2][0]) * raw_x
                + float(matrix[2][1]) * raw_y
                + float(matrix[2][2]) * raw_z,
            )
            if not all(map(math.isfinite, corrected)):
                return None
            return corrected
        scale = getattr(self, "_mag_scale", (1.0, 1.0, 1.0))
        return tuple(
            value * float(scale[index])
            for index, value in enumerate((raw_x, raw_y, raw_z))
        )

    def _magnetic_field_is_stable(self, corrected):
        """Reject field-strength disturbances before they can pull the AHRS."""
        magnitude = math.sqrt(sum(value * value for value in corrected))
        if not math.isfinite(magnitude) or magnitude <= 1e-9:
            self._mag_field_valid = False
            return False
        reference = getattr(self, "_mag_field_reference", None)
        if reference is None or reference <= 1e-9:
            self._mag_field_reference = magnitude
            self._mag_field_valid = True
            return True
        ratio = magnitude / reference
        stable = 0.70 <= ratio <= 1.30
        self._mag_field_valid = stable
        if stable:
            # Very slow learning follows environmental scale without accepting
            # a sudden magnet or powered-device disturbance as the new normal.
            self._mag_field_reference += (magnitude - reference) * 0.001
        return stable

    def _absolute_steering_orientation(self, accelerometer, magnetometer):
        """Return direct calibrated (magnetic yaw, gravity pitch)."""
        corrected_accel = self._correct_accelerometer(accelerometer)
        if corrected_accel is None or not self._magnetometer_is_plausible(magnetometer):
            return None

        accel_x, accel_y, accel_z = (
            float(value) for value in corrected_accel
        )
        raw_mag = self._correct_magnetometer(magnetometer)
        if raw_mag is None:
            return None
        # Empirically verified Switch 2 Pro magnetic frame:
        # controller (X, Y, Z) = report (m0, m2, m1).
        mag_x, mag_y, mag_z = raw_mag[0], raw_mag[2], raw_mag[1]
        roll = math.atan2(accel_y, accel_z)
        pitch = math.atan2(
            -accel_x, math.hypot(accel_y, accel_z)
        )

        cos_roll = math.cos(roll)
        sin_roll = math.sin(roll)
        cos_pitch = math.cos(pitch)
        sin_pitch = math.sin(pitch)
        horizontal_x = mag_x * cos_pitch + mag_z * sin_pitch
        horizontal_y = (
            mag_x * sin_roll * sin_pitch
            + mag_y * cos_roll
            - mag_z * sin_roll * cos_pitch
        )
        if math.hypot(horizontal_x, horizontal_y) < 1e-6:
            return None
        heading = math.degrees(math.atan2(-horizontal_y, horizontal_x))
        return self._wrap_degrees(heading), self._wrap_degrees(
            -math.degrees(roll)
        )

    def _update_nine_axis_orientation(
        self, gyroscope, accelerometer, magnetometer, dt
    ):
        """Return magnetometer-anchored (yaw, pitch), or None without 9-axis data."""
        accel = self._correct_accelerometer(
            accelerometer, self._fusion_accel
        )
        if dt <= 0.0 or accel is None:
            return self._nine_axis_orientation if self._nine_axis_has_magnetometer else None

        gyro = self._fusion_gyro
        gyro_scale = 1.0 / 14.285714
        gyro[0] = (float(gyroscope[0]) - self._gyro_bias[0]) * gyro_scale
        gyro[1] = (float(gyroscope[1]) - self._gyro_bias[1]) * gyro_scale
        gyro[2] = (float(gyroscope[2]) - self._gyro_bias[2]) * gyro_scale
        # Blend toward predicted gravity during normal high acceleration and
        # force it briefly after a detected impact. Gyro/mouse output continues.
        gravity = self._ahrs.gravity
        gravity_x = float(gravity[0])
        gravity_y = float(gravity[1])
        gravity_z = float(gravity[2])
        gravity_magnitude = math.sqrt(
            gravity_x * gravity_x
            + gravity_y * gravity_y
            + gravity_z * gravity_z
        )
        accel_magnitude = math.sqrt(
            float(accel[0]) * float(accel[0])
            + float(accel[1]) * float(accel[1])
            + float(accel[2]) * float(accel[2])
        )
        blend = 0.0
        accel_suppression = getattr(self, "gyro_accel_suppression", 0.0)
        if (
            accel_suppression > 0.0
            and 0.5 <= gravity_magnitude <= 1.5
            and accel_magnitude > 1e-9
        ):
            angular_speed = math.sqrt(
                float(gyro[0]) * float(gyro[0])
                + float(gyro[1]) * float(gyro[1])
                + float(gyro[2]) * float(gyro[2])
            )
            speed_ratio = angular_speed / 30.0
            speed_factor = speed_ratio * speed_ratio / (1.0 + speed_ratio * speed_ratio)
            magnitude_factor = min(1.0, abs(accel_magnitude - 1.0) / 0.12)
            blend = accel_suppression * max(speed_factor, magnitude_factor)

        now = time.perf_counter()
        if now < self._impact_accel_reject_until:
            impact_blend = 1.0
        elif now < self._impact_accel_recover_until:
            recovery_span = max(
                1e-6,
                self._impact_accel_recover_until - self._impact_accel_reject_until,
            )
            impact_blend = (
                self._impact_accel_recover_until - now
            ) / recovery_span
        else:
            impact_blend = 0.0
        blend = max(blend, impact_blend)
        if blend > 0.0 and 0.5 <= gravity_magnitude <= 1.5:
            accel_weight = 1.0 - blend
            accel[0] = float(accel[0]) * accel_weight + gravity_x * blend
            accel[1] = float(accel[1]) * accel_weight + gravity_y * blend
            accel[2] = float(accel[2]) * accel_weight + gravity_z * blend
        corrected_mag = self._correct_magnetometer(magnetometer)
        magnetometer_valid = (
            corrected_mag is not None
            and self._magnetic_field_is_stable(corrected_mag)
        )
        use_magnetometer = False
        if magnetometer_valid:
            previous_valid = self._mag_last_valid_time
            if (
                previous_valid is None
                or now - previous_valid > self.MAGNETOMETER_TIMEOUT_SECONDS
            ):
                self._mag_recovery_started = now
                self._mag_recovery_accumulator = 0.0
                self._nine_axis_has_magnetometer = False
            self._mag_last_valid_time = now
            if self._mag_recovery_started is None:
                self._mag_recovery_started = now
            recovery_weight = min(
                1.0,
                max(0.0, now - self._mag_recovery_started)
                / self.MAGNETOMETER_RECOVERY_SECONDS,
            )
            if recovery_weight >= 1.0:
                use_magnetometer = True
            else:
                # Gradually increase how often magnetic correction is applied.
                # This avoids a heading snap while keeping every AHRS step valid.
                self._mag_recovery_accumulator += recovery_weight
                if self._mag_recovery_accumulator >= 1.0:
                    self._mag_recovery_accumulator -= 1.0
                    use_magnetometer = True
        elif (
            self._mag_last_valid_time is None
            or now - self._mag_last_valid_time
            > self.MAGNETOMETER_TIMEOUT_SECONDS
        ):
            self._mag_recovery_started = None
            self._mag_recovery_accumulator = 0.0
            self._nine_axis_has_magnetometer = False

        try:
            if use_magnetometer:
                # Align the magnetic package frame with the controller's
                # accelerometer/gyro frame before 9-axis fusion.
                mag = self._fusion_mag
                mag[0] = corrected_mag[0]
                mag[1] = corrected_mag[2]
                mag[2] = corrected_mag[1]
                self._ahrs.update(gyro, accel, mag, float(min(dt, 0.05)))
                self._nine_axis_has_magnetometer = True
            else:
                self._ahrs.update_no_magnetometer(
                    gyro, accel, float(min(dt, 0.05))
                )
        except (TypeError, ValueError, RuntimeError):
            return self._nine_axis_orientation if self._nine_axis_has_magnetometer else None

        euler = self._ahrs.quaternion.to_euler()
        if len(euler) < 3 or not all(math.isfinite(float(value)) for value in euler):
            return self._nine_axis_orientation if self._nine_axis_has_magnetometer else None
        quaternion = self._ahrs.quaternion
        self._nine_axis_quaternion = tuple(
            float(value) for value in quaternion.wxyz
        )
        self._nine_axis_orientation = (
            self._wrap_degrees(float(euler[2])),
            self._wrap_degrees(-float(euler[0])),
        )
        return self._nine_axis_orientation if self._nine_axis_has_magnetometer else None

    def _update_tilt_orientation(self, gyroscope, accelerometer, dt):
        """Track roll/pitch with gyro response and accelerometer drift correction."""
        if dt <= 0.0:
            return self._tilt_orientation

        # Native Switch 2 Pro IMU order is pitch, -yaw, roll.  Keep the
        # complementary filter in the same physical frame as the gravity
        # angles below; feeding yaw into pitch causes rapid neutral drift.
        raw_pitch, _raw_yaw, raw_roll = (
            float(value) for value in gyroscope
        )
        pitch_rate = (raw_pitch - self._gyro_bias[0]) / 14.285714
        roll_rate = (raw_roll - self._gyro_bias[2]) / 14.285714

        if self._tilt_orientation is None:
            predicted_roll = 0.0
            predicted_pitch = 0.0
        else:
            predicted_roll = self._tilt_orientation[0] + roll_rate * dt
            predicted_pitch = self._tilt_orientation[1] + pitch_rate * dt

        corrected_accel = self._correct_accelerometer(accelerometer)
        accel_orientation = (
            self._orientation_from_gravity_values(corrected_accel)
            if corrected_accel is not None
            else None
        )
        if accel_orientation is not None:
            accel_roll, accel_pitch = accel_orientation
            if self._tilt_orientation is None:
                predicted_roll = accel_roll
                predicted_pitch = accel_pitch
            else:
                # A slow gravity correction preserves quick gyro response while
                # preventing long-term roll/pitch drift.  It is time based, so
                # BLE and ESP32 report-rate differences do not change the feel.
                accel_magnitude = math.sqrt(
                    sum(
                        float(value) * float(value)
                        for value in accelerometer
                    )
                )
                nominal_gravity = min(
                    (4096.0, 16384.0),
                    key=lambda scale: abs(accel_magnitude - scale),
                )
                magnitude_error = abs(
                    accel_magnitude - nominal_gravity
                ) / nominal_gravity
                magnitude_confidence = max(
                    0.0, 1.0 - magnitude_error / 0.12
                )
                # A large orientation error must not suppress gravity recovery:
                # that made an over-range turn take many seconds to return to
                # its original center.  During motion, trust gyro response and
                # correct gently; once rotation stops, converge quickly to the
                # absolute gravity angle.
                angular_speed = math.hypot(roll_rate, pitch_rate)
                motion_ratio = min(1.0, angular_speed / 10.0)
                correction_time = 0.15 + 1.35 * motion_ratio
                accel_confidence = (
                    magnitude_confidence * magnitude_confidence
                )
                correction = (
                    1.0 - math.exp(-dt / correction_time)
                ) * accel_confidence
                predicted_roll += self._wrap_degrees(
                    accel_roll - predicted_roll
                ) * correction
                predicted_pitch += self._wrap_degrees(
                    accel_pitch - predicted_pitch
                ) * correction
        elif self._tilt_orientation is None:
            return None

        self._tilt_orientation = (
            self._wrap_degrees(predicted_roll),
            self._wrap_degrees(predicted_pitch),
        )
        return self._tilt_orientation

    def _gravity_aware_aim_axes(self, gyroscope, dt):
        """Project gyro rates into gravity-aligned player-space aiming axes."""
        gyro_scale = 1.0 / 14.285714
        rate_x = (float(gyroscope[0]) - self._gyro_bias[0]) * gyro_scale
        rate_y = (float(gyroscope[1]) - self._gyro_bias[1]) * gyro_scale
        rate_z = (float(gyroscope[2]) - self._gyro_bias[2]) * gyro_scale
        legacy = (-rate_z, rate_x)
        if not self.gyro_player_space:
            self._aim_gravity_sign = None
            self._aim_pose_ready_since = None
            self._aim_player_space_blend = 0.0
            return legacy

        # Each Hold/Toggle activation begins on the familiar fixed axes. A valid
        # pose is then accepted and blended in, preventing an edge-on start from
        # selecting an arbitrary hemisphere or causing a one-frame axis jump.
        if not self._gyro_was_active:
            self._aim_gravity_sign = None
            self._aim_pose_ready_since = None
            self._aim_player_space_blend = 0.0
        if self._nine_axis_quaternion is None:
            return legacy

        gravity = self._ahrs.gravity
        try:
            gravity_x = float(gravity[0])
            gravity_y = float(gravity[1])
            gravity_z = float(gravity[2])
        except (IndexError, TypeError, ValueError):
            return legacy
        magnitude_squared = (
            gravity_x * gravity_x
            + gravity_y * gravity_y
            + gravity_z * gravity_z
        )
        if (
            not math.isfinite(magnitude_squared)
            or not 0.25 <= magnitude_squared <= 2.25
        ):
            return legacy
        inverse_magnitude = 1.0 / math.sqrt(magnitude_squared)
        gravity_x *= inverse_magnitude
        gravity_y *= inverse_magnitude
        gravity_z *= inverse_magnitude
        now = time.perf_counter()

        if self._aim_gravity_sign is None:
            if abs(gravity_z) < 0.25:
                self._aim_pose_ready_since = None
                return legacy
            if self._aim_pose_ready_since is None:
                self._aim_pose_ready_since = now
                return legacy
            if now - self._aim_pose_ready_since < self.AIM_POSE_SETTLE_SECONDS:
                return legacy
            self._aim_gravity_sign = 1.0 if gravity_z >= 0.0 else -1.0
        gravity_sign = self._aim_gravity_sign
        gravity_x *= gravity_sign
        gravity_y *= gravity_sign
        gravity_z *= gravity_sign

        # Project the controller X axis onto the plane perpendicular to
        # gravity.  Scalar math avoids allocating three NumPy arrays for every
        # 250 Hz report while preserving the original vector calculation.
        vertical_x = 1.0 - gravity_x * gravity_x
        vertical_y = -gravity_x * gravity_y
        vertical_z = -gravity_x * gravity_z
        vertical_norm = math.sqrt(
            vertical_x * vertical_x
            + vertical_y * vertical_y
            + vertical_z * vertical_z
        )
        if vertical_norm <= 0.15:
            return legacy
        inverse_vertical_norm = 1.0 / vertical_norm
        vertical_x *= inverse_vertical_norm
        vertical_y *= inverse_vertical_norm
        vertical_z *= inverse_vertical_norm
        transformed = (
            -(
                rate_x * gravity_x
                + rate_y * gravity_y
                + rate_z * gravity_z
            ),
            (
                rate_x * vertical_x
                + rate_y * vertical_y
                + rate_z * vertical_z
            ),
        )

        blend_alpha = 1.0 - math.exp(
            -max(0.0, float(dt)) / self.AIM_BLEND_SECONDS
        )
        self._aim_player_space_blend += (
            1.0 - self._aim_player_space_blend
        ) * blend_alpha
        singularity_confidence = max(
            0.0, min(1.0, (vertical_norm - 0.15) / 0.25)
        )
        blend = self._aim_player_space_blend * singularity_confidence
        return (
            legacy[0] + (transformed[0] - legacy[0]) * blend,
            legacy[1] + (transformed[1] - legacy[1]) * blend,
        )
    def reset_tilt_neutral(self):
        """Use the next active Tilt sample as the new neutral pose."""
        if self.gyro_motion_mode != "TILT" or self.gyro_target == "MOUSE":
            return False
        self._gyro_was_active = False
        self._tilt_neutral = None
        self._tilt_neutral_quaternion = None
        self._gyro_smoothed = (0.0, 0.0)
        return True

    def _get_gyro_output(self, state, dt):
        """Return (stick_x, stick_y, mouse_dx, mouse_dy) for this report."""
        trigger_button_state = 0
        trigger_states = []
        for button in self.gyro_activation_buttons:
            mask = SWITCH_BUTTONS.get(button)
            pressed = bool(mask is not None and state.buttons & mask)
            trigger_states.append(pressed)
            if pressed and mask is not None:
                trigger_button_state |= mask
        if self.gyro_activation_match == "ALL":
            trigger_pressed = bool(trigger_states) and all(trigger_states)
        else:
            trigger_pressed = any(trigger_states)
        trigger_changed = trigger_button_state != getattr(
            self, "_gyro_trigger_button_state", 0
        )
        rising_edge = trigger_pressed and not self._gyro_trigger_was_pressed
        self._gyro_trigger_button_state = trigger_button_state
        if self.gyro_activation_mode == "TOGGLE" and rising_edge:
            self._gyro_toggle_enabled = not self._gyro_toggle_enabled
        self._gyro_trigger_was_pressed = trigger_pressed

        if self.gyro_activation_mode == "OFF":
            active = False
            self._gyro_toggle_enabled = False
        elif self.gyro_activation_mode == "HOLD":
            active = trigger_pressed
        else:
            active = self._gyro_toggle_enabled
        recenter_mask = SWITCH_BUTTONS.get(self.gyro_tilt_recenter_button)
        recenter_pressed = bool(
            recenter_mask is not None and state.buttons & recenter_mask
        )
        recenter_changed = (
            recenter_pressed != self._tilt_recenter_was_pressed
        )
        recenter_rising_edge = (
            recenter_pressed and not self._tilt_recenter_was_pressed
        )
        self._tilt_recenter_was_pressed = recenter_pressed
        if self.gyro_motion_mode == "TILT" and recenter_rising_edge:
            self.reset_tilt_neutral()
        stabilization_button_state = 0
        for button in getattr(self, "gyro_stabilization_buttons", ()):
            mask = SWITCH_BUTTONS.get(button)
            if mask is not None and state.buttons & mask:
                stabilization_button_state |= mask
        stabilization_changed = stabilization_button_state != getattr(
            self, "_gyro_stabilization_button_state", 0
        )
        self._gyro_stabilization_button_state = stabilization_button_state
        if (
            self.gyro_activation_mode != "OFF"
            and getattr(self, "gyro_button_freeze_ms", 0.0) > 0.0
            and (trigger_changed or recenter_changed or stabilization_changed)
        ):
            self._gyro_freeze_until = max(
                getattr(self, "_gyro_freeze_until", 0.0),
                time.perf_counter()
                + getattr(self, "gyro_button_freeze_ms", 0.0) / 1000.0,
            )
        gyroscope = getattr(state, "gyroscope", (0, 0, 0))
        accelerometer = getattr(state, "accelerometer", (0, 0, 0))
        magnetometer = getattr(state, "magnetometer", (0, 0, 0))
        self._update_impact_state(gyroscope, accelerometer, dt)
        if self._process_accelerometer_calibration(accelerometer, gyroscope):
            self._gyro_smoothed = (0.0, 0.0)
            self._gyro_was_active = False
            self._tilt_neutral = None
            self._tilt_neutral_quaternion = None
            return 0.0, 0.0, 0.0, 0.0
        if self._process_magnetometer_calibration(magnetometer):
            self._gyro_smoothed = (0.0, 0.0)
            self._gyro_was_active = False
            self._tilt_neutral = None
            self._tilt_neutral_quaternion = None
            return 0.0, 0.0, 0.0, 0.0
        if self._process_gyro_calibration(gyroscope, accelerometer):
            self._gyro_smoothed = (0.0, 0.0)
            self._gyro_was_active = False
            self._tilt_neutral = None
            self._tilt_neutral_quaternion = None
            return 0.0, 0.0, 0.0, 0.0
        # Tilt uses full attitude directly. Center/mouse keeps low-latency angular
        # velocity output, but runs AHRS to rotate those rates into player space.
        tilt_tracking_enabled = (
            self.gyro_activation_mode != "OFF"
            and self.gyro_motion_mode == "TILT"
            and self.gyro_target != "MOUSE"
        )
        player_space_tracking_enabled = (
            self.gyro_activation_mode != "OFF"
            and self.gyro_motion_mode == "CENTER"
            and self.gyro_player_space
        )
        nine_axis_orientation = None
        if tilt_tracking_enabled or player_space_tracking_enabled:
            nine_axis_orientation = self._update_nine_axis_orientation(
                gyroscope, accelerometer, magnetometer, dt
            )
        if not active:
            self._update_gyro_bias(gyroscope, accelerometer, magnetometer)
            if tilt_tracking_enabled:
                self._update_tilt_orientation(gyroscope, accelerometer, dt)
            self._gyro_smoothed = (0.0, 0.0)
            self._gyro_was_active = False
            self._gyro_motion_envelope = 0.0
            self._tilt_neutral = None
            self._tilt_neutral_quaternion = None
            return 0.0, 0.0, 0.0, 0.0
        # If the user is already holding/toggling the activation button when the
        # connector starts, still allow a stationary controller to establish its
        # initial center. Otherwise that session could remain at zero forever.
        if self._gyro_bias_samples < 16:
            self._update_gyro_bias(gyroscope, accelerometer, magnetometer)
            if tilt_tracking_enabled:
                self._update_tilt_orientation(gyroscope, accelerometer, dt)
            self._gyro_smoothed = (0.0, 0.0)
            self._gyro_was_active = False
            return 0.0, 0.0, 0.0, 0.0
        if dt <= 0.0:
            return 0.0, 0.0, 0.0, 0.0
        if time.perf_counter() < getattr(self, "_gyro_freeze_until", 0.0):
            self._gyro_smoothed = (0.0, 0.0)
            return 0.0, 0.0, 0.0, 0.0

        if self.gyro_motion_mode == "TILT" and self.gyro_target != "MOUSE":
            # Use one consistent 9-axis attitude for both axes: magnetic yaw
            # drives steering X, gravity-stabilized roll drives optional Y.
            orientation = nine_axis_orientation
            if orientation is None:
                orientation = self._update_tilt_orientation(
                    gyroscope, accelerometer, dt
                )
            if orientation is None:
                self._gyro_was_active = True
                self._gyro_smoothed = (0.0, 0.0)
                return 0.0, 0.0, 0.0, 0.0
            current_quaternion = self._nine_axis_quaternion
            if (
                not self._gyro_was_active
                or self._tilt_neutral is None
                or (
                    current_quaternion is not None
                    and self._tilt_neutral_quaternion is None
                )
            ):
                # Each Hold press / Toggle enable defines a comfortable neutral
                # without overwriting the persistent gyro zero-bias calibration.
                self._tilt_neutral = orientation
                self._tilt_neutral_quaternion = current_quaternion
                self._gyro_smoothed = (0.0, 0.0)
            relative_orientation = None
            if (
                current_quaternion is not None
                and self._tilt_neutral_quaternion is not None
            ):
                relative_orientation = self._relative_orientation_from_quaternions(
                    current_quaternion, self._tilt_neutral_quaternion
                )
            if relative_orientation is not None:
                horizontal = -relative_orientation[0]
                vertical = (
                    -relative_orientation[1]
                    if self.gyro_tilt_axis == "DUAL"
                    else 0.0
                )
            else:
                horizontal, vertical = self._tilt_axes_from_orientation(
                    orientation,
                    self._tilt_neutral,
                    self.gyro_tilt_axis == "DUAL",
                )
        else:
            horizontal, vertical = self._gravity_aware_aim_axes(gyroscope, dt)
        if self.gyro_motion_mode == "TILT":
            active_deadzone = self.gyro_tilt_deadzone
            self._gyro_motion_envelope = 0.0
        else:
            motion_speed = math.hypot(horizontal, vertical)
            time_constant = (
                0.035
                if motion_speed > getattr(self, "_gyro_motion_envelope", 0.0)
                else 0.120
            )
            envelope_alpha = 1.0 - math.exp(-dt / time_constant)
            self._gyro_motion_envelope = getattr(
                self, "_gyro_motion_envelope", 0.0
            ) + (
                motion_speed - getattr(self, "_gyro_motion_envelope", 0.0)
            ) * envelope_alpha
            active_deadzone = _adaptive_gyro_deadzone(
                self.gyro_deadzone,
                self._gyro_motion_envelope,
                getattr(self, "gyro_adaptive_deadzone", 0.0),
            )
        horizontal = _soft_deadzone(horizontal, active_deadzone)
        vertical = _soft_deadzone(vertical, active_deadzone)
        horizontal *= self.gyro_x_ratio
        vertical *= self.gyro_y_ratio
        if self.gyro_invert_x:
            horizontal = -horizontal
        if self.gyro_invert_y:
            vertical = -vertical

        if self.gyro_motion_mode == "TILT" and self.gyro_target != "MOUSE":
            # Bound the command before it enters the smoothing state.  This
            # prevents rotations beyond the configured maximum from creating
            # a hidden over-range value that takes time to unwind on return.
            tilt_range = max(
                1.0, self.gyro_tilt_max_angle - self.gyro_tilt_deadzone
            )
            target_side = (
                "left" if self.gyro_target == "LEFT_STICK" else "right"
            )
            bounded_x, bounded_y = _clamp_vector_to_shape(
                horizontal / tilt_range,
                vertical / tilt_range,
                self.stick_output_shape[target_side],
            )
            horizontal = bounded_x * tilt_range
            vertical = bounded_y * tilt_range

        active_smoothing_ms = (
            self.gyro_tilt_smoothing_ms
            if self.gyro_motion_mode == "TILT"
            else _adaptive_gyro_smoothing_ms(
                self.gyro_smoothing_ms,
                math.hypot(horizontal, vertical),
            )
        )
        if active_smoothing_ms <= 0.0:
            smoothed_x, smoothed_y = horizontal, vertical
        else:
            alpha = 1.0 - math.exp(
                -dt / max(0.001, active_smoothing_ms / 1000.0)
            )
            previous_x, previous_y = self._gyro_smoothed
            smoothed_x = previous_x + (horizontal - previous_x) * alpha
            smoothed_y = previous_y + (vertical - previous_y) * alpha
        self._gyro_smoothed = (smoothed_x, smoothed_y)
        self._gyro_was_active = True

        if self.gyro_target == "MOUSE":
            return (
                0.0,
                0.0,
                smoothed_x * self.gyro_mouse_sensitivity * dt,
                -smoothed_y * self.gyro_mouse_sensitivity * dt,
            )
        if self.gyro_motion_mode == "TILT":
            # Deadzone is subtracted above; compensate the remaining range so
            # the configured maximum angle still reaches full stick output.
            stick_scale = 1.0 / max(
                1.0, self.gyro_tilt_max_angle - self.gyro_tilt_deadzone
            )
        else:
            stick_scale = self.gyro_stick_sensitivity * 0.016
        stick_x = smoothed_x * stick_scale
        stick_y = smoothed_y * stick_scale
        if self.gyro_motion_mode == "CENTER":
            stick_x, stick_y = _apply_gyro_response_curve(
                stick_x,
                stick_y,
                self.gyro_response_curve,
                self.gyro_curve_strength,
            )
        return stick_x, stick_y, 0.0, 0.0

    def _emit_mouse_move(self, delta_x, delta_y):
        self._mouse_residual[0] += delta_x
        self._mouse_residual[1] += delta_y
        move_x = int(self._mouse_residual[0])
        move_y = int(self._mouse_residual[1])
        self._mouse_residual[0] -= move_x
        self._mouse_residual[1] -= move_y
        if move_x or move_y:
            user32.mouse_event(
                MOUSEEVENTF_MOVE,
                ctypes.c_long(move_x),
                ctypes.c_long(move_y),
                0,
                0,
            )

    def _begin_runtime_stick_neutral_capture(self, eligible_modes, layer=False):
        """Start an eight-report temporary-center capture for eligible sticks."""
        samples = {
            side: []
            if self.stick_direction_config[side]["mode"] in eligible_modes
            else None
            for side in ("LEFT", "RIGHT")
        }
        if layer:
            self._mapping_layer_neutral_raw = {"LEFT": None, "RIGHT": None}
            self._mapping_layer_neutral_samples = samples
        else:
            self._main_mapping_neutral_raw = {"LEFT": None, "RIGHT": None}
            self._main_mapping_neutral_samples = samples

    def _advance_runtime_stick_neutral_capture(self, state):
        """Accept only stable samples within 10% of permanent calibration."""
        if self._active_mapping_layer_id is None:
            samples_by_side = self._main_mapping_neutral_samples
            captured = self._main_mapping_neutral_raw
        else:
            samples_by_side = self._mapping_layer_neutral_samples
            captured = self._mapping_layer_neutral_raw

        for side, raw_xy in (
            ("LEFT", state.left_stick), ("RIGHT", state.right_stick),
        ):
            samples = samples_by_side[side]
            if samples is None:
                continue
            cal = self.cal[side.lower()]
            cx, cy = cal["center"]
            max_x, max_y = cal["max"]
            min_x, min_y = cal["min"]
            offset_x = apply_calibration_to_axis(raw_xy[0], cx, max_x, min_x)
            offset_y = apply_calibration_to_axis(raw_xy[1], cy, max_y, min_y)
            if math.hypot(offset_x, offset_y) > 0.10:
                # Reject this unstable/held position, but remain armed so the
                # capture can begin after the stick genuinely returns neutral.
                samples.clear()
                continue

            sample = (
                float(raw_xy[0]), float(raw_xy[1]), offset_x, offset_y,
            )
            if samples and math.hypot(
                offset_x - samples[0][2], offset_y - samples[0][3]
            ) > 0.02:
                # Still settling: restart the stability window here.
                samples[:] = [sample]
            else:
                samples.append(sample)
            if len(samples) < 8:
                continue

            captured[side] = (
                sum(item[0] for item in samples) / len(samples),
                sum(item[1] for item in samples) / len(samples),
            )
            samples_by_side[side] = None

    def _select_mapping_layer(self, state):
        """Apply a layer transition only when the physical button mask changes."""
        buttons = int(state.buttons)
        if buttons == self._mapping_layer_last_buttons:
            return
        first_report = self._mapping_layer_last_buttons is None
        self._mapping_layer_last_buttons = buttons
        layer_id = self._mapping_layer_selector.update(buttons)
        if layer_id == self._active_mapping_layer_id:
            if first_report and layer_id is None:
                self._begin_runtime_stick_neutral_capture(
                    {
                        "映射為滑鼠", "MOUSE_WHEEL_LINEAR",
                        "XINPUT_LT_LINEAR", "XINPUT_RT_LINEAR",
                    },
                )
            return

        # A mapping can change while its old keyboard/mouse action is held.
        # Release tracked external outputs before processing this same report
        # with the new layer, preventing stuck keys without adding a frame gap.
        self.release_all_keyboard_buttons()
        self.release_all_mouse_buttons()
        self._active_stick_directions = {"LEFT": None, "RIGHT": None}
        self._wheel_residual = {
            "LEFT": [0.0, 0.0], "RIGHT": [0.0, 0.0]
        }
        runtime = self._mapping_layer_runtimes.get(
            layer_id, self._base_mapping_runtime
        )
        (
            self._compiled_mapping,
            self.stick_direction_config,
            self._stick_direction_mapping_enabled,
        ) = runtime
        self._active_mapping_layer_id = layer_id
        self._mapping_layer_neutral_raw = {"LEFT": None, "RIGHT": None}
        if layer_id is not None:
            analog_modes = {
                "映射為滑鼠", "MOUSE_WHEEL_LINEAR",
                "XINPUT_LT_LINEAR", "XINPUT_RT_LINEAR",
            }
            self._begin_runtime_stick_neutral_capture(analog_modes, layer=True)
        elif not any(self._main_mapping_neutral_raw.values()):
            # The connection may have begun while a HOLD layer was active.
            # Capture the main mapping the first time it actually becomes active.
            self._begin_runtime_stick_neutral_capture(
                {
                    "映射為滑鼠", "MOUSE_WHEEL_LINEAR",
                    "XINPUT_LT_LINEAR", "XINPUT_RT_LINEAR",
                },
            )


    def update(self, state):
        # Reuse the ctypes XUSB_REPORT instead of allocating a new structure in
        # vgamepad.reset() for every input frame.
        report = self.pad.report
        report.wButtons = 0
        report.bLeftTrigger = 0
        report.bRightTrigger = 0
        report.sThumbLX = 0
        report.sThumbLY = 0
        report.sThumbRX = 0
        report.sThumbRY = 0
        self._select_mapping_layer(state)
        self._advance_runtime_stick_neutral_capture(state)
        now = time.perf_counter()
        if self._last_update_time is None:
            host_dt = 0.0
        else:
            host_dt = max(0.0, min(0.05, now - self._last_update_time))
        self._last_update_time = now
        dt = host_dt
        report_time = getattr(state, "report_time", None)
        if report_time is not None:
            report_time = int(report_time) & 0xFFFFFFFF
            if self._last_report_time is not None:
                delta = (report_time - self._last_report_time) & 0xFFFFFFFF
                if 0 < delta < 0x80000000:
                    self._last_report_delta = delta
                    # Real Switch 2 captures show 7/8 timer units at ~125 Hz
                    # and ~500 units over each 500 ms status interval.  The
                    # report clock is therefore milliseconds.  Prefer it over
                    # BLE/serial arrival timing, with a conservative fallback
                    # for resets, stalls, or malformed reports.
                    sensor_dt = delta / 1000.0
                    if 0.001 <= sensor_dt <= 0.05:
                        dt = sensor_dt
            self._last_report_time = report_time

        # =========================
        # Switch 按鍵 → Xbox 搖桿方向
        # =========================
        #
        # 這些值稍後會和正常的類比搖桿輸出整合。

        button_lx = 0.0
        button_ly = 0.0

        button_rx = 0.0
        button_ry = 0.0


        for switch_name, mask, kind, value, source in self._compiled_mapping:

            if mask is None:
                continue

            is_pressed = bool(
                state.buttons & mask
            )

            # A configured Tilt recenter shortcut is dedicated to neutral reset
            # and must not simultaneously trigger its game/keyboard mapping.
            if (
                self.gyro_motion_mode == "TILT"
                and self.gyro_tilt_recenter_button != "NONE"
                and switch_name == self.gyro_tilt_recenter_button
            ):
                continue

            # =========================
            # 自定義滑鼠按鍵映射
            # =========================
            if kind == _MAP_MOUSE:
                previous_mouse_action = self._active_mouse_buttons.get(switch_name)
                current_mouse_action = value if is_pressed else None
                if (
                    previous_mouse_action
                    and previous_mouse_action != current_mouse_action
                ):
                    self._release_mouse_button(previous_mouse_action, source)
                    self._active_mouse_buttons.pop(switch_name, None)
                if (
                    current_mouse_action
                    and current_mouse_action != previous_mouse_action
                ):
                    self._acquire_mouse_button(current_mouse_action, source)
                    self._active_mouse_buttons[switch_name] = current_mouse_action
                continue

            # =========================
            # 自定義鍵盤映射
            # =========================
            if kind == _MAP_KEYBOARD:
                # Switch 按鍵剛按下
                if (
                    is_pressed
                    and switch_name
                    not in self._active_keyboard_buttons
                ):
                    self._acquire_keyboard_combo(
                        value,
                        source
                    )

                    self._active_keyboard_buttons.add(
                        switch_name
                    )

                # Switch 按鍵剛放開
                elif (
                    not is_pressed
                    and switch_name
                    in self._active_keyboard_buttons
                ):
                    self._release_keyboard_combo_source(
                        value,
                        source
                    )

                    self._active_keyboard_buttons.discard(
                        switch_name
                    )

                continue

            # =========================
            # 一般 XInput 映射
            # =========================
            if not is_pressed or kind == _MAP_NONE:
                continue

            # =========================
            # Xbox 左搖桿方向
            # =========================
            if kind == _MAP_LX:
                button_lx = value
            elif kind == _MAP_LY:
                button_ly = value
            elif kind == _MAP_RX:
                button_rx = value
            elif kind == _MAP_RY:
                button_ry = value
            elif kind == _MAP_LT:
                report.bLeftTrigger = 255
            elif kind == _MAP_RT:
                report.bRightTrigger = 255
            elif kind == _MAP_BUTTON:
                report.wButtons |= value

        # =========================
        # 搖桿
        # =========================
        left_pointer_mode = self.stick_direction_config["LEFT"]["mode"] in (
            "映射為滑鼠", "MOUSE_WHEEL_LINEAR"
        )
        right_pointer_mode = self.stick_direction_config["RIGHT"]["mode"] in (
            "映射為滑鼠", "MOUSE_WHEEL_LINEAR"
        )
        neutral_raw = (
            self._mapping_layer_neutral_raw
            if self._active_mapping_layer_id is not None
            else self._main_mapping_neutral_raw
        )
        lx, ly = self._axis_pair(
            state.left_stick,
            "left",
            self.stick_direction_config["LEFT"]["mouse_deadzone"]
            if left_pointer_mode else 0.0,
            left_pointer_mode,
            neutral_raw["LEFT"],
        )

        rx, ry = self._axis_pair(
            state.right_stick,
            "right",
            self.stick_direction_config["RIGHT"]["mouse_deadzone"]
            if right_pointer_mode else 0.0,
            right_pointer_mode,
            neutral_raw["RIGHT"],
        )

        # 判斷左右搖桿是否啟用方向映射
        left_mapping_enabled = self._stick_direction_mapping_enabled["LEFT"]
        right_mapping_enabled = self._stick_direction_mapping_enabled["RIGHT"]

        # =========================
        # 搖桿方向映射
        # =========================

        if left_mapping_enabled:
            left_direction = (
                self._get_stick_direction(
                    lx,
                    ly,
                    "LEFT"
                )
            )

            self._active_stick_directions[
                "LEFT"
            ] = left_direction

        else:
            left_direction = None

            self._active_stick_directions[
                "LEFT"
            ] = None

        if right_mapping_enabled:
            right_direction = (
                self._get_stick_direction(
                    rx,
                    ry,
                    "RIGHT"
                )
            )

            self._active_stick_directions[
                "RIGHT"
            ] = right_direction

        else:
            right_direction = None

            self._active_stick_directions[
                "RIGHT"
            ] = None

        # =========================
        # 方向 → Xbox / 鍵盤映射
        # =========================

        for side, direction in (
            ("LEFT", left_direction),
            ("RIGHT", right_direction),
        ):
            # 取得這支搖桿上一幀
            # 正在保持的鍵盤組合
            previous_keyboard = (
                self._active_stick_keyboard[
                    side
                ]
            )

            # 預設目前沒有鍵盤輸出
            current_keyboard = None

            # =========================
            # 取得目前方向的映射
            # =========================
            if direction is not None:
                target = self.stick_direction_config[side]["mappings"].get(
                    direction, "NONE"
                )

            else:
                target = "NONE"

            # 滾輪是進入方向時觸發一次的瞬時事件。
            current_mouse_action = None
            if target in ("MOUSE:WHEEL_UP", "MOUSE:WHEEL_DOWN"):
                current_mouse_action = target[len("MOUSE:"):]
            previous_mouse_action = self._active_stick_mouse_action[side]
            source = "STICK:" + side
            if (
                current_mouse_action
                and current_mouse_action != previous_mouse_action
            ):
                self._emit_mouse_wheel(current_mouse_action)
            self._active_stick_mouse_action[side] = current_mouse_action

            # =========================
            # 鍵盤映射
            # =========================
            if target.startswith(
                "KEYBOARD:"
            ):
                current_keyboard = target[
                    len("KEYBOARD:"):
                ].strip()

            # =========================
            # 如果鍵盤組合改變：
            # 先放開舊的
            # =========================
            # 每支搖桿都有自己的唯一來源
            source = "STICK:" + side

            # =========================
            # 鍵盤組合改變：
            # 先釋放舊映射的來源
            # =========================
            if (
                previous_keyboard
                and previous_keyboard
                != current_keyboard
            ):
                self._release_keyboard_combo_source(
                    previous_keyboard,
                    source
                )

            # =========================
            # 再取得新映射
            # =========================
            if (
                current_keyboard
                and current_keyboard
                != previous_keyboard
            ):
                self._acquire_keyboard_combo(
                    current_keyboard,
                    source
                )

            # 保存目前鍵盤狀態
            self._active_stick_keyboard[
                side
            ] = current_keyboard

            # =========================
            # Xbox 映射
            # =========================

            # NONE 或鍵盤映射
            # 不需要再處理 Xbox 輸出
            if (
                target in (
                    "",
                    "NONE"
                )
                or target.startswith(
                    "KEYBOARD:"
                )
                or target.startswith(
                    "MOUSE:"
                )
            ):
                continue

            # LT
            if target == "LT":
                report.bLeftTrigger = 255

            # RT
            elif target == "RT":
                report.bRightTrigger = 255

            # 一般 Xbox 按鍵
            elif target in XB_BUTTONS:
                report.wButtons |= int(XB_BUTTONS[target])

        # =========================
        # 類比搖桿重新映射模式
        # =========================

        left_stick_mode = (
            self.stick_direction_config[
                "LEFT"
            ][
                "mode"
            ]
        )

        right_stick_mode = (
            self.stick_direction_config[
                "RIGHT"
            ][
                "mode"
            ]
        )

        # 左實體搖桿 → Xbox 右搖桿
        left_to_right = (
            left_stick_mode
            == "映射為右搖桿"
        )

        # 右實體搖桿 → Xbox 左搖桿
        right_to_left = (
            right_stick_mode
            == "映射為左搖桿"
        )
        left_to_mouse = left_stick_mode == "映射為滑鼠"
        right_to_mouse = right_stick_mode == "映射為滑鼠"
        left_to_wheel = left_stick_mode == "MOUSE_WHEEL_LINEAR"
        right_to_wheel = right_stick_mode == "MOUSE_WHEEL_LINEAR"
        left_to_trigger = left_stick_mode in (
            "XINPUT_LT_LINEAR", "XINPUT_RT_LINEAR"
        )
        right_to_trigger = right_stick_mode in (
            "XINPUT_LT_LINEAR", "XINPUT_RT_LINEAR"
        )


        # =========================
        # 輸出類比搖桿
        # =========================
        #
        # 如果該搖桿啟用了方向映射：
        # 原本的 XInput 類比搖桿輸出歸零。
        #
        # 如果沒有啟用方向映射：
        # 保持原本的類比搖桿輸出。

        # =========================
        # 最終左搖桿輸出
        # =========================

        left_button_stick_active = (
            button_lx != 0.0
            or button_ly != 0.0
        )

        if left_button_stick_active:
            # Switch 按鍵模擬 Xbox 左搖桿
            # 優先級最高。
            final_lx = button_lx
            final_ly = button_ly

        elif right_to_left:
            # 右實體搖桿
            # → Xbox 左搖桿
            final_lx = rx
            final_ly = ry

        elif left_to_right:
            # 左實體搖桿已改送到 Xbox 右搖桿，
            # 因此不再輸出自己的 Xbox 左搖桿。
            final_lx = 0.0
            final_ly = 0.0

        elif left_to_mouse or left_to_wheel or left_to_trigger:
            final_lx = 0.0
            final_ly = 0.0

        elif left_mapping_enabled:
            # 左實體搖桿作為方向映射使用
            final_lx = 0.0
            final_ly = 0.0

        else:
            # 正常實體左搖桿輸出
            final_lx = lx
            final_ly = ly

        # =========================
        # 最終右搖桿輸出
        # =========================

        right_button_stick_active = (
            button_rx != 0.0
            or button_ry != 0.0
        )

        if right_button_stick_active:
            # Switch 按鍵模擬 Xbox 右搖桿
            # 優先級最高。
            final_rx = button_rx
            final_ry = button_ry

        elif left_to_right:
            # 左實體搖桿
            # → Xbox 右搖桿
            final_rx = lx
            final_ry = ly

        elif right_to_left:
            # 右實體搖桿已改送到 Xbox 左搖桿，
            # 因此不再輸出自己的 Xbox 右搖桿。
            final_rx = 0.0
            final_ry = 0.0

        elif right_to_mouse or right_to_wheel or right_to_trigger:
            final_rx = 0.0
            final_ry = 0.0

        elif right_mapping_enabled:
            # 右實體搖桿作為方向映射使用
            final_rx = 0.0
            final_ry = 0.0

        else:
            # 正常實體右搖桿輸出
            final_rx = rx
            final_ry = ry

        # =========================
        # 陀螺儀與滑鼠輸出整合
        # =========================
        gyro_x, gyro_y, mouse_dx, mouse_dy = self._get_gyro_output(state, dt)

        # 搖桿映射為游標時使用實際時間積分，更新頻率改變不會改變速度。
        if left_to_mouse:
            mouse_speed = self.stick_direction_config["LEFT"]["mouse_speed"]
            mouse_dx += lx * mouse_speed * dt
            mouse_dy += -ly * mouse_speed * dt
        if right_to_mouse:
            mouse_speed = self.stick_direction_config["RIGHT"]["mouse_speed"]
            mouse_dx += rx * mouse_speed * dt
            mouse_dy += -ry * mouse_speed * dt

        # A wheel has discrete 120-unit detents. Integrate the curved stick
        # value over real time so its detent rate remains linear and transport
        # polling frequency does not alter scrolling speed.
        for side, enabled, x_value, y_value in (
            ("LEFT", left_to_wheel, lx, ly),
            ("RIGHT", right_to_wheel, rx, ry),
        ):
            residual = self._wheel_residual[side]
            if not enabled:
                residual[0] = residual[1] = 0.0
                continue
            detents_per_second = (
                self.stick_direction_config[side]["mouse_speed"] / 50.0
            )
            horizontal_steps, vertical_steps = _accumulate_wheel_detents(
                residual, x_value, y_value, detents_per_second, dt
            )
            for _ in range(abs(horizontal_steps)):
                self._emit_mouse_wheel(
                    "WHEEL_RIGHT" if horizontal_steps > 0 else "WHEEL_LEFT"
                )
            for _ in range(abs(vertical_steps)):
                self._emit_mouse_wheel(
                    "WHEEL_UP" if vertical_steps > 0 else "WHEEL_DOWN"
                )

        # Linear trigger modes consume the already calibrated, deadzoned and
        # curved stick output. Physical/digital trigger mappings remain valid;
        # the stronger source wins for each XInput 0..255 trigger channel.
        for side, mode, x_value, y_value in (
            ("LEFT", left_stick_mode, lx, ly),
            ("RIGHT", right_stick_mode, rx, ry),
        ):
            if mode not in ("XINPUT_LT_LINEAR", "XINPUT_RT_LINEAR"):
                continue
            amount = _directional_axis_value(
                x_value,
                y_value,
                self.stick_direction_config[side]["analog_direction"],
            )
            trigger_value = max(0, min(255, round(amount * 255.0)))
            if mode == "XINPUT_LT_LINEAR":
                report.bLeftTrigger = max(
                    int(report.bLeftTrigger), trigger_value
                )
            else:
                report.bRightTrigger = max(
                    int(report.bRightTrigger), trigger_value
                )

        # 陀螺儀與實體搖桿採向量相加，再依目標搖桿的輸出形狀限制邊界。
        # 這保留小幅實體微調，也避免逐軸截斷造成斜向失真。
        if self.gyro_target == "LEFT_STICK":
            if self.gyro_motion_mode == "CENTER":
                gyro_x, gyro_y = _apply_gyro_stick_anti_deadzone(
                    gyro_x,
                    gyro_y,
                    self.gyro_stick_anti_deadzone,
                    math.hypot(final_lx, final_ly),
                )
            final_lx, final_ly = _clamp_vector_to_shape(
                final_lx + gyro_x,
                final_ly + gyro_y,
                self.stick_output_shape["left"],
            )
        elif self.gyro_target == "RIGHT_STICK":
            if self.gyro_motion_mode == "CENTER":
                gyro_x, gyro_y = _apply_gyro_stick_anti_deadzone(
                    gyro_x,
                    gyro_y,
                    self.gyro_stick_anti_deadzone,
                    math.hypot(final_rx, final_ry),
                )
            final_rx, final_ry = _clamp_vector_to_shape(
                final_rx + gyro_x,
                final_ry + gyro_y,
                self.stick_output_shape["right"],
            )

        self._emit_mouse_move(mouse_dx, mouse_dy)

        # =========================
        # 寫入 Xbox 虛擬搖桿
        # =========================

        report.sThumbLX = round(final_lx * 32767)
        report.sThumbLY = round(final_ly * 32767)
        report.sThumbRX = round(final_rx * 32767)
        report.sThumbRY = round(final_ry * 32767)

        self.pad.update()
