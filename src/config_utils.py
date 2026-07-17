import configparser
import json
import math
import os
import re
import tempfile
from pathlib import Path


CONFIG_PATH = Path(__file__).with_name("config.ini")


def parse_output_shape_steps(value):
    """Normalize legacy CIRCLE/SQUARE or a numeric value to 0..10 steps."""
    text = str(value).strip().upper()
    if text == "CIRCLE":
        return 0
    if text == "SQUARE":
        return 10
    try:
        return max(0, min(10, int(round(float(text)))))
    except (TypeError, ValueError):
        return 0


def normalize_controller_id(controller_id):
    """Return a stable ConfigParser-safe identifier for one controller."""
    if not controller_id:
        return None
    normalized = re.sub(r"[^0-9A-Za-z]", "", str(controller_id)).upper()
    return normalized or None


def calibration_section(controller_id):
    normalized = normalize_controller_id(controller_id)
    return f"sticks.{normalized}" if normalized else "sticks"


def gyro_calibration_section(controller_id):
    normalized = normalize_controller_id(controller_id)
    return f"gyro.{normalized}" if normalized else "gyro"


def resolve_wired_calibration_id(config, wired_id):
    """Alias a USB instance to an existing single-controller profile when safe."""
    normalized = normalize_controller_id(wired_id)
    if not normalized:
        return wired_id
    explicit = config.get("wired_aliases", normalized, fallback="").strip()
    if explicit:
        return normalize_controller_id(explicit) or wired_id
    if config.has_section(f"sticks.{normalized}") or config.has_section(
        f"gyro.{normalized}"
    ):
        return normalized
    candidates = set()
    for section in config.sections():
        prefix, separator, suffix = section.partition(".")
        candidate = normalize_controller_id(suffix) if separator else None
        if prefix in ("sticks", "gyro") and candidate and not candidate.startswith("USB"):
            candidates.add(candidate)
    return next(iter(candidates)) if len(candidates) == 1 else normalized


def read_pair(config, section, key):
    value = config.get(section, key)
    x, y = value.split(",", 1)
    return int(x.strip()), int(y.strip())


def load_stick_calibration(config, controller_id=None):
    """Load per-controller calibration, falling back to legacy [sticks]."""
    section = calibration_section(controller_id)
    required = (
        "left_center", "left_max", "left_min",
        "right_center", "right_max", "right_min",
    )
    if section != "sticks" and not (
        config.has_section(section)
        and all(config.has_option(section, key) for key in required)
    ):
        section = "sticks"

    return {
        "left": {
            "center": read_pair(config, section, "left_center"),
            "max": read_pair(config, section, "left_max"),
            "min": read_pair(config, section, "left_min"),
        },
        "right": {
            "center": read_pair(config, section, "right_center"),
            "max": read_pair(config, section, "right_max"),
            "min": read_pair(config, section, "right_min"),
        },
    }


def store_stick_calibration(config, calibration, controller_id=None):
    """Store both a device profile and the legacy last-used calibration."""
    sections = ["sticks"]
    device_section = calibration_section(controller_id)
    if device_section != "sticks":
        sections.append(device_section)

    for section in sections:
        if not config.has_section(section):
            config.add_section(section)
        for side in ("left", "right"):
            values = calibration[side]
            for field in ("center", "max", "min"):
                x, y = values[field]
                config.set(section, f"{side}_{field}", f"{int(x)}, {int(y)}")


def load_gyro_bias(config, controller_id=None):
    """Load a per-controller three-axis gyro zero bias, if available."""
    section = gyro_calibration_section(controller_id)
    if section != "gyro" and not config.has_option(section, "bias"):
        return None
    if not config.has_option(section, "bias"):
        section = "gyro"
    if not config.has_option(section, "bias"):
        return None
    values = [
        float(value.strip())
        for value in config.get(section, "bias").split(",")
    ]
    if len(values) != 3 or any(
        not math.isfinite(value) or abs(value) > 4096.0
        for value in values
    ):
        raise ValueError("gyro bias must contain three reasonable values")
    return tuple(values)


