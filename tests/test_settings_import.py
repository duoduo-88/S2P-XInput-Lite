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
    SettingsFileChanges,
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
    def test_source_profiles_with_invalid_mappings_are_rejected_without_writes(self):
        for payload in (
            "[buttons]\na = CUSTOM_KEYBOARD\n",
            "[buttons]\nunknown_button = A\n",
            "[stick_direction_left]\nup = MOUSE:LEFT\n",
        ):
            with self.subTest(payload=payload):
                temporary, source, destination = self.make_installations()
                with temporary:
                    self.write_source_config(source)
                    (source / "src" / "profiles" / "Imported.ini").write_text(payload, encoding="utf-8")
                    self.write_profile(destination / "src" / "profiles", "Imported", "0.22")
                    path = destination / "src" / "config.ini"
                    before = path.read_bytes()
                    target = destination / "src" / "profiles" / "Imported.ini"
                    profile_before = target.read_bytes()
                    with self.assertRaises(SettingsImportError):
                        self.scan(source, destination)
                    self.assertEqual(path.read_bytes(), before)
                    self.assertEqual(target.read_bytes(), profile_before)

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
            original_snapshot = SettingsFileChanges._original
            original_restore = SettingsFileChanges.restore
            original_writer = SettingsFileChanges.write_config
            events = []

            @contextmanager
            def observed_lock():
                self.assertFalse(locked["value"])
                locked["value"] = True
                try:
                    yield
                finally:
                    locked["value"] = False

            def observed_snapshot(changes, path):
                self.assertTrue(locked["value"])
                events.append("snapshot")
                return original_snapshot(changes, path)

            def observed_restore(changes):
                self.assertTrue(locked["value"])
                events.append("restore")
                return original_restore(changes)

            def fail_final_config(changes, config, path):
                self.assertTrue(locked["value"])
                if Path(path) == destination_config:
                    raise OSError("simulated final config failure")
                return original_writer(changes, config, path)

            with (
                patch.object(settings_import_module, "config_file_lock", observed_lock),
                patch.object(SettingsFileChanges, "_original", observed_snapshot),
                patch.object(SettingsFileChanges, "restore", observed_restore),
                patch.object(SettingsFileChanges, "write_config", fail_final_config),
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

    def test_import_preserves_destination_updates_made_during_preview(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            self.write_source_config(source)
            self.write_profile(source / "src" / "profiles", "Imported", "0.77")
            path = destination / "src" / "config.ini"
            current = load_config(path, persist=False)
            current.add_section("gyro.PAD02")
            current.set("gyro.PAD02", "bias", "0.0, 0.0, 0.0")
            atomic_write_config(current, path)
            plan = self.scan(source, destination)
            current = load_config(path, persist=False)
            current.add_section("gyro.NEW_CONTROLLER")
            current.set("gyro.NEW_CONTROLLER", "bias", "0.1, 0.2, 0.3")
            current.set("gyro.PAD02", "bias", "0.4, 0.5, 0.6")
            current.add_section("gyro.PAD01")
            current.set("gyro.PAD01", "bias", "9.0, 9.0, 9.0")
            current.set("gui", "automatic_update_checks", "false")
            current.set("serial", "port", "COM77")
            atomic_write_config(current, path)
            self.apply(plan, destination)
            imported = load_config(path, persist=False)
            self.assertEqual(imported.get("gyro.NEW_CONTROLLER", "bias", fallback=""), "0.1, 0.2, 0.3")
            self.assertEqual(imported.get("gyro.PAD02", "bias"), "0.4, 0.5, 0.6")
            self.assertFalse(imported.getboolean("gui", "automatic_update_checks"))
            self.assertEqual(imported.get("serial", "port"), "COM77")
            self.assertEqual(imported.get("gyro.PAD01", "bias"), "1.0, 2.0, 3.0")
            self.assertEqual(imported.get("gui", "language"), "en")
            self.assertEqual(imported.get("rumble", "lf_strength"), "0.77")

    def test_import_keeps_reviewed_source_and_does_not_mutate_preview(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            self.write_source_config(source)
            self.write_profile(source / "src" / "profiles", "Imported", "0.77")
            plan = self.scan(source, destination)
            preview = {section: dict(plan.config.items(section)) for section in plan.config.sections()}
            self.write_source_config(source, language="zh")
            self.apply(plan, destination)
            imported = load_config(destination / "src" / "config.ini", persist=False)
            self.assertEqual(imported.get("gui", "language"), "en")
            self.assertEqual(
                {section: dict(plan.config.items(section)) for section in plan.config.sections()},
                preview,
            )

    def test_import_without_source_active_profile_preserves_latest_active_profile(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            self.write_source_config(source, active="Missing")
            for name in ("General", "Fresh"):
                self.write_profile(destination / "src" / "profiles", name, "0.50")
            path = destination / "src" / "config.ini"
            current = load_config(path, persist=False)
            current.set("gui", "active_profile", "General")
            atomic_write_config(current, path)
            plan = self.scan(source, destination)
            current.set("gui", "active_profile", "Fresh")
            current.set("rumble", "lf_strength", "0.31")
            atomic_write_config(current, path)
            result = self.apply(plan, destination)
            self.assertEqual(result.active_profile, "Fresh")
            self.assertEqual(load_config(path, persist=False).get("gui", "active_profile"), "Fresh")
            self.assertEqual(load_config(path, persist=False).get("rumble", "lf_strength"), "0.31")

    def test_deleted_active_profile_fallback_loads_the_fallback_settings(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            self.write_source_config(source, active="Missing")
            profiles = destination / "src" / "profiles"
            self.write_profile(profiles, "General", "0.66")
            self.write_profile(profiles, "Gone", "0.22")
            path = destination / "src" / "config.ini"
            current = load_config(path, persist=False)
            current.set("gui", "active_profile", "Gone")
            current.set("rumble", "lf_strength", "0.22")
            atomic_write_config(current, path)
            plan = self.scan(source, destination)
            (profiles / "Gone.ini").unlink()
            result = self.apply(plan, destination)
            imported = load_config(path, persist=False)
            self.assertEqual(result.active_profile, "General")
            self.assertEqual(imported.get("gui", "active_profile"), "General")
            self.assertEqual(imported.get("rumble", "lf_strength"), "0.66")
            self.assertEqual(imported.get("gyro.PAD01", "bias"), "1.0, 2.0, 3.0")

    def test_invalid_fallback_profile_cancels_import_before_any_writes(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            self.write_source_config(source, active="Missing")
            self.write_profile(source / "src" / "profiles", "Imported", "0.77")
            profiles = destination / "src" / "profiles"
            self.write_profile(profiles, "General", "0.66")
            path = destination / "src" / "config.ini"
            current = load_config(path, persist=False)
            current.set("gui", "active_profile", "Gone")
            atomic_write_config(current, path)
            plan = self.scan(source, destination)
            before = path.read_bytes()
            for payload in (
                "[rumble]\nlf_strength = invalid\n",
                "[buttons]\na = KEYBOARD:NOT_A_KEY\n",
            ):
                with self.subTest(payload=payload):
                    (profiles / "General.ini").write_text(payload, encoding="utf-8")
                    with self.assertRaises(ValueError):
                        self.apply(plan, destination)
                    self.assertEqual(path.read_bytes(), before)
                    self.assertFalse((profiles / "Imported.ini").exists())

    def test_failed_import_restores_latest_destination_not_preview_snapshot(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            self.write_source_config(source)
            self.write_profile(source / "src" / "profiles", "Imported", "0.77")
            plan = self.scan(source, destination)
            path = destination / "src" / "config.ini"
            current = load_config(path, persist=False)
            current.add_section("gyro.NEW_CONTROLLER")
            current.set("gyro.NEW_CONTROLLER", "bias", "0.1, 0.2, 0.3")
            atomic_write_config(current, path)
            before = path.read_bytes()
            original_writer = SettingsFileChanges.write_config

            def fail_final_config(changes, config, destination_path):
                if Path(destination_path) == path:
                    raise OSError("simulated final config failure")
                return original_writer(changes, config, destination_path)

            with patch.object(SettingsFileChanges, "write_config", fail_final_config):
                with self.assertRaises(OSError):
                    self.apply(plan, destination)
            self.assertEqual(path.read_bytes(), before)
            self.assertFalse((destination / "src" / "profiles" / "Imported.ini").exists())

    def test_overwrite_active_profile_with_different_case_uses_imported_name(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            self.write_source_config(source, active="Missing")
            self.write_profile(source / "src" / "profiles", "Imported", "0.77")
            self.write_profile(destination / "src" / "profiles", "imported", "0.22")
            path = destination / "src" / "config.ini"
            current = load_config(path, persist=False)
            current.set("gui", "active_profile", "imported")
            atomic_write_config(current, path)
            plan = self.scan(source, destination)
            result = self.apply(plan, destination, profile_conflict="overwrite")
            imported = load_config(path, persist=False)
            self.assertEqual(result.active_profile, "Imported")
            self.assertEqual(imported.get("gui", "active_profile"), "Imported")
            self.assertEqual(imported.get("rumble", "lf_strength"), "0.77")

    def test_rollback_restores_config_profiles_and_layers_after_write_failure(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            self.write_source_config(source)
            self.write_profile(source / "src" / "profiles", "Imported", "0.77")
            self.copy_layer(source)
            destination_config = destination / "src" / "config.ini"
            config_before = destination_config.read_bytes()
            plan = self.scan(source, destination)
            original_writer = SettingsFileChanges.write_config

            def fail_final_config(changes, config, path):
                if Path(path) == destination_config:
                    raise OSError("simulated config write failure")
                return original_writer(changes, config, path)

            with patch.object(SettingsFileChanges, "write_config", fail_final_config):
                with self.assertRaises(OSError):
                    self.apply(plan, destination)
            self.assertEqual(destination_config.read_bytes(), config_before)
            self.assertFalse((destination / "src" / "profiles" / "Imported.ini").exists())
            self.assertFalse((destination / "src" / "layers" / "mouse.json").exists())

    def test_import_rollback_keeps_external_edits_after_a_partial_write(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            self.write_source_config(source)
            self.write_profile(source / "src" / "profiles", "Imported", "0.77")
            self.copy_layer(source)
            plan = self.scan(source, destination)
            config_path = destination / "src" / "config.ini"
            imported = destination / "src" / "profiles" / "Imported.ini"
            write = SettingsFileChanges.write_config

            def fail_after_external_edits(changes, config, path):
                if Path(path) != config_path:
                    return write(changes, config, path)
                self.assertTrue(imported.exists())
                imported.write_bytes(b"external edit to imported profile")
                config_path.write_bytes(b"external config edit")
                raise OSError("simulated config write failure")

            with patch.object(SettingsFileChanges, "write_config", fail_after_external_edits):
                with self.assertRaises(OSError):
                    self.apply(plan, destination)

            self.assertEqual(imported.read_bytes(), b"external edit to imported profile")
            self.assertEqual(config_path.read_bytes(), b"external config edit")
            self.assertFalse((destination / "src" / "layers" / "mouse.json").exists())

    def test_import_rejects_external_config_edits_before_its_final_write(self):
        temporary, source, destination = self.make_installations()
        with temporary:
            self.write_source_config(source)
            self.write_profile(source / "src" / "profiles", "Imported", "0.77")
            self.copy_layer(source)
            plan = self.scan(source, destination)
            config_path = destination / "src" / "config.ini"
            external = config_path.read_bytes() + b"\n[gyro.EXTERNAL]\nbias = 0.1, 0.2, 0.3\n"
            save_layers = settings_import_module.save_layers

            def save_layers_then_edit_config(*args, **kwargs):
                result = save_layers(*args, **kwargs)
                config_path.write_bytes(external)
                return result

            with patch.object(settings_import_module, "save_layers", save_layers_then_edit_config):
                with self.assertRaises(OSError):
                    self.apply(plan, destination)

            self.assertEqual(config_path.read_bytes(), external)
            self.assertFalse((destination / "src" / "profiles" / "Imported.ini").exists())
            self.assertFalse((destination / "src" / "layers" / "mouse.json").exists())
