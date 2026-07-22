"""Measure native-Bluetooth or wired-USB input through the real ViGEm path."""

import argparse
import configparser
import gc
import struct
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from bluetooth_controller import BluetoothController
from config_utils import CONFIG_PATH, load_stick_calibration
from input_dispatcher import InputDispatcher
from rumble_protocol import (
    CONNECTION_HF_FREQUENCY,
    CONNECTION_LF_FREQUENCY,
    STRESS_AMPLITUDES,
)
from switch2_input import SWITCH_BUTTONS, parse_input_report
from wired_controller import WiredController, find_wired_controller
from xinput_controller import XB_BUTTONS, XInputController


def percentile(values, fraction):
    ordered = sorted(values)
    if not ordered:
        return 0.0
    return ordered[min(len(ordered) - 1, int(len(ordered) * fraction))]


def distribution(values):
    return {
        "p50": round(percentile(values, 0.50), 3),
        "p95": round(percentile(values, 0.95), 3),
        "p99": round(percentile(values, 0.99), 3),
        "max": round(max(values), 3) if values else 0.0,
    }


def create_transport(mode):
    if mode == "bluetooth":
        return BluetoothController()
    entry = find_wired_controller()
    if entry is None:
        raise RuntimeError("No wired Switch 2 controller was detected")
    return WiredController(entry)


