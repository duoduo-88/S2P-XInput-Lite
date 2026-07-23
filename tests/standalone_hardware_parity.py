"""Compare desktop stick algorithms with the active ESP32 standalone profile.

This is an opt-in hardware diagnostic, not part of normal unit-test discovery:

    runtime\python.exe tests\standalone_hardware_parity.py --port COM5
"""
from __future__ import annotations

import argparse
import configparser
import json
import math
from pathlib import Path
import sys

import serial


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config_utils import load_stick_calibration  # noqa: E402
from settings_schema import read_section_settings  # noqa: E402
from standalone_profile import _send_and_expect  # noqa: E402
from stick_curve import apply_stick_curve  # noqa: E402
from stick_processing import (  # noqa: E402
    _apply_linear_axis_response,
    apply_calibration_to_axis,
    apply_output_shape,
    get_stick_curve_slope,
)


def _curve_points(settings):
    return [
        (settings[f"point_{index}_x"], settings[f"point_{index}_y"])
        for index in range(5)
    ]


def _to_xinput(value):
    scale = 32768.0 if value < 0.0 else 32767.0
    return int(round(max(-1.0, min(1.0, value)) * scale))


def _desktop_stick_pair(
    samples, settings, calibration,
):
    curve = _curve_points(settings)
    previous_magnitude = None
    previous_time = None
    results = []
    for raw_x, raw_y, report_time in samples:
        x = max(-1.0, min(1.0, apply_calibration_to_axis(
            raw_x,
            calibration["center"][0],
            calibration["max"][0],
            calibration["min"][0],
        )))
        y = max(-1.0, min(1.0, apply_calibration_to_axis(
            raw_y,
            calibration["center"][1],
            calibration["max"][1],
            calibration["min"][1],
        )))
        raw_magnitude = math.hypot(x, y)
        if raw_magnitude < settings["deadzone"] or raw_magnitude <= 1e-9:
            previous_magnitude = 0.0
            previous_time = None
            results.append((0, 0))
            continue

        curve_start = (
            settings["deadzone"] if settings["deadzone_compress"] else 0.0
        )
        curve_end = (
            1.0 - settings["outer_deadzone"]
            if settings["outer_deadzone_compress"] else 1.0
        )
        if curve_end <= curve_start:
            curve_start, curve_end = 0.0, 1.0
        curve_width = curve_end - curve_start
        curve_input = max(
            0.0,
            min(1.0, (min(1.0, raw_magnitude) - curve_start) / curve_width),
        )
        output_magnitude = max(0.0, min(1.0, apply_stick_curve(
            curve_input, curve, settings["interpolation"],
        )))

        smoothing = settings["smoothing"]
        if smoothing > 0.0:
            slope = (
                get_stick_curve_slope(
                    curve_input, curve, settings["interpolation"],
                )
                / curve_width
            )
            base_alpha = (
                1.0
                if slope <= 1.0
                else 1.0 / (1.0 + (min(slope, 10.0) - 1.0) * smoothing)
            )
            alpha = 1.0
            if base_alpha < 1.0 and previous_time is not None:
                time_constant = -(1.0 / 120.0) / math.log(1.0 - base_alpha)
                delta = max(
                    0.0, min(0.1, (report_time - previous_time) / 1000.0)
                )
                alpha = 1.0 - math.exp(-delta / time_constant)
            if previous_magnitude is not None:
                output_magnitude = previous_magnitude + (
                    output_magnitude - previous_magnitude
                ) * alpha
            previous_time = report_time
        previous_magnitude = output_magnitude

        direction_x = x / raw_magnitude
        direction_y = y / raw_magnitude
        if (
            settings["outer_deadzone"] > 0.0
            and raw_magnitude >= 1.0 - settings["outer_deadzone"]
        ):
            output_magnitude = 1.0
            previous_magnitude = 1.0
        shaped_x, shaped_y = apply_output_shape(
            direction_x * output_magnitude,
            direction_y * output_magnitude,
            settings["output_shape"] / 10.0,
        )
        results.append((_to_xinput(shaped_x), _to_xinput(shaped_y)))
    return results


def _raw_axis(center, maximum, minimum, amount):
    extent = maximum if amount >= 0.0 else minimum
    return max(0, min(4095, int(round(center + amount * extent))))


def _query(port, command):
    return _send_and_expect(
        port, f"algorithm test {command}", "algorithm_test", timeout=2.0
    )


