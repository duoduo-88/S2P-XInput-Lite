"""Secondary controller mapping layers and their activation state machine.

Each mapping layer is stored as one UTF-8 JSON file in ``src/layers``.  The
filename is the user-facing layer name, which makes Explorer-based management
(add/delete/rename) intuitive.  Legacy ``[mapping_layers]`` data in config.ini
is migrated automatically on first load.
"""

from __future__ import annotations

import configparser
import json
import os
import re
import tempfile
import uuid
from pathlib import Path

from config_utils import (
    PROFILE_DIR,
    atomic_write_bytes,
    atomic_write_config,
    config_file_lock,
    is_protected_profile,
    load_config,
    profile_path,
    read_profile,
)
from mapping_targets import (
    validate_button_source,
    validate_button_target,
    validate_direction_target,
    validate_stick_analog_direction,
    validate_stick_direction_mode,
    validate_stick_setting_key,
)


LAYER_DIR = Path(__file__).with_name("layers")
LAYER_SECTION = "mapping_layers"
LAYER_OPTION = "layers"
LAYER_STATE_SECTION = "mapping_layer_state"
LAYER_ORDER_OPTION = "order"
LAYER_ENABLED_OPTION = "enabled"
LAYER_FILE_FORMAT = "S2P-XInput-Lite Mapping Layer"
LAYER_FILE_VERSION = 1
LAYER_MODES = ("HOLD", "TOGGLE")
STICK_SIDES = ("left", "right")
STICK_DIRECTIONS = (
    "up", "up_right", "right", "down_right",
    "down", "down_left", "left", "up_left",
)

_INVALID_LAYER_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}
_LAYER_DATA_KEYS = frozenset((
    "id", "name", "enabled", "activation_buttons", "mode",
    "buttons", "stick_left", "stick_right",
))
_STRICT_LAYER_KEYS = frozenset((
    "id", "activation_buttons", "mode", "buttons", "stick_left", "stick_right",
))


class UnmanagedLayerFile(ValueError):
    """A JSON file that does not claim to be managed by this application."""


def parse_bool(value, default=False):
    """Parse JSON-style booleans without treating "false" as truthy."""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in ("true", "1", "yes", "on"):
            return True
        if normalized in ("false", "0", "no", "off"):
            return False
    return bool(default)


def _looks_like_layer(value):
    return isinstance(value, dict) and bool(_LAYER_DATA_KEYS.intersection(value))


def validate_layer_name(name):
    """Return a safe display/file name or raise a user-facing ValueError."""
    normalized = str(name or "").strip()
    if not normalized:
        raise ValueError("映射層名稱不可空白。")
    if len(normalized) > 64:
        raise ValueError("映射層名稱不可超過 64 個字元。")
    if normalized in (".", "..") or normalized.endswith((".", " ")):
        raise ValueError("映射層名稱不可使用句點或空白結尾。")
    if _INVALID_LAYER_NAME.search(normalized):
        raise ValueError('映射層名稱不可包含 < > : " / \\ | ? *。')
    stem = normalized.split(".", 1)[0].upper()
    if stem in _WINDOWS_RESERVED_NAMES:
        raise ValueError("這個名稱是 Windows 保留名稱，請改用其他名稱。")
    return normalized


def _clean_mapping(mapping, fallback):
    source = mapping if isinstance(mapping, dict) else {}
    return {
        str(name).strip().lower(): str(
            source.get(name, target)
        ).strip().upper()
        for name, target in fallback.items()
    }


def _clean_stick_settings(settings, fallback):
    source = settings if isinstance(settings, dict) else {}
    result = dict(fallback)
    result.update({
        str(key).strip().lower(): str(value).strip()
        for key, value in source.items()
        if str(key).strip().lower() in fallback
    })
    result["mode"] = result.get("mode", "4WAY").upper()
    for direction in STICK_DIRECTIONS:
        result[direction] = result.get(direction, "NONE").upper()
    return result


