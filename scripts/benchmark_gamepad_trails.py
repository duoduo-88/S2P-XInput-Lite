"""Measure Gamepad Tester's two real-sample trails with a 10 kHz synthetic producer."""

import argparse
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tkinter as tk
import traceback
from unittest.mock import patch
import uuid


RATE = 10_000
PERIOD_NS = 1_000_000_000 // RATE


def write_reports(tag, seconds):
    from test_telemetry import SharedTestTelemetry, _open_mapping

    channel = SharedTestTelemetry(_open_mapping(tag))
    origin = time.monotonic_ns()
    sequence = 0
    try:
        while time.monotonic_ns() - origin < seconds * 1e9:
            now = time.monotonic_ns()
            wanted = (now - origin) // PERIOD_NS
            while sequence < wanted:
                sequence += 1
                phase = sequence / RATE
                radius = 0.74 + 0.22 * math.cos(phase * 4.1)
                left = (radius * math.sin(phase * 14.7), radius * math.cos(phase * 14.7))
                right = (radius * math.sin(phase * 10.3), radius * math.cos(phase * 10.3))
                stamp = origin + sequence * PERIOD_NS
                channel.write_trail_sample(stamp, left, right, (0, 0), left, right)
            if sequence:
                channel.publish_if_requested({
                    "timestamp_ns": stamp,
                    "source_rate_hz": RATE,
                    "xinput_slot": 0,
                    "left": {"physical": left, "final": left},
                    "right": {"physical": right, "final": right},
                }, now_ns=now)
            time.sleep(0.00055)
    finally:
        channel.close()


def cadence(stamps):
    gaps = sorted((b - a) * 1000 for a, b in zip(stamps, stamps[1:]))
    if not gaps:
        raise RuntimeError("Too few input monitor frames to measure cadence")
    return {
        "hz": round(len(gaps) / (stamps[-1] - stamps[0]), 2),
        "p95_gap_ms": round(gaps[math.ceil(len(gaps) * 0.95) - 1], 2),
    }


def run(args):
    import gamepad_test_window as monitor
    from gamepad_devices import GamepadDevice
    from gamepad_test_app import GamepadTestHost
    from test_telemetry import SharedTestTelemetry, _open_mapping

    root = tk.Tk()
    root.withdraw()
    errors, polls, batches = [], [], []
    frames = {"left": [], "right": []}
    peaks = dict.fromkeys(frames, 0)
    result = {}
    capture = False

    class Viewer(monitor.GamepadTestWindow):
        def _poll(self):
            if capture:
                polls.append(time.perf_counter())
            super()._poll()

        def _consume_s2p_trail_samples(self, samples, now):
            if capture:
                batches.append((samples[0]["sequence"], samples[-1]["sequence"], len(samples)))
            super()._consume_s2p_trail_samples(samples, now)

        def _draw_plots(self, *args, **kwargs):
            before = time.perf_counter()
            changed = super()._draw_plots(*args, **kwargs)
            if capture:
                for side, plot in self.plots.items():
                    peaks[side] = max(peaks[side], len(self.histories[side].trail))
                    if plot._presentation_times and plot._presentation_times[-1] >= before:
                        frames[side].append(plot._presentation_times[-1])
            return changed

    def callback_error(*info):
        errors.append("".join(traceback.format_exception(*info)))
        root.quit()

    root.report_callback_exception = callback_error
    tag = rf"Local\S2P-Trail-Benchmark-{uuid.uuid4().hex}"
    warmup = args.trail_seconds + 1
    with (
        tempfile.TemporaryDirectory() as directory,
        patch.object(monitor, "CONFIG_PATH", Path(directory) / "bench.ini"),
        patch.object(monitor, "enumerate_raw_hid_gamepads", return_value=()),
        patch.object(monitor.NativeGamepadSampler, "enumerate_devices", return_value=()),
    ):
        viewer = Viewer(GamepadTestHost(root, "en", False))
        viewer.telemetry = SharedTestTelemetry(_open_mapping(tag))
        device = GamepadDevice("s2p", "s2p", 0, "10 kHz synthetic bridge", True)
        viewer._selected_device = lambda: device
        viewer.open()
        viewer.sample_display_percent_var.set(100)
        viewer.shape_enabled_var.set(False)
        viewer.trail_length_var.set(args.trail_seconds)
        writer = subprocess.Popen([
            sys.executable, "-B", __file__, "--source", str(args.source),
            "--writer", tag, "--seconds", str(warmup + args.seconds + 3),
        ], creationflags=subprocess.CREATE_NO_WINDOW)

        def start():
            nonlocal capture
            capture = True
            root.after(round(args.seconds * 1000), finish, time.perf_counter(), time.process_time(),
                       viewer._trail_overwrite_count)

        def finish(started, cpu_started, initial_drops):
            nonlocal capture
            capture = False
            elapsed = time.perf_counter() - started
            cpu = time.process_time() - cpu_started
            received = sum(batch[2] for batch in batches)
            sequence_span = batches[-1][1] - batches[0][0] + 1
            drops = viewer._trail_overwrite_count - initial_drops
            if drops or received != sequence_span or not 0.98 * RATE <= received / elapsed <= 1.02 * RATE:
                raise RuntimeError(f"Incomplete input stream: {received}/{sequence_span}, {drops} overwritten")
            result.update({
                "display_hz": viewer._display_refresh_hz,
                "process_cpu_one_core_percent": round(100 * cpu / elapsed, 2),
                "poll": cadence(polls),
                "received": received,
                "sequence_span": sequence_span,
                "ring_drops": drops,
                "reports_per_second": round(received / elapsed, 2),
                "background_raster": all(
                    getattr(plot, "_bitmap_worker_key", None) is not None
                    and not plot._bitmap_worker_disabled for plot in viewer.plots.values()
                ),
            })
            for side, stamps in frames.items():
                result[side] = {**cadence(stamps), "peak_history": peaks[side]}
            root.quit()

        try:
            root.after(round(warmup * 1000), start)
            root.mainloop()
        finally:
            if writer.poll() is None:
                writer.terminate()
            writer.wait(timeout=5)
            viewer.close()
            root.destroy()
    if errors:
        raise RuntimeError(errors[0])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=lambda path: Path(path).resolve(), default=Path(__file__).resolve().parents[1])
    parser.add_argument("--seconds", type=float, default=8)
    parser.add_argument("--trail-seconds", type=float, default=5)
    parser.add_argument("--writer", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "win32":
        parser.error("The benchmark requires Windows")
    if not args.writer and (not 0 < args.seconds <= 120 or not 0.25 <= args.trail_seconds <= 30):
        parser.error("Use 0 < seconds <= 120 and 0.25 <= trail seconds <= 30")
    sys.path.insert(0, str(args.source / "src"))
    if args.writer:
        write_reports(args.writer, args.seconds)
    else:
        print(json.dumps(run(args), indent=2))


if __name__ == "__main__":
    main()
