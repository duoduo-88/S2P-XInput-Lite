import hashlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import config_gui
import config_utils
from config_gui import ConfigGUI
from config_utils import DEFAULT_CONFIG_PATH, atomic_write_config, load_config, read_profile, save_profile


class ProfileLoadSnapshotTests(unittest.TestCase):
    def test_saved_fingerprints_match_utf8_bytes_and_keep_default_returns(self):
        with tempfile.TemporaryDirectory() as directory:
            config = load_config(DEFAULT_CONFIG_PATH, persist=False)
            config.set("stick_direction_left", "mode", "映射為滑鼠")
            expected = io.StringIO(newline="")
            config.write(expected)
            data = expected.getvalue().encode("utf-8")
            path = Path(directory) / "config.ini"
            self.assertIsNone(atomic_write_config(config, path))
            self.assertEqual(path.read_bytes(), data)
            fingerprint = atomic_write_config(config, path, return_fingerprint=True)
            self.assertEqual(fingerprint, hashlib.sha256(data).hexdigest())
            self.assertEqual(save_profile(config, "Saved", directory), Path(directory) / "Saved.ini")
            profile_path, profile_fingerprint = save_profile(config, "Saved", directory, return_fingerprint=True)
            self.assertEqual(profile_fingerprint, hashlib.sha256(profile_path.read_bytes()).hexdigest())

    def test_writer_fingerprint_is_not_taken_from_a_later_external_edit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            config = load_config(DEFAULT_CONFIG_PATH, persist=False)
            written = []
            real_write = config_utils.atomic_write_bytes

            def commit_then_external_edit(data, destination):
                real_write(data, destination)
                written.append(data)
                Path(destination).write_bytes(b"external replacement")

            with patch.object(config_utils, "atomic_write_bytes", side_effect=commit_then_external_edit):
                fingerprint = atomic_write_config(config, path, return_fingerprint=True)
            self.assertEqual(fingerprint, hashlib.sha256(written[0]).hexdigest())
            self.assertNotEqual(fingerprint, hashlib.sha256(path.read_bytes()).hexdigest())

    def test_atomic_replace_failure_keeps_old_config_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.ini"
            path.write_bytes(b"original config")
            config = load_config(DEFAULT_CONFIG_PATH, persist=False)
            with patch.object(config_utils.os, "replace", side_effect=OSError("forced replace failure")):
                with self.assertRaises(OSError):
                    atomic_write_config(config, path, return_fingerprint=True)
            self.assertEqual(path.read_bytes(), b"original config")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_legacy_cr_newlines_keep_their_values_and_raw_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            data = b"[rumble]\rlf_strength = 0.40\r"
            (Path(directory) / "Legacy.ini").write_bytes(data)
            snapshot, fingerprint = read_profile("Legacy", directory, return_fingerprint=True)
            self.assertEqual(snapshot.get("rumble", "lf_strength"), "0.40")
            self.assertEqual(fingerprint, hashlib.sha256(data).hexdigest())
            self.assertEqual(read_profile("Legacy", directory).get("rumble", "lf_strength"), "0.40")

    def test_optional_fingerprint_matches_original_bytes_and_default_api(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "Partial.ini"
            data = "# 自訂方案\r\n[rumble]\r\nlf_strength = 0.40\r\n".encode("utf-8")
            path.write_bytes(data)
            snapshot, fingerprint = read_profile("Partial", directory, return_fingerprint=True)
            self.assertEqual(fingerprint, hashlib.sha256(data).hexdigest())
            self.assertEqual(snapshot.get("rumble", "lf_strength"), "0.40")
            self.assertTrue(snapshot.has_section("buttons"))
            self.assertEqual(path.read_bytes(), data)
            path.write_text("[rumble]\nlf_strength = 0.80\n", encoding="utf-8")
            self.assertEqual(snapshot.get("rumble", "lf_strength"), "0.40")
            self.assertEqual(read_profile("Partial", directory).get("rumble", "lf_strength"), "0.80")

    def test_switch_and_reload_keep_the_fingerprint_of_the_loaded_contents(self):
        for action in ("switch", "reload"):
            with self.subTest(action=action), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                profiles = root / "profiles"
                config_path = root / "config.ini"
                config = load_config(DEFAULT_CONFIG_PATH, persist=False)
                config.set("gui", "active_profile", "Alpha" if action == "switch" else "Beta")
                config.set("rumble", "lf_strength", "0.20")
                atomic_write_config(config, config_path)
                save_profile(config, "Alpha", profiles)
                config.set("rumble", "lf_strength", "0.40")
                save_profile(config, "Beta", profiles)
                loaded_fingerprint = hashlib.sha256((profiles / "Beta.ini").read_bytes()).hexdigest()
                gui = object.__new__(ConfigGUI)
                gui.root = None
                gui.language = "en"
                gui.config = load_config(config_path, persist=False)
                gui.active_profile = "Alpha" if action == "switch" else "Beta"
                gui._profile_switch_in_progress = False
                gui._external_active_profile_changed = action == "reload"
                gui.main_process = None
                gui.profile_name_var = Mock()
                gui._confirm_profile_transition = Mock(return_value=True)
                for method in (
                    "reload_profile_variables", "request_adaptive_window_update",
                    "refresh_profile_list", "request_live_settings_reload",
                ):
                    setattr(gui, method, Mock())

                changed = True

                def commit_config(candidate, path):
                    atomic_write_config(candidate, path)
                    if not changed:
                        return
                    external = read_profile("Beta", profiles)
                    external.set("rumble", "lf_strength", "0.80")
                    atomic_write_config(external, profiles / "Beta.ini")

                with (
                    patch.object(config_gui, "PROFILE_DIR", profiles),
                    patch.object(config_gui, "CONFIG_PATH", config_path),
                    patch.object(config_gui, "atomic_write_config", side_effect=commit_config),
                    patch.object(config_gui.messagebox, "askyesnocancel", return_value=True),
                    patch.object(config_gui.messagebox, "showinfo"),
                ):
                    if action == "switch":
                        gui.apply_selected_profile("Beta")
                    else:
                        self.assertEqual(gui.resolve_external_active_profile_conflict(), "reloaded")
                    self.assertEqual(gui.config.get("rumble", "lf_strength"), "0.40")
                    self.assertEqual(read_profile("Beta", profiles).get("rumble", "lf_strength"), "0.80")
                    self.assertEqual(gui._active_profile_content_baseline, loaded_fingerprint)
                    self.assertTrue(gui._external_active_profile_changed)
                    with patch.object(config_gui.messagebox, "askyesnocancel", return_value=None) as conflict:
                        self.assertFalse(gui.save_current_profile(show_message=False))
                    conflict.assert_called_once()
                    self.assertEqual(read_profile("Beta", profiles).get("rumble", "lf_strength"), "0.80")
                    # A stable later reload must clear the conflict normally.
                    changed = False
                    with patch.object(config_gui.messagebox, "askyesnocancel", return_value=True):
                        self.assertEqual(gui.resolve_external_active_profile_conflict(), "reloaded")
                    self.assertFalse(gui._external_active_profile_changed)
                    self.assertEqual(gui.config.get("rumble", "lf_strength"), "0.80")
                    self.assertEqual(gui._active_profile_content_baseline, gui.profile_file_fingerprint("Beta"))


if __name__ == "__main__":
    unittest.main()
