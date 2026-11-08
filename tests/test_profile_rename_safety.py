from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import config_gui
from config_gui import ConfigGUI
from config_utils import (
    DEFAULT_CONFIG_PATH, atomic_write_config, load_config, read_profile,
    rename_profile, save_profile,
)


class Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value

    def set(self, value):
        self.value = value


class ProfileRenameSafetyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.profiles = root / "profiles"
        self.profiles.mkdir()
        self.config_path = root / "config.ini"
        config = load_config(DEFAULT_CONFIG_PATH, persist=False)
        config.set("gui", "active_profile", "Alpha")
        config.set("rumble", "lf_strength", "0.25")
        atomic_write_config(config, self.config_path)
        for name in ("Alpha", "Other"):
            save_profile(config, name, self.profiles)
        gui = self.gui = object.__new__(ConfigGUI)
        gui.root = None
        gui.config = config
        gui.active_profile = "Alpha"
        gui.profile_name_var = Value("Alpha")
        gui.profile_names = ["Alpha", "Other"]
        gui.tr = lambda text: text
        gui._profile_context_target = None
        gui._external_active_profile_changed = False
        for patcher in (
            patch.object(config_gui, "PROFILE_DIR", self.profiles),
            patch.object(config_gui, "CONFIG_PATH", self.config_path),
            patch.object(config_gui.messagebox, "showerror"),
            patch.object(config_gui.messagebox, "showwarning"),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        gui._active_profile_content_baseline = gui.profile_file_fingerprint("Alpha")
        gui._profile_folder_snapshot = gui._capture_profile_folder_snapshot()

    def test_case_only_external_rename_during_prompt_keeps_active_profile_linked(self):
        def choose_name(*args, **kwargs):
            rename_profile("Alpha", "alpha", self.profiles)
            self.gui.sync_profile_directory()
            self.assertEqual(self.gui.active_profile, "alpha")
            return "Beta"

        with patch.object(config_gui.simpledialog, "askstring", side_effect=choose_name):
            self.gui.rename_selected_profile("Alpha")
        self.assertTrue((self.profiles / "Beta.ini").is_file())
        self.assertEqual(self.gui.active_profile, "Beta")
        self.assertEqual(load_config(self.config_path, persist=False).get("gui", "active_profile"), "Beta")

    def test_rename_preserves_conflict_detection_for_edit_during_prompt(self):
        original_baseline = self.gui._active_profile_content_baseline

        def choose_name(*args, **kwargs):
            external = read_profile("Alpha", self.profiles)
            external.set("rumble", "lf_strength", "0.77")
            atomic_write_config(external, self.profiles / "Alpha.ini")
            return "Beta"

        with patch.object(config_gui.simpledialog, "askstring", side_effect=choose_name):
            self.gui.rename_selected_profile("Alpha")
        self.assertEqual(self.gui._active_profile_content_baseline, original_baseline)
        self.assertEqual(read_profile("Beta", self.profiles).get("rumble", "lf_strength"), "0.77")
        self.assertEqual(self.gui.config.get("rumble", "lf_strength"), "0.25")
        self.gui.sync_profile_directory()
        with patch.object(config_gui.messagebox, "askyesnocancel", return_value=None):
            self.assertEqual(self.gui.resolve_external_active_profile_conflict(), "cancel")

    def test_case_variant_of_active_profile_cannot_be_deleted(self):
        path = self.profiles / "Alpha.ini"
        before = path.read_bytes()
        with patch.object(config_gui.messagebox, "askyesno", return_value=True):
            self.gui.delete_selected_profile("alpha")
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), before)

    def test_import_cannot_overwrite_active_profile_with_different_name_case(self):
        path = self.profiles / "Alpha.ini"
        before = path.read_bytes()
        source = self.profiles.parent / "Import.ini"
        source.write_text("[rumble]\nlf_strength = 0.77\n", encoding="utf-8")
        self.gui.active_profile = "alpha"
        with (
            patch.object(config_gui.filedialog, "askopenfilename", return_value=str(source)),
            patch.object(config_gui.simpledialog, "askstring", side_effect=["ALPHA", None]),
            patch.object(config_gui.messagebox, "askyesno", return_value=True),
            patch.object(config_gui.messagebox, "showinfo"),
        ):
            self.gui.import_profile_file()
        self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
