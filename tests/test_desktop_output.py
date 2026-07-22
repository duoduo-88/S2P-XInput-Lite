import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from desktop_output import DesktopOutputManager


class FakeUser32:
    def __init__(self):
        self.events = []

    def keybd_event(self, *args):
        self.events.append(("keyboard",) + args)

    def mouse_event(self, *args):
        self.events.append(("mouse",) + args)


class DesktopOutputTests(unittest.TestCase):
    def test_shared_modifier_releases_only_after_last_source(self):
        backend = FakeUser32()
        output = DesktopOutputManager(backend)

        output.acquire_keyboard_combo("CTRL+A", "one")
        output.acquire_keyboard_combo("CTRL+B", "two")
        output.release_keyboard_combo_source("CTRL+A", "one")

        self.assertIn(0x11, output.keyboard_key_sources)
        self.assertNotIn(0x41, output.keyboard_key_sources)
        self.assertIn(0x42, output.keyboard_key_sources)

        output.release_keyboard_combo_source("CTRL+B", "two")
        self.assertEqual(output.keyboard_key_sources, {})
        self.assertEqual(output.keyboard_combo_sources, {})

    def test_shared_mouse_button_releases_only_after_last_source(self):
        backend = FakeUser32()
        output = DesktopOutputManager(backend)

        output.acquire_mouse_button("LEFT", "one")
        output.acquire_mouse_button("LEFT", "two")
        output.release_mouse_button("LEFT", "one")

        self.assertEqual(output.mouse_button_sources["LEFT"], {"two"})
        self.assertEqual(len(backend.events), 1)

        output.release_mouse_button("LEFT", "two")
        self.assertEqual(output.mouse_button_sources["LEFT"], set())
        self.assertEqual(len(backend.events), 2)

    def test_repeated_release_is_idempotent(self):
        backend = FakeUser32()
        output = DesktopOutputManager(backend)
        output.acquire_keyboard_combo("CTRL+A", "one")
        output.release_keyboard_combo_source("CTRL+A", "one")
        event_count = len(backend.events)

        output.release_keyboard_combo_source("CTRL+A", "one")
        output.release_mouse_button("LEFT", "missing")

        self.assertEqual(len(backend.events), event_count)

    def test_reset_motion_residuals_clears_move_and_wheel_state(self):
        output = DesktopOutputManager(FakeUser32())
        output.mouse_residual[:] = (0.75, -0.25)
        output.wheel_residual["LEFT"][:] = (0.5, 0.5)
        output.wheel_residual["RIGHT"][:] = (-0.5, -0.5)

        output.reset_motion_residuals()

        self.assertEqual(output.mouse_residual, [0.0, 0.0])
        self.assertEqual(output.wheel_residual["LEFT"], [0.0, 0.0])
        self.assertEqual(output.wheel_residual["RIGHT"], [0.0, 0.0])


if __name__ == "__main__":
    unittest.main()
