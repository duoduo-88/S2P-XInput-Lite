"""Centralized parsing, validation and formatting for user settings.

System Default.ini remains the single source of default values.  This module
only describes types, legal ranges and cross-field invariants so the GUI and
runtime cannot silently drift apart.
"""
from __future__ import annotations

import configparser
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping


DEFAULT_CONFIG_PATH = Path(__file__).with_name("profiles") / "System Default.ini"


class SettingValidationError(ValueError):
    """Structured validation error shared by the GUI and runtime tests."""

    def __init__(
        self,
        section: str,
        key: str,
        value: Any,
        reason_zh: str,
        reason_en: str | None = None,
    ):
        self.section = section
        self.key = key
        self.value = value
        self.reason_zh = reason_zh
        self.reason_en = reason_en or reason_zh
        super().__init__(reason_zh)

    def localized(self, language: str = "zh") -> str:
        return self.reason_en if str(language).lower().startswith("en") else self.reason_zh


@dataclass(frozen=True)
class SettingSpec:
    kind: str
    label_zh: str
    label_en: str
    minimum: float | int | None = None
    maximum: float | int | None = None
    choices: tuple[str, ...] = ()
    aliases: tuple[tuple[str, str], ...] = ()
    decimals: int | None = None
    formatter: Callable[[Any], str] | None = None


def _float(label_zh, label_en, minimum, maximum, decimals=None):
    return SettingSpec("float", label_zh, label_en, minimum, maximum, decimals=decimals)


def _int(label_zh, label_en, minimum, maximum):
    return SettingSpec("int", label_zh, label_en, minimum, maximum)


def _bool(label_zh, label_en):
    return SettingSpec("bool", label_zh, label_en)


def _enum(label_zh, label_en, *choices, aliases=()):
    return SettingSpec(
        "enum", label_zh, label_en, choices=tuple(choices), aliases=tuple(aliases)
    )


def _shape_formatter(value):
    return str(int(value))


def _output_shape_spec():
    return SettingSpec(
        "output_shape", "輸出形狀", "output shape", 0, 10,
        formatter=_shape_formatter,
    )


def _stick_curve_schema():
    schema = {
        "smoothing": _float("搖桿防抖", "stick smoothing", 0.0, 3.0, 1),
        "deadzone": _float("中心死區", "center deadzone", 0.0, 0.99, 2),
        "outer_deadzone": _float("外圍死區", "outer deadzone", 0.0, 0.99, 2),
        "deadzone_compress": _bool("中心死區壓縮", "center deadzone compression"),
        "outer_deadzone_compress": _bool("外圍死區壓縮", "outer deadzone compression"),
        "output_shape": _output_shape_spec(),
        "interpolation": _enum("曲線插值", "curve interpolation", "LINEAR", "SMOOTH"),
    }
    for index in range(5):
        schema[f"point_{index}_x"] = _float(
            f"曲線點 {index} X", f"curve point {index} X", 0.0, 1.0, 3
        )
        schema[f"point_{index}_y"] = _float(
            f"曲線點 {index} Y", f"curve point {index} Y", 0.0, 1.0, 3
        )
    return schema


STICK_CURVE_SCHEMA = _stick_curve_schema()

RUMBLE_SCHEMA = {
    "lf_strength": _float("LF 強度", "LF strength", 0.0, 1.0, 2),
    "hf_strength": _float("HF 強度", "HF strength", 0.0, 1.0, 2),
    "lf_curve": _float("LF 曲線", "LF curve", 0.1, 5.0, 2),
    "hf_curve": _float("HF 曲線", "HF curve", 0.1, 5.0, 2),
    "lf_to_hf_compensation": _float("LF → HF 補償", "LF to HF compensation", 0.0, 1.0, 2),
    "hf_to_lf_compensation": _float("HF → LF 補償", "HF to LF compensation", 0.0, 1.0, 2),
    "lf_frequency": _int("LF 頻率", "LF frequency", 0, 511),
    "hf_frequency": _int("HF 頻率", "HF frequency", 0, 511),
    "max_amplitude": _int("最大振幅", "maximum amplitude", 0, 1023),
}

AUDIO_HAPTICS_SCHEMA = {
    "mode": _enum("音訊震動模式", "audio haptics mode", "GAME", "AUDIO", "MIX"),
    "mix_ratio": _float("混合比例", "mix ratio", 0.0, 1.0, 2),
    "strength": _float("音訊強度", "audio strength", 0.0, 1.0, 2),
    "lf_hf_balance": _float(
        "LF／HF 分配重心", "LF/HF routing balance", -1.0, 1.0, 2
    ),
    "noise_gate": _float("噪音閘", "noise gate", 0.0, 0.25, 3),
    "attack_ms": _float("啟動 ms", "attack ms", 1.0, 500.0),
    "release_ms": _float("音訊釋放 ms", "release ms", 5.0, 2000.0),
    "final_tail_strength": _float("餘震強度", "tail strength", 0.0, 1.0, 2),
    "final_tail_decay_ms": _float("餘震衰減 ms", "tail decay ms", 50.0, 2000.0),
    **{
        f"band_{index}_gain": _float(
            f"頻段 {index} 增益", f"band {index} gain", 0.0, 2.0, 2
        )
        for index in range(1, 7)
    },
}