def validate_layer_mapping_targets(layer):
    """Reject layer values that the runtime would otherwise treat as no-op."""

    layer_name = str(layer.get("name", "")).strip() or "未命名映射層"
    for source_name, target in layer.get("buttons", {}).items():
        try:
            validate_button_target(target)
        except ValueError as exc:
            raise ValueError(
                f"映射層「{layer_name}」按鍵 {str(source_name).upper()} 無效：{exc}"
            ) from exc

    for side, side_label in (("left", "左搖桿"), ("right", "右搖桿")):
        settings = layer.get(f"stick_{side}", {})
        side_code = side.upper()
        try:
            validate_stick_direction_mode(settings.get("mode", "4WAY"), side_code)
            validate_stick_analog_direction(
                settings.get("analog_direction", "UP")
            )
        except ValueError as exc:
            raise ValueError(
                f"映射層「{layer_name}」{side_label}設定無效：{exc}"
            ) from exc
        for direction in STICK_DIRECTIONS:
            try:
                validate_direction_target(settings.get(direction, "NONE"))
            except ValueError as exc:
                raise ValueError(
                    f"映射層「{layer_name}」{side_label} {direction.upper()} 無效：{exc}"
                ) from exc
    return layer


def normalize_layers(raw_layers, base_buttons, base_sticks, valid_buttons):
    """Return safe, complete layer dictionaries suitable for UI/runtime use."""
    valid = {str(name).strip().upper() for name in valid_buttons}
    if not isinstance(raw_layers, list):
        return []
    result = []
    used_ids = set()
    used_names = set()
    for index, raw in enumerate(raw_layers):
        if not isinstance(raw, dict):
            continue
        layer_id = str(raw.get("id", "")).strip()
        if not layer_id or layer_id in used_ids:
            layer_id = uuid.uuid4().hex
        used_ids.add(layer_id)

        requested_name = str(raw.get("name", "")).strip() or f"Layer {index + 1}"
        try:
            requested_name = validate_layer_name(requested_name)
        except ValueError:
            requested_name = f"Layer {index + 1}"
        name = requested_name
        suffix = 2
        while name.casefold() in used_names:
            name = f"{requested_name} ({suffix})"
            suffix += 1
        used_names.add(name.casefold())

        buttons = []
        for item in raw.get("activation_buttons", []):
            button_name = str(item).strip().upper()
            if button_name in valid and button_name not in buttons:
                buttons.append(button_name)
        mode = str(raw.get("mode", "HOLD")).strip().upper()
        if mode not in LAYER_MODES:
            mode = "HOLD"

        raw_button_mapping = raw.get("buttons", {})
        if isinstance(raw_button_mapping, dict):
            allowed_sources = tuple(base_buttons)
            for source_name, target in raw_button_mapping.items():
                try:
                    validate_button_source(source_name, allowed_sources)
                    validate_button_target(target)
                except ValueError as exc:
                    raise ValueError(
                        f"映射層「{name}」按鍵 {str(source_name).upper()} 無效：{exc}"
                    ) from exc

        for side in STICK_SIDES:
            raw_stick_settings = raw.get(f"stick_{side}", {})
            if not isinstance(raw_stick_settings, dict):
                continue
            for setting_name, setting_value in raw_stick_settings.items():
                try:
                    normalized_setting = validate_stick_setting_key(setting_name)
                    if normalized_setting.lower() in STICK_DIRECTIONS:
                        validate_direction_target(setting_value)
                    elif normalized_setting == "MODE":
                        validate_stick_direction_mode(setting_value, side.upper())
                    elif normalized_setting == "ANALOG_DIRECTION":
                        validate_stick_analog_direction(setting_value)
                except ValueError as exc:
                    raise ValueError(
                        f"映射層「{name}」{side.upper()} 搖桿欄位無效：{exc}"
                    ) from exc

        normalized_layer = {
            "id": layer_id,
            "name": name,
            # Kept as a legacy read fallback. A profile's INI overrides it,
            # and new JSON files no longer serialize this profile-owned flag.
            "enabled": parse_bool(raw.get("enabled", True), default=True),
            "activation_buttons": buttons,
            "mode": mode,
            "buttons": _clean_mapping(raw.get("buttons"), base_buttons),
            "stick_left": _clean_stick_settings(
                raw.get("stick_left"), base_sticks["left"]
            ),
            "stick_right": _clean_stick_settings(
                raw.get("stick_right"), base_sticks["right"]
            ),
        }
        validate_layer_mapping_targets(normalized_layer)
        result.append(normalized_layer)
    return result


