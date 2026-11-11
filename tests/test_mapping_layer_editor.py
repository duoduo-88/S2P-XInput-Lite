import copy
import gc
from pathlib import Path
import sys
import tkinter as tk
from tkinter import ttk
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from config_gui import ConfigGUI
from config_utils import DEFAULT_CONFIG_PATH, load_config
from mapping_layers import normalize_layers
from switch2_input import SWITCH_BUTTONS


class MappingLayerEditorTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.withdraw()
        self.callback_errors = []
        self.root.report_callback_exception = lambda *error: self.callback_errors.append(error)
        gui = self.gui = object.__new__(ConfigGUI)
        gui.root = self.root
        gui.config = load_config(DEFAULT_CONFIG_PATH, persist=False)
        gui.tr = lambda text: text
        gui.ui_scale = 1
        buttons = dict(gui.config.items("buttons"))
        sticks = {
            side: dict(gui.config.items("stick_direction_" + side))
            for side in ("left", "right")
        }
        self.layers = normalize_layers(
            [{"id": name.lower(), "name": name, "activation_buttons": ["C"]}
             for name in ("One", "Two")], buttons, sticks, SWITCH_BUTTONS,
        )
        gui.mapping_layers = copy.deepcopy(self.layers)
        for name in ("_center_child_window", "_bring_child_to_front", "refresh_mapping_layer_rows"):
            setattr(gui, name, lambda *args, **kwargs: None)
        gui._mapping_layer_editor_width = lambda: 760
        gui.button_vars = {
            key.upper(): tk.StringVar(self.root, value=value) for key, value in buttons.items()
        }
        gui.button_options = ("NONE", "A", "B", "CUSTOM_KEYBOARD")
        gui.stick_direction_options = gui.button_options
        gui.gyro_activation_button_options = ("A", "B", "C")
        gui.left_output_shape_var = tk.IntVar(self.root, value=0)
        gui.right_output_shape_var = tk.IntVar(self.root, value=0)
        self.numeric_editors = []

        def bind_numeric_editor(label, variable, title, minimum, maximum, help_text, **options):
            self.numeric_editors.append((variable, options["setter"]))

        gui.bind_slider_value_editor = bind_numeric_editor
        self.persisted = []
        gui.persist_mapping_layer_files = lambda: self.persisted.append(copy.deepcopy(gui.mapping_layers))
        self.warnings = []
        warning_patch = patch(
            "config_gui.messagebox.showwarning",
            side_effect=lambda title, text, **kwargs: self.warnings.append((title, text)),
        )
        warning_patch.start()
        self.addCleanup(warning_patch.stop)

    def tearDown(self):
        self.root.update_idletasks()
        self.root.destroy()
        self.assertEqual(self.callback_errors, [])

    @staticmethod
    def descendants(widget):
        yield widget
        for child in widget.winfo_children():
            yield from MappingLayerEditorTests.descendants(child)

    def open_editor(self, *, activation=False):
        before = set(self.root.winfo_children())
        if activation:
            self.gui.edit_mapping_layer_activation(0)
        else:
            self.gui.edit_mapping_layer(0)
        return next(child for child in self.root.winfo_children()
                    if child not in before and isinstance(child, tk.Toplevel))

    def accept(self, dialog):
        next(child for child in self.descendants(dialog)
             if isinstance(child, ttk.Button) and child.cget("text") == "確定").invoke()

    def test_refreshing_layer_rows_releases_destroyed_variable_traces(self):
        self.gui.mapping_layers_list_frame = ttk.Frame(self.root)
        baseline = {str(name) for name in self.root.tk.call("info", "globals", "PY_VAR*")}
        for _ in range(3):
            ConfigGUI.refresh_mapping_layer_rows(self.gui)
            checkbutton = next(
                child for child in self.descendants(self.gui.mapping_layers_list_frame)
                if isinstance(child, ttk.Checkbutton)
            )
            previous = self.gui.mapping_layers[0]["enabled"]
            checkbutton.invoke()
            self.assertEqual(self.gui.mapping_layers[0]["enabled"], not previous)
        self.gui.mapping_layers_list_frame.destroy()
        del checkbutton
        gc.collect()
        remaining = {str(name) for name in self.root.tk.call("info", "globals", "PY_VAR*")}
        self.assertEqual(remaining, baseline)

    def test_each_numeric_editor_changes_only_its_own_stick(self):
        self.open_editor()
        self.assertEqual(len(self.numeric_editors), 10)
        requested = (9, 0.80, 0.40, 1450, 0.12)
        for side in range(2):
            for offset, value in enumerate(requested):
                with self.subTest(side=("LEFT", "RIGHT")[side], parameter=offset):
                    variable, setter = self.numeric_editors[side * 5 + offset]
                    other_side = self.numeric_editors[(1 - side) * 5:(2 - side) * 5]
                    before = [other.get() for other, _setter in other_side]
                    setter(value)
                    self.assertAlmostEqual(variable.get(), value)
                    self.assertEqual([other.get() for other, _setter in other_side], before)

    def test_deleted_or_changed_layer_is_not_overwritten_by_either_dialog(self):
        for activation in (False, True):
            for change in ("deleted", "changed"):
                with self.subTest(activation=activation, change=change):
                    self.gui.mapping_layers = copy.deepcopy(self.layers)
                    self.persisted.clear()
                    self.warnings.clear()
                    dialog = self.open_editor(activation=activation)
                    updated = copy.deepcopy(self.layers)
                    if change == "deleted":
                        updated.pop(0)
                    else:
                        updated[0]["name"] = "Externally renamed"
                    self.gui.mapping_layers = copy.deepcopy(updated)
                    self.accept(dialog)
                    self.assertEqual(self.gui.mapping_layers, updated)
                    self.assertEqual(self.persisted, [])
                    self.assertEqual(len(self.warnings), 1)
                    if dialog.winfo_exists():
                        dialog.destroy()

    def test_both_dialogs_follow_unchanged_layer_id_after_reload_and_reorder(self):
        for activation in (False, True):
            with self.subTest(activation=activation):
                self.gui.mapping_layers = copy.deepcopy(self.layers)
                self.numeric_editors.clear()
                dialog = self.open_editor(activation=activation)
                if activation:
                    next(child for child in self.descendants(dialog)
                         if isinstance(child, ttk.Checkbutton) and child.cget("text") == "A").invoke()
                else:
                    self.numeric_editors[0][1](9)
                self.gui.mapping_layers = list(reversed(copy.deepcopy(self.layers)))
                self.accept(dialog)
                self.assertEqual([layer["id"] for layer in self.gui.mapping_layers], ["two", "one"])
                self.assertEqual(self.gui.mapping_layers[0], self.layers[1])
                edited = self.gui.mapping_layers[1]
                if activation:
                    self.assertEqual(edited["activation_buttons"], ["A", "C"])
                else:
                    self.assertEqual(float(edited["stick_left"]["direction_deadzone"]), 9)

    def test_repeated_open_and_close_releases_editor_variables(self):
        def variable_names():
            return set(map(str, self.root.tk.splitlist(self.root.tk.call("info", "globals", "PY_VAR*"))))

        baseline = variable_names()
        for iteration in range(3):
            with self.subTest(iteration=iteration):
                dialog = self.open_editor()
                self.numeric_editors.clear()
                dialog.destroy()
                del dialog
                gc.collect()
                remaining = variable_names()
                self.assertEqual(len(remaining - baseline), 0)
                self.assertTrue(baseline.issubset(remaining))


if __name__ == "__main__":
    unittest.main()