def load_magnetometer_bias(config, controller_id=None):
    """Load a per-controller three-axis hard-iron magnetometer offset."""
    section = gyro_calibration_section(controller_id)
    if not config.has_option(section, "mag_bias"):
        return None
    values = [
        float(value.strip())
        for value in config.get(section, "mag_bias").split(",")
    ]
    if len(values) != 3 or any(
        not math.isfinite(value) or not -32768.0 <= value <= 32767.0
        for value in values
    ):
        raise ValueError("mag bias must contain three reasonable values")
    return tuple(values)


def load_magnetometer_scale(config, controller_id=None):
    """Load per-axis soft-iron scale; legacy bias-only profiles use 1.0."""
    section = gyro_calibration_section(controller_id)
    if not config.has_option(section, "mag_scale"):
        return (1.0, 1.0, 1.0)
    values = [
        float(value.strip())
        for value in config.get(section, "mag_scale").split(",")
    ]
    if len(values) != 3 or any(
        not math.isfinite(value) or not 0.25 <= value <= 4.0
        for value in values
    ):
        raise ValueError("mag scale must contain three reasonable values")
    return tuple(values)


def load_magnetometer_matrix(config, controller_id=None):
    """Load a full row-major 3x3 soft-iron calibration matrix, if present."""
    section = gyro_calibration_section(controller_id)
    if config.get(section, "mag_model", fallback="") != "ellipsoid_spd":
        return None
    if not config.has_option(section, "mag_matrix"):
        return None
    values = [
        float(value.strip())
        for value in config.get(section, "mag_matrix").split(",")
    ]
    if len(values) != 9 or any(
        not math.isfinite(value) or abs(value) > 10.0 for value in values
    ):
        raise ValueError("mag matrix must contain nine reasonable values")
    rows = tuple(tuple(values[row * 3:(row + 1) * 3]) for row in range(3))
    if max(
        abs(rows[row][column] - rows[column][row])
        for row in range(3) for column in range(3)
    ) > 1e-8:
        raise ValueError("mag matrix must be symmetric")
    determinant = (
        rows[0][0] * (rows[1][1] * rows[2][2] - rows[1][2] * rows[2][1])
        - rows[0][1] * (rows[1][0] * rows[2][2] - rows[1][2] * rows[2][0])
        + rows[0][2] * (rows[1][0] * rows[2][1] - rows[1][1] * rows[2][0])
    )
    if not math.isfinite(determinant) or determinant <= 1e-12:
        raise ValueError("mag matrix must be invertible and orientation preserving")
    return rows


def load_accelerometer_calibration(config, controller_id=None):
    """Load per-controller accelerometer bias and row-major 3x3 matrix."""
    section = gyro_calibration_section(controller_id)
    # The retired six-face affine model could rotate the accelerometer frame
    # away from the gyro frame.  Keep its data in config for traceability, but
    # only load the orientation-preserving ellipsoid model.
    if config.get(section, "accel_model", fallback="") != "ellipsoid":
        return None, None
    if not (
        config.has_option(section, "accel_bias")
        and config.has_option(section, "accel_matrix")
    ):
        return None, None
    bias = tuple(
        float(value.strip())
        for value in config.get(section, "accel_bias").split(",")
    )
    matrix_values = tuple(
        float(value.strip())
        for value in config.get(section, "accel_matrix").split(",")
    )
    if len(bias) != 3 or any(
        not math.isfinite(value) or abs(value) > 20000.0 for value in bias
    ):
        raise ValueError("invalid accelerometer bias")
    if len(matrix_values) != 9 or any(
        not math.isfinite(value) or abs(value) > 0.01
        for value in matrix_values
    ):
        raise ValueError("invalid accelerometer matrix")
    matrix = tuple(
        tuple(matrix_values[row * 3:(row + 1) * 3]) for row in range(3)
    )
    if max(
        abs(matrix[row][column] - matrix[column][row])
        for row in range(3) for column in range(3)
    ) > 1e-9:
        raise ValueError("accelerometer matrix must be symmetric")
    return bias, matrix