def _atomic_write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise


def _layer_payload(layer):
    validate_layer_mapping_targets(layer)
    return {
        "format": LAYER_FILE_FORMAT,
        "version": LAYER_FILE_VERSION,
        "layer": {
            key: value
            for key, value in layer.items()
            if key not in ("name", "enabled")
            and not str(key).startswith("_")
        },
    }


def _unique_filename(name, used):
    base = validate_layer_name(name)
    candidate = base
    suffix = 2
    while candidate.casefold() in used:
        candidate = f"{base} ({suffix})"
        suffix += 1
    used.add(candidate.casefold())
    return candidate


def save_layers(config, layers, layer_dir=LAYER_DIR, deletable_paths=None):
    """Atomically store every layer as one JSON file and remove legacy INI data."""
    directory = Path(layer_dir)
    directory.mkdir(parents=True, exist_ok=True)

    invalid_files = []
    duplicate_id_files = []
    managed_existing = set()
    unmanaged_names = set()
    existing_ids = {}
    for existing_path in directory.glob("*.json"):
        try:
            records = _raw_layers_from_json(existing_path, strict=True)
        except (UnmanagedLayerFile, OSError, TypeError, json.JSONDecodeError):
            # Ordinary or damaged JSON is not owned by this application.
            # Leave it untouched and let the user manage it explicitly.
            unmanaged_names.add(existing_path.stem.casefold())
            continue
        except ValueError:
            # A file that explicitly claims our format must be fixed before
            # it can safely be rewritten.
            invalid_files.append(existing_path.name)
        else:
            managed_existing.add(existing_path.resolve())
            for _order, raw_layer in records:
                layer_id = str(raw_layer.get("id", "")).strip()
                previous = existing_ids.get(layer_id)
                if previous is not None:
                    duplicate_id_files.extend((previous, existing_path.name))
                else:
                    existing_ids[layer_id] = existing_path.name
    if invalid_files:
        names = "\n".join(f"- {name}" for name in sorted(invalid_files))
        raise ValueError(
            "映射層資料夾包含無法解析的 JSON；為避免資料遺失，"
            "本次儲存已取消：\n" + names
        )
    if duplicate_id_files:
        names = "\n".join(
            f"- {name}" for name in sorted(set(duplicate_id_files))
        )
        raise ValueError(
            "映射層檔案包含重複 ID；請刪除重複檔案或重新匯入，"
            "本次儲存已取消：\n" + names
        )

    conflicting_names = []
    for index, layer in enumerate(layers):
        name = validate_layer_name(layer.get("name", f"Layer {index + 1}"))
        if name.casefold() in unmanaged_names:
            conflicting_names.append(name)
    if conflicting_names:
        names = "\n".join(
            f"- {name}.json" for name in sorted(set(conflicting_names))
        )
        raise ValueError(
            "映射層資料夾已存在同名的非 S2P JSON；請改用其他名稱：\n"
            + names
        )

    used_names = set()
    desired_paths = set()
    for order, layer in enumerate(layers):
        filename = _unique_filename(layer.get("name", f"Layer {order + 1}"), used_names)
        path = directory / f"{filename}.json"
        _atomic_write_json(path, _layer_payload(layer))
        desired_paths.add(path.resolve())

    deletable = (
        managed_existing
        if deletable_paths is None
        else {Path(path).resolve() for path in deletable_paths}
    )
    for path in directory.glob("*.json"):
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        if resolved not in desired_paths and resolved in deletable:
            path.unlink()

    if config is not None:
        config.remove_section(LAYER_SECTION)
    return sorted(desired_paths, key=lambda path: str(path).casefold())


