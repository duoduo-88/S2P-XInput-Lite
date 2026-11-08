import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import config_gui
from config_gui import ConfigGUI
from config_utils import (
    DEFAULT_CONFIG_PATH, atomic_write_config, load_config, read_profile, save_profile,
)
from settings_schema import SettingValidationError


class ProfileSaveSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.profiles = root / "profiles"
        self.layers = root / "layers"
        self.layers.mkdir()
        self.layer_path = self.layers / "Old.json"
        self.layer_path.write_bytes(b"original layer")
        self.config_path = root / "config.ini"
        self.profile_path = self.profiles / "Alpha.ini"
        config = load_config(DEFAULT_CONFIG_PATH, persist=False)
        config.set("gui", "active_profile", "Alpha")
        atomic_write_config(config, self.config_path)
        save_profile(config, "Alpha", self.profiles)
        self.original_config = self.config_path.read_bytes()
        gui = self.gui = object.__new__(ConfigGUI)
        gui.active_profile = "Alpha"
        gui.config = config
        gui.mapping_layers = []
        gui.tr = lambda text: text
        gui._external_active_profile_changed = False
        gui.refresh_saved_settings_baseline = Mock()
        gui.request_live_settings_reload = Mock(return_value=False)
        gui.refresh_profile_list = Mock()
        for patcher in (
            patch.object(config_gui, "PROFILE_DIR", self.profiles),
            patch.object(config_gui, "CONFIG_PATH", self.config_path),
            patch.object(config_gui, "LAYER_DIR", self.layers),
            patch.object(config_gui.messagebox, "showerror"),
            patch.object(config_gui.messagebox, "showinfo"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        gui._active_profile_content_baseline = gui.profile_file_fingerprint("Alpha")

    def test_canceled_save_keeps_external_profile_changes(self):
        for mutation in ("edit", "delete"):
            for outcome in ("success", "rejected", "exception"):
                with self.subTest(mutation=mutation, outcome=outcome):
                    save_profile(self.gui.config, "Alpha", self.profiles)
                    atomic_write_config(self.gui.config, self.config_path)
                    self.gui._external_active_profile_changed = False
                    self.gui._active_profile_content_baseline = self.gui.profile_file_fingerprint("Alpha")

                    def save_settings(**kwargs):
                        kwargs["before_write"]()
                        self.config_path.write_bytes(b"partially saved config")
                        self.layer_path.write_bytes(b"partially saved layer")
                        if mutation == "edit":
                            self.profile_path.write_bytes(b"external profile contents")
                        else:
                            self.profile_path.unlink()
                        if outcome == "exception":
                            raise OSError("forced settings write failure")
                        return outcome == "success"

                    self.gui.save_config = save_settings
                    self.assertFalse(self.gui.save_current_profile(show_message=False))
                    self.assertEqual(self.config_path.read_bytes(), self.original_config)
                    self.assertEqual(self.layer_path.read_bytes(), b"original layer")
                    if mutation == "edit":
                        self.assertEqual(self.profile_path.read_bytes(), b"external profile contents")
                    else:
                        self.assertFalse(self.profile_path.exists())

    def test_profile_write_failure_restores_settings(self):
        original_profile = self.profile_path.read_bytes()

        def save_settings(**kwargs):
            kwargs["before_write"]()
            self.config_path.write_bytes(b"partially saved config")
            return True

        self.gui.save_config = save_settings
        with patch.object(config_gui, "save_profile", side_effect=OSError("write failed")):
            self.assertFalse(self.gui.save_current_profile(show_message=False))
        self.assertEqual(self.config_path.read_bytes(), self.original_config)
        self.assertEqual(self.profile_path.read_bytes(), original_profile)

    def test_successful_save_updates_profile_and_its_loaded_baseline(self):
        def save_settings(**kwargs):
            kwargs["before_write"]()
            self.gui.config.set("rumble", "lf_strength", "0.77")
            atomic_write_config(self.gui.config, self.config_path)
            return True

        self.gui.save_config = save_settings
        self.assertTrue(self.gui.save_current_profile(show_message=False))
        self.assertEqual(read_profile("Alpha", self.profiles).get("rumble", "lf_strength"), "0.77")
        self.assertEqual(load_config(self.config_path, persist=False).get("rumble", "lf_strength"), "0.77")
        self.assertEqual(self.gui._active_profile_content_baseline, self.gui.profile_file_fingerprint("Alpha"))
        self.assertFalse(self.gui._external_active_profile_changed)
        self.gui.request_live_settings_reload.assert_called_once()
        self.gui.refresh_profile_list.assert_called_once_with("Alpha")

    def prepare_save_as(self):
        self.gui.root = None
        self.gui.profile_names = ["Alpha", "Target"]
        self.gui.profile_name_var = Mock()
        self.gui.active_profile = "Alpha"
        self.gui.config.set("gui", "active_profile", "Alpha")
        atomic_write_config(self.gui.config, self.config_path)
        save_profile(self.gui.config, "Target", self.profiles)
        return self.profiles / "Target.ini"

    def test_save_as_failure_preserves_external_target_changes(self):
        for mutation in ("edit", "delete", "new_file"):
            for outcome in ("success", "rejected", "exception"):
                with self.subTest(mutation=mutation, outcome=outcome):
                    target = self.prepare_save_as()
                    if mutation == "new_file":
                        target.unlink()
                        self.gui.profile_names.remove("Target")
                    self.layer_path.write_bytes(b"original layer")

                    def save_settings(**kwargs):
                        kwargs["before_write"]()
                        self.config_path.write_bytes(b"partially saved config")
                        self.layer_path.write_bytes(b"partially saved layer")
                        if mutation == "delete":
                            target.unlink()
                        else:
                            target.write_bytes(b"external target contents")
                        if outcome == "exception":
                            raise OSError("forced settings error")
                        return outcome == "success"

                    self.gui.save_config = save_settings
                    with (
                        patch.object(config_gui.simpledialog, "askstring", return_value="Target"),
                        patch.object(config_gui.messagebox, "askyesno", return_value=True),
                    ):
                        self.assertFalse(self.gui.save_profile_as())
                    self.assertEqual(self.gui.active_profile, "Alpha")
                    self.assertEqual(self.config_path.read_bytes(), self.original_config)
                    self.assertEqual(self.layer_path.read_bytes(), b"original layer")
                    if mutation == "delete":
                        self.assertFalse(target.exists())
                    else:
                        self.assertTrue(target.exists())
                        self.assertEqual(target.read_bytes(), b"external target contents")

    def test_save_as_profile_commit_failure_restores_settings(self):
        target = self.prepare_save_as()
        before = target.read_bytes()

        def save_settings(**kwargs):
            kwargs["before_write"]()
            self.config_path.write_bytes(b"partially saved config")
            return True

        self.gui.save_config = save_settings
        with (
            patch.object(config_gui.simpledialog, "askstring", return_value="Target"),
            patch.object(config_gui.messagebox, "askyesno", return_value=True),
            patch.object(config_gui, "save_profile", side_effect=OSError("forced profile error")),
        ):
            self.assertFalse(self.gui.save_profile_as())
        self.assertEqual(target.read_bytes(), before)
        self.assertEqual(self.config_path.read_bytes(), self.original_config)
        self.assertEqual(self.gui.active_profile, "Alpha")

    def test_save_as_success_uses_existing_canonical_name_and_preserves_old_profile(self):
        target = self.prepare_save_as()
        original_profile = self.profile_path.read_bytes()

        def save_settings(**kwargs):
            kwargs["before_write"]()
            self.gui.config.set("rumble", "lf_strength", "0.77")
            atomic_write_config(self.gui.config, self.config_path)
            return True

        self.gui.save_config = save_settings
        with (
            patch.object(config_gui.simpledialog, "askstring", return_value="target"),
            patch.object(config_gui.messagebox, "askyesno", return_value=True),
        ):
            self.assertTrue(self.gui.save_profile_as())
        self.assertEqual(self.gui.active_profile, "Target")
        self.assertEqual(read_profile("Target", self.profiles).get("rumble", "lf_strength"), "0.77")
        self.assertEqual(load_config(self.config_path, persist=False).get("gui", "active_profile"), "Target")
        self.assertEqual(self.gui._active_profile_content_baseline, self.gui.profile_file_fingerprint("Target"))
        self.assertEqual(self.profile_path.read_bytes(), original_profile)
        self.assertTrue(target.exists())

    def test_save_as_final_config_write_preserves_external_target_on_failure_or_change(self):
        for fail_write in (False, True):
            with self.subTest(fail_write=fail_write):
                target = self.prepare_save_as()

                def save_settings(**kwargs):
                    kwargs["before_write"]()
                    self.config_path.write_bytes(b"partially saved config")
                    return True

                def write_config(config, path):
                    target.write_bytes(b"external change during config commit")
                    if fail_write:
                        raise OSError("forced final config error")
                    atomic_write_config(config, path)

                self.gui.save_config = save_settings
                with (
                    patch.object(config_gui.simpledialog, "askstring", return_value="Target"),
                    patch.object(config_gui.messagebox, "askyesno", return_value=True),
                    patch.object(config_gui, "atomic_write_config", side_effect=write_config),
                ):
                    self.assertFalse(self.gui.save_profile_as())
                self.assertEqual(target.read_bytes(), b"external change during config commit")
                self.assertEqual(self.config_path.read_bytes(), self.original_config)
                self.assertEqual(self.gui.active_profile, "Alpha")

    def test_save_as_conflict_message_follows_selected_language(self):
        for language in ("en", "zh"):
            with self.subTest(language=language):
                target = self.prepare_save_as()
                self.gui.language = language
                self.gui.tr = lambda text: config_gui.translate_text(text, language)

                def save_settings(**kwargs):
                    kwargs["before_write"]()
                    target.write_bytes(b"external target")
                    return True

                self.gui.save_config = save_settings
                with (
                    patch.object(config_gui.simpledialog, "askstring", return_value="Target"),
                    patch.object(config_gui.messagebox, "askyesno", return_value=True),
                    patch.object(config_gui.messagebox, "showerror") as error,
                ):
                    self.assertFalse(self.gui.save_profile_as())
                message = " ".join(error.call_args.args)
                if language == "en":
                    self.assertNotRegex(message, r"[\u4e00-\u9fff]")
                else:
                    self.assertRegex(message, r"[\u4e00-\u9fff]")

    def test_validation_failure_never_rolls_back_external_updates(self):
        for action in ("save_current_profile", "save_profile_as"):
            for phase in ("layer", "mapping", "numeric", "disk"):
                with self.subTest(action=action, phase=phase):
                    self.prepare_save_as()
                    self.layer_path.write_bytes(b"original layer")
                    self.gui.mapping_layers = [{"id": "one", "name": "Old", "enabled": True}] if phase == "disk" else []
                    self.gui.language = "en"
                    self.gui._mapping_layer_load_error = "invalid layer" if phase == "layer" else None
                    self.gui._find_pending_custom_mappings = Mock(return_value=["A"] if phase == "mapping" else [])
                    self.gui._find_invalid_mapping_targets = Mock(return_value=[])
                    external = load_config(self.config_path, persist=False)
                    external.add_section("gyro.EXTERNAL")
                    external.set("gyro.EXTERNAL", "bias", "0.1, 0.2, 0.3")

                    def show_error(*args, **kwargs):
                        atomic_write_config(external, self.config_path)
                        self.layer_path.write_bytes(b"external layer update")

                    bundle = {
                        "sections": {}, "buttons": {},
                        "direction_mappings": {"left": {}, "right": {}},
                        "gyro_activation_buttons": ["ZL"], "gyro_stabilization_buttons": [],
                        "gyro_tilt_recenter_button": "NONE",
                    }

                    with (
                        patch.object(config_gui.simpledialog, "askstring", return_value="Target"),
                        patch.object(config_gui.messagebox, "askyesno", return_value=True),
                        patch.object(config_gui.messagebox, "showerror", side_effect=show_error),
                        patch.object(config_gui, "collect_gui_settings", return_value=bundle, side_effect=None if phase == "disk" else SettingValidationError(
                            "rumble", "lf_strength", "bad", "無效數值", "Invalid value",
                        )),
                    ):
                        self.assertFalse(getattr(self.gui, action)())
                    result = load_config(self.config_path, persist=False)
                    self.assertEqual(result.get("gyro.EXTERNAL", "bias", fallback=""), "0.1, 0.2, 0.3")
                    self.assertEqual(self.layer_path.read_bytes(), b"external layer update")

    def test_rejected_numeric_settings_do_not_normalize_the_disk_config(self):
        self.config_path.write_bytes(b"[gui]\nactive_profile = Alpha\n")
        before = self.config_path.read_bytes()
        self.gui.language = "en"
        self.gui._mapping_layer_load_error = None
        self.gui._find_pending_custom_mappings = Mock(return_value=[])
        self.gui._find_invalid_mapping_targets = Mock(return_value=[])
        with patch.object(config_gui, "collect_gui_settings", side_effect=SettingValidationError(
            "rumble", "lf_strength", "bad", "無效數值", "Invalid value",
        )):
            self.assertFalse(self.gui.save_config(show_confirmation=False))
        self.assertEqual(self.config_path.read_bytes(), before)

    def test_real_save_pipeline_keeps_atomic_profile_failure_rollback(self):
        self.layer_path.unlink()
        self.gui.language = "en"
        self.gui._mapping_layer_load_error = None
        self.gui._find_pending_custom_mappings = Mock(return_value=[])
        self.gui._find_invalid_mapping_targets = Mock(return_value=[])
        self.gui.refresh_mapping_layer_rows = Mock()
        self.gui.build_parameter_default_registry = Mock()
        bundle = {
            "sections": {"rumble": {"lf_strength": "0.77"}},
            "buttons": {}, "direction_mappings": {"left": {}, "right": {}},
            "gyro_activation_buttons": ["ZL"], "gyro_stabilization_buttons": [],
            "gyro_tilt_recenter_button": "NONE",
        }
        with (
            patch.object(config_gui, "collect_gui_settings", return_value=bundle),
            patch.object(config_gui, "save_profile", side_effect=OSError("forced atomic failure")),
        ):
            self.assertFalse(self.gui.save_current_profile(show_message=False))
        self.assertEqual(self.config_path.read_bytes(), self.original_config)
        self.gui.refresh_saved_settings_baseline.assert_called_once()
        with patch.object(config_gui, "collect_gui_settings", return_value=bundle):
            self.assertTrue(self.gui.save_current_profile(show_message=False))
        self.assertEqual(read_profile("Alpha", self.profiles).get("rumble", "lf_strength"), "0.77")

    def test_saved_profile_remains_successful_when_list_refresh_fails(self):
        for action in ("save_current_profile", "save_profile_as"):
            for language in ("en", "zh"):
                with self.subTest(action=action, language=language):
                    self.prepare_save_as()
                    save_profile(self.gui.config, "Alpha", self.profiles)
                    self.gui._active_profile_content_baseline = self.gui.profile_file_fingerprint("Alpha")
                    self.gui._external_active_profile_changed = False
                    self.gui.language = language
                    self.gui.tr = lambda text: config_gui.translate_text(text, language)
                    self.gui.refresh_profile_list = Mock(side_effect=OSError("temporarily unavailable"))
                    self.gui._profile_folder_snapshot = self.gui._capture_profile_folder_snapshot()

                    def save_settings(**kwargs):
                        kwargs["before_write"]()
                        self.gui.config.set("rumble", "lf_strength", "0.77")
                        atomic_write_config(self.gui.config, self.config_path)
                        return True

                    self.gui.save_config = save_settings
                    with (
                        patch.object(config_gui.simpledialog, "askstring", return_value="Target"),
                        patch.object(config_gui.messagebox, "askyesno", return_value=True),
                        patch.object(config_gui.messagebox, "showerror") as error,
                        patch.object(config_gui.messagebox, "showwarning") as warning,
                    ):
                        self.assertTrue(getattr(self.gui, action)())
                    expected = "Alpha" if action == "save_current_profile" else "Target"
                    self.assertEqual(self.gui.active_profile, expected)
                    self.gui.profile_name_var.set.assert_called_with(expected)
                    self.assertEqual(load_config(self.config_path, persist=False).get("gui", "active_profile"), expected)
                    self.assertEqual(read_profile(expected, self.profiles).get("rumble", "lf_strength"), "0.77")
                    self.assertEqual(self.gui._active_profile_content_baseline, self.gui.profile_file_fingerprint(expected))
                    error.assert_not_called()
                    warning.assert_called_once()
                    message = " ".join(warning.call_args.args)
                    if language == "en":
                        self.assertNotRegex(message, r"[\u4e00-\u9fff]")
                    else:
                        self.assertRegex(message, r"[\u4e00-\u9fff]")
                    # A later watcher refresh must recognize our own commit.
                    self.gui.refresh_profile_list = ConfigGUI.refresh_profile_list.__get__(self.gui)
                    self.gui.sync_profile_directory()
                    self.assertFalse(self.gui._external_active_profile_changed)
                    with patch.object(config_gui.messagebox, "askyesnocancel", return_value=None) as conflict:
                        self.assertTrue(self.gui.save_current_profile(show_message=False))
                    conflict.assert_not_called()
                    # An actual subsequent external edit must still conflict.
                    external = read_profile(expected, self.profiles)
                    external.set("rumble", "lf_strength", "0.61")
                    atomic_write_config(external, self.profiles / f"{expected}.ini")
                    self.gui.sync_profile_directory()
                    self.assertTrue(self.gui._external_active_profile_changed)
                    with patch.object(config_gui.messagebox, "askyesnocancel", return_value=None) as conflict:
                        self.assertFalse(self.gui.save_current_profile(show_message=False))
                    conflict.assert_called_once()

    def test_external_edit_just_after_profile_commit_is_not_adopted_as_saved_baseline(self):
        for action in ("save_current_profile", "save_profile_as"):
            with self.subTest(action=action):
                self.prepare_save_as()
                save_profile(self.gui.config, "Alpha", self.profiles)
                self.gui._active_profile_content_baseline = self.gui.profile_file_fingerprint("Alpha")
                self.gui._external_active_profile_changed = False
                written = []

                def save_settings(**kwargs):
                    kwargs["before_write"]()
                    self.gui.config.set("rumble", "lf_strength", "0.77")
                    atomic_write_config(self.gui.config, self.config_path)
                    return True

                def commit_then_external_edit(config, name, directory, **kwargs):
                    result = save_profile(config, name, directory, **kwargs)
                    path = Path(directory) / f"{name}.ini"
                    written.append(hashlib.sha256(path.read_bytes()).hexdigest())
                    external = read_profile(name, directory)
                    external.set("rumble", "lf_strength", "0.61")
                    atomic_write_config(external, path)
                    return result

                self.gui.save_config = save_settings
                with (
                    patch.object(config_gui.simpledialog, "askstring", return_value="Target"),
                    patch.object(config_gui.messagebox, "askyesno", return_value=True),
                    patch.object(config_gui, "save_profile", side_effect=commit_then_external_edit),
                ):
                    self.assertTrue(getattr(self.gui, action)())
                    self.assertEqual(self.gui._active_profile_content_baseline, written[0])
                    self.assertTrue(self.gui._external_active_profile_changed)
                    with patch.object(config_gui.messagebox, "askyesnocancel", return_value=None) as conflict:
                        self.assertFalse(self.gui.save_current_profile(show_message=False))
                    conflict.assert_called_once()
                self.assertEqual(read_profile(self.gui.active_profile, self.profiles).get("rumble", "lf_strength"), "0.61")


if __name__ == "__main__":
    unittest.main()
