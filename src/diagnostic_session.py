"""Timed controller diagnostics and AI-friendly plain-text log formatting."""

from __future__ import annotations

import json
import math
import threading
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import serial

from esp32_detection import (
    S2P_FIRMWARE_PRODUCT,
    S2P_FIRMWARE_PROFILE,
    S2P_PROTOCOL,
    S2P_PROTOCOL_VERSION,
    find_esp32_port,
)


DIAGNOSTIC_LOG_VERSION = 1
DEFAULT_DIAGNOSTIC_SECONDS = 60
DIAGNOSTIC_SAMPLE_INTERVAL_SECONDS = 0.25
DIAGNOSTIC_FIRMWARE_RESPONSE_TIMEOUT_SECONDS = 3.0


def diagnostic_firmware_needs_update(
    firmware, controller_status, elapsed_seconds,
):
    """Return whether an ESP32 diagnostic session requires S2P-FW 1.0.0.

    Older firmware has no ``capabilities`` response, so a bridge session has
    to use a short response timeout rather than fail while constructing the
    tester window.
    """
    firmware = firmware if isinstance(firmware, dict) else {}
    controller_status = (
        controller_status if isinstance(controller_status, dict) else {}
    )
    capabilities = firmware.get("capabilities")
    if isinstance(capabilities, dict) and capabilities:
        features = capabilities.get("features") or {}
        return not (
            capabilities.get("product") == S2P_FIRMWARE_PRODUCT
            and capabilities.get("protocol") == S2P_PROTOCOL
            and capabilities.get("protocol_version") == S2P_PROTOCOL_VERSION
            and capabilities.get("profile") in (None, S2P_FIRMWARE_PROFILE)
            and bool(features.get("diagnostics"))
        )

    error = str(firmware.get("error") or "").lower()
    if firmware.get("state") == "unavailable" and "capabilities" in error:
        return True

    return (
        controller_status.get("mode") == "esp32"
        and controller_status.get("state") == "connected"
        and float(elapsed_seconds) >= DIAGNOSTIC_FIRMWARE_RESPONSE_TIMEOUT_SECONDS
    )


def read_controller_status(path):
    """Return a fresh connector status document, or an empty mapping."""
    try:
        status = json.loads(Path(path).read_text(encoding="utf-8"))
        updated_at = float(status.get("updated_at", 0.0) or 0.0)
        if time.time() - updated_at > 3.0:
            return {}
        return status if isinstance(status, dict) else {}
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}


def _finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _percentile(values, fraction):
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return None
    index = int(round((len(ordered) - 1) * float(fraction)))
    return ordered[max(0, min(len(ordered) - 1, index))]


def _json_value(value):
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


