import ctypes
import math
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from gamepad_test_window import PLOT_SIZE, StickHistory, StickPlot
from gamepad_trail_worker import NativeTrailRaster, TrailFrame, TrailRasterWorker, TrailStyle


STYLE = TrailStyle(100, 2.5, 16.0, 14.0, 1.0, 0.0, 0.0)


class _Raster:
    def __init__(self):
        self.entered = threading.Event()
        self.resume = threading.Event()
        self.calls = []
        self.closed = threading.Event()

    def update(self, samples, style, now, reset):
        self.calls.append((tuple(samples), style, now, reset, threading.get_ident()))
        if len(self.calls) == 1:
            self.entered.set()
            self.resume.wait(2)
        return (0, 0, 1, 1), math.inf

    def read_rows(self, bounds):
        return bytes((len(self.calls),))

    def close(self):
        self.closed.set()


class TrailWorkerTests(unittest.TestCase):
    def setUp(self):
        self.raster = _Raster()
        self.worker = TrailRasterWorker(32, 32, {"left": 1}, raster_factory=lambda *_: self.raster)
        self.addCleanup(self.worker.close)
        self.addCleanup(self.raster.resume.set)

    def _take(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            frame = self.worker.take("left")
            if frame is not None:
                return frame
            time.sleep(0.001)
        self.fail("No trajectory raster result")

    def test_queued_frames_merge_without_losing_pending_reports(self):
        first = (1.0, 0.0, 0.0, 0)
        second = (1.1, 0.5, 0.0, 1)
        third = (1.2, 1.0, 0.0, 2)
        self.worker.submit("left", 1, STYLE, [first], 1.0)
        self.assertTrue(self.raster.entered.wait(2))
        self.worker.submit("left", 1, STYLE, [second], 1.1)
        self.worker.submit("left", 1, STYLE, [third], 1.2)
        self.raster.resume.set()
        frame = self._take()
        if frame.rows == b"\x01":
            frame = self._take()
        self.assertEqual(frame.rows, b"\x02")
        self.assertEqual(self.raster.calls[0][:4], ((first,), STYLE, 1.0, True))
        self.assertEqual(self.raster.calls[1][:4], ((second, third), STYLE, 1.2, False))
        self.assertTrue(all(call[4] != threading.get_ident() for call in self.raster.calls))
        self.worker.close()
        self.assertTrue(self.raster.closed.is_set())

    def test_discard_invalidates_an_in_flight_result_even_if_the_key_is_reused(self):
        old = (1.0, -1.0, 0.0, 0)
        new = (2.0, 1.0, 0.0, 0)
        self.worker.submit("left", "same-view", STYLE, [old], 1.0)
        self.assertTrue(self.raster.entered.wait(2))
        self.worker.discard()
        self.worker.submit("left", "same-view", STYLE, [new], 2.0)
        self.raster.resume.set()
        self.assertEqual(self._take().rows, b"\x02")
        self.assertTrue(self.raster.calls[1][3])

    def test_pending_limit_rebuilds_from_the_retained_original_history(self):
        points = [(1.0 + i / 10, i / 5, 0.0, i) for i in range(5)]
        self.worker.submit("left", 1, STYLE, points[:1], 1.0)
        self.assertTrue(self.raster.entered.wait(2))
        with patch("gamepad_trail_worker.MAX_PENDING_SAMPLES", 3):
            self.worker.submit("left", 1, STYLE, points[1:3], 1.2)
            self.worker.submit("left", 1, STYLE, points[3:], 1.4, full_trail=points[2:])
        self.raster.resume.set()
        frame = self._take()
        if frame.rows == b"\x01":
            frame = self._take()
        self.assertEqual(frame.rows, b"\x02")
        self.assertEqual(self.raster.calls[1][:4], (tuple(points[2:]), STYLE, 1.4, True))

    def test_worker_can_close_after_thread_start_failure(self):
        with (
            patch("gamepad_trail_worker.threading.Thread.start", side_effect=RuntimeError("cannot start")),
            self.assertRaises(RuntimeError),
        ):
            self.worker.submit("left", 1, STYLE, [(1.0, 0.0, 0.0, 0)], 1.0)
        self.worker.close()


class NativeTrailRasterTests(unittest.TestCase):
    def test_partial_dot_footprints_and_independent_expiry_match_python(self):
        dll = Path(__file__).resolve().parents[1] / "src" / "gamepad_render_native.dll"
        if not dll.is_file():
            self.skipTest("native rendering helper has not been built")
        color = 0xFF1976D2
        raster = NativeTrailRaster(ctypes.CDLL(str(dll)), 32, 32, color)
        self.addCleanup(raster.close)
        style = TrailStyle(63, 2.5, 16.0, 14.0, 1.27, -0.15, 1.73)
        samples = [
            (1.0, -0.442, -0.591, 0),
            (1.2, -0.418, -0.622, 1),
            (1.3, 0.453, 0.319, 2),
            (1.4, 0.971, 0.802, 3),
        ]

        def reference(now, points, config):
            expected = [0] * (32 * 32)
            for stamp, x, y, sequence in points:
                if stamp + config.length <= now or (sequence * config.percent) % 100 >= config.percent:
                    continue
                px = config.center + x * config.radius * config.zoom + config.pan_x
                py = config.center - y * config.radius * config.zoom + config.pan_y
                for row in range(32):
                    for column in range(32):
                        if (column + 0.5 - px) ** 2 + (row + 0.5 - py) ** 2 <= 1.15 ** 2:
                            expected[row * 32 + column] = color
            return expected

        for new, retained, now, reset, config in (
            (samples[:2], samples[:2], 1.25, True, style),
            (samples[2:], samples, 1.6, False, style),
            ([], samples, 3.52, False, style),
            (samples[1:], samples[1:], 3.52, True, STYLE),
            ([], [], 4.0, False, STYLE),
        ):
            raster.update(new, config, now, reset)
            observed = (ctypes.c_uint32 * (32 * 32)).from_buffer_copy(raster.read_rows((0, 0, 32, 32)))
            self.assertEqual(list(observed), reference(now, retained, config))


class TrailPlotWorkerTests(unittest.TestCase):
    def test_plot_clears_pixels_and_replays_new_history_when_sequence_is_reused(self):
        plot = object.__new__(StickPlot)
        plot.side = "left"
        plot._bitmap_worker_view = object()
        plot._bitmap_worker_key = None
        plot._bitmap_worker_sequence = -1
        plot._bitmap_worker_force_reset = False
        plot._bitmap_presented = False
        surface = SimpleNamespace(
            pixels=(ctypes.c_uint32 * (PLOT_SIZE * PLOT_SIZE))(),
            invalidate=Mock(),
        )
        history = StickHistory()
        history.add(0.1, 0.2, 1.0, False)
        first_pixel = 0xFF1976D2
        row = first_pixel.to_bytes(4, "little") + bytes((PLOT_SIZE - 1) * 4)
        worker = Mock()
        worker.take.side_effect = [TrailFrame((0, 5, 1, 6), row), None]
        requested = []
        worker.submit.side_effect = lambda side, key, style, samples, now, **options: requested.append(
            (side, key, tuple(samples))
        )
        config = (100, 1.0, 0.0, 0.0, 2.5)

        plot._draw_worker_bitmap(worker, surface, history, config, 1.0)
        self.assertEqual(surface.pixels[5 * PLOT_SIZE], first_pixel)
        surface.invalidate.assert_called_once_with(0, 5, 1, 6)

        history.reset()
        history.add(0.4, 0.6, 2.0, False)
        plot._draw_worker_bitmap(worker, surface, history, config, 2.0)
        self.assertEqual(surface.pixels[5 * PLOT_SIZE], 0)
        surface.invalidate.assert_called_with(0, 0, PLOT_SIZE, PLOT_SIZE)
        self.assertNotEqual(requested[0][1], requested[1][1])
        self.assertEqual(requested[1][2], ((2.0, 0.4, 0.6, 0),))
