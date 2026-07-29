"""30-second COM/ViGEm Fast Path probe for controlled regression testing."""

import argparse
import configparser
import gc
import statistics
import struct
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config_utils import CONFIG_PATH, load_stick_calibration
from esp32_bridge import ESP32Bridge
from input_dispatcher import InputDispatcher
from rumble_protocol import (
    CONNECTION_HF_FREQUENCY,
    CONNECTION_LF_FREQUENCY,
    STRESS_AMPLITUDES,
)
from switch2_input import SWITCH_BUTTONS, parse_input_report
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


def _remember_submission(entries, payload, timestamp_ns, limit=4096):
    """Retain identity; the probe lock serializes remember/take operations."""
    key = id(payload)
    entries[key] = (payload, int(timestamp_ns))
    entries.move_to_end(key)
    while len(entries) > max(1, int(limit)):
        entries.popitem(last=False)


def _take_submission(entries, payload):
    entry = entries.pop(id(payload), None)
    if entry is None or entry[0] is not payload:
        return None
    return entry[1]


def main(port="COM3", seconds=30.0, rumble_stress=False):
    config = configparser.ConfigParser()
    path = (
        CONFIG_PATH if CONFIG_PATH.is_file()
        else ROOT / "src" / "profiles" / "System Default.ini"
    )
    if not config.read(path, encoding="utf-8"):
        raise RuntimeError(f"Could not read configuration: {path}")

    baudrate = config.getint("serial", "baudrate", fallback=2_000_000)
    bridge = ESP32Bridge(port, baudrate)
    xinput = XInputController(config, load_stick_calibration(config))
    connected = threading.Event()
    disconnected = threading.Event()
    lock = threading.Lock()
    submitted_at = OrderedDict()
    sampling = False
    submitted_count = 0
    callback_count = 0
    raw_edges = 0
    processed_raw_edges = 0
    output_edges = 0
    expected_edges = 0
    mapping_mismatches = 0
    last_submitted_buttons = None
    last_processed_buttons = None
    last_output = None
    last_expected = None
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

    def on_input(payload):
        nonlocal callback_count, processed_raw_edges
        nonlocal output_edges, expected_edges
        nonlocal mapping_mismatches, last_output, last_expected
        nonlocal last_processed_buttons
        callback_started = time.perf_counter_ns()
        state = parse_input_report(payload)
        parsed = time.perf_counter_ns()
        if state is None:
            with lock:
                _take_submission(submitted_at, payload)
            return
        xinput.update(state)
        finished = time.perf_counter_ns()
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
            submitted = _take_submission(submitted_at, payload)
            if submitted is None:
                return
            callback_count += 1
            if last_processed_buttons is not None:
                processed_raw_edges += (
                    state.buttons ^ last_processed_buttons
                ).bit_count()
            last_processed_buttons = state.buttons
            if last_output is not None:
                output_edges += (output ^ last_output).bit_count()
            if last_expected is not None:
                expected_edges += (expected ^ last_expected).bit_count()
            if (
                output != expected
                or int(xinput.pad.report.bLeftTrigger) != expected_lt
                or int(xinput.pad.report.bRightTrigger) != expected_rt
            ):
                mapping_mismatches += 1
            last_output = output
            last_expected = expected
            parse_ms.append((parsed - callback_started) / 1_000_000.0)
            xinput_ms.append((finished - parsed) / 1_000_000.0)
            processing_ms.append((finished - callback_started) / 1_000_000.0)
            queue_ms.append((callback_started - submitted) / 1_000_000.0)
            pipeline_ms.append((finished - submitted) / 1_000_000.0)

    dispatcher = InputDispatcher(on_input, max_pending=3, inline_fast_path=True)

    class MeasuredSubmitter:
        @staticmethod
        def _snapshot(payload):
            # Keep one unique identity per logical submission. This probe-only
            # copy avoids ambiguity if a transport ever reuses a bytes object.
            return bytes(bytearray(payload))

        @staticmethod
        def _record(snapshot, arrived_ns=None, sample_arrival=True):
            nonlocal submitted_count, raw_edges
            nonlocal last_submitted_buttons
            nonlocal last_arrival_ns, last_report_time
            if not sampling:
                return
            if arrived_ns is None:
                arrived_ns = time.perf_counter_ns()
            with lock:
                submitted_count += 1
                _remember_submission(submitted_at, snapshot, arrived_ns)
                if len(snapshot) >= 16:
                    report_time, buttons = struct.unpack_from("<II", snapshot, 0)
                    if last_submitted_buttons is not None:
                        raw_edges += (
                            buttons ^ last_submitted_buttons
                        ).bit_count()
                    last_submitted_buttons = buttons
                    if sample_arrival and last_arrival_ns is not None:
                        arrival_delta = (
                            arrived_ns - last_arrival_ns
                        ) / 1_000_000.0
                        arrival_interval_ms.append(arrival_delta)
                        if last_report_time is not None:
                            report_delta = (
                                report_time - last_report_time
                            ) & 0xFFFFFFFF
                        else:
                            report_delta = None
                        if report_delta is not None and 0 < report_delta < 1000:
                            report_interval_ms.append(float(report_delta))
                            transport_variation_ms.append(
                                arrival_delta - report_delta
                            )
                    # Only the first report in a CDC drain batch has a real
                    # host-arrival sample. Keep report-time anchors aligned
                    # first-to-first as well, so transport variation compares
                    # equivalent batch boundaries instead of last-to-first.
                    if sample_arrival:
                        last_report_time = report_time
                if sample_arrival:
                    last_arrival_ns = arrived_ns

        def __call__(self, payload):
            self.submit(payload)

        def submit(self, payload):
            snapshot = self._snapshot(payload)
            self._record(snapshot)
            dispatcher.submit(snapshot)

        def submit_batch(self, payloads):
            snapshots = [
                self._snapshot(payload) for payload in payloads if payload
            ]
            if not snapshots:
                return
            # Serial drain exposes one host-arrival instant for the batch, not
            # an arrival instant for every contained BLE report. Use that same
            # instant for queue/pipeline latency and emit one arrival/jitter
            # sample, while still retaining every report for queue/pipeline
            # latency and raw-edge accounting.
            batch_arrived_ns = time.perf_counter_ns()
            for index, snapshot in enumerate(snapshots):
                self._record(
                    snapshot,
                    arrived_ns=batch_arrived_ns,
                    sample_arrival=(index == 0),
                )
            if len(snapshots) == 1:
                dispatcher.submit(snapshots[0])
                return
            dispatcher.submit_batch(snapshots)

    bridge.input_callback = MeasuredSubmitter()

    def on_connected():
        dispatcher.reset()
        connected.set()

    bridge.connected_callback = on_connected
    bridge.disconnected_callback = disconnected.set

    gc_initially_enabled = gc.isenabled()
    try:
        bridge.open()
        bridge.start_scan()
        if not connected.wait(20.0):
            raise RuntimeError("Controller did not connect within 20 seconds")
        time.sleep(2.0)
        if gc_initially_enabled:
            gc.disable()
        dispatcher.reset()
        sampling = True
        if rumble_stress:
            def rumble_worker():
                nonlocal rumble_submitted
                sequence = STRESS_AMPLITUDES
                index = 0
                next_submit = time.perf_counter()
                while not rumble_stop.is_set():
                    amplitude = sequence[index % len(sequence)]
                    index += 1
                    if bridge.send_pro_rumble_latest(
                        CONNECTION_LF_FREQUENCY,
                        amplitude,
                        CONNECTION_HF_FREQUENCY,
                        amplitude,
                        priority=False, force_zero=(amplitude == 0),
                    ):
                        rumble_submitted += 1
                    next_submit += 0.005
                    while not rumble_stop.is_set():
                        remaining = next_submit - time.perf_counter()
                        if remaining <= 0:
                            break
                        time.sleep(min(remaining, 0.001))
                bridge.send_pro_rumble_latest(
                    CONNECTION_LF_FREQUENCY,
                    0,
                    CONNECTION_HF_FREQUENCY,
                    0,
                    priority=True,
                    force_zero=True,
                )

            rumble_thread = threading.Thread(
                target=rumble_worker,
                daemon=True,
                name="ProbeESP32RumbleStress",
            )
            rumble_thread.start()
        print(
            {
                "ready": True,
                "port": port,
                "seconds": seconds,
                "rumble_stress": rumble_stress,
            },
            flush=True,
        )
        started = time.monotonic()
        for mark in (10.0, 20.0, seconds):
            if mark > seconds:
                continue
            while time.monotonic() - started < mark and not disconnected.is_set():
                time.sleep(0.05)
            elapsed = time.monotonic() - started
            with lock:
                result = {
                    "elapsed_s": round(elapsed, 1),
                    "submitted": submitted_count,
                    "received": callback_count,
                    "rate_hz": round(callback_count / elapsed, 1),
                    "inline": dispatcher.inline_reports,
                    "queued": dispatcher.queued_reports,
                    "backlog_batches": dispatcher.backlog_batches,
                    "dropped_analog": dispatcher.dropped_reports,
                    "raw_edges": raw_edges,
                    "processed_raw_edges": processed_raw_edges,
                    "expected_edges": expected_edges,
                    "output_edges": output_edges,
                    "mapping_mismatches": mapping_mismatches,
                    "queue_ms": distribution(queue_ms),
                    "parse_ms": distribution(parse_ms),
                    "xinput_ms": distribution(xinput_ms),
                    "processing_ms": distribution(processing_ms),
                    "pipeline_ms": distribution(pipeline_ms),
                    "arrival_interval_ms": distribution(arrival_interval_ms),
                    "report_interval_ms": distribution(report_interval_ms),
                    "transport_variation_ms": distribution(transport_variation_ms),
                    "rumble_submitted_by_probe": rumble_submitted,
                    "rumble": bridge.get_rumble_diagnostics(),
                }
            print(result, flush=True)
            if disconnected.is_set():
                break
    finally:
        sampling = False
        rumble_stop.set()
        if rumble_thread is not None:
            rumble_thread.join(timeout=1.0)
        bridge.input_callback = None
        bridge.close()
        dispatcher.stop()
        xinput.close()
        if gc_initially_enabled and not gc.isenabled():
            gc.enable()
            gc.collect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="COM3")
    parser.add_argument("--seconds", type=float, default=30.0)
    parser.add_argument("--rumble-stress", action="store_true")
    args = parser.parse_args()
    main(args.port, max(1.0, args.seconds), args.rumble_stress)
