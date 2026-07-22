import configparser
import json
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config_utils import (
    BUNDLED_PROFILE_NAMES,
    DEFAULT_CONFIG_PATH,
    PROFILE_MAPPING_SECTIONS,
    PROFILE_SECTIONS,
    apply_profile,
    build_profile,
    create_bundled_profiles,
    ensure_bundled_profiles,
    is_protected_profile,
    list_profiles,
    load_config,
    read_profile,
    rename_profile,
    save_profile,
    validate_profile_name,
)
from mapping_targets import validate_button_target


def make_config(marker, calibration="calibration", language="zh"):
    config = configparser.ConfigParser()
    config.add_section("serial")
    config.set("serial", "port", "COM3")
    config.add_section("gui")
    config.set("gui", "language", language)
    config.add_section("sticks")
    config.set("sticks", "left_center", calibration)
    config.add_section("gyro.TESTPAD")
    config.set("gyro.TESTPAD", "bias", calibration)
    for section in PROFILE_SECTIONS:
        config.add_section(section)
        config.set(section, "test_value", f"{marker}-{section}")
    config.add_section("mapping_layers")
    config.set("mapping_layers", "layers", f"{marker}-global-layers")
    return config


class ProfileTests(unittest.TestCase):
    def test_release_profile_designs_are_distinct_and_safe(self):
        profile_dir = ROOT / "src" / "profiles"
        expected_modes = {
            "System Default": ("GAME", "OFF"),
            "General": ("GAME", "OFF"),
            "Action": ("GAME", "HOLD"),
            "Audio": ("AUDIO", "OFF"),
            "FPS-COMP": ("GAME", "HOLD"),
            "FPS-IMM": ("MIX", "HOLD"),
            "Racing": ("GAME", "OFF"),
            "Rhythm": ("AUDIO", "OFF"),
        }
        expected_amplitudes = {
            "System Default": 800,
            "General": 800,
            "Action": 800,
            "Audio": 800,
            "FPS-COMP": 800,
            "FPS-IMM": 800,
            "Racing": 800,
            "Rhythm": 800,
        }
        expected_audio_routing = {
            "System Default": (0.00, 0.15),
            "General": (-0.05, 0.15),
            "Action": (0.00, 0.15),
            "Audio": (0.00, 0.20),
            "FPS-COMP": (0.10, 0.75),
            "FPS-IMM": (0.05, 0.10),
            "Racing": (-0.15, 0.10),
            "Rhythm": (0.05, 0.08),
        }
        defaults = read_profile("System Default", profile_dir)
        fingerprints = {}
        for name, (audio_mode, gyro_mode) in expected_modes.items():
            profile = read_profile(name, profile_dir)
            self.assertEqual(
                set(dict(profile.items("buttons"))),
                set(dict(defaults.items("buttons"))),
                name,
            )
            for target in dict(profile.items("buttons")).values():
                validate_button_target(target)
            self.assertEqual(
                profile.get("gyro_mapping", "activation_buttons"), "ZL", name
            )
            self.assertEqual(
                profile.get("audio_haptics", "mode"), audio_mode, name
            )
            self.assertEqual(
                profile.getint("rumble", "max_amplitude"),
                expected_amplitudes[name],
                name,
            )
            if name != "FPS-COMP":
                self.assertLessEqual(
                    profile.getfloat("rumble", "hf_strength"), 0.50, name
                )
                self.assertLessEqual(
                    profile.getfloat("rumble", "lf_to_hf_compensation"),
                    0.02,
                    name,
                )
                self.assertLessEqual(
                    profile.getint("rumble", "hf_frequency"), 325, name
                )
            self.assertEqual(
                (
                    profile.getfloat("audio_haptics", "lf_hf_balance"),
                    profile.getfloat("audio_haptics", "band_6_gain"),
                ),
                expected_audio_routing[name],
                name,
            )
            self.assertEqual(
                profile.get("gyro_mapping", "activation_mode"), gyro_mode, name
            )
            order = json.loads(profile.get("mapping_layer_state", "order"))
            enabled = json.loads(profile.get("mapping_layer_state", "enabled"))
            self.assertEqual(len(order), len(set(order)), name)
            self.assertTrue(set(enabled).issubset(order), name)
            fingerprints[name] = tuple(
                (section, tuple(profile.items(section)))
                for section in PROFILE_SECTIONS
            )

        self.assertEqual(len(set(fingerprints.values())), len(fingerprints))

    def test_release_profiles_keep_alps_center_deadzone_at_least_three_percent(self):
        profile_dir = ROOT / "src" / "profiles"
        for path in profile_dir.glob("*.ini"):
            config = configparser.ConfigParser()
            self.assertTrue(config.read(path, encoding="utf-8"), path.name)
            for side in ("left", "right"):
                section = f"stick_curve_{side}"
                self.assertGreaterEqual(
                    config.getfloat(section, "deadzone"),
                    0.03,
                    f"{path.name} [{section}]",
                )

    def test_profile_round_trip_replaces_every_gameplay_section(self):
        with tempfile.TemporaryDirectory() as directory:
            profile_dir = Path(directory)
            source = make_config("aim")
            save_profile(source, "Aim Lab", profile_dir)
            profile = read_profile("Aim Lab", profile_dir)

            destination = make_config(
                "racing", calibration="keep-me", language="en"
            )
            apply_profile(destination, profile)

            for section in PROFILE_SECTIONS:
                self.assertEqual(
                    destination.get(section, "test_value"),
                    f"aim-{section}",
                )
            self.assertEqual(
                destination.get("mapping_layers", "layers"),
                "racing-global-layers",
            )
            self.assertEqual(destination.get("sticks", "left_center"), "keep-me")
            self.assertEqual(destination.get("gyro.TESTPAD", "bias"), "keep-me")
            self.assertEqual(destination.get("gui", "language"), "en")
            self.assertEqual(destination.get("serial", "port"), "COM3")

    def test_profile_file_contains_only_gameplay_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            profile_dir = Path(directory)
            save_profile(make_config("all"), "完整方案", profile_dir)
            saved = read_profile("完整方案", profile_dir)
            self.assertEqual(tuple(saved.sections()), PROFILE_SECTIONS)
            self.assertFalse(saved.has_section("mapping_layers"))
            self.assertEqual(list_profiles(profile_dir), ["完整方案"])

    def test_legacy_profile_mapping_layers_are_removed_without_touching_global(self):
        with tempfile.TemporaryDirectory() as directory:
            config = make_config("global")
            config.set("gui", "bundled_profiles_version", "4")
            save_profile(config, "Legacy", directory)
            legacy_path = Path(directory) / "Legacy.ini"
            legacy = configparser.ConfigParser()
            legacy.read(legacy_path, encoding="utf-8")
            legacy.add_section("mapping_layers")
            legacy.set("mapping_layers", "layers", "legacy-profile-layers")
            with legacy_path.open("w", encoding="utf-8") as handle:
                legacy.write(handle)

            ensure_bundled_profiles(config, directory)

            self.assertFalse(
                read_profile("Legacy", directory).has_section("mapping_layers")
            )
            self.assertEqual(
                config.get("mapping_layers", "layers"),
                "global-global-layers",
            )

    def test_invalid_or_reserved_names_are_rejected(self):
        for name in ("", "../escape", "bad:name", "CON", "name.", "通用"):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    validate_profile_name(name)

    def test_incomplete_profile_is_filled_from_system_default(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "broken.ini"
            path.write_text("[buttons]\na = b\n", encoding="utf-8")
            profile = read_profile("broken", directory)
            self.assertEqual(profile.get("buttons", "a"), "b")
            self.assertTrue(profile.has_section("rumble"))
            self.assertTrue(profile.has_option("stick_curve_left", "deadzone"))

    def test_bundled_genre_profiles_ignore_caller_mappings(self):
        with tempfile.TemporaryDirectory() as directory:
            config = make_config("base")
            config.set("buttons", "test_value", "CUSTOM_USER_BUTTONS")
            config.set(
                "stick_direction_left", "test_value", "CUSTOM_DIRECTION_MAP"
            )
            created = create_bundled_profiles(config, directory)
            self.assertEqual(tuple(created), BUNDLED_PROFILE_NAMES)
            self.assertEqual(
                tuple(list_profiles(directory)), BUNDLED_PROFILE_NAMES
            )

            for name in BUNDLED_PROFILE_NAMES:
                profile = read_profile(name, directory)
                packaged = read_profile(name, ROOT / "src" / "profiles")
                for section in PROFILE_MAPPING_SECTIONS:
                    self.assertEqual(
                        dict(profile.items(section)),
                        dict(packaged.items(section)),
                    )
                self.assertEqual(
                    profile.get("gyro_mapping", "activation_buttons"), "ZL"
                )

    def test_bundled_profile_copies_match_packaged_release_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            config = make_config("base")
            create_bundled_profiles(config, directory)
            for name in BUNDLED_PROFILE_NAMES:
                copied = read_profile(name, directory)
                packaged = read_profile(name, ROOT / "src" / "profiles")
                for section in PROFILE_SECTIONS:
                    self.assertEqual(
                        dict(copied.items(section)),
                        dict(packaged.items(section)),
                        f"{name} [{section}]",
                    )

    def test_deleted_bundled_profile_is_not_recreated(self):
        with tempfile.TemporaryDirectory() as directory:
            config = make_config("base")
            create_bundled_profiles(config, directory)
            deleted = Path(directory) / "FPS-COMP.ini"
            deleted.unlink()
            self.assertEqual(ensure_bundled_profiles(config, directory), [])
            self.assertFalse(deleted.exists())

    def test_startup_never_creates_optional_profiles(self):
        with tempfile.TemporaryDirectory() as directory:
            config = make_config("base")
            system_default = build_profile(config)
            with (Path(directory) / "System Default.ini").open(
                "w", encoding="utf-8"
            ) as handle:
                system_default.write(handle)

            self.assertEqual(ensure_bundled_profiles(config, directory), [])
            self.assertEqual(list_profiles(directory), ["System Default"])

    def test_version_one_does_not_revive_deleted_fps_presets(self):
        with tempfile.TemporaryDirectory() as directory:
            config = make_config("base")
            config.set("gui", "bundled_profiles_version", "1")
            config.set("gui", "active_profile", "FPS（無震動）")
            save_profile(config, "FPS（無震動）", directory)
            save_profile(config, "FPS（無震動_啟用陀螺儀）", directory)

            ensure_bundled_profiles(config, directory)

            names = list_profiles(directory)
            self.assertIn("FPS（無震動）", names)
            self.assertIn("FPS（無震動_啟用陀螺儀）", names)
            self.assertNotIn("FPS (NR)", names)
            self.assertNotIn("FPS (NR+Gyro)", names)
            self.assertEqual(
                config.get("gui", "active_profile"), "FPS（無震動）"
            )

    def test_legacy_default_profile_is_named_system_default(self):
        with tempfile.TemporaryDirectory() as directory:
            config = make_config("base")
            config.set("gui", "bundled_profiles_version", "2")
            config.set("gui", "active_profile", "Default")
            with (Path(directory) / "Default.ini").open(
                "w", encoding="utf-8"
            ) as handle:
                build_profile(config).write(handle)

            ensure_bundled_profiles(config, directory)

            self.assertNotIn("Default", list_profiles(directory))
            self.assertIn("System Default", list_profiles(directory))
            self.assertEqual(config.get("gui", "active_profile"), "System Default")
            self.assertTrue(is_protected_profile("System Default"))
            save_profile(config, "User Profile", directory)
            self.assertEqual(list_profiles(directory)[-1], "System Default")
            with self.assertRaises(PermissionError):
                save_profile(config, "System Default", directory)
            with self.assertRaises(PermissionError):
                rename_profile("System Default", "可修改名稱", directory)

    def test_different_legacy_alias_is_preserved_instead_of_deleted(self):
        with tempfile.TemporaryDirectory() as directory:
            current = make_config("current")
            legacy = make_config("legacy")
            save_profile(current, "General", directory)
            with (Path(directory) / "通用.ini").open(
                "w", encoding="utf-8"
            ) as handle:
                build_profile(legacy).write(handle)
            legacy.set("gui", "active_profile", "通用")

            ensure_bundled_profiles(legacy, directory)

            names = list_profiles(directory)
            self.assertIn("General", names)
            self.assertIn("通用 (Legacy)", names)
            self.assertEqual(
                legacy.get("gui", "active_profile"), "通用 (Legacy)"
            )
            self.assertEqual(
                read_profile("通用 (Legacy)", directory).get(
                    "buttons", "test_value"
                ),
                "legacy-buttons",
            )

    def test_profile_can_be_renamed_without_overwriting_another(self):
        with tempfile.TemporaryDirectory() as directory:
            config = make_config("base")
            save_profile(config, "舊名稱", directory)
            rename_profile("舊名稱", "新名稱", directory)
            self.assertFalse((Path(directory) / "舊名稱.ini").exists())
            self.assertTrue((Path(directory) / "新名稱.ini").exists())
            self.assertEqual(
                read_profile("新名稱", directory).get(
                    "buttons", "test_value"
                ),
                "base-buttons",
            )

            save_profile(config, "另一個", directory)
            with self.assertRaises(FileExistsError):
                rename_profile("新名稱", "另一個", directory)
            self.assertTrue((Path(directory) / "新名稱.ini").exists())


if __name__ == "__main__":
    unittest.main()
