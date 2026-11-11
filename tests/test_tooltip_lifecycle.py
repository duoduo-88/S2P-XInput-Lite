from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config_gui import ConfigGUI, ToolTip
from readable_combobox import MappingCombobox


class TooltipLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()

    def tearDown(self):
        self.root.update_idletasks()
        self.root.destroy()

    def test_destroying_control_closes_its_visible_tooltip(self):
        label = ttk.Label(self.root, text="Help")
        tooltip = ToolTip(label, "Explanation")
        tooltip.show()
        popup = str(tooltip.window)
        self.assertTrue(self.root.tk.call("winfo", "exists", popup))
        label.destroy()
        self.assertFalse(self.root.tk.call("winfo", "exists", popup))
        self.assertIsNone(tooltip.window)
        tooltip.hide()

    def test_closing_repeated_editor_controls_releases_all_combobox_registrations(self):
        gui = object.__new__(ConfigGUI)
        gui.root = self.root
        gui.profile_combo = None
        gui._parameter_wheel_bound_widgets = set()
        gui._parameter_reset_bound_widgets = set()
        gui._combobox_overflow_bound_widgets = set()
        gui._combobox_overflow_tooltips = {}
        for _iteration in range(4):
            dialog = tk.Toplevel(self.root)
            dialog.withdraw()
            variable = tk.StringVar(dialog, value="KEYBOARD:1")
            combo = MappingCombobox(dialog, textvariable=variable, values=("NONE",))
            gui.parameter_binding_for_widget = lambda _widget: (variable, "NONE", "NONE")
            gui._bind_parameter_reset_widget(combo)
            tooltip = gui._combobox_overflow_tooltips[str(combo)]
            tooltip.show()
            self.assertEqual(len(gui._combobox_overflow_tooltips), 1)
            dialog.destroy()
            self.assertIsNone(tooltip.window)
            self.assertEqual(gui._combobox_overflow_tooltips, {})
            self.assertEqual(gui._combobox_overflow_bound_widgets, set())
            self.assertEqual(gui._parameter_wheel_bound_widgets, set())
            self.assertEqual(gui._parameter_reset_bound_widgets, set())


if __name__ == "__main__":
    unittest.main()
