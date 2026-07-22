import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import config_gui


class FakeRoot:
    def __init__(self, alpha):
        self.alpha = float(alpha)
        self.cancelled_jobs = []
        self.after_jobs = []
        self.idle_jobs = []

    def attributes(self, name, *values):
        if name != "-alpha":
            raise AssertionError(name)
        if values:
            self.alpha = float(values[0])
            return None
        return self.alpha

    def after_cancel(self, job):
        self.cancelled_jobs.append(job)

    def after(self, delay, callback):
        token = f"after-{len(self.after_jobs)}"
        self.after_jobs.append((token, delay, callback))
        return token

    def after_idle(self, callback):
        token = f"idle-{len(self.idle_jobs)}"
        self.idle_jobs.append((token, callback))
        return token


class WindowRestoreTests(unittest.TestCase):
    @staticmethod
    def gui(alpha):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.root = FakeRoot(alpha)
        gui._root_restore_alpha_hidden = False
        gui._root_restore_original_alpha = 1.0
        gui._root_restore_generation = 0
        gui._root_restore_repaint_job = None
        gui._root_restore_repaint_retry_job = None
        gui._root_restore_show_job = "pending"
        gui._repaint_root_after_restore = lambda: None
        gui._flush_dwm_composition = lambda: None
        return gui

    def test_restore_preserves_low_custom_alpha_exactly(self):
        gui = self.gui(0.03)
        with patch.object(config_gui.sys, "platform", "win32"):
            gui._hide_root_until_restore_painted()
            self.assertEqual(gui.root.alpha, 0.01)
            gui._show_root_after_restore_painted()

        self.assertEqual(gui.root.alpha, 0.03)
        self.assertFalse(gui._root_restore_alpha_hidden)

    def test_repeated_hide_does_not_replace_original_alpha(self):
        gui = self.gui(0.6)
        with patch.object(config_gui.sys, "platform", "win32"):
            gui._hide_root_until_restore_painted()
            gui.root.alpha = 0.02
            gui._hide_root_until_restore_painted()
            gui._show_root_after_restore_painted()

        self.assertEqual(gui.root.alpha, 0.6)

    def test_stale_restore_generation_cannot_reveal_window(self):
        gui = self.gui(0.01)
        gui._root_restore_original_alpha = 0.8
        gui._root_restore_alpha_hidden = True
        gui._root_restore_generation = 4

        gui._show_root_after_restore_painted(3)

        self.assertEqual(gui.root.alpha, 0.01)
        self.assertTrue(gui._root_restore_alpha_hidden)

        gui._show_root_after_restore_painted(4)

        self.assertEqual(gui.root.alpha, 0.8)
        self.assertFalse(gui._root_restore_alpha_hidden)

    def test_new_restore_cycle_cancels_every_old_job(self):
        gui = self.gui(1.0)
        gui._root_restore_repaint_job = "paint"
        gui._root_restore_repaint_retry_job = "retry"
        gui._root_restore_show_job = "fallback"

        generation = gui._begin_root_restore_cycle()

        self.assertEqual(generation, 1)
        self.assertEqual(
            gui.root.cancelled_jobs,
            ["paint", "retry", "fallback"],
        )
        self.assertIsNone(gui._root_restore_repaint_job)
        self.assertIsNone(gui._root_restore_repaint_retry_job)
        self.assertIsNone(gui._root_restore_show_job)

    def test_restore_uses_idle_paints_and_150_ms_only_as_fallback(self):
        gui = self.gui(0.01)
        gui._root_restore_show_job = None
        repaints = []
        reveals = []
        gui._repaint_root_after_restore = lambda: repaints.append("paint")
        gui._show_root_after_restore_painted = (
            lambda generation=None: reveals.append(generation)
        )

        gui._schedule_root_restore_repaint()

        self.assertEqual(len(gui.root.after_jobs), 1)
        fallback_token, delay, stale_fallback = gui.root.after_jobs[0]
        self.assertEqual(delay, 150)
        self.assertEqual(len(gui.root.idle_jobs), 1)

        gui.root.idle_jobs[0][1]()
        self.assertEqual(repaints, ["paint"])
        self.assertEqual(len(gui.root.idle_jobs), 2)

        gui.root.idle_jobs[1][1]()
        self.assertEqual(reveals, [1])
        self.assertIn(fallback_token, gui.root.cancelled_jobs)

        gui._begin_root_restore_cycle()
        stale_fallback()
        self.assertEqual(reveals, [1])

    def test_dwm_flush_waits_for_one_composition(self):
        class FakeFlush:
            def __init__(self):
                self.calls = 0

            def __call__(self):
                self.calls += 1
                return 0

        flush = FakeFlush()
        fake_windll = SimpleNamespace(
            dwmapi=SimpleNamespace(DwmFlush=flush)
        )
        gui = self.gui(1.0)

        with (
            patch.object(config_gui.sys, "platform", "win32"),
            patch.object(config_gui.ctypes, "windll", fake_windll),
        ):
            config_gui.ConfigGUI._flush_dwm_composition(gui)

        self.assertEqual(flush.calls, 1)


if __name__ == "__main__":
    unittest.main()
