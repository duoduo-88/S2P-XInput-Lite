"""Measure wired IMU cadence and the complete gyro-to-XInput pipeline."""

import argparse
import configparser
import gc
import math
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config_utils import (
    CONFIG_PATH,
    load_accelerometer_calibration,
    load_gyro_bias,
    load_magnetometer_bias,
    load_magnetometer_matrix,
    load_magnetometer_scale,
    load_stick_calibration,
)
from input_dispatcher import InputDispatcher
from switch2_input import parse_input_report
from wired_controller import WiredController, find_wired_controller
from xinput_controller import XInputController


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(len(ordered) - 1, int((len(ordered) - 1) * fraction))
    return ordered[index]


def distribution(values):
    return {
        "p50": round(percentile(values, 0.50), 3),
        "p95": round(percentile(values, 0.95), 3),
        "p99": round(percentile(values, 0.99), 3),
        "max": round(max(values), 3) if values else 0.0,
    }


def reset_gyro_session(
    xinput,
    *,
    motion_mode="CENTER",
    player_space=True,
    smoothing_ms=0.0,
):
    """Enable one complete gyro path without changing the saved config."""
    xinput.reset_output_state()
    xinput.gyro_activation_mode = "TOGGLE"
    # The diagnostic owns activation for the whole phase.  Do not let the
    # user's configured Toggle button accidentally turn the probe back off.
    xinput.gyro_activation_buttons = ()
    xinput.gyro_motion_mode = motion_mode
    xinput.gyro_target = "RIGHT_STICK"
    xinput.gyro_player_space = bool(player_space)
    xinput.gyro_smoothing_ms = float(smoothing_ms)
    xinput.gyro_tilt_smoothing_ms = float(smoothing_ms)
    xinput.gyro_tilt_axis = "DUAL"
    xinput._gyro_toggle_enabled = True


