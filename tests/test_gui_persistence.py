import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import config_gui


class DummyVariable:
    def __init__(self, value=""):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class DummyLabel:
    def __init__(self):
        self.options = {}

    def config(self, **options):
        self.options.update(options)


class GuiPersistenceTests(unittest.TestCase):
    def test_parameter_mousewheel_scrolls_canvas_and_stops_value_binding(self):
        class Widget:
            def __init__(self, widget_class, master=None):
                self.widget_class = widget_class
                self.master = master
                self.scrolls = []

            def winfo_class(self):
                return self.widget_class

            def yview_scroll(self, units, mode):
                self.scrolls.append((units, mode))

        canvas = Widget("Canvas")
        scale = Widget("TScale", master=canvas)
        gui = object.__new__(config_gui.ConfigGUI)

        result = gui._on_parameter_control_mousewheel(
            SimpleNamespace(widget=scale, delta=-120)
        )

        self.assertEqual(result, "break")
        self.assertEqual(canvas.scrolls, [(1, "units")])

    def test_parameter_controls_receive_widget_level_wheel_guard(self):
        class Widget:
            master = None

            def __init__(self):
                self.bindings = []

            def winfo_class(self):
                return "TCombobox"

            def bind(self, sequence, callback, add=None):
                self.bindings.append((sequence, callback, add))

            def __str__(self):
                return ".parameter"

        gui = object.__new__(config_gui.ConfigGUI)
        gui.profile_combo = None
        gui._parameter_wheel_bound_widgets = set()
        gui._parameter_reset_bound_widgets = set()
        gui.parameter_binding_for_widget = lambda _widget: None
        widget = Widget()

        gui._bind_parameter_reset_widget(widget)

        self.assertEqual(len(widget.bindings), 1)
        sequence, callback, add = widget.bindings[0]
        self.assertEqual(sequence, "<MouseWheel>")
        self.assertEqual(callback, gui._on_parameter_control_mousewheel)
        self.assertEqual(add, "+")

    def test_combobox_popdown_list_ignores_mousewheel(self):
        calls = []

        class Tcl:
            def call(self, *args):
                calls.append(args)
                if args[0] == "ttk::combobox::PopdownWindow":
                    return ".parameter.popdown"
                return ""

        gui = object.__new__(config_gui.ConfigGUI)
        gui.root = SimpleNamespace(tk=Tcl())

        gui._bind_combobox_popdown_mousewheel_guard(".parameter")

        self.assertEqual(
            calls,
            [
                (
                    "ttk::combobox::PopdownWindow",
                    ".parameter",
                ),
                (
                    "bind",
                    ".parameter.popdown.f.l",
                    "<MouseWheel>",
                    "break",
                ),
            ],
        )

    def test_esp32_write_requires_unsaved_profile_to_be_saved_first(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.active_profile = "General"
        gui.tr = lambda text: text
        states = iter((True, False))
        gui.has_unsaved_changes = lambda: next(states)
        gui.save_current_profile = unittest.mock.Mock(return_value=True)

        with patch.object(
            config_gui.messagebox, "askyesno", return_value=True
        ) as prompt:
            self.assertTrue(
                gui._confirm_saved_profile_before_esp32_action()
            )

        prompt.assert_called_once()
        gui.save_current_profile.assert_called_once_with(show_message=False)

    def test_esp32_write_stops_when_unsaved_save_is_declined(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.active_profile = "General"
        gui.tr = lambda text: text
        gui.has_unsaved_changes = lambda: True
        gui.save_current_profile = unittest.mock.Mock(return_value=True)

        with patch.object(
            config_gui.messagebox, "askyesno", return_value=False
        ):
            self.assertFalse(
                gui._confirm_saved_profile_before_esp32_action()
            )

        gui.save_current_profile.assert_not_called()

    def test_esp32_write_saves_read_only_profile_as_new_profile(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.active_profile = "System Default"
        gui.tr = lambda text: text
        states = iter((True, False))
        gui.has_unsaved_changes = lambda: next(states)
        gui.save_profile_as = unittest.mock.Mock(return_value=True)

        with patch.object(
            config_gui.messagebox, "askyesno", return_value=True
        ):
            self.assertTrue(
                gui._confirm_saved_profile_before_esp32_action()
            )

        gui.save_profile_as.assert_called_once_with()

    def test_declining_missing_hidhide_prompt_is_remembered(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.ini"
            missing_status = {"state": "not_installed", "installed": False}

            first_gui = object.__new__(config_gui.ConfigGUI)
            first_gui.config = config_gui.load_config(config_path)
            first_gui.hidhide_prompt_shown = False
            first_gui.hidhide_status = None
            first_gui.hidhide_status_label = DummyLabel()
            first_gui.tr = lambda text: text

            with (
                patch.object(config_gui, "CONFIG_PATH", config_path),
                patch.object(config_gui, "inspect_hidhide", return_value=missing_status),
                patch.object(config_gui.messagebox, "askyesno", return_value=False) as prompt,
            ):
                self.assertTrue(first_gui.prepare_hidhide_before_start())
                prompt.assert_called_once()

            saved = config_gui.load_config(config_path)
            self.assertTrue(
                saved.getboolean("gui", "hidhide_missing_prompt_dismissed")
            )

            second_gui = object.__new__(config_gui.ConfigGUI)
            second_gui.config = saved
            second_gui.hidhide_prompt_shown = False
            second_gui.hidhide_status = None
            second_gui.hidhide_status_label = DummyLabel()
            second_gui.tr = lambda text: text

            with (
                patch.object(config_gui, "CONFIG_PATH", config_path),
                patch.object(config_gui, "inspect_hidhide", return_value=missing_status),
                patch.object(config_gui.messagebox, "askyesno") as repeated_prompt,
            ):
                self.assertTrue(second_gui.prepare_hidhide_before_start())
                repeated_prompt.assert_not_called()

    def test_declining_installed_hidhide_setup_is_remembered(self):
        with tempfile.TemporaryDirectory() as directory:
            config_path = Path(directory) / "config.ini"
            setup_status = {
                "state": "disabled",
                "installed": True,
                "cloak_active": False,
                "hidden_count": 0,
            }

            first_gui = object.__new__(config_gui.ConfigGUI)
            first_gui.config = config_gui.load_config(config_path)
            first_gui.hidhide_prompt_shown = False
            first_gui.hidhide_status = None
            first_gui.hidhide_status_label = DummyLabel()
            first_gui.tr = lambda text: text

            with (
                patch.object(config_gui, "CONFIG_PATH", config_path),
                patch.object(config_gui, "inspect_hidhide", return_value=setup_status),
                patch.object(config_gui.messagebox, "askyesno", return_value=False) as prompt,
            ):
                self.assertTrue(first_gui.prepare_hidhide_before_start())
                prompt.assert_called_once()

            saved = config_gui.load_config(config_path)
            self.assertTrue(
                saved.getboolean("gui", "hidhide_setup_prompt_dismissed")
            )

            second_gui = object.__new__(config_gui.ConfigGUI)
            second_gui.config = saved
            second_gui.hidhide_prompt_shown = False
            second_gui.hidhide_status = None
            second_gui.hidhide_status_label = DummyLabel()
            second_gui.tr = lambda text: text

            with (
                patch.object(config_gui, "CONFIG_PATH", config_path),
                patch.object(config_gui, "inspect_hidhide", return_value=setup_status),
                patch.object(config_gui.messagebox, "askyesno") as repeated_prompt,
            ):
                self.assertTrue(second_gui.prepare_hidhide_before_start())
                repeated_prompt.assert_not_called()

    def test_clicking_setup_status_reenables_the_prompt(self):
        gui = object.__new__(config_gui.ConfigGUI)
        gui.hidhide_status = {"state": "setup"}
        gui.hidhide_prompt_shown = True
        dismissed_values = []
        prompt_calls = []
        gui._set_hidhide_setup_prompt_dismissed = dismissed_values.append
        gui.prepare_hidhide_before_start = lambda: prompt_calls.append(True)

        gui.handle_hidhide_status_click()

        self.assertEqual(dismissed_values, [False])
        self.assertFalse(gui.hidhide_prompt_shown)
        self.assertEqual(prompt_calls, [True])

    def test_failed_bundle_restore_recovers_replaced_files_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.ini"
            profile_dir = root / "profiles"
            layer_dir = root / "layers"
            profile_dir.mkdir()
            layer_dir.mkdir()
            profile_path = profile_dir / "General.ini"
            old_layer = layer_dir / "Old.json"
            config_path.write_bytes(b"old config")
            profile_path.write_bytes(b"old profile")
            old_layer.write_bytes(b"old layer")

            gui = object.__new__(config_gui.ConfigGUI)
            gui.mapping_layers = [{"name": "New"}]
            with (
                patch.object(config_gui, "CONFIG_PATH", config_path),
                patch.object(config_gui, "PROFILE_DIR", profile_dir),
                patch.object(config_gui, "LAYER_DIR", layer_dir),
            ):
                snapshot = gui.capture_settings_bundle("General")
                config_path.write_bytes(b"new config")
                profile_path.write_bytes(b"new profile")
                old_layer.unlink()
                (layer_dir / "New.json").write_bytes(b"new layer")
                external = layer_dir / "External.json"
                external.write_bytes(b"external race")

                gui.restore_settings_bundle(snapshot)

            self.assertEqual(config_path.read_bytes(), b"old config")
            self.assertEqual(profile_path.read_bytes(), b"old profile")
            self.assertEqual(old_layer.read_bytes(), b"old layer")
            self.assertFalse((layer_dir / "New.json").exists())
            self.assertEqual(external.read_bytes(), b"external race")

    def test_active_profile_rename_rolls_back_when_config_write_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.ini"
            profile_dir = root / "profiles"
            profile_dir.mkdir()
            config = config_gui.load_config(config_path)
            config.set("gui", "active_profile", "Old")
            config_gui.atomic_write_config(config, config_path)
            config_gui.save_profile(config, "Old", profile_dir)
            original_config = config_path.read_bytes()

            gui = object.__new__(config_gui.ConfigGUI)
            gui.active_profile = "Old"
            gui.config = config
            with (
                patch.object(config_gui, "CONFIG_PATH", config_path),
                patch.object(config_gui, "PROFILE_DIR", profile_dir),
                patch.object(
                    config_gui,
                    "atomic_write_config",
                    side_effect=OSError("forced write failure"),
                ),
            ):
                with self.assertRaises(OSError):
                    gui.rename_active_profile_transaction("Old", "New")

            self.assertTrue((profile_dir / "Old.ini").is_file())
            self.assertFalse((profile_dir / "New.ini").exists())
            self.assertEqual(config_path.read_bytes(), original_config)
            self.assertEqual(gui.active_profile, "Old")

    def test_profile_switch_refreshes_active_file_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.ini"
            profile_dir = root / "profiles"
            profile_dir.mkdir()
            config = config_gui.load_config(config_path)
            config_gui.atomic_write_config(config, config_path)
            config_gui.save_profile(config, "General", profile_dir)
            config.set("rumble", "lf_strength", "0.37")
            config_gui.save_profile(config, "Racing", profile_dir)

            gui = object.__new__(config_gui.ConfigGUI)
            gui.active_profile = "General"
            gui.language = "zh_TW"
            gui.profile_name_var = DummyVariable("Racing")
            gui._profile_switch_in_progress = False
            gui.main_process = None
            gui.config = config
            gui._active_profile_content_baseline = "old-profile-hash"
            gui._confirm_profile_transition = lambda: True
            gui.reload_profile_variables = lambda: None
            gui.request_adaptive_window_update = lambda: None

            with (
                patch.object(config_gui, "CONFIG_PATH", config_path),
                patch.object(config_gui, "PROFILE_DIR", profile_dir),
                patch.object(config_gui.messagebox, "showinfo"),
                patch.object(config_gui.messagebox, "showerror"),
            ):
                gui.apply_selected_profile("Racing")
                expected = gui.profile_file_fingerprint("Racing")

            self.assertEqual(gui.active_profile, "Racing")
            self.assertEqual(gui._active_profile_content_baseline, expected)


if __name__ == "__main__":
    unittest.main()
