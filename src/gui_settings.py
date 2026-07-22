"""Shared GUI settings collection, comparison and config writing.

This module deliberately has no Tk imports.  It reads variable-like objects
through ``.get()`` so the same logic can be exercised with lightweight fakes in
unit tests.  System Default.ini remains the only default-value source through
``settings_schema``.
"""
from __future__ import annotations

import configparser
from typing import Any, Mapping

from settings_schema import (
    SECTION_SCHEMAS,
    format_setting_value,
    normalize_section_values,
    read_section_settings,
    write_section_settings,
)


def _value(variable: Any) -> Any:
    return variable.get() if hasattr(variable, "get") else variable


def _button_list(raw_value: Any, valid_buttons, fallback=()) -> list[str]:
    valid = set(valid_buttons)
    result: list[str] = []
    for item in str(raw_value or "").split(","):
        name = item.strip().upper()
        if name and name != "NONE" and name in valid and name not in result:
            result.append(name)
    if result:
        return result
    return [name for name in fallback if name in valid]


def _stick_curve_values(gui, side: str) -> dict[str, Any]:
    prefix = side.lower()
    curve_vars = getattr(gui, f"{prefix}_curve_vars")
    return {
        "deadzone": _value(getattr(gui, f"{prefix}_deadzone_var")),
        "outer_deadzone": _value(
            getattr(gui, f"{prefix}_outer_deadzone_var")
        ),
        "deadzone_compress": _value(
            getattr(gui, f"{prefix}_deadzone_compress_var")
        ),
        "outer_deadzone_compress": _value(
            getattr(gui, f"{prefix}_outer_deadzone_compress_var")
        ),
        "output_shape": _value(
            getattr(gui, f"{prefix}_output_shape_var")
        ),
        "interpolation": _value(
            getattr(gui, f"{prefix}_interpolation_var")
        ),
        "smoothing": _value(
            getattr(gui, f"{prefix}_stick_smoothing_var")
        ),
        **{
            f"point_{index}_{axis}": _value(curve_vars[index][axis])
            for index in range(5)
            for axis in ("x", "y")
        },
    }


def _rumble_values(gui) -> dict[str, Any]:
    return {
        "lf_strength": _value(gui.lf_strength_var),
        "hf_strength": _value(gui.hf_strength_var),
        "lf_curve": _value(gui.lf_curve_var),
        "hf_curve": _value(gui.hf_curve_var),
        "lf_to_hf_compensation": _value(gui.lf_to_hf_compensation_var),
        "hf_to_lf_compensation": _value(gui.hf_to_lf_compensation_var),
        "lf_frequency": _value(gui.lf_frequency_var),
        "hf_frequency": _value(gui.hf_frequency_var),
        "max_amplitude": _value(gui.max_amplitude_var),
    }


def _audio_values(gui) -> dict[str, Any]:
    return {
        "mode": _value(gui.audio_haptics_mode_var),
        "mix_ratio": _value(gui.audio_haptics_mix_ratio_var),
        "strength": _value(gui.audio_haptics_strength_var),
        "lf_hf_balance": _value(gui.audio_haptics_lf_hf_balance_var),
        "noise_gate": _value(gui.audio_haptics_noise_gate_var),
        "attack_ms": _value(gui.audio_haptics_attack_var),
        "release_ms": _value(gui.audio_haptics_release_var),
        "final_tail_strength": _value(
            gui.audio_haptics_final_tail_strength_var
        ),
        "final_tail_decay_ms": _value(
            gui.audio_haptics_final_tail_decay_var
        ),
        **{
            f"band_{index}_gain": _value(variable)
            for index, variable in enumerate(
                gui.audio_haptics_band_gain_vars, start=1
            )
        },
    }


def _stick_direction_values(gui, side: str) -> dict[str, Any]:
    return {
        "mode": _value(gui.stick_direction_mode_vars[side]),
        "trigger_threshold": _value(gui.stick_direction_trigger_vars[side]),
        "release_threshold": _value(gui.stick_direction_release_vars[side]),
        "direction_deadzone": _value(gui.stick_direction_deadzone_vars[side]),
        "mouse_speed": _value(gui.stick_mouse_speed_vars[side]),
        "mouse_deadzone": _value(gui.stick_mouse_deadzone_vars[side]),
        "analog_direction": _value(gui.stick_analog_direction_vars[side]),
    }


