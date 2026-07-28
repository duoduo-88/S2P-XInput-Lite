"""Optional effective Raw HID report-rate analysis for the gamepad tester.

This module is kept separate from the generic Raw HID probe so the feature can
be reviewed and reverted without replacing the existing finite timing helper.
"""

from __future__ import annotations

import base64
import hashlib
import math
import mmap
import os
import subprocess
import threading
import time
import tkinter as tk
import uuid
from collections import Counter
from pathlib import Path
from tkinter import ttk

import gamepad_test_window as tester
import raw_hid_probe as raw_hid


STREAM_PROBE_EXECUTABLE = Path(__file__).with_name("raw_hid_stream_probe.exe")
STREAM_PROBE_PAYLOAD = Path(__file__).with_name("raw_hid_stream_probe.exe.b64")
STREAM_PROBE_SHA256 = (
    "27c0a9ff6e3442439958acd23330c0e35e3e6dab597f20d18f7fa1488cb62241"
)

EN_TEXT = {
    "有效回報率": "Effective Report Rate",
    "判讀：量測時請持續以中等速度，大幅繞圈轉動一支搖桿。\n完成後會同時判讀回報穩定度與有效狀態更新率。":
        "Reading: continuously rotate one stick in wide circles at a moderate "
        "speed during measurement.\nThe result will evaluate both delivery "
        "stability and effective state update rate.",
    "判讀：量測中，請持續以中等速度，大幅繞圈轉動一支搖桿。\n請避免長時間停在中心或壓住外圈不動。":
        "Reading: keep rotating one stick in wide circles at a moderate "
        "speed.\nAvoid holding it at the centre or against the outer edge.",
    "判讀：量測失敗，無法分析回報穩定度與有效狀態更新率。":
        "Reading: measurement failed; delivery stability and effective state "
        "update rate could not be analysed.",
    "回報間隔資料不足，無法判讀穩定度。":
        "There is not enough report-interval data to evaluate stability.",
    "主要回報間隔接近預期值，P95／P99 尾端也集中，回報穩定。":
        "The main report interval is near the expected value, and the P95/P99 "
        "tail is concentrated; delivery is stable.",
    "主要回報間隔接近預期值，尾端有少量跨週期回報，未見持續堆積。":
        "The main interval is near the expected value. A small tail crosses "
        "one period, with no sign of continuing backlog.",
    "回報間隔分散或偏離預期值，存在較明顯的排程波動。":
        "Report intervals are dispersed or differ from the expected value, "
        "indicating more noticeable scheduling variation.",
    "無法解析標準搖桿軸，因此本次不提供有效回報率。":
        "Standard stick axes could not be parsed, so no effective report rate "
        "is available for this measurement.",
    "搖桿活動不足，請持續大幅繞圈後重新量測，避免把靜止資料判成重複回報。":
        "Stick movement was insufficient. Repeat the test with continuous wide "
        "circles so stationary data is not mistaken for repeated reports.",
    "偵測到規律重複狀態：每個搖桿狀態通常維持 {count} 筆；有效更新率明顯低於 HID 回報率。":
        "Regular repeated states were detected: each stick state usually lasts "
        "{count} reports; the effective update rate is well below the HID "
        "report rate.",
    "有效狀態更新率接近 HID 回報率，未發現明顯規律重複。":
        "The effective state update rate is close to the HID report rate; no "
        "clear regular repetition was detected.",
    "有效狀態更新率略低於 HID 回報率，未發現固定重複規律。":
        "The effective state update rate is slightly below the HID report rate; "
        "no fixed repetition pattern was detected.",
    "有效狀態更新率明顯較低，但未發現固定重複規律；可能受軸解析度、濾波或轉動速度影響。":
        "The effective state update rate is substantially lower, but no fixed "
        "repetition pattern was found; axis resolution, filtering, or rotation "
        "speed may be contributing.",
    "判讀：{cadence}\n{effective}": "Reading: {cadence}\n{effective}",
}


def translate_effective_rate(text, language):
    """Translate strings owned by this optional feature."""
    if str(language or "").lower() == "en":
        return EN_TEXT.get(text)
    return None


def _fixed_stream_probe_supported(device_path):
    folded = str(device_path or "").upper()
    return (
        "&IG_" in folded
        or "VID_CAFE&PID_4020" in folded
        or "ROOT#VIGEM" in folded
    )