STICK_DIRECTION_SCHEMA = {
    "mode": _enum(
        "方向模式", "direction mode",
        "4WAY", "8WAY", "映射為滑鼠", "MOUSE_WHEEL_LINEAR",
        "XINPUT_LT_LINEAR", "XINPUT_RT_LINEAR",
        "映射為右搖桿", "映射為左搖桿",
    ),
    "trigger_threshold": _float("觸發門檻", "trigger threshold", 0.10, 1.0, 2),
    "release_threshold": _float("放開門檻", "release threshold", 0.0, 0.97, 2),
    "direction_deadzone": _float("方向死區", "direction deadzone", 0.0, 20.0),
    "mouse_speed": _float("游標速度", "pointer speed", 100.0, 3000.0),
    "mouse_deadzone": _float("滑鼠中心死區", "pointer center deadzone", 0.0, 0.30, 2),
    "analog_direction": _enum("類比輸入方向", "analog input direction", "UP", "DOWN", "LEFT", "RIGHT"),
}

GYRO_SCHEMA = {
    "activation_mode": _enum("陀螺儀啟動方式", "gyro activation mode", "OFF", "HOLD", "TOGGLE"),
    "activation_match": _enum("陀螺儀啟動條件", "gyro activation match", "ANY", "ALL"),
    "target": _enum("陀螺儀輸出目標", "gyro output target", "LEFT_STICK", "RIGHT_STICK", "MOUSE"),
    "motion_mode": _enum("陀螺儀控制模式", "gyro motion mode", "CENTER", "TILT"),
    "tilt_axis": _enum("傾斜軸向", "tilt axis", "HORIZONTAL", "DUAL"),
    "tilt_max_angle": _float("最大傾斜角", "maximum tilt angle", 10.0, 60.0),
    "tilt_deadzone": _float("傾斜死區", "tilt deadzone", 0.0, 5.0, 2),
    "tilt_smoothing_ms": _float("傾斜平滑 ms", "tilt smoothing ms", 0.0, 150.0),
    "stick_sensitivity": _float("陀螺儀搖桿感度", "gyro stick sensitivity", 0.1, 10.0, 2),
    "mouse_sensitivity": _float("陀螺儀滑鼠感度", "gyro mouse sensitivity", 0.5, 30.0, 2),
    "deadzone": _float("陀螺儀死區", "gyro deadzone", 0.0, 5.0, 2),
    "stick_anti_deadzone": _float("陀螺儀反死區", "gyro stick anti-deadzone", 0.0, 30.0),
    "response_curve": _enum(
        "陀螺儀感度曲線", "gyro response curve", "LINEAR", "LATE", "EARLY",
        aliases=(("DYNAMIC", "LATE"),),
    ),
    "curve_strength": _float("陀螺儀曲線強度", "gyro curve strength", 0.0, 10.0),
    "smoothing_ms": _float("陀螺儀平滑 ms", "gyro smoothing ms", 0.0, 100.0),
    "invert_x": _bool("陀螺儀反轉 X", "invert gyro X"),
    "invert_y": _bool("陀螺儀反轉 Y", "invert gyro Y"),
    "x_ratio": _float("陀螺儀 X 比例", "gyro X ratio", 0.5, 2.0, 2),
    "y_ratio": _float("陀螺儀 Y 比例", "gyro Y ratio", 0.5, 2.0, 2),
    "accel_suppression": _float("加速度抑制", "acceleration suppression", 0.0, 100.0),
    "adaptive_deadzone": _float("自適應死區", "adaptive deadzone", 0.0, 100.0),
    "button_freeze_ms": _float("按鍵防晃 ms", "button freeze ms", 0.0, 120.0),
    "player_space": _bool("玩家空間", "player space"),
}

SECTION_SCHEMAS = {
    "stick_curve_left": STICK_CURVE_SCHEMA,
    "stick_curve_right": STICK_CURVE_SCHEMA,
    "rumble": RUMBLE_SCHEMA,
    "audio_haptics": AUDIO_HAPTICS_SCHEMA,
    "stick_direction_left": STICK_DIRECTION_SCHEMA,
    "stick_direction_right": STICK_DIRECTION_SCHEMA,
    "gyro_mapping": GYRO_SCHEMA,
}


