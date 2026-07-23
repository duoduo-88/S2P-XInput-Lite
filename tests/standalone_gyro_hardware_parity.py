"""Replay identical IMU samples through desktop and ESP32 gyro processing.

This is an opt-in hardware diagnostic:

    runtime\python.exe tests\standalone_gyro_hardware_parity.py --port COM5

The active ESP32 profile must have been written from the selected config file.
The diagnostic state is isolated from live controller input and does not write
either persistent profile slot.
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

import serial


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import xinput_controller as xinput_module  # noqa: E402
from config_utils import (  # noqa: E402
    load_accelerometer_calibration,
    load_config,
    load_gyro_bias,
    load_magnetometer_bias,
    load_magnetometer_matrix,
    load_magnetometer_scale,
    load_stick_calibration,
)
from gyro_processing import (  # noqa: E402
    _apply_gyro_stick_anti_deadzone,
    _clamp_vector_to_shape,
)
from standalone_profile import (  # noqa: E402
    StandaloneTransferError,
    _send_and_expect,
)
from switch2_input import InputState, SWITCH_BUTTONS  # noqa: E402
from xinput_controller import XInputController  # noqa: E402


def _controller_id(config):
    identifiers = {
        section.partition(".")[2]
        for section in config.sections()
        if section.startswith("gyro.") and section.partition(".")[2]
    }
    return next(iter(identifiers)) if len(identifiers) == 1 else None


def _build_desktop(config_path, motion_mode):
    config = load_config(config_path)
    config.set("gyro_mapping", "motion_mode", motion_mode)
    calibration_id = _controller_id(config)
    controller = XInputController(
        config,
        load_stick_calibration(config, calibration_id),
        activate_runtime=False,
    )
    controller.set_gyro_bias(load_gyro_bias(config, calibration_id))
    accel_bias, accel_matrix = load_accelerometer_calibration(
        config, calibration_id
    )
    controller.set_accelerometer_calibration(accel_bias, accel_matrix)
    controller.set_magnetometer_calibration(
        load_magnetometer_bias(config, calibration_id),
        load_magnetometer_scale(config, calibration_id),
        load_magnetometer_matrix(config, calibration_id),
    )
    return controller


def _axis(value):
    scale = 32768.0 if value < 0.0 else 32767.0
    return int(round(max(-1.0, min(1.0, value)) * scale))


def _expected_stick(controller, state, dt, existing):
    gyro_x, gyro_y, _, _ = controller._get_gyro_output(state, dt)
    if controller.gyro_motion_mode == "CENTER":
        existing_magnitude = math.hypot(
            existing[0] / (32768.0 if existing[0] < 0 else 32767.0),
            existing[1] / (32768.0 if existing[1] < 0 else 32767.0),
        )
        gyro_x, gyro_y = _apply_gyro_stick_anti_deadzone(
            gyro_x,
            gyro_y,
            controller.gyro_stick_anti_deadzone,
            existing_magnitude,
        )
    existing_x = existing[0] / (
        32768.0 if existing[0] < 0 else 32767.0
    )
    existing_y = existing[1] / (
        32768.0 if existing[1] < 0 else 32767.0
    )
    side = "left" if controller.gyro_target == "LEFT_STICK" else "right"
    final_x, final_y = _clamp_vector_to_shape(
        existing_x + gyro_x,
        existing_y + gyro_y,
        controller.stick_output_shape[side],
    )
    return (_axis(final_x), _axis(final_y)), (gyro_x, gyro_y)


def _sequence():
    result = []
    resting_accel = (0, -3500, 2100)
    # Establish gravity, magnetic reference, and the 750 ms recovery ramp.
    result.extend([((500, 100, 200), resting_accel, (4, 0, -20))] * 120)
    # Exercise each native IMU axis, diagonal motion, return, and one impact.
    result.extend([((500, 100, 200), resting_accel, (430, 0, -20))] * 35)
    result.extend([((500, 100, 200), resting_accel, (4, 360, -20))] * 35)
    result.extend([((500, 100, 200), resting_accel, (4, 0, -390))] * 35)
    result.extend([((500, 100, 200), resting_accel, (280, -220, 310))] * 35)
    result.extend([((500, 100, 200), resting_accel, (4, 0, -20))] * 45)
    result.extend([
        ((500, 100, 200), (2500, -6500, 1200), (1500, -900, 800)),
        ((500, 100, 200), resting_accel, (4, 0, -20)),
    ])
    return result


def _query(port, text):
    return _send_and_expect(port, text, "gyro_test", timeout=2.0)


def _source_for(controller, index):
    mask = 0
    for name in controller.gyro_activation_buttons:
        mask |= SWITCH_BUTTONS[name]
    if controller.gyro_activation_mode == "HOLD":
        return mask
    if controller.gyro_activation_mode == "TOGGLE" and index == 0:
        return mask
    return 0


def run(port_name, baudrate, config_path, motion_modes):
    failures = []
    checks = 0
    maximum_axis_error = 0
    maximum_output_error = 0.0
    original_perf_counter = xinput_module.time.perf_counter
    try:
        with serial.Serial(
            port_name, baudrate, timeout=0.1, write_timeout=0.5
        ) as port:
            port.reset_input_buffer()
            for motion_mode in motion_modes:
                controller = _build_desktop(config_path, motion_mode)
                try:
                    reset = _query(port, f"gyro test reset {motion_mode}")
                except StandaloneTransferError:
                    # The first diagnostic firmware accepted only plain reset.
                    reset = _query(port, "gyro test reset")
                if reset["motion"] != motion_mode:
                    failures.append(
                        f"{motion_mode}: reset returned {reset['motion']}"
                    )
                    continue
                report_time = 1000
                previous_time = None
                for index, (mag, accel, gyro) in enumerate(_sequence()):
                    source = _source_for(controller, index)
                    existing = (5000, -2500) if 175 <= index < 190 else (0, 0)
                    now_seconds = report_time / 1000.0
                    xinput_module.time.perf_counter = (
                        lambda value=now_seconds: value
                    )
                    state = InputState(
                        buttons=source,
                        left_stick=(2048, 2048),
                        right_stick=(2048, 2048),
                        accelerometer=accel,
                        gyroscope=gyro,
                        magnetometer=mag,
                        battery_percent=None,
                        battery_voltage=None,
                        charging=False,
                        report_time=report_time,
                    )
                    dt = (
                        0.0 if previous_time is None
                        else (report_time - previous_time) / 1000.0
                    )
                    expected_stick, expected_output = _expected_stick(
                        controller, state, dt, existing
                    )
                    command = (
                        f"gyro test sample {report_time} {source:x} "
                        f"{mag[0]} {mag[1]} {mag[2]} "
                        f"{accel[0]} {accel[1]} {accel[2]} "
                        f"{gyro[0]} {gyro[1]} {gyro[2]} "
                        f"{existing[0]} {existing[1]}"
                    )
                    actual = _query(port, command)
                    axis_error = max(
                        abs(actual["stick"][axis] - expected_stick[axis])
                        for axis in range(2)
                    )
                    output_error = max(
                        abs(actual["output"][axis] - expected_output[axis])
                        for axis in range(2)
                    )
                    maximum_axis_error = max(maximum_axis_error, axis_error)
                    maximum_output_error = max(
                        maximum_output_error, output_error
                    )
                    checks += 1
                    axis_limit = 5 if motion_mode == "CENTER" else 32
                    output_limit = (
                        2e-4 if motion_mode == "CENTER" else 1e-3
                    )
                    if (
                        axis_error > axis_limit
                        or output_error > output_limit
                    ):
                        failures.append(
                            f"{motion_mode} sample {index}: "
                            f"ESP32 stick={actual['stick']} "
                            f"desktop={expected_stick}, "
                            f"output={actual['output']} "
                            f"desktop_output={expected_output}, "
                            f"orientation={actual['orientation']} "
                            f"desktop_orientation="
                            f"{controller._nine_axis_orientation}"
                        )
                        if len(failures) >= 30:
                            break
                    previous_time = report_time
                    report_time += 8
    finally:
        xinput_module.time.perf_counter = original_perf_counter

    result = {
        "checks": checks,
        "failures": len(failures),
        "max_stick_axis_error": maximum_axis_error,
        "max_output_error": maximum_output_error,
        "details": failures,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM5")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument(
        "--config", type=Path, default=ROOT / "src" / "config.ini"
    )
    parser.add_argument(
        "--motion",
        choices=("CENTER", "TILT", "BOTH"),
        default="BOTH",
    )
    args = parser.parse_args()
    motion_modes = (
        ("CENTER", "TILT")
        if args.motion == "BOTH"
        else (args.motion,)
    )
    raise SystemExit(
        run(args.port, args.baudrate, args.config, motion_modes)
    )


if __name__ == "__main__":
    main()