def main(seconds_per_phase=15.0, check_mode=None):
    config = configparser.ConfigParser()
    path = (
        CONFIG_PATH if CONFIG_PATH.is_file()
        else ROOT / "src" / "profiles" / "System Default.ini"
    )
    if not config.read(path, encoding="utf-8"):
        raise RuntimeError(f"Could not read configuration: {path}")

    entry = find_wired_controller()
    if entry is None:
        raise RuntimeError("No wired Switch 2 controller was detected")

    transport = WiredController(entry)
    xinput = XInputController(config, load_stick_calibration(config))
    connected = threading.Event()
    disconnected = threading.Event()
    lock = threading.Lock()
    submitted_at = OrderedDict()
    sampling = False
    phase = None
    phase_started = 0.0
    stats = {}

    def new_stats():
        return {
            "submitted": 0,
            "processed": 0,
            "valid_motion": 0,
            "zero_motion": 0,
            "duplicate_motion": 0,
            "changed_motion": 0,
            "output_changed": 0,
            "output_active": 0,
            "above_deadzone": 0,
            "report_clock_repeats": 0,
            "report_clock_reversals": 0,
            "last_motion": None,
            "last_output": None,
            "last_arrival_ns": None,
            "last_report_time": None,
            "arrival_ms": [],
            "sensor_dt_ms": [],
            "raw_gyro_speed_dps": [],
            "aim_speed_dps": [],
            "parse_ms": [],
            "xinput_ms": [],
            "processing_ms": [],
            "pipeline_ms": [],
        }

    def payload_key(payload):
        return id(payload)

    def on_input(payload):
        nonlocal phase
        callback_started = time.perf_counter_ns()
        state = parse_input_report(payload)
        parsed = time.perf_counter_ns()
        if state is None:
            return
        xinput.update(state)
        finished = time.perf_counter_ns()
        if not sampling or phase is None:
            return

        motion = (
            tuple(state.gyroscope),
            tuple(state.accelerometer),
            tuple(state.magnetometer),
        )
        motion_nonzero = any(abs(value) > 1 for group in motion for value in group)
        output = (
            int(xinput.pad.report.sThumbRX),
            int(xinput.pad.report.sThumbRY),
        )
        rates = tuple(
            (float(state.gyroscope[index]) - xinput._gyro_bias[index])
            / 14.285714
            for index in range(3)
        )
        raw_speed = math.sqrt(sum(value * value for value in rates))
        aim_speed = math.hypot(rates[2], rates[0])

        with lock:
            current = stats[phase]
            current["processed"] += 1
            if motion_nonzero:
                current["valid_motion"] += 1
            else:
                current["zero_motion"] += 1
            if current["last_motion"] is not None:
                if motion == current["last_motion"]:
                    current["duplicate_motion"] += 1
                else:
                    current["changed_motion"] += 1
            current["last_motion"] = motion

            if current["last_output"] is not None and output != current["last_output"]:
                current["output_changed"] += 1
            if output != (0, 0):
                current["output_active"] += 1
            current["last_output"] = output
            current["raw_gyro_speed_dps"].append(raw_speed)
            current["aim_speed_dps"].append(aim_speed)
            if aim_speed > xinput.gyro_deadzone:
                current["above_deadzone"] += 1

            now_ns = callback_started
            if current["last_arrival_ns"] is not None:
                current["arrival_ms"].append(
                    (now_ns - current["last_arrival_ns"]) / 1_000_000.0
                )
            current["last_arrival_ns"] = now_ns

            report_time = state.report_time
            if report_time is not None:
                report_time = int(report_time) & 0xFFFFFFFF
                previous = current["last_report_time"]
                if previous is not None:
                    delta = (report_time - previous) & 0xFFFFFFFF
                    if delta == 0:
                        current["report_clock_repeats"] += 1
                    elif delta < 0x80000000:
                        current["sensor_dt_ms"].append(float(delta))
                    else:
                        current["report_clock_reversals"] += 1
                current["last_report_time"] = report_time

            current["parse_ms"].append((parsed - callback_started) / 1_000_000.0)
            current["xinput_ms"].append((finished - parsed) / 1_000_000.0)
            current["processing_ms"].append((finished - callback_started) / 1_000_000.0)
            submitted = submitted_at.pop(payload_key(payload), None)
            if submitted is not None:
                current["pipeline_ms"].append((finished - submitted) / 1_000_000.0)

    dispatcher = InputDispatcher(on_input, max_pending=3, inline_fast_path=True)

    def submit(payload):
        snapshot = payload if isinstance(payload, bytes) else bytes(payload)
        if sampling and phase is not None:
            with lock:
                stats[phase]["submitted"] += 1
                submitted_at[payload_key(snapshot)] = time.perf_counter_ns()
                while len(submitted_at) > 4096:
                    submitted_at.popitem(last=False)
        dispatcher.submit(snapshot)

    transport.input_callback = submit

    def on_connected():
        dispatcher.reset()
        controller_id = getattr(transport, "controller_id", None)
        try:
            xinput.set_calibration(load_stick_calibration(config, controller_id))
        except (KeyError, TypeError, ValueError):
            pass
        try:
            xinput.set_gyro_bias(load_gyro_bias(config, controller_id))
            accel_bias, accel_matrix = load_accelerometer_calibration(
                config, controller_id
            )
            xinput.set_accelerometer_calibration(accel_bias, accel_matrix)
            xinput.set_magnetometer_calibration(
                load_magnetometer_bias(config, controller_id),
                load_magnetometer_scale(config, controller_id),
                load_magnetometer_matrix(config, controller_id),
            )
        except (KeyError, TypeError, ValueError, configparser.Error):
            xinput.set_gyro_bias(None)
            xinput.set_accelerometer_calibration(None)
            xinput.set_magnetometer_calibration(None)
        connected.set()

    transport.connected_callback = on_connected
    transport.disconnected_callback = disconnected.set
    gc_initially_enabled = gc.isenabled()

    try:
        transport.open()
        if not connected.wait(30.0):
            raise RuntimeError("Wired controller did not connect within 30 seconds")
        time.sleep(2.0)
        if gc_initially_enabled:
            gc.disable()

        print({"ready": True, "mode": "wired", "full_report": transport.full_report})
        if check_mode == "center":
            phases = (("center_player_0ms_check", "CENTER", True, 0.0),)
        elif check_mode == "tilt":
            phases = (("tilt_9axis_0ms_check", "TILT", True, 0.0),)
        else:
            phases = (
                ("center_player_15ms", "CENTER", True, 15.0),
                ("center_player_0ms", "CENTER", True, 0.0),
                ("center_controller_0ms", "CENTER", False, 0.0),
                ("tilt_9axis_0ms", "TILT", True, 0.0),
            )
        for name, motion_mode, player_space, smoothing_ms in phases:
            if disconnected.is_set():
                break
            with lock:
                stats[name] = new_stats()
                submitted_at.clear()
            dispatcher.reset()
            reset_gyro_session(
                xinput,
                motion_mode=motion_mode,
                player_space=player_space,
                smoothing_ms=smoothing_ms,
            )
            phase = name
            phase_started = time.monotonic()
            sampling = True
            print(
                {
                    "phase": name,
                    "seconds": seconds_per_phase,
                    "motion_mode": motion_mode,
                    "player_space": player_space,
                    "smoothing_ms": smoothing_ms,
                    "instruction": "Move and rotate the controller continuously",
                },
                flush=True,
            )
            while (
                time.monotonic() - phase_started < seconds_per_phase
                and not disconnected.is_set()
            ):
                time.sleep(0.05)
            sampling = False

            elapsed = max(0.001, time.monotonic() - phase_started)
            with lock:
                current = stats[name]
                processed = current["processed"]
                valid = current["valid_motion"]
                changed = current["changed_motion"]
                result = {
                    "phase": name,
                    "elapsed_s": round(elapsed, 2),
                    "submitted": current["submitted"],
                    "processed": processed,
                    "rate_hz": round(processed / elapsed, 1),
                    "transport_rate_hz": round(transport.polling_rate_hz or 0.0, 1),
                    "full_report": transport.full_report,
                    "valid_motion": valid,
                    "zero_motion": current["zero_motion"],
                    "duplicate_motion": current["duplicate_motion"],
                    "changed_motion": changed,
                    "fresh_motion_pct": round(100.0 * changed / max(1, processed - 1), 2),
                    "output_changed": current["output_changed"],
                    "output_active_pct": round(100.0 * current["output_active"] / max(1, processed), 2),
                    "above_deadzone_pct": round(
                        100.0 * current["above_deadzone"] / max(1, processed), 2
                    ),
                    "clock_repeats": current["report_clock_repeats"],
                    "clock_reversals": current["report_clock_reversals"],
                    "inline": dispatcher.inline_reports,
                    "queued": dispatcher.queued_reports,
                    "backlog_batches": dispatcher.backlog_batches,
                    "dropped_imu": dispatcher.dropped_reports,
                    "arrival_interval_ms": distribution(current["arrival_ms"]),
                    "sensor_interval_ms": distribution(current["sensor_dt_ms"]),
                    "raw_gyro_speed_dps": distribution(
                        current["raw_gyro_speed_dps"]
                    ),
                    "aim_speed_dps": distribution(current["aim_speed_dps"]),
                    "parse_ms": distribution(current["parse_ms"]),
                    "gyro_xinput_ms": distribution(current["xinput_ms"]),
                    "pipeline_ms": distribution(current["pipeline_ms"]),
                }
            print(result, flush=True)
            phase = None
            time.sleep(1.0)
    finally:
        sampling = False
        phase = None
        transport.input_callback = None
        transport.close()
        dispatcher.stop()
        xinput.close()
        if gc_initially_enabled and not gc.isenabled():
            gc.enable()
            gc.collect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=15.0)
    parser.add_argument("--center-check", action="store_true")
    parser.add_argument("--tilt-check", action="store_true")
    args = parser.parse_args()
    if args.center_check and args.tilt_check:
        parser.error("choose only one check mode")
    check_mode = "center" if args.center_check else "tilt" if args.tilt_check else None
    main(max(1.0, args.seconds), check_mode)
