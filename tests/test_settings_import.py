import configparser
from contextlib import contextmanager
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from config_utils import (  # noqa: E402
    DEFAULT_CONFIG_PATH,
    atomic_write_config,
    load_config,
)
from settings_import import (  # noqa: E402
    SettingsImportError,
    apply_settings_import,
    scan_settings_import,
)
import settings_import as settings_import_module  # noqa: E402


class SettingsImportTests(unittest.TestCase):
    def test_clean_package_root_imports_config_profiles_and_layers(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            (source / "app").mkdir()
            shutil.move(str(source / "src"), str(source / "app" / "src"))
            self.write_source_config(source / "app")
            self.write_profile(source / "app" / "src" / "profiles", "Imported", "0.77")
            self.copy_layer(source / "app")
            plan = self.scan(source, destination)
            self.assertEqual(plan.source_root, source)
            self.assertIn("Imported", plan.profiles)
            self.assertTrue(plan.layers)
            self.apply(plan, destination)
            self.assertEqual(load_config(destination / "src" / "config.ini", persist=False).get("gui", "language"), "en")
            self.assertTrue((destination / "src" / "profiles" / "Imported.ini").is_file())

    def make_installations(self):
        temporary = tempfile.TemporaryDirectory()
        root = Path(temporary.name)
        source = root / "old-s2p"
        destination = root / "current-s2p"
        for install in (source, destination):
            (install / "src" / "profiles").mkdir(parents=True)
            (install / "src" / "layers").mkdir()
        destination_config = destination / "src" / "config.ini"
        load_config(destination_config)
        return temporary, source, destination

    @staticmethod
    def write_source_config(source, active="Imported", language="en"):
        (source / "src" / "config.ini").write_text(
            "[gui]\n"
            f"language = {language}\n"
            f"active_profile = {active}\n"
            "\n[sticks.PAD01]\n"
            "left_center = 1, 2\nleft_max = 3, 4\nleft_min = 5, 6\n"
            "right_center = 7, 8\nright_max = 9, 10\nright_min = 11, 12\n"
            "\n[gyro.PAD01]\nbias = 1.0, 2.0, 3.0\n",
            encoding="utf-8",
        )

    @staticmethod
    def write_profile(directory, name, marker):
        profile = configparser.ConfigParser()
        profile.read(DEFAULT_CONFIG_PATH, encoding="utf-8")
        profile.set("rumble", "lf_strength", marker)
        atomic_write_config(profile, directory / f"{name}.ini")

    @staticmethod
    def copy_layer(source):
        shutil.copy2(SRC / "layers" / "mouse.json", source / "src" / "layers" / "mouse.json")

    def scan(self, source, destination):
        return scan_settings_import(
            source,
            config_path=destination / "src" / "config.ini",
            profile_dir=destination / "src" / "profiles",
            layer_dir=destination / "src" / "layers",
        )

    def apply(self, plan, destination, **kwargs):
        return apply_settings_import(
            plan,
            config_path=destination / "src" / "config.ini",
            profile_dir=destination / "src" / "profiles",
            layer_dir=destination / "src" / "layers",
            **kwargs,
        )

    def test_v0710_to_v0711_merges_current_defaults_and_calibration(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            self.write_source_config(source)
            self.write_profile(source / "src" / "profiles", "Imported", "0.77")
            self.write_profile(source / "src" / "profiles", "System Default", "0.01")
            shutil.copy2(
                DEFAULT_CONFIG_PATH,
                destination / "src" / "profiles" / "System Default.ini",
            )
            system_default_before = (
                destination / "src" / "profiles" / "System Default.ini"
            ).read_bytes()
            self.copy_layer(source)
            plan = self.scan(source, destination)
            result = self.apply(plan, destination)
            migrated = load_config(destination / "src" / "config.ini", persist=False)
            self.assertEqual(migrated.get("gui", "language"), "en")
            self.assertTrue(migrated.has_option("gui", "automatic_update_checks"))
            self.assertEqual(migrated.get("sticks.PAD01", "left_center"), "1, 2")
            self.assertEqual(result.active_profile, "Imported")
            self.assertEqual(result.calibration_count, 1)
            self.assertTrue((destination / "src" / "profiles" / "Imported.ini").exists())
            self.assertEqual(
                (destination / "src" / "profiles" / "System Default.ini").read_bytes(),
                system_default_before,
            )
            self.assertTrue((destination / "src" / "layers" / "mouse.json").exists())

    def test_same_version_conflicts_are_explicitly_skipped_or_overwritten(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            self.write_source_config(source)
            self.write_profile(source / "src" / "profiles", "Imported", "0.77")
            self.copy_layer(source)
            self.write_profile(destination / "src" / "profiles", "Imported", "0.22")
            self.copy_layer(destination)
            plan = self.scan(source, destination)
            self.assertEqual(plan.profile_conflicts, ("Imported",))
            self.assertEqual(plan.layer_conflicts, ("mouse",))
            skipped = self.apply(plan, destination)
            self.assertEqual(skipped.profiles_skipped, ("Imported",))
            self.assertEqual(skipped.layers_skipped, ("mouse",))
            destination_profile = configparser.ConfigParser()
            destination_profile.read(destination / "src" / "profiles" / "Imported.ini")
            self.assertEqual(destination_profile.get("rumble", "lf_strength"), "0.22")
            overwritten = self.apply(
                plan, destination,
                profile_conflict="overwrite", layer_conflict="overwrite",
            )
            self.assertEqual(overwritten.profiles_imported, ("Imported",))
            destination_profile.read(destination / "src" / "profiles" / "Imported.ini")
            self.assertEqual(destination_profile.get("rumble", "lf_strength"), "0.77")

    def test_missing_source_active_profile_uses_existing_safe_fallback(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            self.write_source_config(source, active="Missing Profile")
            self.write_profile(source / "src" / "profiles", "Imported", "0.77")
            self.write_profile(destination / "src" / "profiles", "General", "0.33")
            current = load_config(destination / "src" / "config.ini", persist=False)
            current.set("gui", "active_profile", "General")
            atomic_write_config(current, destination / "src" / "config.ini")
            result = self.apply(self.scan(source, destination), destination)
            self.assertEqual(result.active_profile, "General")
            self.assertEqual(
                load_config(destination / "src" / "config.ini", persist=False).get(
                    "gui", "active_profile"
                ),
                "General",
            )

    def test_legacy_in_config_mapping_layers_use_existing_normalization(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            self.write_source_config(source)
            source_config = configparser.ConfigParser()
            source_config.read(source / "src" / "config.ini")
            source_config.add_section("mapping_layers")
            source_config.set(
                "mapping_layers",
                "layers",
                '[{"id":"legacy-layer","name":"Legacy Layer",'
                '"activation_buttons":["GL"],"mode":"HOLD",'
                '"buttons":{},"stick_left":{},"stick_right":{}}]',
            )
            atomic_write_config(source_config, source / "src" / "config.ini")
            result = self.apply(self.scan(source, destination), destination)
            self.assertEqual(result.layers_imported, ("Legacy Layer",))
            self.assertTrue(
                (destination / "src" / "layers" / "Legacy Layer.json").is_file()
            )

    def test_invalid_sources_do_not_change_destination(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            destination_config = destination / "src" / "config.ini"
            original = destination_config.read_bytes()
            self.write_source_config(source)
            (source / "src" / "config.ini").write_text("[broken\n", encoding="utf-8")
            with self.assertRaises(SettingsImportError):
                self.scan(source, destination)
            self.assertEqual(destination_config.read_bytes(), original)
            with self.assertRaises(SettingsImportError):
                self.scan(source.parent / "not-s2p", destination)

    def test_invalid_profile_or_managed_layer_prevents_partial_migration(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            self.write_source_config(source)
            self.write_profile(source / "src" / "profiles", "Imported", "0.77")
            original = (destination / "src" / "config.ini").read_bytes()
            (source / "src" / "profiles" / "Broken.ini").write_text(
                "[rumble]\nlf_strength = invalid\n", encoding="utf-8"
            )
            with self.assertRaises(SettingsImportError):
                self.scan(source, destination)
            self.assertEqual((destination / "src" / "config.ini").read_bytes(), original)
            (source / "src" / "profiles" / "Broken.ini").unlink()
            (source / "src" / "layers" / "bad.json").write_text(
                '{"format":"S2P-XInput-Lite Mapping Layer","version":1,"layer":{}}',
                encoding="utf-8",
            )
            with self.assertRaises(SettingsImportError):
                self.scan(source, destination)

    def test_invalid_global_settings_rejected_without_changing_destination(self):
        cases = (
            ("serial", "baudrate", "not-a-number"),
            ("serial", "baudrate", "0"),
            ("gui", "language", "unexpected"),
            ("gui", "idle_disconnect_minutes", "abc"),
            ("gui", "idle_disconnect_minutes", "7"),
            ("gui", "automatic_update_checks", "maybe"),
            ("gui", "ignored_update_version", "nonsense"),
        )
        for section, option, value in cases:
            with self.subTest(section=section, option=option, value=value):
                temporary, source, destination = self.make_installations()
                with temporary:
                    self.write_source_config(source)
                    source_config_path = source / "src" / "config.ini"
                    source_config = configparser.ConfigParser()
                    source_config.read(source_config_path, encoding="utf-8")
                    if not source_config.has_section(section):
                        source_config.add_section(section)
                    source_config.set(section, option, value)
                    atomic_write_config(source_config, source_config_path)
                    destination_config = destination / "src" / "config.ini"
                    before = destination_config.read_bytes()
                    with self.assertRaises(SettingsImportError):
                        self.scan(source, destination)
                    self.assertEqual(destination_config.read_bytes(), before)

    def test_rollback_stays_inside_cross_process_lock(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            self.write_source_config(source)
            self.write_profile(source / "src" / "profiles", "Imported", "0.77")
            plan = self.scan(source, destination)
            destination_config = destination / "src" / "config.ini"
            locked = {"value": False}
            original_snapshot = settings_import_module._snapshot
            original_restore = settings_import_module._restore
            original_writer = settings_import_module.atomic_write_config
            events = []

            @contextmanager
            def observed_lock():
                self.assertFalse(locked["value"])
                locked["value"] = True
                try:
                    yield
                finally:
                    locked["value"] = False

            def observed_snapshot(paths):
                self.assertTrue(locked["value"])
                events.append("snapshot")
                return original_snapshot(paths)

            def observed_restore(snapshot):
                self.assertTrue(locked["value"])
                events.append("restore")
                return original_restore(snapshot)

            def fail_final_config(config, path):
                self.assertTrue(locked["value"])
                if Path(path) == destination_config:
                    raise OSError("simulated final config failure")
                return original_writer(config, path)

            with (
                patch.object(settings_import_module, "config_file_lock", observed_lock),
                patch.object(settings_import_module, "_snapshot", observed_snapshot),
                patch.object(settings_import_module, "_restore", observed_restore),
                patch.object(settings_import_module, "atomic_write_config", fail_final_config),
            ):
                with self.assertRaises(OSError):
                    self.apply(plan, destination)
            self.assertEqual(events, ["snapshot", "restore"])
            self.assertFalse(locked["value"])

    def test_destination_snapshot_is_built_after_acquiring_lock(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            self.write_source_config(source)
            plan = self.scan(source, destination)
            locked = {"value": False}
            original_list_profiles = settings_import_module.list_profiles
            original_load_layers = settings_import_module.load_managed_layer_directory

            @contextmanager
            def observed_lock():
                self.assertFalse(locked["value"])
                locked["value"] = True
                try:
                    yield
                finally:
                    locked["value"] = False

            def observed_list_profiles(*args, **kwargs):
                self.assertTrue(locked["value"])
                return original_list_profiles(*args, **kwargs)

            def observed_load_layers(*args, **kwargs):
                self.assertTrue(locked["value"])
                return original_load_layers(*args, **kwargs)

            with (
                patch.object(settings_import_module, "config_file_lock", observed_lock),
                patch.object(settings_import_module, "list_profiles", observed_list_profiles),
                patch.object(settings_import_module, "load_managed_layer_directory", observed_load_layers),
            ):
                self.apply(plan, destination)

            self.assertFalse(locked["value"])

    def test_rollback_restores_config_profiles_and_layers_after_write_failure(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            self.write_source_config(source)
            self.write_profile(source / "src" / "profiles", "Imported", "0.77")
            self.copy_layer(source)
            destination_config = destination / "src" / "config.ini"
            config_before = destination_config.read_bytes()
            plan = self.scan(source, destination)
            original_writer = settings_import_module.atomic_write_config

            def fail_final_config(config, path):
                if Path(path) == destination_config:
                    raise OSError("simulated config write failure")
                return original_writer(config, path)

            with patch.object(settings_import_module, "atomic_write_config", fail_final_config):
                with self.assertRaises(OSError):
                    self.apply(plan, destination)
            self.assertEqual(destination_config.read_bytes(), config_before)
            self.assertFalse((destination / "src" / "profiles" / "Imported.ini").exists())
            self.assertFalse((destination / "src" / "layers" / "mouse.json").exists())