def store_gyro_bias(config, bias, controller_id=None):
    """Store both the device profile and last-used gyro zero bias."""
    values = tuple(float(value) for value in bias)
    if len(values) != 3 or any(
        not math.isfinite(value) or abs(value) > 4096.0
        for value in values
    ):
        raise ValueError("gyro bias must contain three reasonable values")
    sections = ["gyro"]
    device_section = gyro_calibration_section(controller_id)
    if device_section != "gyro":
        sections.append(device_section)
    serialized = ", ".join(f"{value:.3f}" for value in values)
    for section in sections:
        if not config.has_section(section):
            config.add_section(section)
        config.set(section, "bias", serialized)


def store_magnetometer_calibration(
    config, bias, scale=None, controller_id=None, matrix=None, quality=None
):
    """Store hard-iron offsets and full or legacy diagonal correction."""
    bias_values = tuple(float(value) for value in bias)
    scale_values = tuple(float(value) for value in (scale or (1.0, 1.0, 1.0)))
    if len(bias_values) != 3 or any(
        not math.isfinite(value) or not -32768.0 <= value <= 32767.0
        for value in bias_values
    ):
        raise ValueError("mag bias must contain three reasonable values")
    if len(scale_values) != 3 or any(
        not math.isfinite(value) or not 0.25 <= value <= 4.0
        for value in scale_values
    ):
        raise ValueError("mag scale must contain three reasonable values")

    section = gyro_calibration_section(controller_id)
    if not config.has_section(section):
        config.add_section(section)
    config.set(
        section, "mag_bias",
        ", ".join(f"{value:.3f}" for value in bias_values),
    )
    config.set(
        section, "mag_scale",
        ", ".join(f"{value:.6f}" for value in scale_values),
    )
    if matrix is not None:
        matrix_values = tuple(
            float(value) for row in matrix for value in row
        )
        if len(matrix_values) != 9 or any(
            not math.isfinite(value) or abs(value) > 10.0
            for value in matrix_values
        ):
            raise ValueError("mag matrix must contain nine reasonable values")
        config.set(
            section, "mag_matrix",
            ", ".join(f"{value:.9f}" for value in matrix_values),
        )
        config.set(section, "mag_model", "ellipsoid_spd")
    elif config.has_option(section, "mag_matrix"):
        config.remove_option(section, "mag_matrix")
    if quality is not None:
        config.set(
            section,
            "mag_quality",
            json.dumps(quality, ensure_ascii=True, separators=(",", ":")),
        )


def store_accelerometer_calibration(
    config, bias, matrix, controller_id=None, quality=None
):
    """Store a per-controller six-position accelerometer calibration."""
    bias_values = tuple(float(value) for value in bias)
    matrix_values = tuple(float(value) for row in matrix for value in row)
    if len(bias_values) != 3 or any(
        not math.isfinite(value) or abs(value) > 20000.0
        for value in bias_values
    ):
        raise ValueError("invalid accelerometer bias")
    if len(matrix_values) != 9 or any(
        not math.isfinite(value) or abs(value) > 0.01
        for value in matrix_values
    ):
        raise ValueError("invalid accelerometer matrix")
    matrix_rows = tuple(
        tuple(matrix_values[row * 3:(row + 1) * 3]) for row in range(3)
    )
    if max(
        abs(matrix_rows[row][column] - matrix_rows[column][row])
        for row in range(3) for column in range(3)
    ) > 1e-9:
        raise ValueError("accelerometer matrix must be symmetric")
    section = gyro_calibration_section(controller_id)
    if not config.has_section(section):
        config.add_section(section)
    config.set(
        section, "accel_bias",
        ", ".join(f"{value:.6f}" for value in bias_values),
    )
    config.set(
        section, "accel_matrix",
        ", ".join(f"{value:.12f}" for value in matrix_values),
    )
    config.set(section, "accel_model", "ellipsoid")
    if quality is not None:
        config.set(
            section,
            "accel_quality",
            json.dumps(quality, ensure_ascii=True, separators=(",", ":")),
        )


def atomic_write_config(config, path=CONFIG_PATH):
    """Write ConfigParser data in the same directory, then atomically replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            config.write(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def load_config(path=CONFIG_PATH):
    config = configparser.ConfigParser()
    if not config.read(path, encoding="utf-8"):
        raise FileNotFoundError(f"{Path(path).name} not found")
    return config
