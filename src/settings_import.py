"""Transactional migration of user settings between S2P installations.

This module intentionally owns no Tk state.  The GUI only supplies a selected
installation root and an explicit conflict choice, which keeps validation and
rollback straightforward to test.
"""

from __future__ import annotations

import configparser
import json
from dataclasses import dataclass
from pathlib import Path

from config_utils import (
    CONFIG_PATH,
    PROFILE_DIR,
    apply_profile,
    atomic_write_bytes,
    atomic_write_config,
    config_file_lock,
    is_protected_profile,
    list_profiles,
    load_accelerometer_calibration,
    load_config,
    load_gyro_bias,
    load_magnetometer_bias,
    load_magnetometer_matrix,
    load_magnetometer_scale,
    load_stick_calibration,
    profile_path,
    read_profile,
    validate_profile_name,
)
from mapping_layers import (
    LAYER_DIR,
    LAYER_OPTION,
    LAYER_SECTION,
    apply_layer_state,
    load_managed_layer_directory,
    normalize_layers,
    save_layers,
    store_layer_state,
)
from switch2_input import SWITCH_BUTTONS


class SettingsImportError(ValueError):
    """A source installation is unsafe or incompatible to import."""


@dataclass
class ImportPlan:
    source_root: Path
    config: configparser.ConfigParser
    profiles: dict[str, configparser.ConfigParser]
    layers: list[dict]
    profile_conflicts: tuple[str, ...]
    layer_conflicts: tuple[str, ...]
    ignored_system_default: bool
    calibration_count: int
    source_active_profile: str
    current_active_profile: str


@dataclass
class ImportResult:
    source_root: Path
    active_profile: str
    profiles_imported: tuple[str, ...]
    profiles_skipped: tuple[str, ...]
    layers_imported: tuple[str, ...]
    layers_skipped: tuple[str, ...]
    calibration_count: int
    ignored_system_default: bool


_STICK_KEYS = (
    "left_center", "left_max", "left_min",
    "right_center", "right_max", "right_min",
)


def _read_ini(path: Path) -> configparser.ConfigParser:
    parser = configparser.ConfigParser()
    try:
        with path.open("r", encoding="utf-8-sig") as handle:
            parser.read_file(handle)
    except (OSError, UnicodeError, configparser.Error) as exc:
        raise SettingsImportError(f"無法讀取設定檔：{path.name}") from exc
    return parser


def _base_layer_defaults(config):
    return (
        dict(config.items("buttons")),
        {
            "left": dict(config.items("stick_direction_left")),
            "right": dict(config.items("stick_direction_right")),
        },
    )


def _source_paths(source_root) -> tuple[Path, Path, Path, Path]:
    root = Path(source_root).expanduser()
    config_path = root / "src" / "config.ini"
    if not root.is_dir() or not config_path.is_file():
        raise SettingsImportError(
            "找不到有效的 S2P-XInput-Lite 設定資料。請選擇 "
            "S2P-XInput-Lite 的程式根目錄。"
        )
    return root, config_path, root / "src" / "profiles", root / "src" / "layers"


def _copy_known_global_settings(target, source):
    """Merge source values only into current-version known global keys."""
    for section in ("serial", "gui"):
        if not source.has_section(section) or not target.has_section(section):
            continue
        for key, value in source.items(section):
            if key != "active_profile" and target.has_option(section, key):
                target.set(section, key, value)

    # These are the actual persisted controller identity/calibration sections.
    for section in source.sections():
        prefix, dot, _identity = section.partition(".")
        if section not in ("sticks", "gyro", "wired_aliases") and not (
            dot and prefix in ("sticks", "gyro")
        ):
            continue
        if not target.has_section(section):
            target.add_section(section)
        for key, value in source.items(section):
            target.set(section, key, value)


