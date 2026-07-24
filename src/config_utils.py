import configparser
import contextlib
import ctypes
import json
import math
import os
import re
import tempfile
import threading
from pathlib import Path
from settings_schema import (
    normalize_config_in_place,
    validate_config_sections,
)


CONFIG_PATH = Path(__file__).with_name("config.ini")
PROFILE_DIR = Path(__file__).with_name("profiles")
DEFAULT_CONFIG_PATH = PROFILE_DIR / "System Default.ini"
_CONFIG_THREAD_LOCK = threading.RLock()
_CONFIG_MUTEX_NAME = "Local\\S2P-XInput-Lite-config"

# Game profiles contain settings that vary by game. Mapping layers are kept
# global so the same shortcuts remain available when switching profiles.
PROFILE_SECTIONS = (
    "stick_curve_left",
    "stick_curve_right",
    "buttons",
    "rumble",
    "audio_haptics",
    "gyro_mapping",
    "stick_direction_left",
    "stick_direction_right",
    "mapping_layer_state",
)

PROFILE_MAPPING_SECTIONS = (
    "buttons",
    "stick_direction_left",
    "stick_direction_right",
)

_INVALID_PROFILE_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}

BUNDLED_PROFILE_VERSION = 6
BUNDLED_PROFILE_NAMES = (
    "General",
    "FPS-COMP",
    "FPS-IMM",
    "Action",
    "Racing",
    "Rhythm",
    "Audio",
)

PROTECTED_PROFILE_NAMES = frozenset(("System Default",))
PROFILE_DISPLAY_ORDER = (
    "General",
    "FPS-COMP",
    "FPS-IMM",
    "Action",
    "Racing",
    "Rhythm",
    "Audio",
)

_REMOVED_BUNDLED_PROFILES = (
    "Aim Lab（無震動）",
    "Aim Lab（無震動_啟用陀螺儀）",
)

# Genre presets are ordinary user-managed files.  Renaming an old preset into
# a deleted canonical name would effectively revive it during startup.
_RENAMED_BUNDLED_PROFILES = {}

_LEGACY_PROFILE_NAMES = {
    "Default": "System Default",
    "原始設定": "System Default",
    "原始設定（舊版）": "System Default",
    "系統預設": "System Default",
    "通用": "General",
    "賽車": "Racing",
    "動作遊戲": "Action",
    "音樂遊戲": "Rhythm",
}


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


def select_standalone_calibration_id(config, controller_status=None):
    """Select the connected controller profile or reject an ambiguous export."""
    status = controller_status if isinstance(controller_status, dict) else {}
    connected_id = normalize_controller_id(status.get("controller_id"))
    if connected_id:
        return connected_id

    candidates = set()
    for section in config.sections():
        prefix, separator, suffix = section.partition(".")
        candidate = normalize_controller_id(suffix) if separator else None
        if prefix in ("sticks", "gyro") and candidate:
            candidates.add(candidate)
    if len(candidates) == 1:
        return next(iter(candidates))
    if len(candidates) > 1:
        raise ValueError(
            "Multiple controller calibrations exist. Connect the controller "
            "whose calibration should be embedded before writing to ESP32."
        )
    return None


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