def _gyro_values(gui) -> dict[str, Any]:
    return {
        "activation_mode": _value(gui.gyro_activation_mode_var),
        "activation_match": _value(gui.gyro_activation_match_var),
        "target": _value(gui.gyro_target_var),
        "motion_mode": _value(gui.gyro_motion_mode_var),
        "tilt_axis": _value(gui.gyro_tilt_axis_var),
        "tilt_max_angle": _value(gui.gyro_tilt_max_angle_var),
        "tilt_deadzone": _value(gui.gyro_tilt_deadzone_var),
        "tilt_smoothing_ms": _value(gui.gyro_tilt_smoothing_var),
        "stick_sensitivity": _value(gui.gyro_stick_sensitivity_var),
        "mouse_sensitivity": _value(gui.gyro_mouse_sensitivity_var),
        "deadzone": _value(gui.gyro_deadzone_var),
        "stick_anti_deadzone": _value(gui.gyro_stick_anti_deadzone_var),
        "response_curve": _value(gui.gyro_response_curve_var),
        "curve_strength": _value(gui.gyro_curve_strength_var),
        "smoothing_ms": _value(gui.gyro_smoothing_var),
        "invert_x": _value(gui.gyro_invert_x_var),
        "invert_y": _value(gui.gyro_invert_y_var),
        "x_ratio": _value(gui.gyro_x_ratio_var),
        "y_ratio": _value(gui.gyro_y_ratio_var),
        "accel_suppression": _value(gui.gyro_accel_suppression_var),
        "adaptive_deadzone": _value(gui.gyro_adaptive_deadzone_var),
        "button_freeze_ms": _value(gui.gyro_button_freeze_var),
        "player_space": _value(gui.gyro_player_space_var),
    }


def collect_gui_settings(gui, *, strict: bool = True) -> dict[str, Any]:
    """Collect every profile-editable GUI value into one typed bundle."""
    sections = {
        "stick_curve_left": normalize_section_values(
            "stick_curve_left", _stick_curve_values(gui, "LEFT"), strict=strict
        ),
        "stick_curve_right": normalize_section_values(
            "stick_curve_right", _stick_curve_values(gui, "RIGHT"), strict=strict
        ),
        "rumble": normalize_section_values(
            "rumble", _rumble_values(gui), strict=strict
        ),
        "audio_haptics": normalize_section_values(
            "audio_haptics", _audio_values(gui), strict=strict
        ),
        "stick_direction_left": normalize_section_values(
            "stick_direction_left",
            _stick_direction_values(gui, "LEFT"),
            strict=strict,
        ),
        "stick_direction_right": normalize_section_values(
            "stick_direction_right",
            _stick_direction_values(gui, "RIGHT"),
            strict=strict,
        ),
        "gyro_mapping": normalize_section_values(
            "gyro_mapping", _gyro_values(gui), strict=strict
        ),
    }

    activation_buttons = [
        str(button).strip().upper()
        for button in gui.gyro_activation_buttons
        if str(button).strip()
    ]
    stabilization_buttons = [
        str(button).strip().upper()
        for button in gui.gyro_stabilization_buttons
        if str(button).strip() and str(button).strip().upper() != "NONE"
    ]
    tilt_recenter_button = str(
        _value(gui.gyro_tilt_recenter_button_var)
    ).strip().upper()

    if strict:
        if sections["gyro_mapping"]["activation_mode"] != "OFF" and not activation_buttons:
            raise ValueError("至少選擇一個啟動按鍵。")
        if any(
            button not in gui.gyro_activation_button_options
            for button in activation_buttons
        ):
            raise ValueError("陀螺儀啟動按鍵無效。")
        if tilt_recenter_button not in gui.gyro_tilt_recenter_button_options:
            raise ValueError("重設中立按鍵無效。")
        if any(
            button not in gui.gyro_stabilization_button_options
            for button in stabilization_buttons
        ):
            raise ValueError("陀螺儀防晃按鍵無效。")

    return {
        "sections": sections,
        "buttons": {
            str(name).strip().lower(): str(_value(variable)).strip().upper()
            for name, variable in gui.button_vars.items()
        },
        "direction_mappings": {
            side.lower(): {
                str(direction).strip().lower(): str(_value(variable)).strip().upper()
                for direction, variable in gui.stick_direction_vars[side].items()
            }
            for side in ("LEFT", "RIGHT")
        },
        "gyro_activation_buttons": activation_buttons,
        "gyro_tilt_recenter_button": tilt_recenter_button,
        "gyro_stabilization_buttons": stabilization_buttons,
    }


