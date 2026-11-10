"""Angular deadzones shared by stick direction mapping and its radar editor."""

import json
import math


CUSTOM_DEADZONE_KEY = "direction_deadzone_custom"
DIRECTION_SECTORS = {
    "4WAY": ("RIGHT", "UP", "LEFT", "DOWN"),
    "8WAY": ("RIGHT", "UP_RIGHT", "UP", "UP_LEFT", "LEFT", "DOWN_LEFT", "DOWN", "DOWN_RIGHT"),
}
MAX_ZONE_PERCENT = 90


def sector_step(mode):
    return 360 / len(DIRECTION_SECTORS[mode])


def clamp_zone(mode, offset, percent):
    """Keep both edges at least 5% of a sector away from its direction centers."""
    offset, percent = float(offset), float(percent)
    if not math.isfinite(offset) or not math.isfinite(percent):
        raise ValueError("Non-finite angular deadzone")
    percent = max(0, min(MAX_ZONE_PERCENT, percent))
    limit = sector_step(mode) * (MAX_ZONE_PERCENT - percent) / 200
    return max(-limit, min(limit, offset)), percent


def parse_custom_zones(value):
    """Read per-mode sequences of (center offset in degrees, width percent)."""
    if isinstance(value, str):
        value = value.strip()
    if value is None or value == "":
        return {}
    try:
        values = json.loads(value) if isinstance(value, str) else value
    except RecursionError as exc:
        raise ValueError("Invalid angular deadzone nesting") from exc
    if not isinstance(values, dict):
        raise ValueError("Expected angular deadzone modes")
    result = {}
    for mode, zones in values.items():
        if mode not in DIRECTION_SECTORS or not isinstance(zones, (list, tuple)):
            raise ValueError("Invalid angular deadzone mode")
        if len(zones) != len(DIRECTION_SECTORS[mode]):
            raise ValueError("Incorrect number of angular deadzones")
        normalized = []
        for zone in zones:
            if not isinstance(zone, (list, tuple)) or len(zone) != 2:
                raise ValueError("Invalid angular deadzone")
            normalized.append(clamp_zone(mode, *zone))
        result[mode] = tuple(normalized)
    return result


def format_custom_zones(values):
    return json.dumps(values, separators=(",", ":"), sort_keys=True) if values else ""


def uniform_zones(mode, half_width):
    percent = 200 * half_width / sector_step(mode)
    return (clamp_zone(mode, 0, percent),) * len(DIRECTION_SECTORS[mode])


def zone_edges(mode, zones):
    """Return pairs of angular edges measured from each gap's first direction."""
    step = sector_step(mode)
    return tuple(
        (step / 2 + offset - step * percent / 200, step / 2 + offset + step * percent / 200)
        for offset, percent in zones
    )


def pick_direction(mode, angle, edges, current):
    sectors = DIRECTION_SECTORS[mode]
    step = 360 / len(sectors)
    angle %= 360
    gap = int(angle // step)
    position = angle - gap * step
    start, end = edges[gap]
    lower, upper = sectors[gap], sectors[(gap + 1) % len(sectors)]
    if position < start:
        return lower
    if position > end:
        return upper
    if start < position < end or current in (lower, upper):
        return current
    return lower if position < end else upper