def _read_id_list(config, option):
    if config is None or not config.has_section(LAYER_STATE_SECTION):
        return None
    try:
        value = json.loads(
            config.get(LAYER_STATE_SECTION, option, fallback="[]")
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        layer_id = str(item).strip()
        if layer_id and layer_id not in result:
            result.append(layer_id)
    return result


def store_layer_state(config, layers):
    """Store only profile-owned layer order and enabled state in an INI."""
    if config is None:
        return
    if not config.has_section(LAYER_STATE_SECTION):
        config.add_section(LAYER_STATE_SECTION)
    order = [str(layer.get("id", "")).strip() for layer in layers]
    order = [layer_id for layer_id in order if layer_id]
    enabled = [
        str(layer.get("id", "")).strip()
        for layer in layers
        if layer.get("enabled", False)
    ]
    enabled = [layer_id for layer_id in enabled if layer_id]
    config.set(LAYER_STATE_SECTION, LAYER_ORDER_OPTION, json.dumps(order))
    config.set(LAYER_STATE_SECTION, LAYER_ENABLED_OPTION, json.dumps(enabled))


def apply_layer_state(config, layers):
    """Apply one profile's order/enabled list and report stale state."""
    order = _read_id_list(config, LAYER_ORDER_OPTION)
    enabled = _read_id_list(config, LAYER_ENABLED_OPTION)
    if order is None or enabled is None:
        return layers, False

    by_id = {
        str(layer.get("id", "")).strip(): layer
        for layer in layers
        if str(layer.get("id", "")).strip()
    }
    known_order = [layer_id for layer_id in order if layer_id in by_id]
    remaining = [
        layer_id for layer_id in by_id
        if layer_id not in known_order
    ]
    ordered_ids = known_order + remaining
    enabled_set = {layer_id for layer_id in enabled if layer_id in by_id}
    result = []
    for layer_id in ordered_ids:
        layer = by_id[layer_id]
        layer["enabled"] = layer_id in enabled_set
        result.append(layer)

    canonical_order = list(by_id)
    stale = (
        order != ordered_ids
        or any(layer_id not in by_id for layer_id in enabled)
        or canonical_order != ordered_ids and not order
    )
    return result, stale


def _persist_reconciled_state(config, layers, config_path):
    """Remove missing IDs from config and its active editable profile."""
    with config_file_lock():
        latest = load_config(config_path)
        store_layer_state(latest, layers)
        active = latest.get("gui", "active_profile", fallback="").strip()
        active_path = None
        active_snapshot = None
        if active and not is_protected_profile(active):
            active_path = profile_path(active, PROFILE_DIR)
            active_snapshot = (
                active_path.read_bytes() if active_path.exists() else None
            )
            active_profile = read_profile(active, PROFILE_DIR)
            store_layer_state(active_profile, layers)
            atomic_write_config(active_profile, active_path)
        try:
            atomic_write_config(latest, config_path)
        except OSError:
            if active_path is not None:
                if active_snapshot is None:
                    if active_path.exists():
                        active_path.unlink()
                else:
                    atomic_write_bytes(active_snapshot, active_path)
            raise
    store_layer_state(config, layers)


def _raw_layers_from_json(path, strict=False):
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)

    claims_s2p_format = (
        isinstance(payload, dict)
        and payload.get("format") == LAYER_FILE_FORMAT
    )
    if strict and not claims_s2p_format:
        raise UnmanagedLayerFile(Path(path).name)
    if strict or claims_s2p_format:
        if payload.get("version") != LAYER_FILE_VERSION:
            raise ValueError("不支援的映射層檔案版本。")
        raw_layer = payload.get("layer")
        if not (
            isinstance(raw_layer, dict)
            and _STRICT_LAYER_KEYS.issubset(raw_layer)
        ):
            raise ValueError("映射層檔案缺少必要欄位。")
        if not (
            isinstance(raw_layer.get("id"), str)
            and raw_layer["id"].strip()
            and isinstance(raw_layer.get("activation_buttons"), list)
            and isinstance(raw_layer.get("buttons"), dict)
            and isinstance(raw_layer.get("stick_left"), dict)
            and isinstance(raw_layer.get("stick_right"), dict)
        ):
            raise ValueError("映射層必要欄位的資料類型不正確。")

    default_name = Path(path).stem
    order = 1_000_000
    if isinstance(payload, dict) and isinstance(payload.get("layer"), dict):
        if not _looks_like_layer(payload["layer"]):
            raise ValueError("選擇的檔案不包含可匯入的映射層設定。")
        raw = dict(payload["layer"])
        raw["name"] = default_name
        try:
            order = int(payload.get("order", order))
        except (TypeError, ValueError):
            pass
        return [(order, raw)]

    if isinstance(payload, dict) and isinstance(payload.get("layers"), list):
        payload = payload["layers"]

    if isinstance(payload, dict):
        if not _looks_like_layer(payload):
            raise ValueError("選擇的檔案不包含可匯入的映射層設定。")
        raw = dict(payload)
        raw.setdefault("name", default_name)
        return [(order, raw)]

    if isinstance(payload, list):
        records = []
        for index, item in enumerate(payload):
            if not _looks_like_layer(item):
                continue
            raw = dict(item)
            raw.setdefault(
                "name",
                default_name if len(payload) == 1 else f"{default_name} {index + 1}",
            )
            records.append((order + index, raw))
        return records

    raise ValueError("選擇的檔案不包含可匯入的映射層設定。")