def config_settings_bundle(gui, config: configparser.ConfigParser) -> dict[str, Any]:
    """Build the same typed bundle from a loaded ConfigParser."""
    sections = {
        section: read_section_settings(config, section, strict=False)
        for section in SECTION_SCHEMAS
    }

    activation_buttons = _button_list(
        config.get(
            "gyro_mapping",
            "activation_buttons",
            fallback=config.get(
                "gyro_mapping", "activation_button", fallback="ZL"
            ),
        ),
        gui.gyro_activation_button_options,
        fallback=("ZL",),
    )
    stabilization_buttons = _button_list(
        config.get(
            "gyro_mapping",
            "stabilization_buttons",
            fallback=config.get(
                "gyro_mapping", "stabilization_button", fallback="NONE"
            ),
        ),
        gui.gyro_stabilization_button_options,
    )

    return {
        "sections": sections,
        "buttons": {
            str(name).strip().lower(): config.get(
                "buttons", str(name), fallback="NONE"
            ).strip().upper()
            for name in gui.button_vars
        },
        "direction_mappings": {
            side.lower(): {
                str(direction).strip().lower(): config.get(
                    f"stick_direction_{side.lower()}",
                    str(direction).lower(),
                    fallback="NONE",
                ).strip().upper()
                for direction in gui.stick_direction_vars[side]
            }
            for side in ("LEFT", "RIGHT")
        },
        "gyro_activation_buttons": activation_buttons,
        "gyro_tilt_recenter_button": config.get(
            "gyro_mapping", "tilt_recenter_button", fallback="NONE"
        ).strip().upper(),
        "gyro_stabilization_buttons": stabilization_buttons,
    }


def canonical_settings_snapshot(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Return a deterministic representation matching on-disk formatting."""
    sections = bundle["sections"]
    return {
        "sections": {
            section: {
                key: format_setting_value(section, key, values[key])
                for key in SECTION_SCHEMAS[section]
            }
            for section, values in sections.items()
        },
        "buttons": dict(sorted(bundle["buttons"].items())),
        "direction_mappings": {
            side: dict(sorted(values.items()))
            for side, values in sorted(bundle["direction_mappings"].items())
        },
        "gyro_activation_buttons": tuple(bundle["gyro_activation_buttons"]),
        "gyro_tilt_recenter_button": bundle["gyro_tilt_recenter_button"],
        "gyro_stabilization_buttons": tuple(
            bundle["gyro_stabilization_buttons"]
        ),
    }


def apply_gui_settings_to_config(
    config: configparser.ConfigParser,
    bundle: Mapping[str, Any],
) -> None:
    """Write a collected bundle while preserving unrelated/calibration data."""
    sections = bundle["sections"]
    for section_name, values in sections.items():
        write_section_settings(config, section_name, values)

    for index in range(5):
        config.remove_option("stick_curve_left", f"point_{index}")
        config.remove_option("stick_curve_right", f"point_{index}")

    config.remove_option("rumble", "enabled")
    for legacy_option in ("low_gain", "high_gain", "crossover_hz"):
        config.remove_option("audio_haptics", legacy_option)
    config.remove_option("gyro_mapping", "sensor_axes")

    activation_buttons = list(bundle["gyro_activation_buttons"])
    stabilization_buttons = list(bundle["gyro_stabilization_buttons"])
    config.set(
        "gyro_mapping", "activation_buttons", ",".join(activation_buttons)
    )
    config.set(
        "gyro_mapping",
        "activation_button",
        activation_buttons[0] if activation_buttons else "ZL",
    )
    config.set(
        "gyro_mapping",
        "tilt_recenter_button",
        str(bundle["gyro_tilt_recenter_button"]),
    )
    config.set(
        "gyro_mapping",
        "stabilization_buttons",
        ",".join(stabilization_buttons) if stabilization_buttons else "NONE",
    )
    config.set(
        "gyro_mapping",
        "stabilization_button",
        stabilization_buttons[0] if stabilization_buttons else "NONE",
    )

    if not config.has_section("buttons"):
        config.add_section("buttons")
    for switch_name, value in bundle["buttons"].items():
        config.set("buttons", switch_name, value)

    for side in ("left", "right"):
        section_name = f"stick_direction_{side}"
        if not config.has_section(section_name):
            config.add_section(section_name)
        for direction, value in bundle["direction_mappings"][side].items():
            config.set(section_name, direction, value)

    config.remove_section("stick_direction")
