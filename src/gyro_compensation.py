"""Calculate a gyro axis ratio from two turn measurements."""

import json
import math
from dataclasses import dataclass, replace


DEFAULT_COMPENSATION = {"X": 1.0, "Y": 1.0}
COMPENSATION_FIELDS = {"X": "x_compensation", "Y": "y_compensation"}


@dataclass(frozen=True)
class CompensationSnapshot:
    profile: str | None
    multipliers: dict[str, float]
    saved_multipliers: dict[str, float] | None = None
    connection: tuple[str | None, str | None] | None = None
    output_target: str = "RIGHT_STICK"
    saved_output_target: str | None = None

    def with_multipliers(self, multipliers):
        saved = multipliers if self.saved_multipliers is not None else None
        return replace(self, multipliers=multipliers, saved_multipliers=saved)


def profile_compensation(config):
    return {
        axis: config.getfloat("gyro_mapping", key, fallback=DEFAULT_COMPENSATION[axis])
        for axis, key in COMPENSATION_FIELDS.items()
    }


def compensation_connection(status):
    if status.get("state") == "connected":
        return status.get("controller_id"), status.get("connection_id")
    return None


def compensation_reload_signature(config):
    """Identify the compensation settings a live reload is expected to apply."""
    ratios = profile_compensation(config)
    profile = config.get("gui", "active_profile", fallback="")
    target = config.get("gyro_mapping", "target", fallback="RIGHT_STICK").strip().upper()
    return json.dumps([profile, target, ratios["X"], ratios["Y"]], separators=(",", ":"))


def suggested_axis_ratio(original_ratio, old_measurement, new_measurement, *, elapsed_time=False):
    """Use angles over equal time, or durations over the same turn."""
    if not all(math.isfinite(value) and value > 0
               for value in (original_ratio, old_measurement, new_measurement)):
        raise ValueError("Expected finite positive values")
    if elapsed_time:
        ratio = original_ratio * (new_measurement / old_measurement)
    else:
        ratio = original_ratio * (old_measurement / new_measurement)
    if not math.isfinite(ratio):
        raise ValueError("Invalid compensation ratio")
    return round(ratio, 2)
