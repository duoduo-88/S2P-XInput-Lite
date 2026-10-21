import configparser
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from mapping_layers import (
    LayerSelector,
    apply_layer_state,
    import_layers_file,
    load_layers,
    normalize_layers,
    parse_bool,
    save_layers,
    store_layer_state,
)
from config_utils import atomic_write_config, load_config, read_profile, save_profile
import mapping_layers as mapping_layers_module


MASKS = {"GL": 0x01, "GR": 0x02, "ZL": 0x04, "A": 0x08}
BASE_BUTTONS = {"a": "B", "gl": "NONE", "gr": "NONE", "zl": "LT"}
BASE_STICK = {
    "mode": "4WAY", "direction_deadzone": "5",
    "trigger_threshold": "0.60", "release_threshold": "0.50",
    "mouse_speed": "900", "up": "NONE", "up_right": "NONE",
    "right": "NONE", "down_right": "NONE", "down": "NONE",
    "down_left": "NONE", "left": "NONE", "up_left": "NONE",
}
BASE_STICKS = {"left": BASE_STICK, "right": BASE_STICK}


def layer(layer_id, chord, mode="HOLD", enabled=True):
    return {
        "id": layer_id, "name": layer_id, "enabled": enabled,
        "activation_buttons": chord, "mode": mode,
    }


def strict_payload(layer_id):
    return {
        "format": "S2P-XInput-Lite Mapping Layer",
        "version": 1,
        "layer": {
            "id": layer_id,
            "activation_buttons": ["GL"],
            "mode": "HOLD",
            "buttons": dict(BASE_BUTTONS),
            "stick_left": dict(BASE_STICK),
            "stick_right": dict(BASE_STICK),
        },
    }


