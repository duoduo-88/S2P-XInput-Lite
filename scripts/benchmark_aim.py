"""Run the same seeded 2D and 3D tracking scenes on two Windows checkouts."""

import argparse
import ctypes
import json
import math
from pathlib import Path
import sys
import time
import tkinter as tk
import traceback


def percentile(values, quantile):
    return sorted(values)[math.ceil(len(values) * quantile) - 1]


def summarize(timestamps, target_hz, wall_seconds, cpu_seconds, *, active):
    intervals = [b - a for a, b in zip(timestamps, timestamps[1:])]
    if active and not intervals:
        raise RuntimeError("Too few completed frames to measure frame cadence")
    metrics = {
        "target_hz": target_hz,
        "completed_frames": len(timestamps),
        "process_cpu_one_core_percent": round(100 * cpu_seconds / wall_seconds, 2),
        "wall_seconds": round(wall_seconds, 3),
    }
    if not active:
        metrics["frame_fps"] = round(len(timestamps) / wall_seconds, 2)
        return metrics
    metrics.update({
        "frame_fps": round(len(intervals) / sum(intervals), 2),
        "avg_frame_ms": round(1000 * sum(intervals) / len(intervals), 3),
        "p95_frame_ms": round(1000 * percentile(intervals, 0.95), 3),
        "p99_frame_ms": round(1000 * percentile(intervals, 0.99), 3),
        "over_target_percent": round(100 * sum(gap > 1 / target_hz for gap in intervals) / len(intervals), 2),
        "over_1_5x_target_percent": round(100 * sum(gap > 1.5 / target_hz for gap in intervals) / len(intervals), 2),
    })
    return metrics


