from pathlib import Path
import sys
import tkinter as tk
import unittest
from unittest.mock import patch


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import dialog_layout


class NewWindowFocusTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("200x120+40+40")
        self.addCleanup(self.root.destroy)
        self.root.update()

    def dialog(self):
        window = tk.Toplevel(self.root)
        window.geometry("120x80+60+60")
        self.addCleanup(lambda: window.winfo_exists() and window.destroy())
        window.update()
        return window

    def test_focus_moves_to_the_new_window_while_s2p_is_active(self):
        window = self.dialog()
        with patch.object(window, "focus_force") as force:
            self.assertTrue(dialog_layout.focus_new_window(
                window, owns_foreground=lambda: True, is_foreground=lambda _window: False,
            ))
        force.assert_called_once_with()

    def test_the_field_chosen_with_focus_set_is_activated(self):
        # focus_set() already moved Tk's focus, but Windows kept the window
        # behind it active: the dialog is activated on the chosen field.
        window = self.dialog()
        entry = tk.Entry(window)
        entry.pack()
        entry.focus_set()
        window.update()
        with patch.object(entry, "focus_force") as force:
            self.assertTrue(dialog_layout.focus_new_window(
                window, owns_foreground=lambda: True, is_foreground=lambda _window: False,
            ))
        force.assert_called_once_with()

    def test_an_already_active_window_is_left_as_it_is(self):
        window = self.dialog()
        with patch.object(window, "focus_force") as force:
            self.assertFalse(dialog_layout.focus_new_window(
                window, owns_foreground=lambda: True, is_foreground=lambda _window: True,
            ))
        force.assert_not_called()

    def test_a_window_opened_in_the_background_does_not_take_the_foreground(self):
        window = self.dialog()
        with patch.object(window, "focus_force") as force:
            self.assertFalse(dialog_layout.focus_new_window(
                window, owns_foreground=lambda: False, is_foreground=lambda _window: False,
            ))
        force.assert_not_called()

    def test_borderless_windows_and_windows_behind_a_modal_are_left_alone(self):
        tooltip = self.dialog()
        tooltip.overrideredirect(True)
        modal = self.dialog()
        modal.grab_set()
        self.addCleanup(modal.grab_release)
        behind = self.dialog()
        for window in (tooltip, behind):
            with self.subTest(window=str(window)), patch.object(window, "focus_force") as force:
                self.assertFalse(dialog_layout.focus_new_window(
                    window, owns_foreground=lambda: True, is_foreground=lambda _window: False,
                ))
                force.assert_not_called()

    def test_each_window_is_focused_once_when_first_shown(self):
        focused = []
        with patch.object(
            dialog_layout, "focus_new_window",
            side_effect=lambda window, _owns, _active: focused.append(window),
        ):
            dialog_layout.install_focus_new_windows(self.root, owns_foreground=lambda: True)
            window = self.dialog()
            self.root.update()
            window.withdraw()
            window.deiconify()
            window.update()
            self.root.update()
        self.assertEqual(focused, [window])


if __name__ == "__main__":
    unittest.main()