class MappingLayerTests(unittest.TestCase):
    def test_normalization_fills_complete_mapping_and_round_trips_unicode(self):
        with tempfile.TemporaryDirectory() as directory:
            config = configparser.ConfigParser()
            config.add_section("mapping_layers")
            config.set("mapping_layers", "layers", json.dumps([{
                "name": "桌面層", "activation_buttons": ["gl", "bad", "GL"],
                "buttons": {"a": "keyboard:ctrl+c"},
            }]))
            layers = load_layers(
                config, BASE_BUTTONS, BASE_STICKS, MASKS, directory
            )
            self.assertEqual(layers[0]["name"], "桌面層")
            self.assertEqual(layers[0]["activation_buttons"], ["GL"])
            self.assertEqual(layers[0]["buttons"]["a"], "KEYBOARD:CTRL+C")
            self.assertEqual(layers[0]["buttons"]["zl"], "LT")
            save_layers(config, layers, directory)
            self.assertFalse(config.has_section("mapping_layers"))
            layer_path = Path(directory) / "桌面層.json"
            self.assertTrue(layer_path.is_file())
            payload = json.loads(layer_path.read_text(encoding="utf-8"))
            self.assertNotIn("order", payload)
            self.assertNotIn("enabled", payload["layer"])

    def test_string_false_is_not_treated_as_enabled(self):
        self.assertFalse(parse_bool("false", default=True))
        self.assertFalse(parse_bool("0", default=True))
        self.assertTrue(parse_bool("true", default=False))

    def test_save_ignores_and_preserves_malformed_unmanaged_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            broken = path / "broken.json"
            broken.write_text("{broken", encoding="utf-8")
            config = configparser.ConfigParser()
            save_layers(
                config,
                [layer("valid", ["GL"])],
                directory,
            )
            self.assertTrue(broken.is_file())
            self.assertEqual(broken.read_text(encoding="utf-8"), "{broken")
            self.assertTrue((path / "valid.json").exists())

    def test_ordinary_json_with_name_is_not_auto_loaded_or_managed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            ordinary = path / "notes.json"
            original = {"name": "ordinary metadata", "value": 123}
            ordinary.write_text(json.dumps(original), encoding="utf-8")
            config = configparser.ConfigParser()
            layers = load_layers(
                config, BASE_BUTTONS, BASE_STICKS, MASKS, directory
            )
            self.assertEqual(layers, [])
            save_layers(config, [], directory, deletable_paths=())
            self.assertEqual(
                json.loads(ordinary.read_text(encoding="utf-8")), original
            )

    def test_invalid_file_claiming_application_format_blocks_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            claimed = path / "claimed.json"
            claimed.write_text(json.dumps({
                "format": "S2P-XInput-Lite Mapping Layer",
                "version": 1,
                "layer": {"name": "missing required fields"},
            }), encoding="utf-8")
            with self.assertRaises(ValueError):
                save_layers(configparser.ConfigParser(), [], directory)
            self.assertTrue(claimed.is_file())

    def test_incomplete_scan_does_not_remove_saved_layer_state(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            layer_dir = root / "layers"
            layer_dir.mkdir()
            profile_dir = root / "profiles"
            profile_dir.mkdir()
            config_path = root / "config.ini"
            config = load_config(config_path)
            config.set("gui", "active_profile", "User")
            config.set("mapping_layer_state", "order", '["keep-me"]')
            config.set("mapping_layer_state", "enabled", '["keep-me"]')
            atomic_write_config(config, config_path)
            save_profile(config, "User", profile_dir)
            (layer_dir / "BrokenLayer.json").write_text(json.dumps({
                "format": "S2P-XInput-Lite Mapping Layer",
                "version": 1,
                "layer": {"id": "keep-me"},
            }), encoding="utf-8")

            with patch.object(mapping_layers_module, "PROFILE_DIR", profile_dir):
                loaded = load_layers(
                    config, BASE_BUTTONS, BASE_STICKS, MASKS,
                    layer_dir, config_path=config_path,
                )

            self.assertEqual(loaded, [])
            latest = load_config(config_path)
            self.assertEqual(
                latest.get("mapping_layer_state", "order"), '["keep-me"]'
            )
            self.assertEqual(
                latest.get("mapping_layer_state", "enabled"), '["keep-me"]'
            )
            self.assertEqual(
                read_profile("User", profile_dir).get(
                    "mapping_layer_state", "order"
                ),
                '["keep-me"]',
            )

    def test_duplicate_disk_ids_are_stable_and_block_save(self):
        with tempfile.TemporaryDirectory() as directory:
            layer_dir = Path(directory)
            payload = strict_payload("duplicate")
            (layer_dir / "First.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            (layer_dir / "Second.json").write_text(
                json.dumps(payload), encoding="utf-8"
            )
            config = configparser.ConfigParser()

            first = load_layers(
                config, BASE_BUTTONS, BASE_STICKS, MASKS, layer_dir
            )
            second = load_layers(
                config, BASE_BUTTONS, BASE_STICKS, MASKS, layer_dir
            )

            self.assertEqual([item["id"] for item in first], ["duplicate"])
            self.assertEqual(first, second)
            with self.assertRaises(ValueError):
                save_layers(config, first, layer_dir)
            self.assertTrue((layer_dir / "First.json").is_file())
            self.assertTrue((layer_dir / "Second.json").is_file())

    def test_load_uses_one_directory_scan_and_prefers_valid_json_to_legacy(self):
        with tempfile.TemporaryDirectory() as directory:
            layer_dir = Path(directory)
            good = layer_dir / "Good.json"
            good.write_text(
                json.dumps(strict_payload("good")), encoding="utf-8"
            )
            config = configparser.ConfigParser()
            config.add_section("mapping_layers")
            config.set("mapping_layers", "layers", json.dumps([
                layer("legacy", ["GR"])
            ]))
            original_reader = mapping_layers_module._raw_layers_from_json
            reads = []

            def counted_reader(path, strict=False):
                reads.append(Path(path).name)
                return original_reader(path, strict=strict)

            with patch.object(
                mapping_layers_module,
                "_raw_layers_from_json",
                side_effect=counted_reader,
            ):
                loaded, managed_paths, scan_complete = load_layers(
                    config, BASE_BUTTONS, BASE_STICKS, MASKS, layer_dir,
                    return_metadata=True,
                )

            self.assertEqual(reads, ["Good.json"])
            self.assertEqual([item["id"] for item in loaded], ["good"])
            self.assertEqual(managed_paths, {good.resolve()})
            self.assertTrue(scan_complete)
            self.assertTrue(good.is_file())
            self.assertFalse((layer_dir / "legacy.json").exists())

    def test_formal_s2p_manual_import_enforces_basic_field_types(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "Invalid.json"
            payload = strict_payload("invalid")
            payload["layer"]["buttons"] = []
            source.write_text(json.dumps(payload), encoding="utf-8")

            with self.assertRaises(ValueError):
                import_layers_file(
                    source, BASE_BUTTONS, BASE_STICKS, MASKS
                )

    def test_layer_name_conflicting_with_unmanaged_json_blocks_save(self):
        with tempfile.TemporaryDirectory() as directory:
            layer_dir = Path(directory)
            ordinary = layer_dir / "Mouse.json"
            ordinary.write_text('{"name": "notes"}', encoding="utf-8")
            candidate = normalize_layers(
                [{**layer("mouse-id", ["GL"]), "name": "Mouse"}],
                BASE_BUTTONS, BASE_STICKS, MASKS,
            )

            with self.assertRaises(ValueError):
                save_layers(None, candidate, layer_dir)

            self.assertEqual(
                ordinary.read_text(encoding="utf-8"), '{"name": "notes"}'
            )
            self.assertFalse((layer_dir / "Mouse (2).json").exists())

    def test_save_does_not_delete_valid_file_outside_managed_baseline(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            external = path / "external.json"
            external.write_text(
                json.dumps({"layer": {"id": "external", "buttons": {}}}),
                encoding="utf-8",
            )
            config = configparser.ConfigParser()
            save_layers(
                config,
                [layer("managed", ["GL"])],
                directory,
                deletable_paths=(),
            )
            self.assertTrue(external.is_file())
            self.assertTrue((path / "managed.json").is_file())

    def test_profile_state_restores_order_and_enabled_flags(self):
        layers = [layer("one", ["GL"]), layer("two", ["GR"])]
        config = configparser.ConfigParser()
        store_layer_state(config, [
            {**layers[1], "enabled": True},
            {**layers[0], "enabled": False},
        ])
        restored, changed = apply_layer_state(config, layers)
        self.assertFalse(changed)
        self.assertEqual([item["id"] for item in restored], ["two", "one"])
        self.assertEqual([item["enabled"] for item in restored], [True, False])

    def test_missing_layer_id_is_removed_from_profile_state(self):
        config = configparser.ConfigParser()
        config.add_section("mapping_layer_state")
        config.set("mapping_layer_state", "order", '["gone", "one"]')
        config.set("mapping_layer_state", "enabled", '["gone", "one"]')
        restored, changed = apply_layer_state(
            config, [layer("one", ["GL"], enabled=False)]
        )
        self.assertTrue(changed)
        store_layer_state(config, restored)
        self.assertEqual(config.get("mapping_layer_state", "order"), '["one"]')
        self.assertEqual(config.get("mapping_layer_state", "enabled"), '["one"]')

    def test_runtime_reconciliation_updates_config_and_active_profile(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config_path = root / "config.ini"
            profile_dir = root / "profiles"
            layer_dir = root / "layers"
            profile_dir.mkdir()
            config = load_config(config_path)
            config.set("gui", "active_profile", "User")
            config.set("mapping_layer_state", "order", '["gone", "one"]')
            config.set("mapping_layer_state", "enabled", '["gone", "one"]')
            atomic_write_config(config, config_path)
            save_profile(config, "User", profile_dir)
            normalized = normalize_layers(
                [layer("one", ["GL"])],
                BASE_BUTTONS,
                BASE_STICKS,
                MASKS,
            )
            save_layers(None, normalized, layer_dir)

            with patch.object(mapping_layers_module, "PROFILE_DIR", profile_dir):
                loaded = load_layers(
                    config,
                    BASE_BUTTONS,
                    BASE_STICKS,
                    MASKS,
                    layer_dir,
                    config_path=config_path,
                )

            self.assertEqual([item["id"] for item in loaded], ["one"])
            latest = load_config(config_path)
            self.assertEqual(
                latest.get("mapping_layer_state", "order"), '["one"]'
            )
            self.assertEqual(
                read_profile("User", profile_dir).get(
                    "mapping_layer_state", "order"
                ),
                '["one"]',
            )

    def test_hold_temporarily_overrides_toggle_then_returns(self):
        selector = LayerSelector([
            layer("game", ["GL"], "TOGGLE"),
            layer("desktop", ["GR"], "HOLD"),
        ], MASKS)
        self.assertEqual(selector.update(MASKS["GL"]), "game")
        self.assertEqual(selector.update(0), "game")
        self.assertEqual(selector.update(MASKS["GR"]), "desktop")
        self.assertEqual(selector.update(0), "game")

    def test_toggle_requires_release_and_second_press_disables(self):
        selector = LayerSelector([layer("game", ["GL"], "TOGGLE")], MASKS)
        self.assertEqual(selector.update(MASKS["GL"]), "game")
        self.assertEqual(selector.update(MASKS["GL"]), "game")
        self.assertEqual(selector.update(0), "game")
        self.assertIsNone(selector.update(MASKS["GL"]))

    def test_more_specific_hold_chord_wins_then_falls_back(self):
        selector = LayerSelector([
            layer("one", ["GL"]),
            layer("two", ["GL", "GR"]),
        ], MASKS)
        self.assertEqual(selector.update(MASKS["GL"] | MASKS["GR"]), "two")
        self.assertEqual(selector.update(MASKS["GL"]), "one")

    def test_disabled_or_empty_chord_never_activates(self):
        selector = LayerSelector([
            layer("disabled", ["GL"], enabled=False),
            layer("empty", []),
        ], MASKS)
        self.assertIsNone(selector.update(MASKS["GL"]))


if __name__ == "__main__":
    unittest.main()
