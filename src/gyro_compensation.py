"""Calculate a gyro axis ratio from two turn measurements."""

import math


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