def _validate_source_calibrations(source):
    controller_ids = set()
    for section in source.sections():
        prefix, dot, identity = section.partition(".")
        if prefix not in ("sticks", "gyro"):
            continue
        if prefix == "sticks":
            if not all(source.has_option(section, key) for key in _STICK_KEYS):
                raise SettingsImportError(f"手把校正資料不完整：[{section}]")
            load_stick_calibration(source, identity if dot else None)
        else:
            if source.has_option(section, "bias"):
                load_gyro_bias(source, identity if dot else None)
            if source.has_option(section, "mag_bias"):
                load_magnetometer_bias(source, identity if dot else None)
                load_magnetometer_scale(source, identity if dot else None)
                load_magnetometer_matrix(source, identity if dot else None)
            if source.has_option(section, "accel_bias") or source.has_option(section, "accel_matrix"):
                bias, matrix = load_accelerometer_calibration(source, identity if dot else None)
                if bias is None or matrix is None:
                    raise SettingsImportError(f"加速度計校正資料不完整：[{section}]")
        if dot and identity:
            controller_ids.add(identity)
    return len(controller_ids)


def _read_source_profiles(directory: Path):
    profiles = {}
    ignored_system_default = False
    if not directory.is_dir():
        return profiles, ignored_system_default
    for path in sorted(directory.glob("*.ini"), key=lambda item: item.name.casefold()):
        try:
            name = validate_profile_name(path.stem)
        except ValueError as exc:
            raise SettingsImportError(f"方案名稱無效：{path.name}") from exc
        if is_protected_profile(name):
            ignored_system_default = True
            continue
        try:
            profiles[name] = read_profile(name, directory)
        except (OSError, ValueError, configparser.Error) as exc:
            raise SettingsImportError(f"方案檔無法匯入：{path.name}") from exc
    return profiles, ignored_system_default


def _read_source_layers(source_config, base_buttons, base_sticks, layer_dir):
    """Read managed files or the previous in-config S2P layer format."""
    layers, _managed_paths = load_managed_layer_directory(
        base_buttons, base_sticks, SWITCH_BUTTONS, layer_dir
    )
    if layers or not source_config.has_option(LAYER_SECTION, LAYER_OPTION):
        return layers
    try:
        raw_layers = json.loads(source_config.get(LAYER_SECTION, LAYER_OPTION))
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise SettingsImportError("舊版映射層資料無法驗證。") from exc
    if not isinstance(raw_layers, list):
        raise SettingsImportError("舊版映射層資料無法驗證。")
    return normalize_layers(raw_layers, base_buttons, base_sticks, SWITCH_BUTTONS)


def scan_settings_import(source_root, *, config_path=CONFIG_PATH, profile_dir=PROFILE_DIR,
                         layer_dir=LAYER_DIR):
    """Validate an installation and return a no-write migration preview."""
    root, source_config_path, source_profiles_dir, source_layers_dir = _source_paths(source_root)
    source_config = _read_ini(source_config_path)
    calibration_count = _validate_source_calibrations(source_config)

    # This is load_config's normal schema/migration path, explicitly in
    # no-persist mode so a rejected source never changes the destination.
    current = load_config(config_path, persist=False)
    _copy_known_global_settings(current, source_config)

    profiles, ignored_system_default = _read_source_profiles(source_profiles_dir)
    destination_profiles = list_profiles(profile_dir)
    destination_names = {name.casefold(): name for name in destination_profiles}
    profile_conflicts = tuple(
        name for name in profiles if name.casefold() in destination_names
    )

    base_buttons, base_sticks = _base_layer_defaults(current)
    try:
        source_layers = _read_source_layers(
            source_config, base_buttons, base_sticks, source_layers_dir
        )
        destination_layers, _destination_paths = load_managed_layer_directory(
            base_buttons, base_sticks, SWITCH_BUTTONS, layer_dir
        )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise SettingsImportError("映射層資料無法驗證。") from exc
    used_names = {str(layer["name"]).casefold() for layer in destination_layers}
    used_ids = {str(layer["id"]) for layer in destination_layers}
    layer_conflicts = tuple(
        str(layer["name"]) for layer in source_layers
        if str(layer["name"]).casefold() in used_names or str(layer["id"]) in used_ids
    )
    source_active = source_config.get("gui", "active_profile", fallback="").strip()
    current_active = current.get("gui", "active_profile", fallback="").strip()
    return ImportPlan(
        root, current, profiles, source_layers, profile_conflicts, layer_conflicts,
        ignored_system_default, calibration_count, source_active, current_active,
    )