class DiagnosticSession:
    """Accumulate bounded, low-rate snapshots without driving the controller."""

    def __init__(self, duration_seconds=DEFAULT_DIAGNOSTIC_SECONDS):
        self.duration_seconds = max(1.0, float(duration_seconds))
        self.running = False
        self.completed = False
        self.started_monotonic = None
        self.ended_monotonic = None
        self.started_at = None
        self.ended_at = None
        self.stop_reason = None
        self.samples = []
        self.events = []
        self.input_intervals_ms = []
        self.ble_raw_report_rates_hz = []
        self._last_input_timestamp_ns = None
        self._last_sample_at = 0.0
        self._last_ble_input_reports = None
        self._last_ble_input_sample_at = None
        self.rumble_peak_input = [0, 0]
        self.rumble_peak_output = [0, 0]

    def start(self, now=None, wall_time=None):
        now = time.monotonic() if now is None else float(now)
        wall_time = time.time() if wall_time is None else float(wall_time)
        self.running = True
        self.completed = False
        self.started_monotonic = now
        self.ended_monotonic = None
        self.started_at = datetime.fromtimestamp(
            wall_time, tz=timezone.utc
        ).astimezone().isoformat(timespec="seconds")
        self.ended_at = None
        self.stop_reason = None
        self.samples.clear()
        self.events.clear()
        self.input_intervals_ms.clear()
        self.ble_raw_report_rates_hz.clear()
        self._last_input_timestamp_ns = None
        self._last_sample_at = 0.0
        self._last_ble_input_reports = None
        self._last_ble_input_sample_at = None
        self.rumble_peak_input[:] = (0, 0)
        self.rumble_peak_output[:] = (0, 0)
        self.events.append({"t_ms": 0, "type": "session_started"})

    def elapsed(self, now=None):
        if self.started_monotonic is None:
            return 0.0
        now = time.monotonic() if now is None else float(now)
        end = self.ended_monotonic
        if end is not None:
            now = min(now, end)
        return max(0.0, now - self.started_monotonic)

    def remaining(self, now=None):
        return max(0.0, self.duration_seconds - self.elapsed(now))

    def due(self, now=None):
        now = time.monotonic() if now is None else float(now)
        return (
            self.running
            and now - self._last_sample_at
            >= DIAGNOSTIC_SAMPLE_INTERVAL_SECONDS
        )

    def add_sample(self, telemetry=None, status=None, firmware=None, now=None):
        if not self.running:
            return False
        now = time.monotonic() if now is None else float(now)
        telemetry = telemetry if isinstance(telemetry, dict) else {}
        status = status if isinstance(status, dict) else {}
        firmware = firmware if isinstance(firmware, dict) else {}

        report_interval_ms = _finite_number(
            telemetry.get(
                "report_interval_ms", status.get("report_delta")
            )
        )
        if (
            report_interval_ms is not None
            and 0.25 < report_interval_ms < 10_000.0
        ):
            self.input_intervals_ms.append(report_interval_ms)

        latency = firmware.get("latency_status") or {}
        raw_reports = _finite_number(latency.get("ble_input_reports"))
        if raw_reports is not None and raw_reports >= 0:
            previous_reports = self._last_ble_input_reports
            previous_at = self._last_ble_input_sample_at
            elapsed = (
                now - previous_at
                if previous_at is not None else 0.0
            )
            delta = raw_reports - previous_reports if previous_reports is not None else 0
            if elapsed > 0.0 and delta >= 0:
                self.ble_raw_report_rates_hz.append(delta / elapsed)
            self._last_ble_input_reports = raw_reports
            self._last_ble_input_sample_at = now

        rumble = {}
        for source in (
            status.get("rumble"),
            firmware.get("rumble_status"),
        ):
            if isinstance(source, dict):
                rumble.update(source)
        raw = (
            rumble.get("input")
            or rumble.get("raw_motors")
            or rumble.get("latest_input")
            or (0, 0)
        )
        output = (
            rumble.get("output")
            or rumble.get("latest_output")
            or (0, 0)
        )
        for index in range(2):
            try:
                self.rumble_peak_input[index] = max(
                    self.rumble_peak_input[index], int(raw[index])
                )
            except (IndexError, TypeError, ValueError):
                pass
            try:
                self.rumble_peak_output[index] = max(
                    self.rumble_peak_output[index], int(output[index])
                )
            except (IndexError, TypeError, ValueError):
                pass

        sample = {
            "t_ms": int(round(self.elapsed(now) * 1000.0)),
            "telemetry": deepcopy(telemetry),
            "status": deepcopy(status),
            "firmware": deepcopy(firmware),
        }
        self.samples.append(sample)
        self._last_sample_at = now
        if self.elapsed(now) >= self.duration_seconds:
            self.stop("completed", now=now)
        return True

    def add_event(self, event_type, **details):
        if self.started_monotonic is None:
            return
        event = {
            "t_ms": int(round(self.elapsed() * 1000.0)),
            "type": str(event_type),
        }
        event.update(details)
        self.events.append(event)

    def stop(self, reason="stopped_by_user", now=None, wall_time=None):
        if self.started_monotonic is None:
            return False
        now = time.monotonic() if now is None else float(now)
        wall_time = time.time() if wall_time is None else float(wall_time)
        was_running = self.running
        self.running = False
        self.ended_monotonic = now
        self.completed = reason == "completed"
        self.stop_reason = str(reason)
        self.ended_at = datetime.fromtimestamp(
            wall_time, tz=timezone.utc
        ).astimezone().isoformat(timespec="seconds")
        self.events.append({
            "t_ms": int(round(self.elapsed(now) * 1000.0)),
            "type": "session_stopped",
            "reason": self.stop_reason,
        })
        return was_running

    def summary(self):
        latest = self.samples[-1] if self.samples else {}
        status = latest.get("status") or {}
        firmware = latest.get("firmware") or {}
        capabilities = firmware.get("capabilities") or {}
        runtime = firmware.get("runtime_status") or {}
        mode = (
            capabilities.get("mode")
            or status.get("mode")
            or "unknown"
        )
        rates = [
            number for number in (
                _finite_number((sample.get("telemetry") or {}).get(
                    "source_rate_hz"
                ))
                for sample in self.samples
            )
            if number is not None
        ]
        warnings = []
        notices = []
        latency = firmware.get("latency_status") or {}
        ble_input_reports = int(
            latency.get("ble_input_reports", 0) or 0
        )
        source_gap_events = int(
            latency.get("source_gap_events", 0) or 0
        )
        source_gap_ratio = (
            source_gap_events / ble_input_reports
            if ble_input_reports > 0 else None
        )
        if int(latency.get("notify_queue_drops", 0) or 0) > 0:
            warnings.append("FIRMWARE_NOTIFY_QUEUE_DROPS")
        if source_gap_events > 0:
            if source_gap_ratio is None or source_gap_ratio >= 0.01:
                warnings.append("INPUT_SOURCE_GAPS")
            else:
                notices.append("OCCASIONAL_INPUT_SOURCE_GAPS")
        rumble = status.get("rumble") or firmware.get("rumble_status") or {}
        if int(rumble.get("send_failures", 0) or 0) > 0:
            warnings.append("RUMBLE_SEND_FAILURES")
        sensor_mode = status.get("sensor_mode")
        if status.get("state") == "connected" and not sensor_mode:
            warnings.append("SENSOR_MODE_UNAVAILABLE")
        link_status = firmware.get("link_status") or {}
        ready_links = [
            link
            for link in (link_status.get("links") or ())
            if (
                isinstance(link, (list, tuple))
                and len(link) >= 6
                and bool(link[2])
            )
        ]
        primary_link = ready_links[0] if ready_links else None
        link_rssi_dbm = None
        link_interval_ms = None
        controller_mac = None
        if primary_link is not None:
            controller_mac = primary_link[1]
            link_interval_ms = _finite_number(primary_link[3])
            candidate_rssi = _finite_number(primary_link[4])
            if candidate_rssi is not None and -127 <= candidate_rssi <= 20:
                link_rssi_dbm = int(candidate_rssi)
                if link_rssi_dbm < -85:
                    warnings.append("BLE_SIGNAL_VERY_WEAK")
                elif link_rssi_dbm < -75:
                    notices.append("BLE_SIGNAL_WEAK")
        raw_rate_average = (
            sum(self.ble_raw_report_rates_hz)
            / len(self.ble_raw_report_rates_hz)
            if self.ble_raw_report_rates_hz else None
        )
        expected_rate_hz = (
            1000.0 / link_interval_ms
            if link_interval_ms and link_interval_ms > 0 else None
        )
        if (
            raw_rate_average is not None
            and expected_rate_hz is not None
            and raw_rate_average < expected_rate_hz * 0.80
        ):
            warnings.append("BLE_RAW_REPORT_RATE_LOW")
        setup_errors = firmware.get("setup_errors") or ()
        if setup_errors:
            warnings.append("FIRMWARE_SELF_TEST_INCOMPLETE")
        return {
            "result": self.stop_reason or (
                "running" if self.running else "not_started"
            ),
            "mode": mode,
            "connector_state": status.get("state"),
            "firmware_product": capabilities.get("product"),
            "firmware_version": capabilities.get("version"),
            "protocol": capabilities.get("protocol"),
            "protocol_version": capabilities.get("protocol_version"),
            "sensor_mode": status.get("sensor_mode"),
            "gyro_calibration_state": status.get(
                "gyro_calibration_state"
            ),
            "gyro_bias_samples": int(_finite_number(
                status.get("gyro_bias_samples")
                or runtime.get("gyro_bias_samples")
                or 0
            ) or 0),
            "samples": len(self.samples),
            "source_rate_hz_avg": (
                sum(rates) / len(rates) if rates else None
            ),
            "ble_raw_report_rate_hz_avg": raw_rate_average,
            "ble_raw_report_rate_hz_p50": _percentile(
                self.ble_raw_report_rates_hz, 0.50
            ),
            "ble_raw_report_rate_hz_p95": _percentile(
                self.ble_raw_report_rates_hz, 0.95
            ),
            "ble_raw_report_rate_hz_p99": _percentile(
                self.ble_raw_report_rates_hz, 0.99
            ),
            "input_interval_ms_p50": _percentile(
                self.input_intervals_ms, 0.50
            ),
            "input_interval_ms_p95": _percentile(
                self.input_intervals_ms, 0.95
            ),
            "input_interval_ms_p99": _percentile(
                self.input_intervals_ms, 0.99
            ),
            "input_interval_ms_max": (
                max(self.input_intervals_ms)
                if self.input_intervals_ms else None
            ),
            "ble_input_reports": ble_input_reports,
            "source_gap_events": source_gap_events,
            "source_gap_ratio": source_gap_ratio,
            "source_gap_max_ms": int(
                latency.get("source_gap_max_ms", 0) or 0
            ),
            "notify_queue_drops": int(
                latency.get("notify_queue_drops", 0) or 0
            ),
            "usb_wait_avg_us": int(
                latency.get("usb_wait_avg_us", 0) or 0
            ),
            "usb_wait_max_us": int(
                latency.get("usb_wait_max_us", 0) or 0
            ),
            "bridge_mac": link_status.get("bridge_mac"),
            "controller_mac": controller_mac,
            "link_rssi_dbm": link_rssi_dbm,
            "link_interval_ms": link_interval_ms,
            "expected_ble_rate_hz": expected_rate_hz,
            "self_test_replies": len(firmware.get("self_tests") or ()),
            "self_test_failures": len(setup_errors),
            "rumble_peak_input": list(self.rumble_peak_input),
            "rumble_peak_output": list(self.rumble_peak_output),
            "warnings": warnings,
            "notices": notices,
            "verdict": "WARN" if warnings else "OK",
        }

    def format_log(self):
        summary = self.summary()
        latest = self.samples[-1] if self.samples else {}
        status = latest.get("status") or {}
        firmware = latest.get("firmware") or {}
        lines = [
            "S2P_XINPUT_LITE_DIAGNOSTIC_LOG",
            f"LOG_VERSION={DIAGNOSTIC_LOG_VERSION}",
            "",
            "[SESSION]",
            f"started_at={self.started_at or 'unknown'}",
            f"ended_at={self.ended_at or 'unknown'}",
            f"requested_duration_s={self.duration_seconds:g}",
            f"result={summary['result']}",
            f"mode={summary['mode']}",
            f"verdict={summary['verdict']}",
            f"warnings={_json_value(summary['warnings'])}",
            "",
            "[SUMMARY_JSON]",
            _json_value(summary),
            "",
            "[LATEST_CONNECTOR_STATUS_JSON]",
            _json_value(status),
            "",
            "[LATEST_FIRMWARE_STATUS_JSON]",
            _json_value(firmware),
            "",
            "[EVENTS_JSONL]",
        ]
        lines.extend(_json_value(event) for event in self.events)
        lines.extend(("", "[SAMPLES_JSONL]"))
        lines.extend(_json_value(sample) for sample in self.samples)
        lines.extend((
            "",
            "[AI_ANALYSIS_HINT]",
            "Check mode-specific availability before judging missing fields.",
            "Prioritize queue drops, source gaps, send failures, extreme "
            "intervals, unstable sensor calibration, and rumble values that "
            "remain non-zero after the session.",
            "",
        ))
        return "\n".join(lines)


