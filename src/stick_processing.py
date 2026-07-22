"""Pure stick configuration and response helpers.

This module deliberately contains no controller, ViGEm, threading, or I/O state.
"""

import math

from mapping_targets import (
    validate_stick_direction_mode,
    validate_stick_setting_key,
)
from settings_schema import normalize_section_values
from stick_curve import apply_stick_curve


_STICK_DIRECTION_NAMES = (
    "UP", "UP_RIGHT", "RIGHT", "DOWN_RIGHT",
    "DOWN", "DOWN_LEFT", "LEFT", "UP_LEFT",
)


def _normalize_stick_direction_mode(value, side):
    """Return a validated side-specific mode instead of silently falling back."""
    return validate_stick_direction_mode(value, side)

def _compile_stick_direction_settings(raw_sides):
    """Normalize serialized left/right direction settings for the hot path."""
    result = {}
    for side in ("LEFT", "RIGHT"):
        raw = raw_sides[side.lower()]
        for setting_name in raw:
            validate_stick_setting_key(setting_name)
        settings = normalize_section_values(
            f"stick_direction_{side.lower()}", raw, strict=False
        )
        result[side] = {
            "mode": settings["mode"],
            "mappings": {
                direction: str(raw.get(direction.lower(), "NONE")).strip().upper()
                for direction in _STICK_DIRECTION_NAMES
            },
            "trigger_threshold": settings["trigger_threshold"],
            "release_threshold": settings["release_threshold"],
            "direction_deadzone": settings["direction_deadzone"],
            "mouse_speed": settings["mouse_speed"],
            "mouse_deadzone": settings["mouse_deadzone"],
            "analog_direction": settings["analog_direction"],
        }
    return result

def _apply_linear_axis_response(
    amount, deadzone, outer_deadzone, deadzone_compress,
    outer_deadzone_compress, curve_points, interpolation,
):
    """Apply one stick's calibrated curve to a directional 0..1 axis."""
    amount = max(0.0, min(1.0, float(amount)))
    deadzone = max(0.0, min(1.0, float(deadzone)))
    outer_deadzone = max(0.0, min(1.0, float(outer_deadzone)))
    if amount < deadzone:
        return 0.0
    outer_threshold = 1.0 - outer_deadzone
    if outer_deadzone > 0.0 and amount >= outer_threshold:
        return 1.0
    curve_start = deadzone if deadzone_compress else 0.0
    curve_end = outer_threshold if outer_deadzone_compress else 1.0
    if curve_end <= curve_start:
        curve_start, curve_end = 0.0, 1.0
    curve_input = max(
        0.0,
        min(1.0, (amount - curve_start) / (curve_end - curve_start)),
    )
    return max(0.0, min(1.0, apply_stick_curve(
        curve_input, curve_points, interpolation,
    )))

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

def apply_calibration_to_axis(raw_value, center, max_abs, min_abs):
    """Normalize the positive and negative travel around center independently."""
    signed_value = raw_value - center
    if signed_value > 0:
        return min(signed_value / max(1, max_abs), 1.0)
    if signed_value < 0:
        return -min(-signed_value / max(1, min_abs), 1.0)
    return 0.0

def _calibration_with_center_override(calibration, center_override=None):
    """Return a working center and ranges while preserving physical endpoints."""
    calibrated_center = tuple(float(value) for value in calibration["center"])
    maximum = tuple(float(value) for value in calibration["max"])
    minimum = tuple(float(value) for value in calibration["min"])
    if center_override is None:
        return calibrated_center, maximum, minimum

    center = tuple(float(value) for value in center_override)
    maximum = tuple(
        max(
            1.0,
            calibrated_center[index] + maximum[index] - center[index],
        )
        for index in (0, 1)
    )
    minimum = tuple(
        max(
            1.0,
            center[index] - (calibrated_center[index] - minimum[index]),
        )
        for index in (0, 1)
    )
    return center, maximum, minimum

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