def _raw_layers_from_ini(path):
    parser = configparser.ConfigParser()
    if not parser.read(path, encoding="utf-8-sig"):
        raise ValueError("無法讀取映射層設定檔。")
    if not parser.has_option(LAYER_SECTION, LAYER_OPTION):
        raise ValueError("選擇的檔案不包含可匯入的映射層設定。")
    try:
        raw_layers = json.loads(parser.get(LAYER_SECTION, LAYER_OPTION))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("映射層設定格式不正確。") from exc
    if not isinstance(raw_layers, list):
        raise ValueError("選擇的檔案不包含可匯入的映射層設定。")
    records = [
        (1_000_000 + index, item)
        for index, item in enumerate(raw_layers)
        if _looks_like_layer(item)
    ]
    if not records:
        raise ValueError("選擇的檔案不包含可匯入的映射層設定。")
    return records


def import_layers_file(path, base_buttons, base_sticks, valid_buttons):
    """Read one exported JSON layer or legacy INI and return normalized layers."""
    source = Path(path)
    if source.suffix.casefold() == ".ini":
        records = _raw_layers_from_ini(source)
    else:
        records = _raw_layers_from_json(source)
    raw_layers = [raw for _order, raw in sorted(records, key=lambda item: item[0])]
    layers = normalize_layers(raw_layers, base_buttons, base_sticks, valid_buttons)
    if not layers:
        raise ValueError("選擇的檔案不包含可匯入的映射層設定。")
    return layers


def _read_layer_directory(layer_dir):
    """Return layers, managed paths, and completeness from one scan."""
    records = []
    managed_paths = set()
    scan_complete = True
    used_ids = set()
    for path in sorted(
        Path(layer_dir).glob("*.json"),
        key=lambda item: item.name.casefold(),
    ):
        try:
            file_records = _raw_layers_from_json(path, strict=True)
        except UnmanagedLayerFile:
            # A readable non-S2P JSON is unrelated and does not make the scan
            # ambiguous.
            continue
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            # A transient read/parse failure must not look like a deletion.
            scan_complete = False
            continue
        managed_paths.add(path.resolve())
        for record in file_records:
            layer_id = str(record[1].get("id", "")).strip()
            if not layer_id or layer_id in used_ids:
                # Keep the first file deterministically instead of generating
                # a different in-memory UUID on every startup.
                scan_complete = False
                continue
            used_ids.add(layer_id)
            records.append(record)
    layers = [
        raw for _order, raw in sorted(
            records,
            key=lambda item: (
                item[0],
                str(item[1].get("name", "")).casefold(),
            ),
        )
    ]
    return layers, managed_paths, scan_complete