class ESP32DiagnosticReader:
    """Poll the standalone firmware CDC interface outside Tk's UI thread."""

    QUERY_COMMANDS = (
        ("runtime status", "runtime_status"),
        ("latency status", "latency_status"),
        ("ble timing", "ble_timing"),
        ("link status", "link_status"),
        ("rumble status", "rumble_status"),
        ("profile status", "profile_status"),
    )
    SELF_TEST_COMMANDS = (
        ("algorithm test stick L 2048 2048 0 2048 2048 8", "algorithm_test"),
        ("algorithm test stick R 2048 2048 0 2048 2048 8", "algorithm_test"),
        ("algorithm test direction L 0 0 0 0", "algorithm_test"),
        ("gyro test reset", "gyro_test"),
    )

    def __init__(self, baudrate=2_000_000):
        self.baudrate = int(baudrate)
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread = None
        self._snapshot = {
            "state": "idle",
            "port": None,
            "error": None,
        }

    @property
    def active(self):
        return bool(self._thread and self._thread.is_alive())

    def start(self):
        with self._lifecycle_lock:
            if self.active:
                return False
            self._stop_event.clear()
            self._set_snapshot(state="opening", error=None)
            thread = threading.Thread(
                target=self._run,
                daemon=True,
                name="ESP32DiagnosticReader",
            )
            self._thread = thread
            thread.start()
            return True

    def stop(self, timeout=1.0):
        with self._lifecycle_lock:
            self._stop_event.set()
            thread = self._thread
        if (
            thread is not None
            and thread.is_alive()
            and threading.current_thread() is not thread
        ):
            thread.join(max(0.0, float(timeout)))
        alive = bool(thread is not None and thread.is_alive())
        with self._lifecycle_lock:
            if self._thread is thread and not alive:
                self._thread = None
        return not alive

    def snapshot(self):
        with self._lock:
            return deepcopy(self._snapshot)

    def _set_snapshot(self, **changes):
        with self._lock:
            self._snapshot.update(changes)
            self._snapshot["updated_at"] = time.time()

    def _read_response(self, port, expected, timeout=1.0):
        deadline = time.monotonic() + float(timeout)
        while time.monotonic() < deadline:
            if self._stop_event.is_set():
                raise InterruptedError("diagnostic reader stopped")
            line = port.readline()
            if not line:
                continue
            text = line.decode("utf-8", errors="ignore").strip()
            if "{" not in text or "}" not in text:
                continue
            try:
                response = json.loads(
                    text[text.find("{"):text.rfind("}") + 1]
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if response.get("cmd") == expected:
                return response
        raise TimeoutError(f"ESP32 did not answer {expected}")

    def _query(self, port, command, expected, timeout=1.0):
        port.write((command + "\n").encode("ascii"))
        port.flush()
        return self._read_response(port, expected, timeout=timeout)

    def _run(self):
        try:
            port_name = find_esp32_port(self.baudrate)
            if not port_name:
                raise OSError("compatible ESP32 diagnostic port not found")
            with serial.Serial(
                port_name,
                self.baudrate,
                timeout=0.10,
                write_timeout=0.50,
            ) as port:
                port.reset_input_buffer()
                capabilities = self._query(
                    port, "capabilities", "capabilities", timeout=1.5
                )
                mode = str(capabilities.get("mode") or "unknown")
                self._set_snapshot(
                    state="running",
                    port=port_name,
                    mode=mode,
                    capabilities=capabilities,
                    error=None,
                )
                if mode.startswith("standalone"):
                    setup_errors = []
                    for command, expected in (
                        ("latency reset", "latency_reset"),
                        ("rumble reset", "rumble_reset"),
                    ):
                        try:
                            self._query(port, command, expected)
                        except TimeoutError as exc:
                            setup_errors.append(str(exc))
                    self_tests = []
                    for command, expected in self.SELF_TEST_COMMANDS:
                        try:
                            self_tests.append(
                                self._query(port, command, expected)
                            )
                        except TimeoutError as exc:
                            setup_errors.append(str(exc))
                    self._set_snapshot(
                        self_tests=self_tests,
                        setup_errors=setup_errors,
                    )
                while not self._stop_event.wait(0.25):
                    updates = {}
                    for command, expected in self.QUERY_COMMANDS:
                        try:
                            updates[expected] = self._query(
                                port, command, expected
                            )
                        except TimeoutError:
                            continue
                    if updates:
                        self._set_snapshot(**updates)
        except (
            OSError,
            TimeoutError,
            serial.SerialException,
            ValueError,
        ) as exc:
            self._set_snapshot(state="unavailable", error=str(exc))
        finally:
            if self._stop_event.is_set():
                self._set_snapshot(state="stopped")