def _snapshot(paths):
    return {Path(path): (Path(path).read_bytes() if Path(path).exists() else None) for path in paths}


def _restore(snapshot):
    for path, data in snapshot.items():
        if data is None:
            if path.exists():
                path.unlink()
        else:
            atomic_write_bytes(data, path)


def _fallback_active(current_active, profile_dir):
    names = list_profiles(profile_dir)
    by_name = {name.casefold(): name for name in names}
    return by_name.get(current_active.casefold(), by_name.get("general", names[0] if names else ""))


def apply_settings_import(plan: ImportPlan, *, profile_conflict="skip", layer_conflict="skip",
                          config_path=CONFIG_PATH, profile_dir=PROFILE_DIR, layer_dir=LAYER_DIR):
    """Commit a reviewed plan with atomic file writes and full rollback."""
    if profile_conflict not in ("overwrite", "skip") or layer_conflict not in ("overwrite", "skip"):
        raise ValueError("conflict policy must be 'overwrite' or 'skip'")

    destination_names = {name.casefold(): name for name in list_profiles(profile_dir)}
    profiles_to_write = {}
    profiles_skipped = []
    for name, profile in plan.profiles.items():
        if name.casefold() in destination_names and profile_conflict == "skip":
            profiles_skipped.append(name)
        else:
            profiles_to_write[name] = profile

    base_buttons, base_sticks = _base_layer_defaults(plan.config)
    destination_layers, managed_paths = load_managed_layer_directory(
        base_buttons, base_sticks, SWITCH_BUTTONS, layer_dir
    )
    desired_layers = list(destination_layers)
    layers_imported, layers_skipped = [], []
    for source_layer in plan.layers:
        name = str(source_layer["name"])
        conflicts = [
            layer for layer in desired_layers
            if str(layer["name"]).casefold() == name.casefold()
            or str(layer["id"]) == str(source_layer["id"])
        ]
        if conflicts and layer_conflict == "skip":
            layers_skipped.append(name)
            continue
        if conflicts:
            desired_layers = [layer for layer in desired_layers if layer not in conflicts]
        desired_layers.append(source_layer)
        layers_imported.append(name)

    accepted_profiles = {name.casefold(): name for name in profiles_to_write}
    active = _fallback_active(plan.current_active_profile, profile_dir)
    source_active = plan.source_active_profile
    if source_active.casefold() in accepted_profiles:
        active = accepted_profiles[source_active.casefold()]

    candidate = plan.config
    if active.casefold() in accepted_profiles:
        apply_profile(candidate, profiles_to_write[active])
    if not candidate.has_section("gui"):
        candidate.add_section("gui")
    candidate.set("gui", "active_profile", active)
    # Current config owns the active runtime state; profile files retain their
    # individual imported state.  This ensures new layer IDs are consistent.
    state_layers, _stale = apply_layer_state(candidate, desired_layers)
    store_layer_state(candidate, state_layers)

    paths = {Path(config_path), *[profile_path(name, profile_dir) for name in profiles_to_write]}
    paths.update(Path(path) for path in managed_paths)
    paths.update(Path(layer_dir) / f"{layer['name']}.json" for layer in desired_layers)
    snapshot = _snapshot(paths)
    try:
        with config_file_lock():
            for name, profile in profiles_to_write.items():
                atomic_write_config(profile, profile_path(name, profile_dir))
            save_layers(candidate, desired_layers, layer_dir, deletable_paths=managed_paths)
            atomic_write_config(candidate, config_path)
    except Exception:
        _restore(snapshot)
        raise
    return ImportResult(
        plan.source_root, active, tuple(profiles_to_write), tuple(profiles_skipped),
        tuple(layers_imported), tuple(layers_skipped), plan.calibration_count,
        plan.ignored_system_default,
    )