def load_layers(
    config, base_buttons, base_sticks, valid_buttons, layer_dir=LAYER_DIR,
    config_path=None, return_metadata=False,
):
    """Load layers from ``src/layers`` and migrate legacy config.ini data once."""
    directory = Path(layer_dir)
    directory.mkdir(parents=True, exist_ok=True)
    folder_layers, managed_files, scan_complete = _read_layer_directory(
        directory
    )

    if managed_files or not scan_complete:
        normalized = normalize_layers(
            folder_layers, base_buttons, base_sticks, valid_buttons
        )
        normalized, state_changed = apply_layer_state(config, normalized)
        if state_changed and config_path is not None and scan_complete:
            try:
                _persist_reconciled_state(config, normalized, config_path)
            except (OSError, ValueError, configparser.Error):
                pass
        if return_metadata:
            return normalized, managed_files, scan_complete
        return normalized

    raw_layers = []
    had_legacy_section = bool(config is not None and config.has_section(LAYER_SECTION))
    if config is not None:
        raw_text = config.get(LAYER_SECTION, LAYER_OPTION, fallback="[]")
        try:
            raw_layers = json.loads(raw_text)
        except (TypeError, ValueError, json.JSONDecodeError):
            raw_layers = []

    normalized = normalize_layers(raw_layers, base_buttons, base_sticks, valid_buttons)
    if had_legacy_section:
        managed_files = set(save_layers(config, normalized, directory))
        try:
            if config_path is not None:
                atomic_write_config(config, config_path)
        except (OSError, ValueError, configparser.Error):
            # The files are already migrated. Keeping the old INI section is
            # harmless because folder files take precedence on future loads.
            pass
    normalized, state_changed = apply_layer_state(config, normalized)
    if state_changed and config_path is not None and scan_complete:
        try:
            _persist_reconciled_state(config, normalized, config_path)
        except (OSError, ValueError, configparser.Error):
            pass
    if return_metadata:
        return normalized, managed_files, scan_complete
    return normalized


class LayerSelector:
    """Select one effective layer from Hold and Toggle activation chords."""

    def __init__(self, layers, button_masks):
        self._layers = []
        for priority, layer in enumerate(layers):
            mask = 0
            for name in layer.get("activation_buttons", ()):
                mask |= int(button_masks.get(name, 0) or 0)
            self._layers.append({
                "id": layer["id"],
                "enabled": bool(layer.get("enabled", True)),
                "mode": layer.get("mode", "HOLD"),
                "mask": mask,
                "specificity": len(layer.get("activation_buttons", ())),
                "priority": priority,
            })
        self.reset()

    def reset(self):
        self._previous_buttons = 0
        self._toggle_id = None
        self.effective_id = None

    @staticmethod
    def _active(buttons, layer):
        mask = layer["mask"]
        return bool(mask) and (buttons & mask) == mask

    @staticmethod
    def _best(candidates):
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda layer: (-layer["specificity"], layer["priority"]),
        )

    def update(self, buttons):
        buttons = int(buttons)
        toggled = []
        held = []
        for layer in self._layers:
            if not layer["enabled"] or not layer["mask"]:
                continue
            active = self._active(buttons, layer)
            was_active = self._active(self._previous_buttons, layer)
            if layer["mode"] == "TOGGLE":
                if active and not was_active:
                    toggled.append(layer)
            elif active:
                held.append(layer)

        chosen_toggle = self._best(toggled)
        if chosen_toggle is not None:
            chosen_id = chosen_toggle["id"]
            self._toggle_id = None if self._toggle_id == chosen_id else chosen_id

        best_hold = self._best(held)
        self.effective_id = (
            best_hold["id"] if best_hold is not None else self._toggle_id
        )
        self._previous_buttons = buttons
        return self.effective_id