def main(
    mode, seconds=30.0, inline=False, rumble_stress=False,
    rumble_profile="priority",
):
    config = configparser.ConfigParser()
    path = (
        CONFIG_PATH if CONFIG_PATH.is_file()
        else ROOT / "src" / "profiles" / "System Default.ini"
    )
    if not config.read(path, encoding="utf-8"):
        raise RuntimeError(f"Could not read configuration: {path}")

    transport = create_transport(mode)
    xinput = XInputController(config, load_stick_calibration(config))
    connected = threading.Event()
    disconnected = threading.Event()
    lock = threading.Lock()
    submitted_at = OrderedDict()
    sampling = False
    submitted_count = 0
    callback_count = 0
    expected_edges = 0
    output_edges = 0
    mapping_mismatches = 0
    last_expected = None
    last_output = None
    queue_ms = []
    parse_ms = []
    xinput_ms = []
    processing_ms = []
    pipeline_ms = []
    arrival_interval_ms = []
    report_interval_ms = []
    transport_variation_ms = []
    last_arrival_ns = None
    last_report_time = None
    rumble_submitted = 0
    rumble_stop = threading.Event()
    rumble_thread = None
    strict_button_check = not any(
        xinput._stick_direction_mapping_enabled.values()
    )

    def payload_key(payload):
        # Keep identity in the key so identical stationary reports remain
        # distinct while waiting in the dispatcher.
        return id(payload)

    def on_input(payload):
        nonlocal callback_count, expected_edges, output_edges
        nonlocal mapping_mismatches, last_expected, last_output
        callback_started = time.perf_counter_ns()
        state = parse_input_report(payload)
        parsed = time.perf_counter_ns()
        if state is None:
            return
        xinput.update(state)
        finished = time.perf_counter_ns()
        if not sampling:
            return

        output = int(xinput.pad.report.wButtons)
        expected = 0
        expected_lt = 0
        expected_rt = 0
        for name, target in xinput.mapping.items():
            if not state.buttons & SWITCH_BUTTONS.get(name, 0):
                continue
            if target in XB_BUTTONS:
                expected |= int(XB_BUTTONS[target])
            elif target == "LT":
                expected_lt = 255
            elif target == "RT":
                expected_rt = 255

        with lock:
            callback_count += 1
            if last_expected is not None:
                expected_edges += (expected ^ last_expected).bit_count()
            if last_output is not None:
                output_edges += (output ^ last_output).bit_count()
            output_missing = (
                output != expected
                if strict_button_check
                else bool(expected & ~output)
            )
            left_trigger = int(xinput.pad.report.bLeftTrigger)
            right_trigger = int(xinput.pad.report.bRightTrigger)
            left_trigger_mismatch = (
                left_trigger != expected_lt
                if strict_button_check
                else bool(expected_lt and left_trigger != expected_lt)
            )
            right_trigger_mismatch = (
                right_trigger != expected_rt
                if strict_button_check
                else bool(expected_rt and right_trigger != expected_rt)
            )
            if (
                output_missing
                or left_trigger_mismatch
                or right_trigger_mismatch
            ):
                mapping_mismatches += 1
            last_expected = expected
            last_output = output
            parse_ms.append((parsed - callback_started) / 1_000_000.0)
            xinput_ms.append((finished - parsed) / 1_000_000.0)
            processing_ms.append((finished - callback_started) / 1_000_000.0)
            submitted = submitted_at.pop(payload_key(payload), None)
            if submitted is not None:
                queue_ms.append((callback_started - submitted) / 1_000_000.0)
                pipeline_ms.append((finished - submitted) / 1_000_000.0)

    dispatcher = InputDispatcher(
        on_input,
        max_pending=3,
        # Default matches current production behavior.  --inline is an
        # explicit experiment used before enabling a transport in main.py.
        inline_fast_path=inline,
    )

    def submit(payload):
        nonlocal submitted_count, last_arrival_ns, last_report_time
        snapshot = payload if isinstance(payload, bytes) else bytes(payload)
        if sampling:
            arrived_ns = time.perf_counter_ns()
            with lock:
                submitted_count += 1
                submitted_at[payload_key(snapshot)] = arrived_ns
                if len(snapshot) >= 4:
                    report_time = struct.unpack_from("<I", snapshot, 0)[0]
                    if last_arrival_ns is not None and last_report_time is not None:
                        arrival_delta = (arrived_ns - last_arrival_ns) / 1_000_000.0
                        report_delta = (report_time - last_report_time) & 0xFFFFFFFF
                        arrival_interval_ms.append(arrival_delta)
                        if 0 < report_delta < 1000:
                            report_interval_ms.append(float(report_delta))
                            transport_variation_ms.append(arrival_delta - report_delta)
                    last_report_time = report_time
                last_arrival_ns = arrived_ns
                # Reports coalesced by the dispatcher never reach on_input.
                # Bound diagnostic bookkeeping without affecting dispatch.
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
        connected.set()

    transport.connected_callback = on_connected
    transport.disconnected_callback = disconnected.set
    gc_initially_enabled = gc.isenabled()

    try:
        transport.open()
        if not connected.wait(30.0):
            raise RuntimeError(f"{mode} controller did not connect within 30 seconds")
        time.sleep(2.0)
        if gc_initially_enabled:
            gc.disable()
        dispatcher.reset()
        reset_rumble_diagnostics = getattr(
            transport, "reset_rumble_diagnostics", None
        )
        if reset_rumble_diagnostics is not None:
            reset_rumble_diagnostics()
        sampling = True
        if rumble_stress:
            if mode == "wired":
                transport.set_audio_haptics_active(
                    rumble_profile == "audio"
                )

            def rumble_worker():
                nonlocal rumble_submitted
                sequence = STRESS_AMPLITUDES
                index = 0
                next_submit = time.perf_counter()
                while not rumble_stop.is_set():
                    amplitude = sequence[index % len(sequence)]
                    index += 1
                    if mode == "wired":
                        accepted = transport.send_pro_rumble(
                            CONNECTION_LF_FREQUENCY,
                            amplitude,
                            CONNECTION_HF_FREQUENCY,
                            amplitude,
                            priority=(rumble_profile == "priority"),
                            force_zero=(amplitude == 0),
                        )
                    else:
                        accepted = transport.send_pro_rumble(
                            CONNECTION_LF_FREQUENCY,
                            amplitude,
                            CONNECTION_HF_FREQUENCY,
                            amplitude,
                        )
                    if accepted:
                        rumble_submitted += 1
                    next_submit += 0.005
                    while not rumble_stop.is_set():
                        remaining = next_submit - time.perf_counter()
                        if remaining <= 0:
                            break
                        time.sleep(min(remaining, 0.001))
                transport.send_pro_rumble(
                    CONNECTION_LF_FREQUENCY,
                    0,
                    CONNECTION_HF_FREQUENCY,
                    0,
                )

            rumble_thread = threading.Thread(
                target=rumble_worker,
                daemon=True,
                name="ProbeRumbleStress",
            )
            rumble_thread.start()
        print(
            {
                "ready": True,
                "mode": mode,
                "seconds": seconds,
                "inline": inline,
                "rumble_stress": rumble_stress,
                "rumble_profile": rumble_profile,
            },
            flush=True,
        )
        started = time.monotonic()
        marks = sorted(set(mark for mark in (10.0, 20.0, seconds) if mark <= seconds))
        for mark in marks:
            while time.monotonic() - started < mark and not disconnected.is_set():
                time.sleep(0.05)
            elapsed = time.monotonic() - started
            with lock:
                result = {
                    "elapsed_s": round(elapsed, 1),
                    "submitted": submitted_count,
                    "processed": callback_count,
                    "rate_hz": round(callback_count / elapsed, 1),
                    "transport_rate_hz": getattr(transport, "polling_rate_hz", None),
                    "inline": dispatcher.inline_reports,
                    "queued": dispatcher.queued_reports,
                    "backlog_batches": dispatcher.backlog_batches,
                    "dropped_analog": dispatcher.dropped_reports,
                    "expected_edges": expected_edges,
                    "output_edges": output_edges,
                    "mapping_mismatches": mapping_mismatches,
                    "strict_button_check": strict_button_check,
                    "rumble_submitted": rumble_submitted,
                    "rumble_write_failures": getattr(
                        transport, "_rumble_write_failures", None
                    ),
                    "rumble": (
                        transport.get_rumble_diagnostics()
                        if hasattr(transport, "get_rumble_diagnostics")
                        else None
                    ),
                    "recovery_requested": bool(
                        getattr(transport, "_recover_requested", threading.Event()).is_set()
                    ),
                    "queue_ms": distribution(queue_ms),
                    "parse_ms": distribution(parse_ms),
                    "xinput_ms": distribution(xinput_ms),
                    "processing_ms": distribution(processing_ms),
                    "pipeline_ms": distribution(pipeline_ms),
                    "arrival_interval_ms": distribution(arrival_interval_ms),
                    "report_interval_ms": distribution(report_interval_ms),
                    "transport_variation_ms": distribution(transport_variation_ms),
                }
            print(result, flush=True)
            if disconnected.is_set():
                break
    finally:
        sampling = False
        rumble_stop.set()
        if rumble_thread is not None:
            rumble_thread.join(timeout=1.0)
        transport.input_callback = None
        transport.close()
        dispatcher.stop()
        xinput.close()
        if gc_initially_enabled and not gc.isenabled():
            gc.enable()
            gc.collect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("bluetooth", "wired"), required=True)
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--inline", action="store_true")
    parser.add_argument("--rumble-stress", action="store_true")
    parser.add_argument(
        "--rumble-profile",
        choices=("priority", "audio", "normal"),
        default="priority",
    )
    args = parser.parse_args()
    main(
        args.mode,
        max(1.0, args.seconds),
        args.inline,
        args.rumble_stress,
        args.rumble_profile,
    )
