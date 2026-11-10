import sys
import threading
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from gamepad_aim_timing import FrameStats, next_deadline, refresh_interval, timer_delay_ms


class AimTimingTests(unittest.TestCase):
    def test_refresh_interval_and_timer_phase_through_600_hz(self):
        for hz in (60, 120, 144, 180, 240, 360, 500, 540, 600):
            with self.subTest(hz=hz):
                period = refresh_interval(hz)
                self.assertAlmostEqual(period * hz, 1.0)
                deadline = period
                now = 0.0
                for index in range(1, hz + 1):
                    now += timer_delay_ms(now, deadline) / 1000
                    self.assertLessEqual(abs(now - index / hz), 0.00050001)
                    deadline = next_deadline(deadline, now, period)

    def test_long_delay_skips_obsolete_slots(self):
        deadline = next_deadline(10.0, 14.0, refresh_interval(600))
        self.assertGreater(deadline, 14.0)
        self.assertLessEqual(deadline, 14.0 + 1 / 600 + 1e-9)

    def test_stats_use_measured_events_and_time(self):
        stats = FrameStats()
        stats.reset(0)
        for stamp in (0.01, 0.02, 0.04, 0.05):
            stats.submitted(stamp)
        stats.simulations = 25
        measured = stats.take(0.1, 0.015)
        self.assertAlmostEqual(measured.fps, 40)
        self.assertAlmostEqual(measured.simulation_hz, 250)
        self.assertAlmostEqual(measured.mean_ms, 40 / 3)
        self.assertAlmostEqual(measured.p95_ms, 20)
        self.assertAlmostEqual(measured.p99_ms, 20)
        self.assertAlmostEqual(measured.over_budget, 100 / 3)

    def test_no_submissions_have_no_frame_time(self):
        stats = FrameStats()
        measured = stats.take(1, 1 / 60)
        self.assertEqual(measured.fps, 0)
        self.assertIsNone(measured.mean_ms)


@unittest.skipUnless(sys.platform == "win32", "Native aim timer requires Windows")
class NativeAimTimerTests(unittest.TestCase):
    def test_native_timer_posts_one_wake_and_can_be_cancelled_and_closed(self):
        from gamepad_aim_native_timer import NativeAimTimer

        timer = NativeAimTimer(0)
        self.addCleanup(timer.close)
        woke = threading.Event()
        timer._post = lambda *args: woke.set()
        timer.arm(0.03)
        timer.cancel()
        self.assertFalse(woke.wait(0.06))
        timer.arm(0.005)
        self.assertTrue(woke.wait(0.5))
        timer.close()
        self.assertFalse(timer._thread.is_alive())
