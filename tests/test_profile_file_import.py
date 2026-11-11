from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import config_gui
from config_utils import DEFAULT_CONFIG_PATH, load_config, read_profile, save_profile
from settings_schema import SettingValidationError


class ProfileFileImportTests(unittest.TestCase):
    def import_into_existing_profile(self, directory, payload, language="zh"):
        root = Path(directory)
        profiles = root / "profiles"
        profiles.mkdir()
        source = root / "Import.ini"
        source.write_text(payload, encoding="utf-8-sig")
        save_profile(load_config(DEFAULT_CONFIG_PATH, persist=False), "Target", profiles)
        target = profiles / "Target.ini"
        before = target.read_bytes()
        gui = object.__new__(config_gui.ConfigGUI)
        gui.root = None
        gui.language = language
        gui.active_profile = "General"
        gui.profile_names = ["General", "Target", "System Default"]
        gui.refresh_profile_list = lambda *args: None
        errors, successes, prompts = [], [], []

        def ask_name(*args, **kwargs):
            prompts.append(True)
            return "Target"

        with (
            patch.object(config_gui, "PROFILE_DIR", profiles),
            patch.object(config_gui.filedialog, "askopenfilename", return_value=str(source)),
            patch.object(config_gui.simpledialog, "askstring", side_effect=ask_name),
            patch.object(config_gui.messagebox, "askyesno", return_value=True),
            patch.object(config_gui.messagebox, "showinfo", side_effect=lambda *args: successes.append(args)),
            patch.object(config_gui.messagebox, "showerror", side_effect=lambda *args: errors.append(args)),
        ):
            gui.import_profile_file()
        return target, before, errors, successes, prompts

    def test_invalid_numeric_or_conflicting_values_do_not_overwrite_existing_profile(self):
        cases = (
            "[rumble]\nlf_strength = definitely-not-a-number\n",
            "[rumble]\nlf_strength = 1.1\n",
            "[stick_direction_left]\ntrigger_threshold = 0.30\nrelease_threshold = 0.70\n",
        )
        for payload in cases:
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory:
                target, before, errors, successes, prompts = self.import_into_existing_profile(directory, payload)
                self.assertEqual(target.read_bytes(), before)
                self.assertEqual(len(errors), 1)
                self.assertEqual(successes, [])
                self.assertEqual(prompts, [])

    def test_valid_partial_older_profile_with_bom_can_still_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            target, before, errors, successes, prompts = self.import_into_existing_profile(
                directory, "[rumble]\nlf_strength = 0.77\n"
            )
            self.assertNotEqual(target.read_bytes(), before)
            self.assertEqual(read_profile("Target", target.parent).get("rumble", "lf_strength"), "0.77")
            self.assertEqual(errors, [])
            self.assertEqual(len(successes), 1)
            self.assertEqual(prompts, [True])

    def test_invalid_mapping_targets_and_sources_cannot_overwrite_profiles(self):
        cases = (
            "[buttons]\na = KEYBOARD:NOT_A_KEY\n",
            "[buttons]\na = CUSTOM_KEYBOARD\n",
            "[buttons]\na = \n",
            "[buttons]\nunknown_button = A\n",
            "[stick_direction_left]\nup = MOUSE:LEFT\n",
            "[stick_direction_right]\nup = KEYBOARD:CTRL++A\n",
            "[stick_direction_left]\nunknown_direction = A\n",
        )
        for language in ("zh", "en"):
            for payload in cases:
                with self.subTest(language=language, payload=payload), tempfile.TemporaryDirectory() as directory:
                    target, before, errors, successes, prompts = self.import_into_existing_profile(
                        directory, payload, language=language,
                    )
                    self.assertEqual(target.read_bytes(), before)
                    self.assertEqual(len(errors), 1)
                    self.assertEqual(successes, [])
                    self.assertEqual(prompts, [])
                    if language == "en":
                        self.assertNotRegex(config_gui.translate_text(errors[0][1], "en"), r"[\u4e00-\u9fff]")

    def test_validation_details_follow_the_selected_language(self):
        for language, expected in (("en", "LF strength"), ("zh", "LF 強度")):
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory:
                target, before, errors, successes, prompts = self.import_into_existing_profile(
                    directory, "[rumble]\nlf_strength = 1.1\n", language=language,
                )
                self.assertEqual(target.read_bytes(), before)
                self.assertIn(expected.lower(), errors[0][1].lower())
                if language == "en":
                    self.assertNotRegex(config_gui.translate_text(errors[0][1], "en"), r"[\u4e00-\u9fff]")
                self.assertEqual(successes, [])
                self.assertEqual(prompts, [])

    def test_supported_partial_mappings_remain_accepted(self):
        payload = (
            "[buttons]\na = keyboard:ctrl+a\nb = L_STICK_LEFT\nx = MOUSE:LEFT\n"
            "[stick_direction_left]\nup = MOUSE:WHEEL_UP\ndown = KEYBOARD:MEDIA_PLAY_PAUSE\n"
            "[stick_direction_right]\nup = LT\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            target, _before, errors, successes, prompts = self.import_into_existing_profile(directory, payload)
            self.assertEqual(errors, [])
            self.assertEqual(len(successes), 1)
            self.assertEqual(prompts, [True])
            profile = read_profile("Target", target.parent)
            self.assertEqual(profile.get("buttons", "a"), "keyboard:ctrl+a")
            self.assertEqual(profile.get("buttons", "b"), "L_STICK_LEFT")
            self.assertEqual(profile.get("buttons", "x"), "MOUSE:LEFT")
            self.assertEqual(profile.get("stick_direction_left", "up"), "MOUSE:WHEEL_UP")
            self.assertEqual(profile.get("stick_direction_left", "down"), "KEYBOARD:MEDIA_PLAY_PAUSE")
            self.assertEqual(profile.get("stick_direction_right", "up"), "LT")

    def test_switch_and_external_reload_validation_details_follow_language(self):
        for action in ("switch", "reload"):
            for language, expected in (("en", "LF strength"), ("zh", "LF 強度")):
                with self.subTest(action=action, language=language), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    config_path = root / "config.ini"
                    config = load_config(DEFAULT_CONFIG_PATH, persist=False)
                    config_gui.atomic_write_config(config, config_path)
                    before = config_path.read_bytes()
                    (root / "Invalid.ini").write_text("[rumble]\nlf_strength = 1.1\n", encoding="utf-8")
                    gui = object.__new__(config_gui.ConfigGUI)
                    gui.root = None
                    gui.language = language
                    gui.active_profile = "General" if action == "switch" else "Invalid"
                    gui._profile_switch_in_progress = False
                    gui._external_active_profile_changed = True
                    gui.main_process = None
                    gui.profile_name_var = Mock()
                    gui._confirm_profile_transition = Mock(return_value=True)
                    gui.request_adaptive_window_update = Mock()
                    with (
                        patch.object(config_gui, "PROFILE_DIR", root),
                        patch.object(config_gui, "CONFIG_PATH", config_path),
                        patch.object(config_gui.messagebox, "showerror") as error,
                        patch.object(config_gui.messagebox, "askyesnocancel", return_value=True),
                    ):
                        if action == "switch":
                            gui.apply_selected_profile("Invalid")
                        else:
                            self.assertEqual(gui.resolve_external_active_profile_conflict(), "cancel")
                    self.assertEqual(config_path.read_bytes(), before)
                    message = error.call_args.args[1]
                    self.assertIn(expected.lower(), message.lower())
                    if language == "en":
                        self.assertNotRegex(config_gui.translate_text(message, "en"), r"[\u4e00-\u9fff]")

    def test_folder_import_validation_details_follow_language(self):
        error = SettingValidationError("rumble", "lf_strength", "1.1", "LF 強度無效。", "Invalid LF strength.")
        for language, expected in (("en", "Invalid LF strength."), ("zh", "LF 強度無效。")):
            with self.subTest(language=language):
                gui = object.__new__(config_gui.ConfigGUI)
                gui.language = language
                gui.root = None
                gui.has_unsaved_changes = Mock(return_value=False)
                plan = SimpleNamespace(
                    source_root=Path("Source"), profiles={}, layers=[],
                    calibration_count=0, ignored_system_default=False,
                    profile_conflicts=(), layer_conflicts=(),
                )
                with (
                    patch.object(config_gui.filedialog, "askdirectory", return_value="Source"),
                    patch.object(config_gui, "scan_settings_import", return_value=plan),
                    patch.object(config_gui, "apply_settings_import", side_effect=error),
                    patch.object(config_gui.messagebox, "askokcancel", return_value=True),
                    patch.object(config_gui.messagebox, "showerror") as dialog,
                ):
                    gui.import_settings_folder()
                self.assertIn(expected, dialog.call_args.args[1])
                if language == "en":
                    self.assertNotRegex(dialog.call_args.args[1], r"[\u4e00-\u9fff]")

    def test_switch_and_external_reload_reject_invalid_mapping_before_writing(self):
        for action in ("switch", "reload"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                config_path = root / "config.ini"
                config = load_config(DEFAULT_CONFIG_PATH, persist=False)
                config_gui.atomic_write_config(config, config_path)
                before = config_path.read_bytes()
                (root / "Invalid.ini").write_text("[buttons]\na = KEYBOARD:NOT_A_KEY\n", encoding="utf-8")
                gui = object.__new__(config_gui.ConfigGUI)
                gui.root = None
                gui.language = "en"
                gui.config = config
                gui.active_profile = "General" if action == "switch" else "Invalid"
                gui._profile_switch_in_progress = False
                gui._external_active_profile_changed = True
                gui.main_process = None
                for method in (
                    "request_adaptive_window_update", "reload_profile_variables",
                    "refresh_profile_list", "request_live_settings_reload",
                ):
                    setattr(gui, method, Mock())
                gui.profile_name_var = Mock()
                gui._confirm_profile_transition = Mock(return_value=True)
                with (
                    patch.object(config_gui, "PROFILE_DIR", root),
                    patch.object(config_gui, "CONFIG_PATH", config_path),
                    patch.object(config_gui.messagebox, "showerror") as error,
                    patch.object(config_gui.messagebox, "showinfo"),
                    patch.object(config_gui.messagebox, "askyesnocancel", return_value=True),
                ):
                    if action == "switch":
                        gui.apply_selected_profile("Invalid")
                    else:
                        self.assertEqual(gui.resolve_external_active_profile_conflict(), "cancel")
                self.assertEqual(config_path.read_bytes(), before)
                self.assertIn("NOT_A_KEY", error.call_args.args[1])
                self.assertNotRegex(error.call_args.args[1], r"[\u4e00-\u9fff]")
                gui.reload_profile_variables.assert_not_called()
                gui.request_live_settings_reload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