@lru_cache(maxsize=4)
def _default_config(path_text: str) -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    path = Path(path_text)
    if not config.read(path, encoding="utf-8"):
        raise FileNotFoundError(path)
    return config


def default_section_values(section: str, path: Path = DEFAULT_CONFIG_PATH) -> dict[str, str]:
    config = _default_config(str(Path(path).resolve()))
    if not config.has_section(section):
        raise ValueError(f"System Default.ini 缺少設定區段：{section}")
    return dict(config.items(section))


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("1", "yes", "true", "on"):
        return True
    if text in ("0", "no", "false", "off"):
        return False
    raise ValueError("invalid boolean")


def _parse_output_shape(value: Any) -> int:
    text = str(value).strip().upper()
    if text == "CIRCLE":
        return 0
    if text == "SQUARE":
        return 10
    number = float(text)
    if not math.isfinite(number):
        raise ValueError("non-finite output shape")
    return int(round(number))


def _parse_one(spec: SettingSpec, value: Any) -> Any:
    if spec.kind == "float":
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError("non-finite number")
        return parsed
    if spec.kind == "int":
        if isinstance(value, str) and any(char in value.strip().lower() for char in (".", "e")):
            number = float(value)
            if not number.is_integer():
                raise ValueError("not an integer")
            return int(number)
        return int(value)
    if spec.kind == "bool":
        return _parse_bool(value)
    if spec.kind == "enum":
        parsed = str(value).strip().upper()
        parsed = dict(spec.aliases).get(parsed, parsed)
        return parsed
    if spec.kind == "output_shape":
        return _parse_output_shape(value)
    return str(value)


def _range_error(section: str, key: str, value: Any, spec: SettingSpec):
    minimum = spec.minimum
    maximum = spec.maximum
    return SettingValidationError(
        section,
        key,
        value,
        f"{spec.label_zh}必須介於 {minimum:g} ～ {maximum:g}。",
        f"{spec.label_en.capitalize()} must be between {minimum:g} and {maximum:g}.",
    )


def _choice_error(section: str, key: str, value: Any, spec: SettingSpec):
    return SettingValidationError(
        section,
        key,
        value,
        f"{spec.label_zh}無效。",
        f"Invalid {spec.label_en}.",
    )