def _materialize_fixed_stream_probe(
    executable=STREAM_PROBE_EXECUTABLE,
    payload=STREAM_PROBE_PAYLOAD,
):
    """Create the reviewed helper EXE from its checked base64 payload."""
    executable = Path(executable)
    if executable.is_file():
        try:
            if hashlib.sha256(executable.read_bytes()).hexdigest() == STREAM_PROBE_SHA256:
                return executable
        except OSError:
            pass
    payload = Path(payload)
    if not payload.is_file():
        return executable
    temporary = executable.with_name(executable.name + ".tmp")
    try:
        binary = base64.b64decode(
            payload.read_text(encoding="ascii").strip(),
            validate=True,
        )
        if hashlib.sha256(binary).hexdigest() != STREAM_PROBE_SHA256:
            return executable
        temporary.write_bytes(binary)
        os.replace(temporary, executable)
    except (OSError, UnicodeError, ValueError):
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
    return executable


class HybridRawHidStreamClient(raw_hid.RawHidStreamClient):
    """Use the fixed helper only for verified XInput-style HID collections."""

    def __init__(
        self,
        executable=raw_hid.PROBE_EXECUTABLE,
        process_factory=subprocess.Popen,
        capacity=raw_hid.STREAM_CAPACITY,
        fixed_executable=STREAM_PROBE_EXECUTABLE,
    ):
        super().__init__(
            executable=executable,
            process_factory=process_factory,
            capacity=capacity,
        )
        self.fixed_executable = Path(fixed_executable)

    @property
    def available(self):
        return bool(
            self.executable.is_file()
            or self.fixed_executable.is_file()
            or STREAM_PROBE_PAYLOAD.is_file()
        )

    def start(self, device_path):
        with self._lock:
            if self._process is not None and self._process.poll() is None:
                return False
            mapping_name = "Local\\S2PRawHidStream_" + uuid.uuid4().hex
            mapping_size = (
                raw_hid.STREAM_HEADER.size
                + self.capacity * raw_hid.STREAM_SLOT.size
            )
            helper = self.executable
            if _fixed_stream_probe_supported(device_path):
                fixed = _materialize_fixed_stream_probe(self.fixed_executable)
                if fixed.is_file():
                    helper = fixed
            try:
                mapping = mmap.mmap(
                    -1,
                    mapping_size,
                    tagname=mapping_name,
                    access=mmap.ACCESS_WRITE,
                )
                process = self.process_factory(
                    [
                        str(helper),
                        "--stream",
                        str(device_path),
                        "--mapping",
                        mapping_name,
                        "--capacity",
                        str(self.capacity),
                    ],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            except (OSError, subprocess.SubprocessError, TypeError):
                try:
                    mapping.close()
                except (NameError, OSError):
                    pass
                return False
            self._mapping_name = mapping_name
            self._mapping = mapping
            self._process = process
            return True


class _StickUpdateTracker:
    """Track exact repeated stick states and verify deliberate circular motion."""

    def __init__(self):
        self.samples = 0
        self.effective_updates = 0
        self.last = None
        self.current_run = 0
        self.run_lengths = Counter()
        self.min_x = math.inf
        self.max_x = -math.inf
        self.min_y = math.inf
        self.max_y = -math.inf
        self.path = 0.0
        self.turn_radians = 0.0
        self.last_angle = None
        self.last_radius = 0.0

    def add(self, point):
        x, y = float(point[0]), float(point[1])
        current = (x, y)
        self.samples += 1
        self.min_x = min(self.min_x, x)
        self.max_x = max(self.max_x, x)
        self.min_y = min(self.min_y, y)
        self.max_y = max(self.max_y, y)
        radius = math.hypot(x, y)
        angle = math.atan2(y, x) if radius >= 0.20 else None
        if self.last is None:
            self.effective_updates = 1
            self.current_run = 1
        elif current == self.last:
            self.current_run += 1
        else:
            self.run_lengths[self.current_run] += 1
            self.current_run = 1
            self.effective_updates += 1
            self.path += math.hypot(x - self.last[0], y - self.last[1])
        if angle is not None and self.last_angle is not None and self.last_radius >= 0.20:
            delta = (angle - self.last_angle + math.pi) % (2.0 * math.pi) - math.pi
            if abs(delta) <= 1.0:
                self.turn_radians += abs(delta)
        self.last_angle = angle
        self.last_radius = radius
        self.last = current

    def summary(self, duration_seconds):
        duration = max(0.001, float(duration_seconds))
        if self.samples <= 0:
            return {
                "samples": 0,
                "effective_rate_hz": 0.0,
                "span": 0.0,
                "turns": 0.0,
                "path_rate": 0.0,
                "dominant_run_length": 0,
                "dominant_run_share": 0.0,
            }
        runs = Counter(self.run_lengths)
        if self.current_run:
            runs[self.current_run] += 1
        total_runs = sum(runs.values())
        dominant_run_length = 0
        dominant_run_share = 0.0
        if total_runs:
            dominant_run_length, dominant_count = max(
                runs.items(), key=lambda item: (item[1], -item[0])
            )
            dominant_run_share = dominant_count / total_runs
        return {
            "samples": self.samples,
            "effective_rate_hz": max(0, self.effective_updates - 1) / duration,
            "span": min(self.max_x - self.min_x, self.max_y - self.min_y),
            "turns": self.turn_radians / (2.0 * math.pi),
            "path_rate": self.path / duration,
            "dominant_run_length": int(dominant_run_length),
            "dominant_run_share": float(dominant_run_share),
        }


def _histogram_percentile(histogram, sample_count, percentile):
    if sample_count <= 0:
        return 0
    target = max(1, math.ceil(sample_count * float(percentile)))
    cumulative = 0
    for index, count in enumerate(histogram):
        cumulative += count
        if cumulative >= target:
            return index
    return len(histogram) - 1


def _measurement_histogram_snapshot(histogram, interval_count):
    if interval_count <= 0:
        return 0, 0, 0, 50, (0,) * 80
    p50 = _histogram_percentile(histogram, interval_count, 0.50)
    p95 = _histogram_percentile(histogram, interval_count, 0.95)
    p99 = _histogram_percentile(histogram, interval_count, 0.99)
    maximum_us = min(
        len(histogram) - 1,
        max(50, ((p99 * 6 + 4) // 5 + 24) // 25 * 25),
    )
    counts = [0] * 80
    for interval_us, count in enumerate(histogram):
        if count:
            counts[min(79, interval_us * 80 // max(1, maximum_us))] += count
    return p50, p95, p99, maximum_us, tuple(counts)


class RawHidAnalysisClient:
    """Measure HID timing and exact stick-state updates from one native stream."""

    def __init__(
        self,
        executable=raw_hid.PROBE_EXECUTABLE,
        process_factory=subprocess.Popen,
    ):
        self.executable = Path(executable)
        self.process_factory = process_factory
        self._lock = threading.Lock()
        self._lifecycle_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._stream = None
        self._fallback = None
        self._snapshot = dict(raw_hid.DEFAULT_SNAPSHOT)

    @property
    def available(self):
        return self.executable.is_file()

    def start(self, device_path, duration_seconds):
        duration_seconds = max(1.0, min(300.0, float(duration_seconds)))
        with self._lifecycle_lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            with self._lock:
                self._snapshot = dict(raw_hid.DEFAULT_SNAPSHOT)
                self._snapshot.update({
                    "state": "opening",
                    "remaining_ms": duration_seconds * 1000.0,
                })
            stream = HybridRawHidStreamClient(
                executable=self.executable,
                process_factory=self.process_factory,
            )
            if not stream.start(device_path):
                self._finish_error(2)
                return False
            self._stream = stream
            self._fallback = None
            self._thread = threading.Thread(
                target=self._run,
                args=(stream, str(device_path), duration_seconds),
                daemon=True,
                name="RawHidAnalysisReader",
            )
            self._thread.start()
            return True

    def _run(self, stream, device_path, duration_seconds):
        opening_deadline = time.perf_counter() + 3.0
        while not self._stop.is_set():
            status = stream.status()
            if status.get("state") == "running":
                break
            if status.get("state") == "error" or time.perf_counter() >= opening_deadline:
                self._finish_error(int(status.get("error_code", 0) or 1))
                stream.stop(timeout=0.2)
                return
            time.sleep(0.01)
        if self._stop.is_set():
            stream.stop(timeout=0.2)
            with self._lock:
                self._snapshot.update(state="stopped", remaining_ms=0.0)
            return

        started_wall = time.perf_counter()
        warmup_deadline = started_wall + min(1.0, max(0.40, duration_seconds * 0.10))
        cursor = 0
        dropped_samples = 0
        warmup_samples = []
        last_status = stream.status()
        while not self._stop.is_set() and time.perf_counter() < warmup_deadline:
            last_status = stream.status()
            if last_status.get("state") == "error":
                self._finish_error(int(last_status.get("error_code", 0) or 1))
                stream.stop(timeout=0.2)
                return
            samples, cursor, dropped = stream.read_samples(cursor)
            dropped_samples += dropped
            if samples:
                warmup_samples.extend(samples)
                break
            time.sleep(0.005)

        if not warmup_samples:
            stream.stop(timeout=0.5)
            fallback = raw_hid.RawHidProbeClient(
                executable=self.executable,
                process_factory=self.process_factory,
            )
            self._fallback = fallback
            if not fallback.start(device_path, duration_seconds):
                self._finish_error(2)
                return
            while not self._stop.is_set():
                snapshot = fallback.read_snapshot()
                snapshot.update({
                    "axes_available": False,
                    "effective_rate_hz": 0.0,
                    "effective_ratio": 0.0,
                    "activity_sufficient": False,
                    "stick_reports": 0,
                    "ignored_reports": int(last_status.get("raw_reports", 0) or 0),
                })
                with self._lock:
                    self._snapshot = snapshot
                if snapshot.get("state") in raw_hid.TERMINAL_STATES:
                    return
                time.sleep(0.05)
            fallback.stop(timeout=0.5)
            return

        histogram = [0] * 100001
        interval_count = 0
        interval_sum_us = 0.0
        interval_min_us = math.inf
        interval_max_us = 0.0
        previous_timestamp = None
        first_timestamp = None
        last_timestamp = None
        trackers = {"left": _StickUpdateTracker(), "right": _StickUpdateTracker()}
        next_publish = started_wall
        terminal_state = "complete"
        pending_samples = tuple(warmup_samples)

        def consume(samples):
            nonlocal interval_count, interval_sum_us, interval_min_us
            nonlocal interval_max_us, previous_timestamp
            nonlocal first_timestamp, last_timestamp
            for timestamp, left, right, _sequence in samples:
                if first_timestamp is None:
                    first_timestamp = timestamp
                last_timestamp = timestamp
                if previous_timestamp is not None:
                    interval_us = max(0.0, (timestamp - previous_timestamp) * 1_000_000.0)
                    interval_count += 1
                    interval_sum_us += interval_us
                    interval_min_us = min(interval_min_us, interval_us)
                    interval_max_us = max(interval_max_us, interval_us)
                    histogram[min(100000, int(interval_us))] += 1
                previous_timestamp = timestamp
                trackers["left"].add(left)
                trackers["right"].add(right)

        while True:
            now = time.perf_counter()
            if self._stop.is_set():
                terminal_state = "stopped"
            elapsed_wall = max(0.0, now - started_wall)
            if terminal_state == "complete" and elapsed_wall >= duration_seconds:
                break
            if terminal_state == "stopped":
                break
            status = stream.status()
            if status.get("state") == "error":
                self._finish_error(int(status.get("error_code", 0) or 1))
                stream.stop(timeout=0.2)
                return
            if pending_samples:
                samples, pending_samples, dropped = pending_samples, (), 0
            else:
                samples, cursor, dropped = stream.read_samples(cursor)
            dropped_samples += dropped
            consume(samples)
            if now >= next_publish:
                self._publish(
                    "running", started_wall, duration_seconds,
                    first_timestamp, last_timestamp, histogram,
                    interval_count, interval_sum_us, interval_min_us,
                    interval_max_us, trackers, status, dropped_samples,
                )
                next_publish = now + 0.10
            if not samples:
                time.sleep(0.005)

        samples, cursor, dropped = stream.read_samples(cursor)
        dropped_samples += dropped
        consume(samples)
        stream_status = stream.status()
        stream.stop(timeout=0.5)
        self._publish(
            terminal_state, started_wall, duration_seconds,
            first_timestamp, last_timestamp, histogram,
            interval_count, interval_sum_us, interval_min_us,
            interval_max_us, trackers, stream_status, dropped_samples,
            finished=True,
        )

    def _publish(
        self, state, started_wall, duration_seconds,
        first_timestamp, last_timestamp, histogram,
        interval_count, interval_sum_us, interval_min_us,
        interval_max_us, trackers, stream_status, dropped_samples,
        finished=False,
    ):
        elapsed_wall = min(duration_seconds, max(0.0, time.perf_counter() - started_wall))
        sample_duration = (
            max(0.001, last_timestamp - first_timestamp)
            if first_timestamp is not None and last_timestamp is not None
            else max(0.001, elapsed_wall)
        )
        summaries = {name: tracker.summary(sample_duration) for name, tracker in trackers.items()}
        active_stick = max(
            summaries,
            key=lambda name: (
                summaries[name]["turns"] * 3.0
                + summaries[name]["span"] * 2.0
                + min(10.0, summaries[name]["path_rate"])
            ),
        )
        active = summaries[active_stick]
        reports = int(active["samples"])
        rate_hz = max(0, reports - 1) / sample_duration
        effective_rate_hz = active["effective_rate_hz"]
        effective_ratio = effective_rate_hz / rate_hz if rate_hz > 0 else 0.0
        activity_sufficient = bool(
            sample_duration >= 3.0
            and reports >= 200
            and active["span"] >= 0.70
            and active["turns"] >= 1.0
            and active["path_rate"] >= 0.80
        )
        dominant = active["dominant_run_length"]
        dominant_share = active["dominant_run_share"]
        expected_ratio = 1.0 / dominant if dominant >= 2 else 1.0
        regular_repeat = bool(
            activity_sufficient
            and dominant >= 2
            and dominant_share >= 0.55
            and abs(effective_ratio - expected_ratio)
                <= max(0.08, expected_ratio * 0.25)
        )
        p50, p95, p99, histogram_max, histogram_counts = (
            _measurement_histogram_snapshot(histogram, interval_count)
        )
        snapshot = dict(raw_hid.DEFAULT_SNAPSHOT)
        snapshot.update({
            "state": state,
            "error_code": 0,
            "elapsed_ms": elapsed_wall * 1000.0,
            "remaining_ms": 0.0 if finished else max(0.0, duration_seconds - elapsed_wall) * 1000.0,
            "reports": reports,
            "intervals": interval_count,
            "rate_hz": rate_hz,
            "p50_us": float(p50),
            "p95_us": float(p95),
            "p99_us": float(p99),
            "min_us": 0.0 if math.isinf(interval_min_us) else interval_min_us,
            "mean_us": interval_sum_us / interval_count if interval_count else 0.0,
            "max_us": interval_max_us,
            "histogram_max_us": histogram_max,
            "histogram_counts": histogram_counts,
            "effective_rate_hz": effective_rate_hz,
            "effective_ratio": effective_ratio,
            "activity_sufficient": activity_sufficient,
            "axes_available": True,
            "active_stick": active_stick,
            "movement_span": active["span"],
            "movement_turns": active["turns"],
            "movement_path_rate": active["path_rate"],
            "dominant_run_length": dominant,
            "dominant_run_share": dominant_share,
            "regular_repeat": regular_repeat,
            "stick_reports": reports,
            "ignored_reports": max(
                0,
                int(stream_status.get("ignored_reports", 0) or 0) + int(dropped_samples),
            ),
        })
        with self._lock:
            self._snapshot = snapshot

    def _finish_error(self, error_code):
        with self._lock:
            self._snapshot.update(
                state="error",
                error_code=max(1, int(error_code or 1)),
                remaining_ms=0.0,
            )

    def read_snapshot(self):
        fallback = self._fallback
        if fallback is not None:
            snapshot = fallback.read_snapshot()
            snapshot.update({
                "axes_available": False,
                "effective_rate_hz": 0.0,
                "effective_ratio": 0.0,
                "activity_sufficient": False,
            })
            return snapshot
        with self._lock:
            snapshot = dict(self._snapshot)
            snapshot["histogram_counts"] = tuple(snapshot.get("histogram_counts") or ())
            return snapshot

    def stop(self, timeout=1.0):
        self._stop.set()
        if self._fallback is not None:
            self._fallback.stop(timeout=timeout)
        if self._stream is not None:
            self._stream.stop(timeout=min(0.5, max(0.0, float(timeout))))
        thread = self._thread
        if thread is not None and thread.is_alive() and thread is not threading.current_thread():
            thread.join(timeout=max(0.0, float(timeout)))
        with self._lifecycle_lock:
            alive = bool(thread is not None and thread.is_alive())
            if not alive:
                self._thread = None
                self._stream = None
                self._fallback = None
        return not alive

    close = stop


def _analysis_text(window, snapshot):
    state = str(snapshot.get("state") or "idle")
    if state in {"opening", "running"}:
        return window.gui.tr(
            "判讀：量測中，請持續以中等速度，大幅繞圈轉動一支搖桿。\n"
            "請避免長時間停在中心或壓住外圈不動。"
        )
    if state == "error":
        return window.gui.tr("判讀：量測失敗，無法分析回報穩定度與有效狀態更新率。")
    if state == "idle":
        return window.gui.tr(
            "判讀：量測時請持續以中等速度，大幅繞圈轉動一支搖桿。\n"
            "完成後會同時判讀回報穩定度與有效狀態更新率。"
        )
    rate = float(snapshot.get("rate_hz", 0.0) or 0.0)
    p50 = float(snapshot.get("p50_us", 0.0) or 0.0) / 1000.0
    p95 = float(snapshot.get("p95_us", 0.0) or 0.0) / 1000.0
    p99 = float(snapshot.get("p99_us", 0.0) or 0.0) / 1000.0
    if rate <= 0 or p50 <= 0:
        cadence = window.gui.tr("回報間隔資料不足，無法判讀穩定度。")
    else:
        expected = 1000.0 / rate
        typical_ratio = p50 / expected
        p95_ratio = p95 / p50
        tail_ratio = p99 / p50
        if 0.85 <= typical_ratio <= 1.15 and p95_ratio <= 1.20 and tail_ratio <= 1.35:
            cadence = window.gui.tr("主要回報間隔接近預期值，P95／P99 尾端也集中，回報穩定。")
        elif 0.75 <= typical_ratio <= 1.25 and p95_ratio <= 1.30 and tail_ratio <= 2.25:
            cadence = window.gui.tr("主要回報間隔接近預期值，尾端有少量跨週期回報，未見持續堆積。")
        else:
            cadence = window.gui.tr("回報間隔分散或偏離預期值，存在較明顯的排程波動。")
    if not snapshot.get("axes_available"):
        effective = window.gui.tr("無法解析標準搖桿軸，因此本次不提供有效回報率。")
    elif not snapshot.get("activity_sufficient"):
        effective = window.gui.tr("搖桿活動不足，請持續大幅繞圈後重新量測，避免把靜止資料判成重複回報。")
    else:
        ratio = float(snapshot.get("effective_ratio", 0.0) or 0.0)
        dominant = int(snapshot.get("dominant_run_length", 0) or 0)
        if snapshot.get("regular_repeat") and dominant >= 2:
            effective = window.gui.tr(
                "偵測到規律重複狀態：每個搖桿狀態通常維持 {count} 筆；有效更新率明顯低於 HID 回報率。"
            ).format(count=dominant)
        elif ratio >= 0.90:
            effective = window.gui.tr("有效狀態更新率接近 HID 回報率，未發現明顯規律重複。")
        elif ratio >= 0.75:
            effective = window.gui.tr("有效狀態更新率略低於 HID 回報率，未發現固定重複規律。")
        else:
            effective = window.gui.tr(
                "有效狀態更新率明顯較低，但未發現固定重複規律；可能受軸解析度、濾波或轉動速度影響。"
            )
    return window.gui.tr("判讀：{cadence}\n{effective}").format(
        cadence=cadence,
        effective=effective,
    )


def _native_device_for_raw_hid(self):
    raw_device = self._selected_raw_hid_device()
    if raw_device is None:
        return None
    candidates = tuple(
        device for device in self._native_test_devices
        if device.kind in {"xinput", "winmm"}
    )
    raw_slot = self.latest_telemetry.get("xinput_slot")
    if raw_device.is_virtual and isinstance(raw_slot, int):
        match = next((
            device for device in candidates
            if device.kind == "xinput" and device.index == raw_slot
        ), None)
        if match is not None:
            return match
    xinput = tuple(device for device in candidates if device.kind == "xinput")
    winmm = tuple(device for device in candidates if device.kind == "winmm")
    vid_pid = (int(raw_device.vendor_id), int(raw_device.product_id))
    if vid_pid == (0xCAFE, 0x4020) and len(xinput) == 1:
        return xinput[0]
    if vid_pid == (0xCAFE, 0x4021):
        named = tuple(
            device for device in winmm
            if "s2p mobile gamepad" in device.name.casefold()
        )
        if len(named) == 1:
            return named[0]
        if len(winmm) == 1:
            return winmm[0]
    if raw_device.is_virtual and len(xinput) == 1:
        return xinput[0]
    return None


def _configure_native_sampler(self):
    if self.native_sampler is None:
        return
    device = self._selected_device()
    if device is not None and device.kind in {"xinput", "winmm"}:
        native_device = device
    elif device is not None and device.kind == "raw_hid":
        native_device = _native_device_for_raw_hid(self)
    else:
        native_device = None
    self.native_sampler.set_device(native_device)


def install_effective_rate_patch():
    """Patch the tester at process startup without changing its window size."""
    if getattr(tester.GamepadTestWindow, "_effective_rate_patch_installed", False):
        return
    tester.RawHidStreamClient = HybridRawHidStreamClient
    cls = tester.GamepadTestWindow
    original_init = cls.__init__
    original_build = cls._build_high_rate_tab
    original_start = cls._start_raw_hid_measurement
    original_update = cls._update_raw_hid_measurement

    def patched_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.raw_hid_probe = RawHidAnalysisClient()
        self.raw_hid_effective_rate_var = tk.StringVar(value="— Hz")
        self.raw_hid_analysis_var = tk.StringVar(
            value=self.gui.tr(
                "判讀：量測時請持續以中等速度，大幅繞圈轉動一支搖桿。\n"
                "完成後會同時判讀回報穩定度與有效狀態更新率。"
            )
        )

    def patched_build(self, parent):
        original_build(self, parent)
        row_widgets = {}
        for child in parent.winfo_children():
            try:
                row = int(child.grid_info().get("row", -1))
            except (TypeError, ValueError, tk.TclError):
                continue
            row_widgets.setdefault(row, []).append(child)
        for child in row_widgets.get(1, ()):
            child.destroy()
        summary = ttk.Frame(parent)
        summary.grid(row=1, column=0, sticky="ew", pady=(10, 8))
        for column in range(4):
            summary.columnconfigure(column, weight=1, uniform="raw_summary")
        items = (
            ("目前回報率", self.raw_hid_rate_var),
            ("有效回報率", self.raw_hid_effective_rate_var),
            ("收到回報數", self.raw_hid_count_var),
            ("剩餘時間", self.raw_hid_remaining_var),
        )
        for column, (title, variable) in enumerate(items):
            box = ttk.LabelFrame(summary, text=self.gui.tr(title), padding=(8, 5))
            box.grid(
                row=0,
                column=column,
                sticky="ew",
                padx=(0 if column == 0 else 4, 0 if column == 3 else 4),
            )
            ttk.Label(box, textvariable=variable, anchor="center").pack(
                fill="both", expand=True, pady=(3, 5)
            )
        for child in row_widgets.get(5, ()):
            child.destroy()
        ttk.Label(
            parent,
            textvariable=self.raw_hid_analysis_var,
            foreground="#666666",
            anchor="w",
            justify="left",
            wraplength=670,
        ).grid(row=5, column=0, sticky="ew", pady=(3, 0))

    def patched_start(self):
        self.raw_hid_effective_rate_var.set("— Hz")
        self.raw_hid_analysis_var.set(self.gui.tr(
            "判讀：量測中，請持續以中等速度，大幅繞圈轉動一支搖桿。\n"
            "請避免長時間停在中心或壓住外圈不動。"
        ))
        return original_start(self)

    def patched_update(self, redraw_chart=True):
        result = original_update(self, redraw_chart=redraw_chart)
        snapshot = self.raw_hid_probe.read_snapshot()
        effective_rate = float(snapshot.get("effective_rate_hz", 0.0) or 0.0)
        self.raw_hid_effective_rate_var.set(
            f"{effective_rate:,.0f} Hz"
            if snapshot.get("axes_available") and effective_rate > 0
            else "— Hz"
        )
        if snapshot.get("state") != "idle":
            self.raw_hid_analysis_var.set(_analysis_text(self, snapshot))
        return result

    cls.__init__ = patched_init
    cls._build_high_rate_tab = patched_build
    cls._start_raw_hid_measurement = patched_start
    cls._update_raw_hid_measurement = patched_update
    cls._raw_hid_analysis_text = _analysis_text
    cls._native_device_for_raw_hid = _native_device_for_raw_hid
    cls._configure_native_sampler = _configure_native_sampler
    cls._effective_rate_patch_installed = True