def _expected_direction(config, first, final):
    mode = config["mode"]
    sector_step = 90.0 if mode == "4WAY" else 45.0
    indices = (
        (2, 0, 6, 4)
        if mode == "4WAY"
        else (2, 1, 0, 7, 6, 5, 4, 3)
    )
    active = None
    for x, y in (first, final):
        magnitude = math.hypot(x, y)
        if active is None:
            if magnitude < config["trigger_threshold"]:
                continue
        elif magnitude <= config["release_threshold"]:
            active = None
            continue
        if magnitude < config["trigger_threshold"]:
            continue
        angle = math.degrees(math.atan2(y, x)) % 360.0
        half = sector_step / 2.0
        sector = int((angle + half) // sector_step) % len(indices)
        candidate = indices[sector]

        def distance(center):
            return abs((angle - center + 180.0) % 360.0 - 180.0)

        if active is not None:
            current_sector = indices.index(active)
            if distance(current_sector * sector_step) <= (
                half + config["direction_deadzone"]
            ):
                continue
        if distance(sector * sector_step) > (
            half - config["direction_deadzone"]
        ):
            continue
        active = candidate
    return -1 if active is None else active


def run(port_name, baudrate, config_path):
    config = configparser.ConfigParser()
    if not config.read(config_path, encoding="utf-8"):
        raise FileNotFoundError(config_path)
    stick_settings = {
        side: read_section_settings(
            config, f"stick_curve_{side}", strict=True
        )
        for side in ("left", "right")
    }
    direction_settings = {
        side: read_section_settings(
            config, f"stick_direction_{side}", strict=True
        )
        for side in ("left", "right")
    }
    calibration_ids = [
        section.partition(".")[2]
        for section in config.sections()
        if section.startswith("gyro.") and section.partition(".")[2]
    ]
    calibration_id = (
        calibration_ids[0] if len(set(calibration_ids)) == 1 else None
    )
    calibration = load_stick_calibration(config, calibration_id)

    failures = []
    checks = 0
    max_axis_error = 0
    max_linear_error = 0.0
    with serial.Serial(
        port_name, baudrate, timeout=0.1, write_timeout=0.5
    ) as port:
        port.reset_input_buffer()
        runtime = _send_and_expect(
            port, "runtime status", "runtime_status", timeout=2.0
        )
        for side_index, side in enumerate(("left", "right")):
            expected_center = list(calibration[side]["center"])
            if runtime[f"{side}_center"] != expected_center:
                failures.append(
                    f"{side} center: ESP32={runtime[f'{side}_center']} "
                    f"desktop={expected_center}"
                )
            if runtime[f"{side}_shape"] != stick_settings[side]["output_shape"]:
                failures.append(f"{side} output shape differs")

            center_x, center_y = calibration[side]["center"]
            max_x, max_y = calibration[side]["max"]
            min_x, min_y = calibration[side]["min"]
            normalized_samples = (
                (-1.0, 0.0), (-0.75, 0.25), (-0.35, -0.55),
                (-0.05, 0.0), (0.0, 0.0), (0.04, 0.02),
                (0.20, 0.70), (0.50, -0.50), (0.85, 0.15),
                (1.0, 0.0), (0.0, 1.0), (0.72, 0.72),
            )
            for nx, ny in normalized_samples:
                raw_x = _raw_axis(center_x, max_x, min_x, nx)
                raw_y = _raw_axis(center_y, max_y, min_y, ny)
                samples = (
                    (raw_x, raw_y, 1000),
                    (raw_x, raw_y, 1008),
                )
                expected = _desktop_stick_pair(
                    samples, stick_settings[side], calibration[side]
                )
                actual = _query(
                    port,
                    f"stick {side[0].upper()} "
                    f"{raw_x} {raw_y} 1000 {raw_x} {raw_y} 1008",
                )
                actual_values = (
                    tuple(actual["first"]), tuple(actual["final"])
                )
                for sample_index in range(2):
                    errors = tuple(
                        abs(actual_values[sample_index][axis] -
                            expected[sample_index][axis])
                        for axis in (0, 1)
                    )
                    max_axis_error = max(max_axis_error, *errors)
                    checks += 2
                    if max(errors) > 4:
                        failures.append(
                            f"{side} stick {nx:.2f},{ny:.2f}: "
                            f"ESP32={actual_values[sample_index]} "
                            f"desktop={expected[sample_index]}"
                        )

            transition_pairs = (
                ((0.10, 0.0), (0.80, 0.0), 8),
                ((0.20, 0.15), (0.75, 0.55), 16),
                ((0.85, 0.0), (0.30, 0.0), 33),
            )
            for first, final, delta_ms in transition_pairs:
                raw = []
                for nx, ny in (first, final):
                    raw.append((
                        _raw_axis(center_x, max_x, min_x, nx),
                        _raw_axis(center_y, max_y, min_y, ny),
                    ))
                samples = (
                    (*raw[0], 2000),
                    (*raw[1], 2000 + delta_ms),
                )
                expected = _desktop_stick_pair(
                    samples, stick_settings[side], calibration[side]
                )
                actual = _query(
                    port,
                    f"stick {side[0].upper()} "
                    f"{raw[0][0]} {raw[0][1]} 2000 "
                    f"{raw[1][0]} {raw[1][1]} {2000 + delta_ms}",
                )
                final_actual = tuple(actual["final"])
                errors = tuple(
                    abs(final_actual[axis] - expected[1][axis])
                    for axis in (0, 1)
                )
                max_axis_error = max(max_axis_error, *errors)
                checks += 2
                if max(errors) > 4:
                    failures.append(
                        f"{side} smoothing {first}->{final}: "
                        f"ESP32={final_actual} desktop={expected[1]}"
                    )

            curve = _curve_points(stick_settings[side])
            for direction in ("UP", "DOWN", "LEFT", "RIGHT"):
                for amount in (0.0, 0.02, 0.05, 0.25, 0.60, 0.98, 1.0):
                    nx = amount if direction == "RIGHT" else (
                        -amount if direction == "LEFT" else 0.0
                    )
                    ny = amount if direction == "UP" else (
                        -amount if direction == "DOWN" else 0.0
                    )
                    raw_x = _raw_axis(center_x, max_x, min_x, nx)
                    raw_y = _raw_axis(center_y, max_y, min_y, ny)
                    calibrated = apply_calibration_to_axis(
                        raw_x if direction in ("LEFT", "RIGHT") else raw_y,
                        center_x if direction in ("LEFT", "RIGHT") else center_y,
                        max_x if direction in ("LEFT", "RIGHT") else max_y,
                        min_x if direction in ("LEFT", "RIGHT") else min_y,
                    )
                    signed = (
                        calibrated
                        if direction in ("UP", "RIGHT")
                        else -calibrated
                    )
                    expected_amount = _apply_linear_axis_response(
                        signed,
                        stick_settings[side]["deadzone"],
                        stick_settings[side]["outer_deadzone"],
                        stick_settings[side]["deadzone_compress"],
                        stick_settings[side]["outer_deadzone_compress"],
                        curve,
                        stick_settings[side]["interpolation"],
                    )
                    actual = _query(
                        port,
                        f"linear {side[0].upper()} {direction} "
                        f"{raw_x} {raw_y}",
                    )
                    error = abs(actual["amount"] - expected_amount)
                    max_linear_error = max(max_linear_error, error)
                    checks += 1
                    if error > 2e-5:
                        failures.append(
                            f"{side} linear {direction} {amount:.2f}: "
                            f"ESP32={actual['amount']:.7f} "
                            f"desktop={expected_amount:.7f}"
                        )

            if direction_settings[side]["mode"] in ("4WAY", "8WAY"):
                first = (0.85, 0.0)
                for angle in (0.0, 39.0, 41.0, 49.0, 51.0, 89.0, 91.0):
                    final = (
                        0.85 * math.cos(math.radians(angle)),
                        0.85 * math.sin(math.radians(angle)),
                    )
                    expected_active = _expected_direction(
                        direction_settings[side], first, final
                    )
                    actual = _query(
                        port,
                        f"direction {side[0].upper()} "
                        f"{first[0]:.7f} {first[1]:.7f} "
                        f"{final[0]:.7f} {final[1]:.7f}",
                    )
                    checks += 1
                    if actual["active"] != expected_active:
                        failures.append(
                            f"{side} direction {angle:.1f}°: "
                            f"ESP32={actual['active']} "
                            f"desktop={expected_active}"
                        )

    result = {
        "checks": checks,
        "failures": len(failures),
        "max_stick_axis_error": max_axis_error,
        "max_linear_error": max_linear_error,
        "details": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT / "src" / "config.ini",
    )
    args = parser.parse_args()
    raise SystemExit(run(args.port, args.baudrate, args.config))


if __name__ == "__main__":
    main()