def normalize_section_values(
    section: str,
    values: Mapping[str, Any],
    *,
    strict: bool,
    defaults: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return typed values using one shared schema.

    strict=True is used by the GUI and rejects invalid user input.
    strict=False is used by the runtime and safely clamps manually edited INI
    values. Invalid syntax falls back to System Default.ini.
    """
    if section not in SECTION_SCHEMAS:
        raise KeyError(section)
    specs = SECTION_SCHEMAS[section]
    if defaults is None:
        defaults = default_section_values(section)
    result: dict[str, Any] = {}
    for key, spec in specs.items():
        raw = values.get(key, defaults.get(key))
        try:
            parsed = _parse_one(spec, raw)
        except (TypeError, ValueError, OverflowError):
            if strict:
                raise SettingValidationError(
                    section,
                    key,
                    raw,
                    f"{spec.label_zh}的數值格式不正確。",
                    f"Invalid value format for {spec.label_en}.",
                ) from None
            parsed = _parse_one(spec, defaults[key])

        if spec.choices and parsed not in spec.choices:
            if strict:
                raise _choice_error(section, key, raw, spec)
            parsed = _parse_one(spec, defaults[key])

        if spec.minimum is not None and parsed < spec.minimum:
            if strict:
                raise _range_error(section, key, raw, spec)
            parsed = spec.minimum
        if spec.maximum is not None and parsed > spec.maximum:
            if strict:
                raise _range_error(section, key, raw, spec)
            parsed = spec.maximum
        result[key] = parsed

    _validate_cross_fields(section, result, strict=strict)
    return result


def normalize_stick_deadzone_pair(deadzone: Any, outer_deadzone: Any) -> tuple[float, float]:
    """Clamp a deadzone pair while preserving at least 1% active travel."""
    center = max(0.0, min(0.99, float(deadzone)))
    outer = max(0.0, min(0.99 - center, float(outer_deadzone)))
    return center, outer


def _validate_cross_fields(section: str, values: dict[str, Any], *, strict: bool):
    if section.startswith("stick_curve_"):
        deadzone = float(values["deadzone"])
        outer = float(values["outer_deadzone"])
        if deadzone + outer >= 1.0:
            if strict:
                side_zh = "左搖桿" if section.endswith("left") else "右搖桿"
                side_en = "Left stick" if section.endswith("left") else "Right stick"
                raise SettingValidationError(
                    section,
                    "outer_deadzone",
                    outer,
                    f"{side_zh}中心死區與外圍死區總和必須小於 1.00",
                    f"{side_en} center and outer deadzones must total less than 1.00.",
                )
            values["deadzone"], values["outer_deadzone"] = (
                normalize_stick_deadzone_pair(deadzone, outer)
            )

        previous_x = -1.0
        for index in range(5):
            x_value = float(values[f"point_{index}_x"])
            if x_value + 1e-9 < previous_x:
                if strict:
                    raise SettingValidationError(
                        section,
                        f"point_{index}_x",
                        x_value,
                        "搖桿曲線的 X 控制點必須由小到大排列。",
                        "Stick curve X control points must be ordered from low to high.",
                    )
                x_value = previous_x
                values[f"point_{index}_x"] = x_value
            previous_x = x_value

    elif section.startswith("stick_direction_"):
        side_invalid_mode = (
            (section.endswith("left") and values["mode"] == "映射為左搖桿")
            or (section.endswith("right") and values["mode"] == "映射為右搖桿")
        )
        if side_invalid_mode:
            if strict:
                raise SettingValidationError(
                    section,
                    "mode",
                    values["mode"],
                    "搖桿模式方向接反。",
                    "Stick remap mode points to the wrong side.",
                )
            values["mode"] = "4WAY"
        trigger = float(values["trigger_threshold"])
        release = float(values["release_threshold"])
        maximum_release = max(0.0, trigger - 0.03)
        if release > maximum_release + 1e-9:
            if strict:
                raise SettingValidationError(
                    section,
                    "release_threshold",
                    release,
                    "放開門檻必須至少比觸發門檻低 3%。",
                    "Release threshold must be at least 3% below the trigger threshold.",
                )
            values["release_threshold"] = maximum_release

    elif section == "gyro_mapping":
        if values["target"] == "MOUSE" and values["motion_mode"] == "TILT":
            if strict:
                raise SettingValidationError(
                    section,
                    "motion_mode",
                    values["motion_mode"],
                    "傾斜模式僅支援左右搖桿輸出。",
                    "Tilt mode only supports left or right stick output.",
                )
            values["motion_mode"] = "CENTER"
        maximum_deadzone = min(5.0, float(values["tilt_max_angle"]) - 1.0)
        if values["tilt_deadzone"] >= values["tilt_max_angle"]:
            if strict:
                raise SettingValidationError(
                    section,
                    "tilt_deadzone",
                    values["tilt_deadzone"],
                    "傾斜死區必須小於最大傾斜角。",
                    "Tilt deadzone must be smaller than the maximum tilt angle.",
                )
            values["tilt_deadzone"] = max(0.0, maximum_deadzone)


def read_section_settings(
    config: configparser.ConfigParser,
    section: str,
    *,
    strict: bool = False,
) -> dict[str, Any]:
    raw = dict(config.items(section)) if config.has_section(section) else {}
    return normalize_section_values(section, raw, strict=strict)


def format_setting_value(section: str, key: str, value: Any) -> str:
    spec = SECTION_SCHEMAS[section][key]
    if spec.formatter is not None:
        return spec.formatter(value)
    if spec.kind == "bool":
        return "true" if bool(value) else "false"
    if spec.kind == "enum":
        return str(value).strip().upper()
    if spec.kind == "int":
        return str(int(value))
    if spec.kind == "float":
        if spec.decimals is None:
            return f"{float(value):g}"
        return f"{float(value):.{spec.decimals}f}"
    return str(value)


def write_section_settings(
    config: configparser.ConfigParser,
    section: str,
    values: Mapping[str, Any],
) -> None:
    if not config.has_section(section):
        config.add_section(section)
    for key in SECTION_SCHEMAS[section]:
        if key in values:
            config.set(section, key, format_setting_value(section, key, values[key]))


def validate_config_sections(
    config: configparser.ConfigParser,
    sections: tuple[str, ...] | None = None,
    *,
    strict: bool = True,
) -> dict[str, dict[str, Any]]:
    selected = sections or tuple(SECTION_SCHEMAS)
    return {
        section: read_section_settings(config, section, strict=strict)
        for section in selected
    }


def normalize_config_in_place(
    config: configparser.ConfigParser,
    sections: tuple[str, ...] | None = None,
) -> bool:
    """Normalize known settings in an existing ConfigParser.

    Unknown sections/options are preserved.  Returns True when at least one
    canonical value changed, allowing callers to decide whether to persist it.
    """
    changed = False
    selected = sections or tuple(SECTION_SCHEMAS)
    for section in selected:
        values = read_section_settings(config, section, strict=False)
        if not config.has_section(section):
            config.add_section(section)
            changed = True
        for key, value in values.items():
            formatted = format_setting_value(section, key, value)
            if config.get(section, key, fallback=None) != formatted:
                config.set(section, key, formatted)
                changed = True
    return changed
