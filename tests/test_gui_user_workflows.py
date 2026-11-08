from pathlib import Path
import shutil
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch
from tkinter import ttk


SRC = Path(__file__).resolve().parents[1] / "src"
sys.path.insert(0, str(SRC))

import config_gui
import config_utils
import mapping_layers
import console_i18n


class GUIUserWorkflowTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.profiles = self.directory / "profiles"
        self.layers = self.directory / "layers"
        self.profiles.mkdir()
        self.layers.mkdir()
        for name in ("System Default", "General", "Action"):
            shutil.copy2(SRC / "profiles" / f"{name}.ini", self.profiles / f"{name}.ini")
        shutil.copy2(SRC / "layers" / "mouse.json", self.layers / "mouse.json")
        self.config_path = self.directory / "config.ini"
        config = config_utils.load_config(self.config_path)
        config.set("gui", "active_profile", "General")
        config.set("gui", "language", "en")
        config_utils.atomic_write_config(config, self.config_path)
        self.errors = []
        self.callback_errors = []
        real_load_layers = config_gui.load_layers

        def load_isolated_layers(*args, **kwargs):
            if len(args) < 5 and "layer_dir" not in kwargs:
                kwargs["layer_dir"] = self.layers
            return real_load_layers(*args, **kwargs)

        for patcher in (
            patch.object(config_gui, "CONFIG_PATH", self.config_path),
            patch.object(config_gui, "PROFILE_DIR", self.profiles),
            patch.object(config_gui, "LAYER_DIR", self.layers),
            patch.object(config_gui, "STATUS_PATH", self.directory / "status.json"),
            patch.object(mapping_layers, "PROFILE_DIR", self.profiles),
            patch.object(config_gui, "load_layers", side_effect=load_isolated_layers),
            patch.object(config_gui, "_GUI_ROOT", None),
            patch.object(config_gui, "_GUI_LANGUAGE", "en"),
            patch.object(console_i18n, "_cached_language", console_i18n.current_language()),
            patch.object(config_gui, "configure_tk_dpi", return_value=1),
            patch.object(config_gui, "is_vigembus_installed", return_value=True),
            patch.object(config_gui.ConfigGUI, "update_driver_status"),
            patch.object(config_gui.ConfigGUI, "update_hidhide_status", return_value={"state": "no_device"}),
            patch.object(config_gui.ConfigGUI, "update_controller_status"),
            patch.object(config_gui.ConfigGUI, "_start_system_tray"),
            patch.object(config_gui.ConfigGUI, "start_connection_on_launch"),
            patch.object(config_gui.ConfigGUI, "start_automatic_update_check"),
            patch.object(config_gui.messagebox, "showinfo"),
            patch.object(config_gui.messagebox, "showwarning"),
            patch.object(config_gui.messagebox, "showerror", side_effect=lambda *args, **kwargs: self.errors.append(args)),
            patch.object(config_gui.messagebox, "askyesnocancel", side_effect=AssertionError("Unexpected confirmation dialog")),
            patch.object(config_gui.messagebox, "askyesno", side_effect=AssertionError("Unexpected confirmation dialog")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        self.root = None
        self.addCleanup(self.close_gui)
        self.open_gui()

    def open_gui(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.report_callback_exception = lambda *args: self.callback_errors.append(args)
        self.gui = config_gui.ConfigGUI(self.root)
        self.root.update_idletasks()

    def close_gui(self):
        if self.root is None:
            return
        try:
            for job in self.root.tk.splitlist(self.root.tk.call("after", "info")):
                self.root.after_cancel(job)
            if self.root.winfo_exists():
                self.root.destroy()
        except tk.TclError:
            pass
        finally:
            self.root = None

    def tearDown(self):
        self.close_gui()
        self.assertEqual(self.callback_errors, [])
        self.assertEqual(self.errors, [])

    def test_edit_save_switch_save_as_and_reopen(self):
        self.assertEqual(self.gui.active_profile, "General")
        self.assertFalse(self.gui.has_unsaved_changes())
        self.gui.lf_strength_var.set("0.37")
        self.gui.left_deadzone_var.set("0.07")
        self.gui.button_vars["A"].set("KEYBOARD:CTRL+A")
        self.assertTrue(self.gui.has_unsaved_changes())
        self.assertTrue(self.gui.save_current_profile(show_message=False))
        self.assertFalse(self.gui.has_unsaved_changes())
        self.gui.apply_selected_profile("Action")
        self.assertEqual(self.gui.active_profile, "Action")
        self.assertFalse(self.gui.has_unsaved_changes())
        self.gui.apply_selected_profile("General")
        self.assertEqual(float(self.gui.lf_strength_var.get()), 0.37)
        self.assertEqual(float(self.gui.left_deadzone_var.get()), 0.07)
        self.assertEqual(self.gui.button_vars["A"].get(), "KEYBOARD:CTRL+A")
        self.assertFalse(self.gui.has_unsaved_changes())
        with patch.object(config_gui.simpledialog, "askstring", return_value="My Profile"):
            self.assertTrue(self.gui.save_profile_as())
        self.assertEqual(self.gui.active_profile, "My Profile")
        self.assertFalse(self.gui.has_unsaved_changes())
        self.close_gui()
        self.open_gui()
        self.assertEqual(self.gui.active_profile, "My Profile")
        self.assertEqual(float(self.gui.lf_strength_var.get()), 0.37)
        self.assertEqual(self.gui.button_vars["A"].get(), "KEYBOARD:CTRL+A")
        self.assertFalse(self.gui.has_unsaved_changes())
        self.pump()

    def pump(self):
        self.root.update()
        self.assertEqual(self.callback_errors, [])
        self.assertEqual(self.errors, [])

    def test_profile_transition_cancel_discard_save_and_protected_default(self):
        self.gui.lf_strength_var.set("0.43")
        before = self.config_path.read_bytes()
        with patch.object(config_gui.messagebox, "askyesnocancel", return_value=None):
            self.gui.apply_selected_profile("Action")
        self.pump()
        self.assertEqual(self.gui.active_profile, "General")
        self.assertEqual(self.config_path.read_bytes(), before)
        self.assertEqual(float(self.gui.lf_strength_var.get()), 0.43)
        self.assertTrue(self.gui.has_unsaved_changes())
        with patch.object(config_gui.messagebox, "askyesnocancel", return_value=False):
            self.gui.apply_selected_profile("Action")
        self.assertEqual(self.gui.active_profile, "Action")
        self.assertFalse(self.gui.has_unsaved_changes())
        self.gui.apply_selected_profile("General")
        self.gui.lf_strength_var.set("0.43")
        with patch.object(config_gui.messagebox, "askyesnocancel", return_value=True):
            self.gui.apply_selected_profile("Action")
        self.gui.apply_selected_profile("General")
        self.assertEqual(float(self.gui.lf_strength_var.get()), 0.43)
        self.gui.apply_selected_profile("System Default")
        original = (self.profiles / "System Default.ini").read_bytes()
        self.gui.lf_strength_var.set("0.38")
        with (
            patch.object(config_gui.messagebox, "askyesnocancel", return_value=True),
            patch.object(config_gui.simpledialog, "askstring", return_value="Custom Default"),
        ):
            self.gui.apply_selected_profile("Action")
        self.assertEqual((self.profiles / "System Default.ini").read_bytes(), original)
        self.gui.apply_selected_profile("Custom Default")
        self.assertEqual(float(self.gui.lf_strength_var.get()), 0.38)
        self.assertFalse(self.gui.has_unsaved_changes())
        self.pump()

    def test_language_toggle_and_invalid_input_recovery(self):
        self.gui.lf_strength_var.set("0.39")
        self.gui.button_vars["A"].set("KEYBOARD:CTRL+A")
        for expected in ("zh", "en", "zh", "en"):
            self.gui.toggle_language()
            self.pump()
            self.assertEqual(self.gui.language, expected)
            self.assertEqual(self.gui.button_vars["A"].get(), "KEYBOARD:CTRL+A")
            self.assertTrue(self.gui.has_unsaved_changes())
        before = self.config_path.read_bytes()
        self.gui.lf_strength_var.set("bad input")
        with patch.object(config_gui.messagebox, "showerror") as error:
            self.assertFalse(self.gui.save_current_profile(show_message=False))
        error.assert_called_once()
        self.assertEqual(self.config_path.read_bytes(), before)
        self.gui.lf_strength_var.set("0.39")
        self.assertTrue(self.gui.save_current_profile(show_message=False))
        self.assertFalse(self.gui.has_unsaved_changes())
        self.close_gui()
        self.open_gui()
        self.pump()
        self.assertEqual(self.gui.language, "en")
        self.assertEqual(float(self.gui.lf_strength_var.get()), 0.39)
        self.assertEqual(self.gui.button_vars["A"].get(), "KEYBOARD:CTRL+A")
        self.assertFalse(self.gui.has_unsaved_changes())

    @staticmethod
    def descendants(widget):
        yield widget
        for child in widget.winfo_children():
            yield from GUIUserWorkflowTests.descendants(child)

    def test_mapping_layer_management_cancel_and_persistence(self):
        original_layers = len(self.gui.mapping_layers)
        with patch.object(self.gui, "_ask_mapping_layer_name", return_value="Workflow Layer"):
            self.gui.add_mapping_layer()
        self.assertEqual(len(self.gui.mapping_layers), original_layers + 1)
        layer_id = self.gui.mapping_layers[-1]["id"]
        with patch.object(self.gui, "_ask_mapping_layer_name", return_value="Renamed Layer"):
            self.gui.rename_mapping_layer(len(self.gui.mapping_layers) - 1)
        self.gui.move_mapping_layer(len(self.gui.mapping_layers) - 1, -original_layers)
        self.assertEqual(self.gui.mapping_layers[0]["id"], layer_id)
        before_editor = repr(self.gui.mapping_layers)
        before_children = set(self.root.winfo_children())
        with patch.object(self.gui, "_bring_child_to_front"):
            self.gui.edit_mapping_layer(0)
        editor = next(child for child in self.root.winfo_children()
                      if child not in before_children and isinstance(child, tk.Toplevel))
        combo = next(widget for widget in self.descendants(editor)
                     if isinstance(widget, config_gui.MappingCombobox))
        combo.variable.set("KEYBOARD:CTRL+B")
        self.assertEqual(combo.full_text(), "KEYBOARD:CTRL+B")
        next(widget for widget in self.descendants(editor)
             if isinstance(widget, ttk.Button) and widget.cget("text") == self.gui.tr("取消")).invoke()
        self.assertFalse(editor.winfo_exists())
        self.assertEqual(repr(self.gui.mapping_layers), before_editor)
        self.assertTrue(self.gui.has_unsaved_changes())
        self.assertTrue(self.gui.save_current_profile(show_message=False))
        self.assertFalse(self.gui.has_unsaved_changes())
        self.gui.apply_selected_profile("Action")
        self.gui.apply_selected_profile("General")
        self.assertEqual(self.gui.mapping_layers[0]["id"], layer_id)
        self.assertEqual(self.gui.mapping_layers[0]["name"], "Renamed Layer")
        with patch.object(config_gui.messagebox, "askyesno", return_value=False):
            self.gui.delete_mapping_layer(0)
        self.assertEqual(self.gui.mapping_layers[0]["id"], layer_id)
        with patch.object(config_gui.messagebox, "askyesno", return_value=True):
            self.gui.delete_mapping_layer(0)
        self.assertEqual(len(self.gui.mapping_layers), original_layers)
        self.assertTrue(self.gui.save_current_profile(show_message=False))
        self.close_gui()
        self.open_gui()
        self.assertEqual(len(self.gui.mapping_layers), original_layers)
        self.assertNotIn(layer_id, [layer["id"] for layer in self.gui.mapping_layers])
        self.assertFalse(self.gui.has_unsaved_changes())
        self.pump()

    def test_cancel_close_then_save_close_and_reopen(self):
        self.gui.lf_strength_var.set("0.44")
        with patch.object(config_gui.messagebox, "askyesno", return_value=False):
            self.gui.on_close()
        self.assertTrue(self.root.winfo_exists())
        self.assertFalse(self.gui._close_in_progress)
        self.assertEqual(float(self.gui.lf_strength_var.get()), 0.44)
        self.assertTrue(self.gui.save_current_profile(show_message=False))
        with (
            patch.object(self.gui, "_close_mode_warning_is_suppressed", return_value=False),
            patch.object(self.gui, "_prompt_close_mode_warning", return_value=(False, False)),
        ):
            self.gui.on_close()
        self.assertTrue(self.root.winfo_exists())
        self.assertFalse(self.gui._close_in_progress)
        with (
            patch.object(self.gui, "_close_mode_warning_is_suppressed", return_value=False),
            patch.object(self.gui, "_prompt_close_mode_warning", return_value=(True, False)),
        ):
            self.gui.on_close()
        self.assertFalse(self.root.tk.call("info", "commands", str(self.root)))
        self.close_gui()
        self.open_gui()
        self.assertEqual(float(self.gui.lf_strength_var.get()), 0.44)
        self.assertFalse(self.gui.has_unsaved_changes())
        self.pump()


if __name__ == "__main__":
    unittest.main()
