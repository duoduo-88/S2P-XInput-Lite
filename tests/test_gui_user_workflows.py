from pathlib import Path
import json
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
from direction_deadzones import CUSTOM_DEADZONE_KEY


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

    def test_gyro_compensation_settings_save_both_axes(self):
        curve = self.gui.gyro_curve_button
        controls = curve.master.pack_slaves()
        compensation = controls[controls.index(curve) + 1]
        self.assertEqual(compensation.cget("text"), self.gui.tr("補償設定"))
        stabilization = self.gui.gyro_stabilization_button_entry.master.master
        help_buttons = [
            widget for row in stabilization.winfo_children() if isinstance(row, ttk.Frame)
            for widget in row.winfo_children()
            if "抑制突波：保留原本的轉動" in getattr(widget, "parameter_help_text", "")
        ]
        self.assertEqual(len(help_buttons), 1)
        self.assertEqual(help_buttons[0].cget("text"), "?")
        compensation.invoke()
        overlay = self.gui._gyro_compensation_window
        overlay.compensation["X"].set("0.75")
        overlay.compensation["Y"].set("0.16")
        overlay.apply_button.invoke()
        self.assertEqual(float(self.gui.gyro_x_ratio_var.get()), 1.0)
        self.assertEqual(float(self.gui.gyro_y_ratio_var.get()), 1.0)
        self.assertTrue(self.gui.save_current_profile(show_message=False))
        self.close_gui()
        self.open_gui()
        self.assertEqual(float(self.gui.gyro_y_ratio_var.get()), 1.0)
        self.gui.open_gyro_settings()
        self.assertEqual(self.gui.gyro_settings_tab.master.select(), str(self.gui.gyro_settings_tab))
        overlay = self.gui._gyro_compensation_window
        self.assertEqual(overlay.compensation["X"].get(), "0.75")
        self.assertEqual(overlay.compensation["Y"].get(), "0.16")
        self.gui.apply_selected_profile("Action")
        self.assertEqual(self.gui.active_profile, "Action")
        self.assertFalse(overlay.window.winfo_exists())
        self.gui.open_gyro_compensation_window()
        overlay = self.gui._gyro_compensation_window
        self.assertEqual(overlay.compensation["X"].get(), "1.00")
        self.assertEqual(overlay.compensation["Y"].get(), "1.00")

    def test_gyro_compensation_is_hidden_for_mouse_output(self):
        curve = self.gui.gyro_curve_button
        controls = curve.master
        compensation = next(button for button in controls.pack_slaves()
                            if button.cget("text") == self.gui.tr("補償設定"))
        compensation.invoke()
        overlay = self.gui._gyro_compensation_window

        self.gui.gyro_target_var.set("MOUSE")
        self.assertNotIn(compensation, controls.pack_slaves())
        self.assertFalse(overlay.window.winfo_exists())
        self.gui.open_gyro_compensation_window()
        self.assertFalse(self.gui._gyro_compensation_window.window.winfo_exists())

        for target in ("LEFT_STICK", "RIGHT_STICK"):
            self.gui.gyro_target_var.set(target)
            buttons = controls.pack_slaves()
            self.assertEqual(buttons[buttons.index(curve) + 1], compensation)

    def test_edit_save_switch_save_as_and_reopen(self):
        self.assertEqual(self.gui.active_profile, "General")
        self.assertFalse(self.gui.has_unsaved_changes())
        self.gui.lf_strength_var.set("0.37")
        self.gui.left_deadzone_var.set("0.07")
        self.gui.button_vars["A"].set("KEYBOARD:CTRL+A")
        zones = '{"4WAY":[[5,20],[0,0],[0,0],[0,0]]}'
        self.gui.stick_direction_custom_deadzone_vars["LEFT"].set(zones)
        self.assertTrue(self.gui.has_unsaved_changes())
        self.assertTrue(self.gui.save_current_profile(show_message=False))
        self.assertFalse(self.gui.has_unsaved_changes())
        self.gui.apply_selected_profile("Action")
        self.assertEqual(self.gui.active_profile, "Action")
        self.assertFalse(self.gui.has_unsaved_changes())
        self.assertEqual(self.gui.stick_direction_custom_deadzone_vars["LEFT"].get(), "")
        self.gui.apply_selected_profile("General")
        self.assertEqual(float(self.gui.lf_strength_var.get()), 0.37)
        self.assertEqual(float(self.gui.left_deadzone_var.get()), 0.07)
        self.assertEqual(self.gui.button_vars["A"].get(), "KEYBOARD:CTRL+A")
        self.assertTrue(self.gui.config.has_option("stick_direction_left", CUSTOM_DEADZONE_KEY))
        self.assertTrue(self.gui.stick_direction_custom_deadzone_vars["LEFT"].get())
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
        self.assertTrue(self.gui.stick_direction_custom_deadzone_vars["LEFT"].get())
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

    def test_save_keeps_layer_changed_on_disk_before_folder_watcher_runs(self):
        self.gui.lf_strength_var.set("0.42")
        self.gui.mapping_layers[0]["activation_buttons"] = ["B"]
        layer_path = self.layers / "mouse.json"
        external = json.loads(layer_path.read_text(encoding="utf-8"))
        external["layer"]["activation_buttons"] = ["X", "Y"]
        layer_path.write_text(json.dumps(external), encoding="utf-8")
        disk_layer = layer_path.read_bytes()
        disk_config = self.config_path.read_bytes()

        with patch.object(config_gui.messagebox, "showerror") as error:
            self.assertFalse(self.gui.save_current_profile(show_message=False))

        error.assert_called_once()
        self.assertEqual(layer_path.read_bytes(), disk_layer)
        self.assertEqual(self.config_path.read_bytes(), disk_config)
        self.assertEqual(self.gui.mapping_layers[0]["activation_buttons"], ["B"])
        with patch.object(config_gui.messagebox, "askyesno", return_value=True):
            self.gui.sync_mapping_layer_directory()
        self.assertEqual(self.gui.mapping_layers[0]["activation_buttons"], ["X", "Y"])
        self.assertTrue(self.gui.save_current_profile(show_message=False))

    def test_failed_save_requires_reloading_a_new_external_layer_before_retry(self):
        self.gui.lf_strength_var.set("0.42")
        external = json.loads((self.layers / "mouse.json").read_text(encoding="utf-8"))
        external["layer"]["id"] = "external-layer"
        external_path = self.layers / "External.json"

        def fail_after_external_write(*_args, **_kwargs):
            external_path.write_text(json.dumps(external), encoding="utf-8")
            raise OSError("forced profile write failure")

        with patch.object(config_gui.messagebox, "showerror"):
            with patch.object(config_gui, "save_profile", side_effect=fail_after_external_write):
                self.assertFalse(self.gui.save_current_profile(show_message=False))
            self.assertTrue(external_path.exists())
            self.assertFalse(self.gui.save_current_profile(show_message=False))

        self.assertEqual(json.loads(external_path.read_text(encoding="utf-8")), external)

    def test_failed_save_reads_an_external_config_without_rewriting_it(self):
        self.gui.lf_strength_var.set("0.42")
        external = b"[gui]\nactive_profile = Alpha\n"

        def fail_after_external_write(*_args, **_kwargs):
            self.config_path.write_bytes(external)
            raise OSError("forced profile write failure")

        with (
            patch.object(config_gui.messagebox, "showerror"),
            patch.object(config_gui, "save_profile", side_effect=fail_after_external_write),
        ):
            self.assertFalse(self.gui.save_current_profile(show_message=False))

        self.assertEqual(self.config_path.read_bytes(), external)
        self.assertEqual(float(self.gui.lf_strength_var.get()), 0.42)

    def test_save_rejects_external_config_edits_during_layer_writes(self):
        self.gui.lf_strength_var.set("0.42")
        self.gui.mapping_layers[0]["activation_buttons"] = ["B"]
        layer_path = self.layers / "mouse.json"
        original_layer = layer_path.read_bytes()
        profile_path = self.profiles / "General.ini"
        original_profile = profile_path.read_bytes()
        external = self.config_path.read_bytes() + b"\n[gyro.EXTERNAL]\nbias = 0.1, 0.2, 0.3\n"
        save_layers = config_gui.save_layers

        def save_layers_then_edit_config(*args, **kwargs):
            result = save_layers(*args, **kwargs)
            self.config_path.write_bytes(external)
            return result

        with (
            patch.object(config_gui, "save_layers", side_effect=save_layers_then_edit_config),
            patch.object(config_gui.messagebox, "showerror"),
        ):
            self.assertFalse(self.gui.save_current_profile(show_message=False))

        self.assertEqual(self.config_path.read_bytes(), external)
        self.assertEqual(profile_path.read_bytes(), original_profile)
        self.assertEqual(layer_path.read_bytes(), original_layer)
        self.assertEqual(self.gui.mapping_layers[0]["activation_buttons"], ["B"])

    def test_save_does_not_copy_a_concurrent_config_edit_into_the_profile(self):
        self.gui.lf_strength_var.set("0.42")
        profile_path = self.profiles / "General.ini"
        original_profile = profile_path.read_bytes()
        external_config = config_utils.load_config(self.config_path, persist=False)
        external_config.set("rumble", "lf_strength", "0.91")
        write = config_utils.SettingsFileChanges.write_config

        def write_then_edit_config(changes, config, path):
            write(changes, config, path)
            config_utils.atomic_write_config(external_config, path)

        with (
            patch.object(config_utils.SettingsFileChanges, "write_config", write_then_edit_config),
            patch.object(config_gui.messagebox, "showerror"),
        ):
            self.assertFalse(self.gui.save_current_profile(show_message=False))

        self.assertEqual(profile_path.read_bytes(), original_profile)
        self.assertEqual(config_utils.load_config(self.config_path, persist=False).get("rumble", "lf_strength"), "0.91")
        self.assertEqual(float(self.gui.lf_strength_var.get()), 0.42)

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