def atomic_write_bytes(data, path):
    """Atomically replace one file with already serialized bytes."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=path.name + ".", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


@contextlib.contextmanager
def config_file_lock(timeout=10.0):
    """Serialize config/profile bundle writes across GUI and connector."""
    with _CONFIG_THREAD_LOCK:
        if os.name != "nt":
            yield
            return

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = (
            ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p
        )
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.WaitForSingleObject.argtypes = (ctypes.c_void_p, ctypes.c_uint)
        kernel32.WaitForSingleObject.restype = ctypes.c_uint
        kernel32.ReleaseMutex.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)

        handle = kernel32.CreateMutexW(None, False, _CONFIG_MUTEX_NAME)
        if not handle:
            raise OSError("Could not create the configuration mutex")
        acquired = False
        try:
            result = kernel32.WaitForSingleObject(
                handle, max(0, int(float(timeout) * 1000.0))
            )
            if result not in (0x00000000, 0x00000080):
                raise TimeoutError("Timed out waiting for the configuration lock")
            acquired = True
            yield
        finally:
            if acquired:
                kernel32.ReleaseMutex(handle)
            kernel32.CloseHandle(handle)


def validate_profile_name(name, allow_legacy=False):
    """Return a safe display/file name or raise a user-facing ValueError."""
    normalized = str(name or "").strip()
    if not normalized:
        raise ValueError("方案名稱不可空白。")
    if len(normalized) > 64:
        raise ValueError("方案名稱不可超過 64 個字元。")
    if normalized in (".", "..") or normalized.endswith((".", " ")):
        raise ValueError("方案名稱不可使用句點結尾。")
    if _INVALID_PROFILE_NAME.search(normalized):
        raise ValueError('方案名稱不可包含 < > : " / \\ | ? *。')
    stem = normalized.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        raise ValueError("這個名稱是 Windows 保留名稱，請改用其他名稱。")
    if not allow_legacy and any(
        normalized.casefold() == legacy.casefold()
        for legacy in _LEGACY_PROFILE_NAMES
    ):
        raise ValueError("這個名稱是舊版遷移保留名稱，請改用其他名稱。")
    return normalized


def is_protected_profile(name):
    """Return whether a profile is an immutable built-in baseline."""
    normalized = str(name or "").strip().casefold()
    return any(
        normalized == protected.casefold()
        for protected in PROTECTED_PROFILE_NAMES
    )


def profile_path(name, profile_dir=PROFILE_DIR, allow_legacy=False):
    """Resolve one validated profile name inside the configured directory."""
    safe_name = validate_profile_name(name, allow_legacy=allow_legacy)
    return Path(profile_dir) / f"{safe_name}.ini"


def list_profiles(profile_dir=PROFILE_DIR):
    """List bundled profiles in UI order, followed by user profiles."""
    directory = Path(profile_dir)
    if not directory.is_dir():
        return []
    names = []
    for path in directory.glob("*.ini"):
        try:
            names.append(validate_profile_name(path.stem))
        except ValueError:
            continue
    unique_names = {name.casefold(): name for name in names}
    bundled_order = {
        name.casefold(): index
        for index, name in enumerate(PROFILE_DISPLAY_ORDER)
    }
    return sorted(
        unique_names.values(),
        key=lambda name: (
            2 if is_protected_profile(name) else (
                0 if name.casefold() in bundled_order else 1
            ),
            bundled_order.get(name.casefold(), len(bundled_order)),
            name.casefold(),
        ),
    )


def build_profile(config):
    """Copy all gameplay-affecting sections into a standalone profile."""
    profile = configparser.ConfigParser()
    missing = [
        section for section in PROFILE_SECTIONS
        if not config.has_section(section)
    ]
    if missing:
        raise ValueError("設定檔缺少方案區段：" + ", ".join(missing))
    for section in PROFILE_SECTIONS:
        profile.add_section(section)
        for option, value in config.items(section):
            profile.set(section, option, value)
    return profile


def replace_profile_sections(target, source, sections):
    """Replace complete profile sections from a canonical source config."""
    for section in sections:
        if not source.has_section(section):
            raise ValueError(f"預設設定缺少方案區段：{section}")
        if target.has_section(section):
            target.remove_section(section)
        target.add_section(section)
        for option, value in source.items(section):
            target.set(section, option, value)
    return target


def save_profile(config, name, profile_dir=PROFILE_DIR):
    """Atomically save the gameplay subset of config as a named profile."""
    if is_protected_profile(name):
        raise PermissionError("System Default 是唯讀方案，不能覆寫。")
    path = profile_path(name, profile_dir)
    atomic_write_config(build_profile(config), path)
    return path


def rename_profile(old_name, new_name, profile_dir=PROFILE_DIR):
    """Rename one profile without overwriting another profile."""
    if is_protected_profile(old_name):
        raise PermissionError("System Default 是唯讀方案，不能重新命名。")
    if is_protected_profile(new_name):
        raise PermissionError("System Default 是保留名稱。")
    old_path = profile_path(old_name, profile_dir)
    new_path = profile_path(new_name, profile_dir)
    if not old_path.is_file():
        raise FileNotFoundError(old_path)
    if old_path == new_path:
        return new_path
    if new_path.exists() and old_path.resolve() != new_path.resolve():
        raise FileExistsError(new_path)

    # A temporary hop also makes case-only renames reliable on Windows.
    temp_path = old_path.with_name(
        f".{old_path.stem}.{os.getpid()}.rename.tmp"
    )
    old_path.rename(temp_path)
    try:
        temp_path.rename(new_path)
    except Exception:
        if temp_path.exists() and not old_path.exists():
            temp_path.rename(old_path)
        raise
    return new_path


def create_bundled_profiles(config, profile_dir=PROFILE_DIR):
    """Copy missing release presets from their packaged INI source files.

    This helper is used by tests and explicit setup tools only. Application
    startup deliberately never recreates a user-deleted optional preset.
    ``config`` is retained in the signature for compatibility with older tools.
    """
    del config
    existing = {name.casefold() for name in list_profiles(profile_dir)}
    created = []
    for name in BUNDLED_PROFILE_NAMES:
        if name.casefold() in existing:
            continue
        profile = read_profile(name, PROFILE_DIR)
        atomic_write_config(profile, profile_path(name, profile_dir))
        created.append(name)
    return created


def reset_bundled_profile_mappings(profile_dir=PROFILE_DIR):
    """Migrate built-in presets to canonical default mappings once."""
    defaults = load_config(DEFAULT_CONFIG_PATH)
    for name in BUNDLED_PROFILE_NAMES:
        path = profile_path(name, profile_dir)
        if not path.is_file():
            continue
        profile = read_profile(name, profile_dir)
        replace_profile_sections(
            profile,
            defaults,
            PROFILE_MAPPING_SECTIONS,
        )
        atomic_write_config(profile, path)


def migrate_profile_sections(profile_dir=PROFILE_DIR):
    """Fill missing gameplay sections and options from System Default."""
    defaults = load_config(DEFAULT_CONFIG_PATH)
    directory = Path(profile_dir)
    if not directory.is_dir():
        return
    for path in directory.glob("*.ini"):
        profile = configparser.ConfigParser()
        if not profile.read(path, encoding="utf-8"):
            continue
        changed = False
        for section in PROFILE_SECTIONS:
            if not defaults.has_section(section):
                continue
            if not profile.has_section(section):
                profile.add_section(section)
                changed = True
            for option, value in defaults.items(section):
                if not profile.has_option(section, option):
                    profile.set(section, option, value)
                    changed = True
        if changed:
            atomic_write_config(profile, path)


def remove_global_sections_from_profiles(profile_dir=PROFILE_DIR):
    """Remove obsolete per-profile copies of settings that are now global."""
    directory = Path(profile_dir)
    if not directory.is_dir():
        return
    for path in directory.glob("*.ini"):
        profile = configparser.ConfigParser()
        if not profile.read(path, encoding="utf-8"):
            continue
        if profile.remove_section("mapping_layers"):
            atomic_write_config(profile, path)


def migrate_bundled_profiles(config, profile_dir=PROFILE_DIR):
    """Remove retired presets and shorten legacy preset names in place."""
    directory = Path(profile_dir)
    directory.mkdir(parents=True, exist_ok=True)

    for name in _REMOVED_BUNDLED_PROFILES:
        path = profile_path(name, directory)
        if path.exists():
            path.unlink()

    for old_name, new_name in _RENAMED_BUNDLED_PROFILES.items():
        old_path = profile_path(old_name, directory)
        new_path = profile_path(new_name, directory)
        if not old_path.exists():
            continue
        if new_path.exists():
            old_path.unlink()
        else:
            old_path.rename(new_path)

    if not config.has_section("gui"):
        config.add_section("gui")
    active = config.get("gui", "active_profile", fallback="").strip()
    if active in _RENAMED_BUNDLED_PROFILES:
        config.set("gui", "active_profile", _RENAMED_BUNDLED_PROFILES[active])
    elif active in _REMOVED_BUNDLED_PROFILES:
        config.set("gui", "active_profile", "General")


def remove_duplicate_new_default_profile(profile_dir=PROFILE_DIR):
    """Remove the accidental New Default copy when it matches System Default."""
    directory = Path(profile_dir)
    new_default = directory / "New Default.ini"
    system_default = directory / "System Default.ini"
    if not new_default.is_file() or not system_default.is_file():
        return False
    try:
        if new_default.read_bytes() != system_default.read_bytes():
            return False
        new_default.unlink()
    except OSError:
        return False
    return True


def migrate_legacy_profile_names(config, profile_dir=PROFILE_DIR):
    """Give retained legacy profiles names that explain their purpose."""
    directory = Path(profile_dir)
    directory.mkdir(parents=True, exist_ok=True)
    for old_name, new_name in _LEGACY_PROFILE_NAMES.items():
        old_path = profile_path(old_name, directory, allow_legacy=True)
        new_path = profile_path(new_name, directory)
        if not old_path.exists():
            continue
        migrated_name = new_name
        if new_path.exists():
            if old_path.read_bytes() == new_path.read_bytes():
                old_path.unlink()
            else:
                suffix = 1
                while True:
                    label = (
                        f"{old_name} (Legacy)" if suffix == 1
                        else f"{old_name} (Legacy {suffix})"
                    )
                    legacy_path = profile_path(label, directory)
                    if not legacy_path.exists():
                        old_path.rename(legacy_path)
                        migrated_name = label
                        break
                    suffix += 1
        else:
            old_path.rename(new_path)

        if config.has_section("gui"):
            active = config.get("gui", "active_profile", fallback="").strip()
            if active.casefold() == old_name.casefold():
                config.set("gui", "active_profile", migrated_name)


def ensure_bundled_profiles(config, profile_dir=PROFILE_DIR):
    """Maintain existing profiles without creating or reviving presets."""
    if not config.has_section("gui"):
        config.add_section("gui")
    migrate_legacy_profile_names(config, profile_dir)
    remove_duplicate_new_default_profile(profile_dir)
    # Keep every existing profile structurally current even when its bundled
    # preset version is newer than this build's migration marker.
    migrate_profile_sections(profile_dir)
    # An older GUI process may still save a stale per-profile copy after the
    # one-time migration, so make this inexpensive cleanup idempotent.
    remove_global_sections_from_profiles(profile_dir)
    # Optional presets are release samples, not required application state.
    # Never use a bundled version marker to rename, reset, or recreate one.
    return []


def read_profile(name, profile_dir=PROFILE_DIR):
    """Read a profile and fill absent gameplay keys from System Default."""
    path = profile_path(name, profile_dir)
    profile = configparser.ConfigParser()
    if not profile.read(path, encoding="utf-8"):
        raise FileNotFoundError(path)
    defaults = configparser.ConfigParser()
    if not defaults.read(DEFAULT_CONFIG_PATH, encoding="utf-8"):
        raise FileNotFoundError(DEFAULT_CONFIG_PATH)
    for section in PROFILE_SECTIONS:
        if not defaults.has_section(section):
            raise ValueError(f"System Default.ini 缺少方案區段：{section}")
        if not profile.has_section(section):
            profile.add_section(section)
        for option, value in defaults.items(section):
            if not profile.has_option(section, option):
                profile.set(section, option, value)
    validate_config_sections(
        profile,
        tuple(section for section in PROFILE_SECTIONS if section in (
            "stick_curve_left", "stick_curve_right", "rumble",
            "audio_haptics", "gyro_mapping",
            "stick_direction_left", "stick_direction_right",
        )),
        strict=True,
    )
    return profile


def apply_profile(config, profile):
    """Replace gameplay sections while preserving global/calibration data."""
    missing = [
        section for section in PROFILE_SECTIONS
        if not profile.has_section(section)
    ]
    if missing:
        raise ValueError("方案檔缺少區段：" + ", ".join(missing))
    for section in PROFILE_SECTIONS:
        config.remove_section(section)
        config.add_section(section)
        for option, value in profile.items(section):
            config.set(section, option, value)
    return config


def load_config(path=CONFIG_PATH):
    """Load configuration, creating it and filling new keys from the template."""
    path = Path(path)
    defaults = configparser.ConfigParser()
    if not defaults.read(DEFAULT_CONFIG_PATH, encoding="utf-8"):
        raise FileNotFoundError(f"{DEFAULT_CONFIG_PATH.name} not found")
    for section in PROFILE_SECTIONS:
        if not defaults.has_section(section):
            raise ValueError(f"System Default.ini 缺少方案區段：{section}")

    config = configparser.ConfigParser()
    existed = bool(config.read(path, encoding="utf-8"))
    changed = not existed
    for section in defaults.sections():
        if not config.has_section(section):
            config.add_section(section)
            changed = True
        for key, value in defaults.items(section):
            if not config.has_option(section, key):
                config.set(section, key, value)
                changed = True
    # Repair known malformed or legacy values through the same schema used by
    # the GUI and runtime. Calibration and unknown extension keys are untouched.
    changed = normalize_config_in_place(config) or changed
    if changed:
        atomic_write_config(config, path)
    return config