def benchmark(args):
    # Resolve imports from the chosen revision, without using its on-disk settings.
    sys.path.insert(0, str(args.source / "src"))
    from gamepad_aim import AimTestPane
    from gamepad_aim_gpu import Direct2DAimRenderer
    from gamepad_aim_settings import AimSettings
    from gamepad_devices import GamepadState
    try:
        from gamepad_aim_display import window_refresh_rate as display_refresh_rate
    except ImportError:
        from gamepad_test_window import display_refresh_rate

    def read_display(window):
        return args.target_hz or display_refresh_rate(window)

    class InputReplay:
        def __init__(self, dimension):
            self.dimension = dimension
            self.start = None
            self.packet = None
            self.state = None

        def read(self):
            elapsed = max(0.0, time.perf_counter() - self.start) if self.start is not None else 0.0
            packet = int(elapsed * 250)
            if packet != self.packet:
                phase = math.tau * packet / 250 / 4
                if self.dimension == "2d":
                    axes = 0.22 * math.cos(phase), 0.22 * math.sin(phase)
                else:
                    axes = 0.40 + 0.15 * math.cos(phase), 0.18 * math.sin(phase)
                self.packet = packet
                self.state = GamepadState(packet, 0, (), (0.0, 0.0), axes, 0.0, 0.0)
            return "benchmark", self.state

    class OffscreenRenderer(Direct2DAimRenderer):
        def _create_target(self, hwnd, width, height):
            from gamepad_aim_gpu import _HRESULT, _PTR, _Size, _UINT, _check_hresult, _method

            super()._create_target(hwnd, width, height)
            compatible = _PTR()
            # A bitmap target compatible with the hardware HWND target runs
            # the same drawing commands but has no window presentation step.
            create = _method(
                self._target, 12, _HRESULT, _PTR, ctypes.POINTER(_Size), _PTR, _UINT,
                ctypes.POINTER(_PTR),
            )
            _check_hresult(
                create(None, ctypes.byref(_Size(width, height)), None, 0, ctypes.byref(compatible)),
                "CreateCompatibleRenderTarget",
            )
            self._parent_target = self._target
            self._target = compatible
            self._bind_render_target()

        def draw(self, width, height, **parts):
            if (width, height) != self._size:
                raise RuntimeError("Offscreen benchmark requires a fixed viewport size")
            super().draw(width, height, **parts)

        def close(self):
            from gamepad_aim_gpu import _release

            try:
                super().close()
            finally:
                _release(getattr(self, "_parent_target", None))
                self._parent_target = None

    renderer = OffscreenRenderer if args.offscreen else Direct2DAimRenderer
    if args.offscreen and not hasattr(renderer, "_bind_render_target"):
        raise RuntimeError("Offscreen mode requires an updated source checkout")

    class MeasuredRenderer(renderer):
        def draw(self, *args, **kwargs):
            super().draw(*args, **kwargs)
            if measuring:
                frames.append(time.perf_counter())

    class MeasuredPane(AimTestPane):
        def _draw_if_due(self, now):
            old_draws = self.draws
            result = super()._draw_if_due(now)
            if measuring and args.renderer == "tk" and self.draws > old_draws:
                frames.append(time.perf_counter())
            return result

    root = tk.Tk()
    root.geometry("650x420+120+100")
    root.title("Aim benchmark")
    container = tk.Frame(root)
    container.pack(fill="both", expand=True)
    measuring = False
    frames = []
    errors = []
    results = []
    modes = iter(("2d", "3d") if args.dimension == "both" else (args.dimension,))

    def on_error(*error):
        errors.append("".join(traceback.format_exception(*error)))
        root.quit()

    root.report_callback_exception = on_error

    def next_scene():
        nonlocal measuring
        dimension = next(modes, None)
        if dimension is None:
            root.quit()
            return
        frame = tk.Frame(container)
        frame.pack(fill="both", expand=True)
        replay = InputReplay(dimension)
        gpu_factory = MeasuredRenderer if args.renderer == "d2d" else None
        worker_options = {"render_in_worker": True} if args.render_worker else {}
        pane = MeasuredPane(
            frame, str, replay.read, display_hz=read_display, gpu_renderer=gpu_factory, **worker_options,
        )
        pane.dimension.set(dimension)
        pane.mode.set("tracking")
        pane._save_settings(AimSettings(
            window_size=args.size, fullscreen=False, tracking_seconds=300,
            seed=20261110, sound=False, rumble=0,
        ))
        root.after(150, begin, pane, frame, replay)

    def begin(pane, frame, replay):
        pane._start()
        if not pane.test_open or pane.countdown_until is None:
            raise RuntimeError("Aim window did not open")
        now = time.perf_counter()
        replay.start = now
        pane.countdown_until = now
        pane._advance_countdown(now)
        root.after(round(args.warmup * 1000), measure, pane, frame, replay.dimension, True)

    def measure(pane, frame, dimension, active):
        nonlocal measuring
        frames.clear()
        measuring = True
        start = time.perf_counter()
        cpu_start = time.process_time()
        duration = args.seconds if active else args.idle_seconds
        root.after(round(duration * 1000), finish, pane, frame, dimension, active, start, cpu_start)

    def finish(pane, frame, dimension, active, start, cpu_start):
        nonlocal measuring
        measuring = False
        end = time.perf_counter()
        wall = end - start
        cpu = time.process_time() - cpu_start
        if args.renderer == "d2d" and pane.gpu is None:
            raise RuntimeError(f"Direct2D did not remain available: {pane.gpu_error}")
        if active and not pane._round().running:
            raise RuntimeError("The tracking scene stopped before the measurement ended")
        results.append({
            "dimension": dimension, "renderer": args.renderer, "scene": "tracking" if active else "idle",
            "frame_event": (
                "Direct2D offscreen submission" if args.offscreen
                else "Direct2D submission" if args.renderer == "d2d" else "Tk update"
            ),
            "size": list(pane.size), "detected_display_hz": display_refresh_rate(pane.window),
            "scheduler": "native" if getattr(pane, "_native_timer", None) is not None else "Tk",
            "render_thread": "worker" if args.render_worker else "ui",
            **summarize([stamp for stamp in frames if start <= stamp <= end], pane.display_hz, wall, cpu, active=active),
        })
        if active and args.idle_seconds:
            pane._reset()
            root.after(500, measure, pane, frame, dimension, False)
            return
        pane.close_test()
        frame.destroy()
        root.after(200, next_scene)

    # Match the existing Gamepad Tester timer request for both revisions.
    winmm = ctypes.WinDLL("winmm")
    timer_enabled = winmm.timeBeginPeriod(1) == 0
    try:
        root.after(0, next_scene)
        root.mainloop()
    finally:
        root.destroy()
        if timer_enabled:
            winmm.timeEndPeriod(1)
    if errors:
        raise RuntimeError(errors[0])
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=lambda value: Path(value).resolve(), default=Path(__file__).resolve().parents[1])
    parser.add_argument("--dimension", choices=("both", "2d", "3d"), default="both")
    parser.add_argument("--renderer", choices=("d2d", "tk"), default="d2d")
    parser.add_argument("--size", default="1280x720")
    parser.add_argument("--offscreen", action="store_true", help="Use a hardware-compatible Direct2D bitmap target")
    parser.add_argument("--render-worker", action="store_true", help="Run Direct2D submissions on the aim worker")
    parser.add_argument("--warmup", type=float, default=1.0)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--idle-seconds", type=float, default=0.0, help="Also measure each visible idle scene")
    parser.add_argument("--target-hz", type=float, help="Override the scheduler target without changing the Windows display mode")
    args = parser.parse_args()
    if (args.offscreen or args.render_worker) and args.renderer != "d2d":
        parser.error("--offscreen and --render-worker require the Direct2D renderer")
    if sys.platform != "win32":
        parser.error("The fixed-scene benchmark requires a Windows desktop")
    if not (0 < args.seconds <= 120 and 0 <= args.warmup <= 30 and 0 <= args.idle_seconds <= 30):
        parser.error("Use 0 < seconds <= 120, 0 <= warmup <= 30, and 0 <= idle-seconds <= 30")
    if args.target_hz is not None and not (30 <= args.target_hz <= 1000):
        parser.error("Use a target from 30 to 1000 Hz")
    print(json.dumps({"source": str(args.source), "results": benchmark(args)}, indent=2))


if __name__ == "__main__":
    main()
