import configparser
import json
import math
import tkinter as tk
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from tkinter import ttk
from unittest.mock import Mock, patch

from config_gui import ConfigGUI
from config_utils import load_stick_calibration
from direction_deadzones import CUSTOM_DEADZONE_KEY, parse_custom_zones
from gui_sections.direction_deadzone import DirectionDeadzonePreview, _point
from localization import translate_text
from mapping_layers import _layer_payload, normalize_layers
from settings_schema import DEFAULT_CONFIG_PATH, SettingValidationError, read_section_settings, write_section_settings
from standalone_profile import (
    STANDALONE_PROFILE_MAX_BYTES,
    STICK_DIRECTION_KEYS,
    compile_standalone_profile,
)
from stick_processing import _compile_stick_direction_settings
from xinput_controller import XInputController


class DirectionDeadzoneTests(unittest.TestCase):
    def test_custom_settings_round_trip_without_changing_uniform_default(self):
        config = configparser.ConfigParser()
        config.read(DEFAULT_CONFIG_PATH, encoding="utf-8")
        section = "stick_direction_left"
        config.set(section, "direction_deadzone_custom", '{"8WAY":[[0,20],[0,0],[0,0],[0,0],[0,0],[0,0],[0,0],[0,0]]}')

        values = read_section_settings(config, section, strict=True)
        self.assertEqual(values["direction_deadzone_custom"]["8WAY"][0], (0, 20))
        uniform = values["direction_deadzone"]
        write_section_settings(config, section, values)
        self.assertEqual(read_section_settings(config, section, strict=True), values)
        self.assertEqual(config.getfloat(section, "direction_deadzone"), uniform)

    def test_rotated_boundary_controls_acquisition_and_held_direction(self):
        zones = '{"4WAY":[[15,20],[0,0],[0,0],[0,0]]}'
        stick = {"mode": "4WAY", "direction_deadzone_custom": zones}
        controller = object.__new__(XInputController)
        controller.stick_direction_config = _compile_stick_direction_settings({"left": stick, "right": {}})
        controller._active_stick_directions = {"LEFT": None}

        def direction(degrees, magnitude=1):
            angle = math.radians(degrees)
            return controller._get_stick_direction(magnitude * math.cos(angle), magnitude * math.sin(angle), "LEFT")

        self.assertEqual(direction(48), "RIGHT")
        self.assertIsNone(direction(60))
        self.assertEqual(direction(70), "UP")
        controller._active_stick_directions["LEFT"] = "RIGHT"
        self.assertEqual(direction(60), "RIGHT")
        self.assertEqual(direction(70), "UP")
        self.assertIsNone(direction(60, 0.1))
        self.assertEqual(direction(359), "RIGHT")

    def test_custom_zone_cannot_cover_adjacent_direction_centers(self):
        from direction_deadzones import clamp_zone, zone_edges

        zones = [clamp_zone("8WAY", 100, 60)] * 8
        for lower, upper in zone_edges("8WAY", zones):
            self.assertGreater(lower, 0)
            self.assertLess(upper, 45)
            self.assertAlmostEqual(upper - lower, 27)

    def test_legacy_mapping_layer_does_not_inherit_main_custom_zones(self):
        custom = '{"4WAY":[[15,20],[0,0],[0,0],[0,0]]}'
        main = {"mode": "4WAY", "direction_deadzone": "5", CUSTOM_DEADZONE_KEY: custom}
        layers = normalize_layers(
            [{"id": "layer", "name": "Layer", "activation_buttons": ["A"]},
             {"id": "custom", "name": "Custom", "activation_buttons": ["A"],
              "stick_left": {CUSTOM_DEADZONE_KEY: custom}}],
            {}, {"left": main, "right": {}}, ("A",),
        )
        self.assertNotIn(CUSTOM_DEADZONE_KEY, layers[0]["stick_left"])
        self.assertEqual(parse_custom_zones(layers[1]["stick_left"][CUSTOM_DEADZONE_KEY]), parse_custom_zones(custom))

    def test_layer_json_accepts_native_zones_and_omits_empty_settings(self):
        zones = {"4WAY": [[15, 20], [0, 0], [0, 0], [0, 0]]}
        raw = {"id": "zones", "stick_left": {CUSTOM_DEADZONE_KEY: zones},
               "stick_right": {CUSTOM_DEADZONE_KEY: ""}}
        layers = normalize_layers([raw], {}, {"left": {}, "right": {}}, ())
        layer = layers[0]
        compiled = _compile_stick_direction_settings({"left": layer["stick_left"], "right": layer["stick_right"]})
        self.assertEqual(compiled["LEFT"]["direction_zone_edges"][0], (51.0, 69.0))
        self.assertNotIn(CUSTOM_DEADZONE_KEY, layer["stick_right"])
        self.assertNotIn(CUSTOM_DEADZONE_KEY, _layer_payload(raw)["layer"]["stick_right"])

    def test_deep_invalid_zone_data_fails_validation_and_falls_back_at_runtime(self):
        config = configparser.ConfigParser()
        config["stick_direction_left"] = {CUSTOM_DEADZONE_KEY: "[" * 1200 + "]" * 1200}
        with self.assertRaises(SettingValidationError):
            read_section_settings(config, "stick_direction_left", strict=True)
        self.assertEqual(read_section_settings(config, "stick_direction_left")[CUSTOM_DEADZONE_KEY], {})

    def test_incomplete_layer_scan_preserves_mappings_and_recovers(self):
        zones = {"4WAY": [[0, 20]] * 4}
        layer = _layer_payload({
            "id": "zones", "mode": "HOLD", "buttons": {}, "activation_buttons": ["A"],
            "stick_left": {"mode": "4WAY", CUSTOM_DEADZONE_KEY: zones},
            "stick_right": {"mode": "4WAY"},
        })
        gui = object.__new__(ConfigGUI)
        gui.tr = str
        gui.root = Mock()
        gui.config = configparser.ConfigParser()
        gui.mapping_layers = []
        gui._mapping_layer_file_defaults = lambda: ({}, {"left": {}, "right": {}})
        gui.refresh_mapping_layer_rows = Mock()

        with TemporaryDirectory() as temporary:
            directory = Path(temporary)
            path = directory / "Zones.json"
            path.write_text(json.dumps(layer), encoding="utf-8")
            with (
                patch("config_gui.LAYER_DIR", directory),
                patch("config_gui.messagebox.showerror") as error,
            ):
                gui.poll_mapping_layer_directory()
                original = gui.mapping_layers
                self.assertEqual(len(original), 1)
                bad_payloads = {
                    "partial write": "{",
                    "too deeply nested": json.dumps(layer)[:-1]
                    + ', "extra": ' + "[" * 1200 + "]" * 1200 + "}",
                }
                for reason, content in bad_payloads.items():
                    with self.subTest(reason=reason):
                        path.write_text(content, encoding="utf-8")
                        gui.poll_mapping_layer_directory()
                        self.assertIs(gui.mapping_layers, original)
                        self.assertIsNotNone(gui._mapping_layer_load_error)

                layer["layer"]["stick_left"][CUSTOM_DEADZONE_KEY] = {"4WAY": [[0, 30]] * 4}
                path.write_text(json.dumps(layer), encoding="utf-8")
                gui.poll_mapping_layer_directory()
                self.assertIsNone(gui._mapping_layer_load_error)
                self.assertEqual(len(gui.mapping_layers), 1)
                actual = parse_custom_zones(gui.mapping_layers[0]["stick_left"][CUSTOM_DEADZONE_KEY])
                self.assertEqual(actual["4WAY"][0], (0, 30))
                self.assertEqual(gui.root.after.call_count, 4)
                self.assertEqual(error.call_count, 2)

                (directory / "Other.json").write_text("{", encoding="utf-8")
                gui.poll_mapping_layer_directory()
                self.assertIsNone(gui._mapping_layer_load_error)
                self.assertEqual(len(gui.mapping_layers), 1)
                self.assertEqual(error.call_count, 2)

    def test_esp32_profile_serializes_only_active_custom_direction_edges(self):
        custom = '{"4WAY":[[15,20],[0,0],[0,0],[0,0]]}'
        settings = {"sections": {
            "stick_direction_left": {"mode": "4WAY", CUSTOM_DEADZONE_KEY: custom},
            "stick_direction_right": {"mode": "8WAY", CUSTOM_DEADZONE_KEY: custom},
        }}
        layer = {
            "id": "layer", "enabled": True, "activation_buttons": ["A"],
            "stick_right": {
                "mode": "8WAY", CUSTOM_DEADZONE_KEY: {"8WAY": [[-5, 40]] * 8},
            },
        }
        compiled = compile_standalone_profile("General", settings, [layer])
        document = json.loads(compiled.payload)

        self.assertFalse(compiled.blocking_issues)
        self.assertEqual(document["stick_direction_left"]["direction_deadzone_edges"],
                         [[51, 69], [45, 45], [45, 45], [45, 45]])
        self.assertNotIn("direction_deadzone_edges", document["stick_direction_right"])
        self.assertEqual(document["mapping_layers"][0]["stick_right"]["direction_deadzone_edges"],
                         [[8.5, 26.5]] * 8)
        self.assertEqual(settings["sections"]["stick_direction_left"][CUSTOM_DEADZONE_KEY], custom)

    def test_inactive_custom_layer_settings_do_not_use_firmware_payload_space(self):
        zones = json.dumps({"8WAY": [[1.12345678901234, 18.12345678901234]] * 8})
        layers = [{"id": str(index), "enabled": True, "activation_buttons": ["A"],
                   "stick_left": {"mode": "4WAY", CUSTOM_DEADZONE_KEY: zones},
                   "stick_right": {"mode": "4WAY", CUSTOM_DEADZONE_KEY: zones}}
                  for index in range(8)]
        compiled = compile_standalone_profile("General", {"sections": {}}, layers)
        self.assertFalse(compiled.blocking_issues)
        self.assertEqual(len(compiled.document["mapping_layers"]), 8)
        for layer in compiled.document["mapping_layers"]:
            self.assertEqual(layer["stick_left"], {"mode": "4WAY"})
            self.assertEqual(layer["stick_right"], {"mode": "4WAY"})
        self.assertEqual(layers[0]["stick_left"][CUSTOM_DEADZONE_KEY], zones)

    def test_eight_full_layers_with_custom_deadzones_fit_in_standalone_profile(self):
        config = configparser.ConfigParser()
        config.read(DEFAULT_CONFIG_PATH, encoding="utf-8")
        sections = {name: dict(config[name]) for name in config.sections()}
        buttons = sections["buttons"]
        zones = {"8WAY": [[1.123456789, 18.123456789]] * 8}
        sticks = {side: sections[f"stick_direction_{side}"] for side in ("left", "right")}
        for stick in sticks.values():
            stick.update(mode="8WAY", direction_deadzone_custom=zones, up="Y")
        layers = normalize_layers([
            {
                "name": f"Layer {index}", "enabled": True, "activation_buttons": ["GL"],
                "buttons": {"a": "NONE"},
                "stick_left": {CUSTOM_DEADZONE_KEY: zones, "up": "NONE", "right": "B"},
                "stick_right": {CUSTOM_DEADZONE_KEY: zones, "trigger_threshold": "0.75"},
            }
            for index in range(8)
        ], buttons, sticks, ("GL",))
        settings = {
            "sections": sections,
            "buttons": buttons,
            "direction_mappings": {
                side: {name: stick[name] for name in STICK_DIRECTION_KEYS}
                for side, stick in sticks.items()
            },
            "calibration": load_stick_calibration(config),
        }

        compiled = compile_standalone_profile("General", settings, layers)
        self.assertFalse(compiled.blocking_issues)
        self.assertLessEqual(len(compiled.payload), STANDALONE_PROFILE_MAX_BYTES)
        self.assertEqual(len(compiled.document["mapping_layers"]), 8)
        for layer in compiled.document["mapping_layers"]:
            self.assertEqual(layer["buttons"], {"a": "NONE"})
            self.assertEqual(layer["stick_left"]["right"], "B")
            self.assertEqual(layer["stick_left"].get("up", "NONE"), "NONE")
            self.assertEqual(float(layer["stick_right"]["trigger_threshold"]), 0.75)
            for side in ("left", "right"):
                self.assertEqual(len(layer[f"stick_{side}"]["direction_deadzone_edges"]), 8)

    def test_failed_live_reload_can_restore_previous_layer_snapshot(self):
        config = configparser.ConfigParser()
        config.read(DEFAULT_CONFIG_PATH, encoding="utf-8")
        calibration = load_stick_calibration(config)
        old_layer = {"id": "zone", "enabled": True, "mode": "HOLD", "buttons": {}, "activation_buttons": ["A"],
                     "stick_left": {"mode": "4WAY", CUSTOM_DEADZONE_KEY: {"4WAY": [[15, 20]] * 4}},
                     "stick_right": {"mode": "4WAY"}}
        with patch("xinput_controller.load_layers", return_value=[old_layer]):
            controller = XInputController(config, calibration, pad=Mock(), activate_runtime=False)
        previous_layers = controller.mapping_layers
        with patch("xinput_controller.load_layers", return_value=[]) as load:
            controller.reconfigure(config, calibration)
            self.assertFalse(controller.mapping_layers)
            controller.reconfigure(config, calibration, mapping_layers=previous_layers)
        load.assert_called_once()
        self.assertEqual(controller.mapping_layers, [old_layer])


class DirectionDeadzoneEditorTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.root.geometry("600x600+60+60")
        self.addCleanup(self.root.destroy)
        self.errors = []
        self.root.report_callback_exception = lambda *error: self.errors.append(error)
        self.gui = object.__new__(ConfigGUI)
        self.gui.root = self.root
        self.gui.tr = str
        self.gui.ui_scale = 1
        self.gui._modal_windows = []
        self.values = {
            key: tk.StringVar(self.root, value=value)
            for key, value in (
                ("mode", "4WAY"), ("direction_deadzone", "5"), (CUSTOM_DEADZONE_KEY, ""),
                ("trigger_threshold", "0.60"), ("release_threshold", "0.50"),
            )
        }
        shape = tk.IntVar(self.root, value=10)
        self.preview = DirectionDeadzonePreview(self.root, self.gui, "LEFT", self.values, shape)
        self.preview.pack()
        self.scale = ttk.Scale(self.root, from_=0, to=20, variable=self.values["direction_deadzone"])
        self.scale.pack()
        self.label = ttk.Label(self.root)
        self.label.pack()
        self.preview.attach_slider(self.scale, self.label)
        self.gui.bind_slider_value_editor(
            self.label, self.values["direction_deadzone"], "死區", 0, 20, "",
            on_disabled_click=self.preview.open_editor,
        )
        self.root.update()

    def _editor(self):
        window = self.preview._dialog
        self.assertIs(self.root.grab_current(), window)
        frame = window.winfo_children()[0]
        canvas = next(widget for widget in frame.winfo_children() if isinstance(widget, tk.Canvas))
        fields = [canvas.nametowidget(canvas.itemcget(item, "window")) for item in canvas.find_withtag("entries")]
        actions = {widget.cget("text"): widget for widget in frame.winfo_children()[-1].winfo_children()}
        return canvas, fields, actions

    def test_thumbnail_tooltip_stays_clear_of_thumbnail(self):
        self.preview._tip.show()
        self.root.update()
        tip = self.preview._tip.window
        self.assertGreaterEqual(tip.winfo_rooty(), self.preview.winfo_rooty() + self.preview.winfo_height())
        self.preview._tip.hide()

    def test_parameter_and_button_rows_align_with_radar_background(self):
        for language in ("zh", "en"):
            with self.subTest(language=language):
                self.gui.tr = lambda text: translate_text(text, language)
                self.preview.open_editor()
                self.root.update()
                canvas, _, actions = self._editor()
                cancel = actions[self.gui.tr("取消")]
                controls, buttons = canvas.master.winfo_children()[-2:]
                border = int(canvas.cget("highlightthickness"))
                left = canvas.winfo_rootx() + border
                right = canvas.winfo_rootx() + canvas.winfo_width() - border
                actual = [(row.winfo_rootx(), row.winfo_rootx() + row.winfo_width()) for row in (controls, buttons)]
                cancel.invoke()
                self.root.update()
                self.assertEqual(actual, [(left, right), (left, right)])

    def test_radar_fields_scrub_and_restore_saved_or_default_values(self):
        saved = self.values[CUSTOM_DEADZONE_KEY]
        self.gui.parameter_saved_registry = {
            str(saved): (saved, '{"4WAY":[[0,32],[0,0],[0,0],[0,0]]}'),
        }
        self.preview.open_editor()
        self.root.update()
        canvas, fields, actions = self._editor()
        width = fields[0].winfo_children()[0]
        trigger = canvas.master.winfo_children()[-2].grid_slaves(row=0, column=3)[0]

        def scrub(entry, dx, dy=0):
            entry.event_generate("<ButtonPress-1>", x=10, y=8, rootx=100, rooty=100)
            entry.event_generate("<B1-Motion>", rootx=100 + dx, rooty=100 + dy)
            entry.event_generate("<ButtonRelease-1>")

        scrub(width, 0, 24)
        self.assertEqual(width.get(), "11.11")
        scrub(width, 24)
        self.assertEqual(width.get(), "14.11")
        scrub(trigger, -16)
        self.assertEqual(trigger.get(), "58")

        menu = Mock()
        with patch("config_gui.tk.Menu", return_value=menu):
            width.event_generate("<Button-3>", x=10, y=8)
        commands = [call.kwargs for call in menu.add_command.call_args_list]
        self.assertEqual([command["label"] for command in commands], ["還原上次儲存", "還原系統預設"])
        commands[0]["command"]()
        self.assertEqual(width.get(), "32")
        commands[1]["command"]()
        self.assertEqual(width.get(), "11.11")
        actions["取消"].invoke()
        self.root.update()
        self.assertFalse(self.preview.custom_zones())
        self.assertEqual(self.values["trigger_threshold"].get(), "0.60")
        self.assertEqual(self.errors, [])

    def test_accepting_typed_uniform_deadzone_keeps_uniform_mode(self):
        self.preview.open_editor()
        self.root.update()
        canvas, _, actions = self._editor()
        controls = canvas.master.winfo_children()[-2]
        uniform = controls.grid_slaves(row=0, column=1)[0]
        uniform.delete(0, "end")
        uniform.insert(0, "11")
        actions["確定"].invoke()
        self.root.update()
        self.assertEqual(self.values["direction_deadzone"].get(), "11")
        self.assertEqual(self.preview.custom_zones(), {})
        self.assertFalse(self.scale.instate(["disabled"]))

    def test_open_editor_cannot_overwrite_reloaded_settings_in_same_mode(self):
        self.gui.active_profile = "General"
        self.preview.open_editor()
        self.root.update()
        _, fields, actions = self._editor()
        width = fields[0].winfo_children()[0]
        width.delete(0, "end")
        width.insert(0, "25")
        self.values["trigger_threshold"].set("0.70")
        newer_zones = '{"4WAY":[[0,32],[0,0],[0,0],[0,0]]}'
        self.values[CUSTOM_DEADZONE_KEY].set(newer_zones)
        with patch("gui_sections.direction_deadzone.messagebox.showwarning") as warning:
            actions["確定"].invoke()
        self.root.update()
        self.assertEqual(self.values["trigger_threshold"].get(), "0.70")
        self.assertEqual(self.values[CUSTOM_DEADZONE_KEY].get(), newer_zones)
        warning.assert_called_once()

    def test_open_editor_cannot_apply_a_stale_saved_value_after_baseline_changes(self):
        trigger_var = self.values["trigger_threshold"]
        self.gui.parameter_saved_registry = {str(trigger_var): (trigger_var, "0.65")}
        self.preview.open_editor()
        self.root.update()
        canvas, _, actions = self._editor()
        trigger = canvas.master.winfo_children()[-2].grid_slaves(row=0, column=3)[0]
        self.gui.parameter_saved_registry = {str(trigger_var): (trigger_var, "0.60")}
        menu = Mock()
        with patch("config_gui.tk.Menu", return_value=menu):
            trigger.event_generate("<Button-3>", x=5, y=5)
        menu.add_command.call_args_list[0].kwargs["command"]()
        self.assertEqual(trigger.get(), "65")

        with patch("gui_sections.direction_deadzone.messagebox.showwarning") as warning:
            actions["確定"].invoke()
        self.root.update()

        self.assertEqual(trigger_var.get(), "0.60")
        warning.assert_called_once()

    def test_thumbnail_press_drag_width_save_and_reset(self):
        self.preview.event_generate("<ButtonPress-1>", x=45, y=45)
        self.assertEqual(self.preview.cget("relief"), "sunken")
        self.assertIsNone(self.preview._dialog)
        self.preview.event_generate("<ButtonRelease-1>", x=45, y=45)
        self.assertEqual(self.preview.cget("relief"), "raised")
        canvas, fields, actions = self._editor()
        self.assertEqual(len(fields), 4)
        controls = canvas.master.winfo_children()[-2]
        release_field = controls.grid_slaves(row=0, column=5)[0]
        cancel = actions["取消"]
        self.root.update()
        self.assertEqual(
            release_field.winfo_rootx() + release_field.winfo_width(),
            cancel.winfo_rootx() + cancel.winfo_width(),
        )
        for event, angle in (("<ButtonPress-1>", 45), ("<B1-Motion>", 70), ("<ButtonRelease-1>", 70)):
            x, y = _point((180, 180), 80, angle)
            canvas.event_generate(event, x=round(x), y=round(y))
        width = fields[0].winfo_children()[0]
        width.delete(0, "end")
        width.insert(0, "20")
        actions["確定"].invoke()
        offset, percent = self.preview.custom_zones()["4WAY"][0]
        self.assertAlmostEqual(offset, 25, delta=1)
        self.assertEqual(percent, 20)
        self.assertTrue(self.scale.instate(["disabled"]))
        self.assertEqual(self.label.cget("text"), "SET")
        self.assertEqual(str(self.scale.cget("cursor")), "hand2")
        self.assertEqual(str(self.label.cget("cursor")), "hand2")

        self.values["mode"].set("8WAY")
        self.preview.redraw()
        self.assertFalse(self.scale.instate(["disabled"]))
        self.values["mode"].set("4WAY")
        self.preview.redraw()
        self.scale.event_generate("<ButtonPress-1>", x=4, y=4)
        self._editor()[2]["取消"].invoke()
        self.assertTrue(self.preview.custom_zones())
        self.label.event_generate("<ButtonPress-1>", x=2, y=2)
        actions = self._editor()[2]
        actions["使用統一死區"].invoke()
        actions["確定"].invoke()
        self.root.update()
        self.assertFalse(self.preview.custom_zones())
        self.assertFalse(self.scale.instate(["disabled"]))
        self.assertEqual(self.label.cget("text"), "5°")
        self.assertEqual(self.errors, [])
