"""Pure gyro response helpers.

The functions here are deterministic and do not own timing, device, or output state.
"""

import math


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
    # Smoothing approaches zero asymptotically.  Do not amplify its sub-count
    # tail back to the full anti-deadzone on every subsequent report.
    if magnitude <= 1e-5:
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
