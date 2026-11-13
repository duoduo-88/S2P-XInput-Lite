"""Compile and transfer the ESP32 standalone profile.

The firmware stores this document as an opaque, versioned blob.  Keeping the
compatibility analysis on the desktop side lets older standalone firmware
reject an unknown schema without having to embed a full JSON parser.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import time
import zlib
from typing import Any, Iterable, Mapping

from esp32_detection import (
    firmware_identity_matches, stable_usb_serial, verified_firmware_status,
)
from idle_disconnect import normalize_idle_disconnect_minutes
from direction_deadzones import CUSTOM_DEADZONE_KEY, DIRECTION_SECTORS, parse_custom_zones, zone_edges

import serial
from serial.tools import list_ports

from mapping_targets import XINPUT_BUTTON_TARGETS
from settings_schema import normalize_section_values
from switch2_input import SWITCH_BUTTONS


STANDALONE_PROFILE_SCHEMA = 1
STANDALONE_PROFILE_MAX_BYTES = 8192
STANDALONE_CHUNK_BYTES = 96
DEADZONE_EDGES_KEY = "direction_deadzone_edges"
SUPPORTED_BUTTON_TARGETS = frozenset(XINPUT_BUTTON_TARGETS) | {"NONE"}
STANDALONE_LAYER_BUTTONS = frozenset(SWITCH_BUTTONS) - {"SR_R", "SL_R", "SR_L", "SL_L"}
STANDALONE_STICK_MODES = frozenset(DIRECTION_SECTORS) | {"XINPUT_LT_LINEAR", "XINPUT_RT_LINEAR"}
STICK_DIRECTION_KEYS = (
    "up",
    "up_right",
    "right",
    "down_right",
    "down",
    "down_left",
    "left",
    "up_left",
)


@dataclass(frozen=True)
class CompatibilityIssue:
    severity: str
    feature: str
    detail: str


@dataclass(frozen=True)
class CompiledStandaloneProfile:
    payload: bytes
    crc32: int
    document: Mapping[str, Any]
    issues: tuple[CompatibilityIssue, ...]

    @property
    def blocking_issues(self) -> tuple[CompatibilityIssue, ...]:
        return tuple(issue for issue in self.issues if issue.severity == "blocking")

    @property
    def uses_custom_deadzones(self) -> bool:
        return any(
            DEADZONE_EDGES_KEY in self.document[f"stick_direction_{side}"]
            or any(
                DEADZONE_EDGES_KEY in layer.get(f"stick_{side}", {})
                for layer in self.document["mapping_layers"]
            )
            for side in ("left", "right")
        )


class StandaloneTransferError(RuntimeError):
    """Raised when the ESP32 rejects or fails to acknowledge a profile write."""


class _StandaloneResponseTimeout(StandaloneTransferError):
    """The firmware did not return the expected response before the deadline."""


def _issue(
    result: list[CompatibilityIssue],
    severity: str,
    feature: str,
    detail: str,
) -> None:
    result.append(CompatibilityIssue(severity, feature, detail))


def analyze_compatibility(
    settings: Mapping[str, Any],
    mapping_layers: Iterable[Mapping[str, Any]] = (),
) -> tuple[CompatibilityIssue, ...]:
    """Report settings that standalone schema 1 cannot reproduce."""
    issues: list[CompatibilityIssue] = []
    sections = settings.get("sections", {})

    audio_mode = str(
        sections.get("audio_haptics", {}).get("mode", "GAME")
    ).upper()
    if audio_mode != "GAME":
        _issue(
            issues,
            "ignored",
            "音訊震動",
            f"{audio_mode} 模式需要 Windows 音訊擷取，獨立模式將使用 GAME。",
        )

    gyro = sections.get("gyro_mapping", {})
    if str(gyro.get("activation_mode", "OFF")).upper() != "OFF":
        _issue(
            issues,
            "blocking",
            "陀螺儀映射",
            "Standalone schema 1 尚未執行陀螺儀映射。",
        )

    for source, raw_target in settings.get("buttons", {}).items():
        target = str(raw_target).strip().upper()
        if target not in SUPPORTED_BUTTON_TARGETS:
            _issue(
                issues,
                "blocking",
                "按鍵映射",
                f"{str(source).upper()} → {target or '<空白>'}",
            )

    for side, mappings in settings.get("direction_mappings", {}).items():
        for direction, raw_target in mappings.items():
            target = str(raw_target).strip().upper()
            if target != "NONE":
                _issue(
                    issues,
                    "blocking",
                    "搖桿方向映射",
                    f"{str(side).upper()} {str(direction).upper()} → {target}",
                )

    layers = list(mapping_layers)
    if layers:
        names = ", ".join(
            str(layer.get("name", "")).strip() or f"Layer {index + 1}"
            for index, layer in enumerate(layers)
        )
        _issue(
            issues,
            "blocking",
            "映射層",
            f"Standalone schema 1 尚未支援：{names}",
        )

    return tuple(issues)


def _enabled_layers_with_activation(mapping_layers):
    return (
        layer for layer in mapping_layers
        if layer.get("enabled", False) and layer.get("activation_buttons")
    )


def _standalone_recovery_chord(buttons: Iterable[str]) -> str | None:
    # The firmware tests only these four bits before suppressing a rescue chord.
    relevant = set(buttons) & {"HOME", "A", "X", "Y"}
    if relevant == {"HOME", "A"}:
        return "HOME+A"
    if relevant == {"HOME", "X"}:
        return "HOME+X"
    return None


def analyze_standalone_v2_compatibility(
    settings: Mapping[str, Any],
    mapping_layers: Iterable[Mapping[str, Any]] = (),
) -> tuple[CompatibilityIssue, ...]:
    """Report only features that genuinely require the Windows host."""
    issues: list[CompatibilityIssue] = []
    sections = settings.get("sections", {})
    audio_mode = str(
        sections.get("audio_haptics", {}).get("mode", "GAME")
    ).upper()
    if audio_mode != "GAME":
        _issue(
            issues, "ignored", "音訊震動",
            f"{audio_mode} 需要 Windows 音訊來源；獨立模式將使用 GAME。",
        )
    gyro = sections.get("gyro_mapping", {})
    gyro_enabled = str(gyro.get("activation_mode", "OFF")).upper() != "OFF"
    if gyro_enabled and str(gyro.get("target", "RIGHT_STICK")).upper() == "MOUSE":
        _issue(
            issues, "blocking", "陀螺儀滑鼠",
            "ESP32 不支援 Windows 滑鼠輸出；請改為左或右搖桿。",
        )
    if gyro_enabled and str(gyro.get("activation_match", "ANY")).upper() == "ALL":
        chord = _standalone_recovery_chord(settings.get("gyro_activation_buttons", ()))
        if chord:
            _issue(
                issues, "blocking", "陀螺儀啟用按鍵",
                f"{chord} 是 ESP32 的 USB 模式救援組合鍵。",
            )
    for source, raw_target in settings.get("buttons", {}).items():
        target = str(raw_target).strip().upper()
        if target not in SUPPORTED_BUTTON_TARGETS:
            _issue(
                issues, "blocking", "按鍵 Windows 輸出",
                f"{str(source).upper()} → {target or '<空白>'}",
            )
    direction_mappings = settings.get("direction_mappings", {})
    for side in ("left", "right"):
        stick = sections.get(f"stick_direction_{side}", {})
        mappings = direction_mappings.get(side, {})
        mode = str(stick.get("mode", "4WAY")).upper()
        if mode not in STANDALONE_STICK_MODES:
            _issue(
                issues, "blocking", "搖桿 Windows 輸出",
                f"{side.upper()} 使用 {mode}。",
            )
        for direction in DIRECTION_SECTORS.get(mode, ()):
            target = str(mappings.get(direction.lower(), "NONE")).strip().upper()
            if target not in SUPPORTED_BUTTON_TARGETS:
                _issue(
                    issues, "blocking", "搖桿方向映射",
                    f"{side.upper()} {direction} → {target}",
                )
    enabled_layers = list(_enabled_layers_with_activation(mapping_layers))
    if len(enabled_layers) > 8:
        _issue(
            issues, "blocking", "映射層數量",
            f"ESP32 獨立模式目前最多支援 8 個啟用層；目前為 {len(enabled_layers)} 個。",
        )
    for index, layer in enumerate(enabled_layers):
        name = str(layer.get("name", "")).strip() or f"Layer {index + 1}"
        activation_buttons = set(map(str, layer["activation_buttons"]))
        unsupported_buttons = activation_buttons - STANDALONE_LAYER_BUTTONS
        if unsupported_buttons:
            _issue(
                issues, "blocking", "映射層啟用按鍵",
                f"{name}：ESP32 不支援 {', '.join(sorted(unsupported_buttons))}。",
            )
        chord = _standalone_recovery_chord(activation_buttons)
        if chord:
            _issue(
                issues, "blocking", "映射層啟用按鍵",
                f"{name}：{chord} 是 ESP32 的 USB 模式救援組合鍵。",
            )
        for source, raw_target in layer.get("buttons", {}).items():
            target = str(raw_target).strip().upper()
            if target not in SUPPORTED_BUTTON_TARGETS:
                _issue(
                    issues, "blocking", "映射層 Windows 輸出",
                    f"{name}: {str(source).upper()} → {target}",
                )
        for side in ("left", "right"):
            stick = layer.get(f"stick_{side}", {})
            base_mode = sections.get(f"stick_direction_{side}", {}).get("mode", "4WAY")
            mode = str(stick.get("mode", base_mode)).upper()
            if mode not in STANDALONE_STICK_MODES:
                _issue(
                    issues, "blocking", "映射層 Windows 輸出",
                    f"{name}: {side.upper()} 使用 {mode}。",
                )
            for direction in DIRECTION_SECTORS.get(mode, ()):
                target = str(stick.get(direction.lower(), "NONE")).strip().upper()
                if target not in SUPPORTED_BUTTON_TARGETS:
                    _issue(
                        issues,
                        "blocking",
                        "映射層方向輸出",
                        (
                            f"{name}: {side.upper()} "
                            f"{direction} → {target}"
                        ),
                    )
    return tuple(issues)


def _compile_direction_deadzone(stick, mode):
    zones = parse_custom_zones(stick.pop(CUSTOM_DEADZONE_KEY, None)).get(mode)
    if zones:
        # Keep dragged edges compact in the 8 KiB profile. The ESP32 receives
        # stick coordinates with much coarser angular resolution.
        stick[DEADZONE_EDGES_KEY] = tuple(
            (round(start, 5), round(end, 5))
            for start, end in zone_edges(mode, zones)
        )


def _standalone_layer(layer, sections):
    snapshot = dict(layer)
    for side in ("left", "right"):
        key = f"stick_{side}"
        if CUSTOM_DEADZONE_KEY in layer.get(key, {}):
            stick = dict(layer[key])
            mode = str(stick.get("mode", sections[f"stick_direction_{side}"]["mode"]))
            _compile_direction_deadzone(stick, mode)
            snapshot[key] = stick
    return snapshot


def _compact_standalone_layer(layer, sections, base_buttons):
    """Omit layer fields the firmware ignores or inherits from the base."""
    snapshot = {key: layer[key] for key in ("activation_buttons", "mode") if key in layer}
    buttons = {
        key: target for key, target in layer.get("buttons", {}).items()
        if target != base_buttons.get(key)
    }
    if buttons:
        snapshot["buttons"] = buttons
    for side in ("left", "right"):
        key = f"stick_{side}"
        if key not in layer:
            continue
        source = layer[key]
        base = sections[f"stick_direction_{side}"]
        stick = {
            name: source[name] for name in STICK_DIRECTION_KEYS
            if name in source and source[name] != "NONE"
        }
        for name in ("mode", "analog_direction"):
            if name in source and source[name] != base[name]:
                stick[name] = source[name]
        for name in ("trigger_threshold", "release_threshold", "direction_deadzone"):
            if name in source:
                value = source[name]
                try:
                    if float(value) == base[name]:
                        continue
                except (TypeError, ValueError, OverflowError):
                    pass
                stick[name] = value
        if DEADZONE_EDGES_KEY in source:
            stick[DEADZONE_EDGES_KEY] = source[DEADZONE_EDGES_KEY]
        # Even an empty object clears the base direction targets in firmware.
        snapshot[key] = stick
    return snapshot


def _encode_profile_document(document):
    return json.dumps(
        document, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def compile_standalone_profile(
    profile_name: str,
    settings: Mapping[str, Any],
    mapping_layers: Iterable[Mapping[str, Any]] = (),
    idle_disconnect_minutes: int = 15,
) -> CompiledStandaloneProfile:
    """Create the deterministic schema-1 document accepted by the firmware."""
    mapping_layers = tuple(mapping_layers)
    issues = analyze_standalone_v2_compatibility(settings, mapping_layers)
    sections = settings["sections"]
    typed_sections = {
        section: normalize_section_values(
            section,
            sections.get(section, {}),
            strict=True,
        )
        for section in (
            "stick_curve_left",
            "stick_curve_right",
            "rumble",
            "stick_direction_left",
            "stick_direction_right",
            "gyro_mapping",
        )
    }
    for side in ("left", "right"):
        stick = typed_sections[f"stick_direction_{side}"]
        _compile_direction_deadzone(stick, stick["mode"])
    document = {
        "schema": STANDALONE_PROFILE_SCHEMA,
        "profile_name": str(profile_name).strip() or "Unnamed",
        "idle_disconnect_minutes": normalize_idle_disconnect_minutes(
            idle_disconnect_minutes
        ),
        "stick_curve_left": typed_sections["stick_curve_left"],
        "stick_curve_right": typed_sections["stick_curve_right"],
        "rumble": typed_sections["rumble"],
        "stick_direction_left": typed_sections["stick_direction_left"],
        "stick_direction_right": typed_sections["stick_direction_right"],
        "direction_mappings": {
            side: dict(sorted(values.items()))
            for side, values in sorted(
                settings.get("direction_mappings", {}).items()
            )
        },
        "gyro_mapping": typed_sections["gyro_mapping"],
        "gyro_activation_buttons": list(
            settings.get("gyro_activation_buttons", ())
        ),
        "gyro_tilt_recenter_button": settings.get(
            "gyro_tilt_recenter_button", "NONE"
        ),
        "gyro_stabilization_buttons": list(
            settings.get("gyro_stabilization_buttons", ())
        ),
        "calibration": settings.get("calibration"),
        "sensor_calibration": settings.get("sensor_calibration"),
        "buttons": dict(sorted(settings.get("buttons", {}).items())),
        "mapping_layers": [
            _standalone_layer(layer, typed_sections)
            for layer in _enabled_layers_with_activation(mapping_layers)
        ],
    }
    payload = _encode_profile_document(document)
    if len(payload) > STANDALONE_PROFILE_MAX_BYTES and document["mapping_layers"]:
        document["mapping_layers"] = [
            _compact_standalone_layer(layer, typed_sections, document["buttons"])
            for layer in document["mapping_layers"]
        ]
        payload = _encode_profile_document(document)
    if len(payload) > STANDALONE_PROFILE_MAX_BYTES:
        raise ValueError(
            f"ESP32 設定資料為 {len(payload)} bytes，"
            f"超過上限 {STANDALONE_PROFILE_MAX_BYTES} bytes。"
        )
    return CompiledStandaloneProfile(
        payload=payload,
        crc32=zlib.crc32(payload) & 0xFFFFFFFF,
        document=document,
        issues=issues,
    )


def _read_json_response(port, deadline: float) -> dict[str, Any]:
    while time.monotonic() < deadline:
        line = port.readline()
        if not line:
            continue
        text = line.decode("utf-8", errors="ignore").strip()
        if text.startswith("[") or "{" not in text or "}" not in text:
            continue
        try:
            return json.loads(text[text.find("{"):text.rfind("}") + 1])
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    raise _StandaloneResponseTimeout("ESP32 回應逾時。")


def _send_and_expect(port, command: str, expected_cmd: str, timeout=2.0):
    port.write((command + "\n").encode("ascii"))
    port.flush()
    deadline = time.monotonic() + timeout
    while True:
        response = _read_json_response(port, deadline)
        if response.get("cmd") != expected_cmd:
            if time.monotonic() >= deadline:
                raise _StandaloneResponseTimeout("ESP32 回應逾時。")
            continue
        if not response.get("ok", 0):
            error = response.get("error", "unknown")
            raise StandaloneTransferError(f"ESP32 拒絕設定：{error}")
        return response


def _read_verified_identity(port, expected_identity=None, *, timeout=2.0):
    port.write(b"status lite\n")
    port.flush()
    deadline = time.monotonic() + timeout
    while True:
        status = _read_json_response(port, deadline)
        if status.get("cmd") not in ("status", "status lite"):
            continue
        if not verified_firmware_status(status):
            raise StandaloneTransferError("ESP32 韌體身分不符。")
        if expected_identity is not None and not firmware_identity_matches(
                status, expected_identity):
            raise StandaloneTransferError("ESP32 裝置序號不符。")
        return status


def _reconnect_candidates(port_name: str, usb_serial: str) -> list[str]:
    detected = [
        info.device for info in list_ports.comports()
        if getattr(info, "serial_number", None) == usb_serial
    ]
    return list(dict.fromkeys((port_name, *detected)))


def _profile_matches(status, compiled: CompiledStandaloneProfile) -> bool:
    return (
        status.get("valid") == 1
        and status.get("length") == len(compiled.payload)
        and str(status.get("crc32", "")).lower() == f"{compiled.crc32:08x}"
    )


def _confirm_esp32_mode(
    port_name, baudrate, expected_identity, mode, *, compiled=None, timeout=12.0
):
    """Verify the active mode and any newly written profile after restart."""
    usb_serial = stable_usb_serial(expected_identity)
    if not usb_serial:
        raise StandaloneTransferError("ESP32 缺少可安全重連的唯一序號；請更新韌體。")

    deadline = time.monotonic() + timeout
    mode_confirmed = False
    profile_error = "ESP32 重新啟動後無法確認已套用寫入的設定。"
    while time.monotonic() < deadline:
        for candidate in _reconnect_candidates(port_name, usb_serial):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                with serial.Serial(
                    candidate, baudrate, timeout=0.10, write_timeout=0.50
                ) as retry_port:
                    retry_port.reset_input_buffer()
                    status = _read_verified_identity(
                        retry_port, expected_identity, timeout=min(2.0, remaining)
                    )
                    if status.get("active_mode") != mode:
                        continue
                    mode_confirmed = True
                    if compiled is not None:
                        status = _send_and_expect(
                            retry_port, "profile status", "profile_status",
                            timeout=min(1.0, max(0, deadline - time.monotonic())),
                        )
            except (StandaloneTransferError, OSError, ValueError):
                continue
            if compiled is not None and (
                not _profile_matches(status, compiled)
                or status.get("runtime_applied", 1) != 1
            ):
                raise StandaloneTransferError(profile_error)
            return
        time.sleep(min(0.15, max(0, deadline - time.monotonic())))
    if mode_confirmed:
        raise StandaloneTransferError(profile_error)
    raise StandaloneTransferError("ESP32 重新啟動後無法確認指定的 USB 模式已生效。")


def _restart_esp32(port, port_name, baudrate, expected_identity, mode, *, compiled=None):
    try:
        _send_and_expect(port, "restart", "restart")
    except (_StandaloneResponseTimeout, OSError):
        # USB can disconnect before the acknowledgement reaches the host.
        pass
    else:
        if not stable_usb_serial(expected_identity):
            return  # Older firmware only supports acknowledging the request.

    # The firmware sends its acknowledgement before restarting.
    port.close()
    _confirm_esp32_mode(port_name, baudrate, expected_identity, mode, compiled=compiled)


def _change_esp32_mode(port, port_name, baudrate, expected_identity, mode, *, compiled=None):
    command = f"mode {mode}"
    try:
        result = _send_and_expect(port, command, "mode")
    except _StandaloneResponseTimeout:
        # Saving the same mode again is safe if the first reply was lost.
        result = _send_and_expect(port, command, "mode")
    if result.get("restart_required", 0):
        _restart_esp32(
            port, port_name, baudrate, expected_identity, mode, compiled=compiled
        )
    return result


def _recover_committed_profile(
    port_name: str,
    baudrate: int,
    compiled: CompiledStandaloneProfile,
    *,
    timeout: float = 12.0,
    expected_identity: Mapping[str, Any] | None = None,
) -> Mapping[str, Any]:
    """Confirm a commit whose CDC acknowledgement was lost during re-enumeration."""
    usb_serial = stable_usb_serial(expected_identity) if expected_identity else None
    if not usb_serial:
        raise StandaloneTransferError("ESP32 缺少可安全重連的唯一序號；請更新韌體。")
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    expected_crc = f"{compiled.crc32:08x}"
    while time.monotonic() < deadline:
        for candidate in _reconnect_candidates(port_name, usb_serial):
            try:
                with serial.Serial(
                    candidate,
                    baudrate,
                    timeout=0.10,
                    write_timeout=0.50,
                ) as retry_port:
                    retry_port.reset_input_buffer()
                    _read_verified_identity(retry_port, expected_identity)
                    status = _send_and_expect(
                        retry_port,
                        "profile status",
                        "profile_status",
                        timeout=1.0,
                    )
                if _profile_matches(status, compiled):
                    return {
                        "cmd": "profile_commit",
                        "ok": 1,
                        "port": candidate,
                        "slot": status.get("slot", "?"),
                        "length": len(compiled.payload),
                        "crc32": expected_crc,
                        "runtime_applied": status.get(
                            "runtime_applied", int(not compiled.uses_custom_deadzones)
                        ),
                        "ack_recovered": 1,
                    }
            except (
                StandaloneTransferError,
                serial.SerialException,
                OSError,
                ValueError,
            ) as exc:
                last_error = exc
        time.sleep(0.15)
    if last_error is not None:
        raise StandaloneTransferError(
            f"ESP32 提交後無法重新確認設定：{last_error}"
        ) from last_error
    raise StandaloneTransferError("ESP32 提交後的設定長度或 CRC 不一致。")


def write_standalone_profile(
    port_name: str,
    baudrate: int,
    compiled: CompiledStandaloneProfile,
    *,
    target_mode: str | None = None,
) -> Mapping[str, Any]:
    """Atomically stage, verify and activate one compiled profile."""
    if compiled.blocking_issues:
        details = "; ".join(
            f"{issue.feature}: {issue.detail}"
            for issue in compiled.blocking_issues
        )
        raise StandaloneTransferError(
            f"Standalone profile contains blocking compatibility issues: "
            f"{details}"
        )
    if target_mode not in (
        None, "bridge", "standalone_hid"
    ):
        raise ValueError(f"Unsupported ESP32 mode: {target_mode}")
    with serial.Serial(
        port_name,
        baudrate,
        timeout=0.10,
        write_timeout=0.50,
    ) as port:
        port.reset_input_buffer()
        expected_identity = _read_verified_identity(port)
        capabilities = _send_and_expect(
            port, "capabilities", "capabilities"
        )
        features = capabilities.get("features", {})
        if not features.get("standalone_profile_write"):
            raise StandaloneTransferError(
                "目前 ESP32 韌體不支援獨立模式設定寫入。"
            )
        if not features.get("standalone_profile_runtime"):
            raise StandaloneTransferError(
                "目前 ESP32 韌體只能儲存設定，不能完整套用；請先更新獨立模式韌體。"
            )
        if compiled.uses_custom_deadzones and not features.get("standalone_custom_deadzones"):
            raise StandaloneTransferError(
                "目前 ESP32 韌體不支援逐區方向死區，請先更新韌體。"
            )
        gyro = compiled.document["gyro_mapping"]
        if (
            gyro["activation_mode"] != "OFF"
            and (gyro["x_compensation"], gyro["y_compensation"]) != (1.0, 1.0)
            and not features.get("standalone_gyro_output_compensation")
        ):
            raise StandaloneTransferError(
                "目前 ESP32 韌體不支援陀螺儀輸出補償，請先更新韌體。"
            )
        if (
            gyro["activation_mode"] != "OFF"
            and min(gyro["x_ratio"], gyro["y_ratio"]) < 0.50
            and not features.get("standalone_extended_gyro_ratio")
        ):
            raise StandaloneTransferError(
                "目前 ESP32 韌體的陀螺儀比例下限為 0.50。若要使用低於 0.50 的比例，請先更新韌體。"
            )
        if (
            gyro["button_stabilization_mode"] == "SPIKE"
            and gyro["activation_mode"] != "OFF"
            and gyro["motion_mode"] == "CENTER"
            and gyro["button_freeze_ms"] > 0
            and compiled.document["gyro_stabilization_buttons"]
            and not features.get("standalone_gyro_spike_guard")
        ):
            raise StandaloneTransferError(
                "目前 ESP32 韌體不支援陀螺儀突波抑制，請先更新韌體。"
            )
        if (
            target_mode == "standalone_hid"
            and not features.get("standalone_usb_hid")
        ):
            raise StandaloneTransferError(
                "目前 ESP32 韌體不支援手機 USB HID 模式，請先更新韌體。"
            )
        schemas = capabilities.get("profile_schemas", [])
        if STANDALONE_PROFILE_SCHEMA not in schemas:
            raise StandaloneTransferError(
                "ESP32 不支援目前的設定格式版本。"
            )

        _send_and_expect(
            port,
            (
                f"profile begin {STANDALONE_PROFILE_SCHEMA} "
                f"{len(compiled.payload)} {compiled.crc32:08x}"
            ),
            "profile_begin",
        )
        try:
            for offset in range(0, len(compiled.payload), STANDALONE_CHUNK_BYTES):
                chunk = compiled.payload[offset:offset + STANDALONE_CHUNK_BYTES]
                response = _send_and_expect(
                    port,
                    f"profile chunk {offset} {chunk.hex()}",
                    "profile_chunk",
                )
                if int(response.get("received", -1)) != offset + len(chunk):
                    raise StandaloneTransferError("ESP32 設定分段位置不一致。")
            try:
                result = _send_and_expect(
                    port, "profile commit", "profile_commit", timeout=4.0
                )
            except (
                _StandaloneResponseTimeout,
                serial.SerialException,
                OSError,
            ):
                # A CDC disconnect can hide the acknowledgement of a commit
                # that already reached the ESP32.
                port.close()
                result = _recover_committed_profile(
                    port_name, baudrate, compiled, expected_identity=expected_identity
                )
            if result.get("runtime_applied") != 1:
                raise StandaloneTransferError(
                    "ESP32 已儲存設定，但無法確認是否已套用。"
                )
            if target_mode is not None:
                mode_port = port
                mode_port_name = port_name
                reopened_port = None
                try:
                    if not port.is_open:
                        mode_port_name = result.get("port", port_name)
                        reopened_port = serial.Serial(
                            mode_port_name,
                            baudrate,
                            timeout=0.10,
                            write_timeout=0.50,
                        )
                        reopened_port.reset_input_buffer()
                        _read_verified_identity(reopened_port, expected_identity)
                        mode_port = reopened_port
                    mode_result = _change_esp32_mode(
                        mode_port, mode_port_name, baudrate, expected_identity,
                        target_mode, compiled=compiled,
                    )
                finally:
                    if reopened_port is not None:
                        reopened_port.close()
                result = dict(result)
                result["mode"] = mode_result.get("mode", target_mode)
                result["restart_required"] = bool(mode_result.get("restart_required", 0))
            return result
        except Exception:
            if port.is_open:
                try:
                    port.write(b"profile abort\n")
                    port.flush()
                except (serial.SerialException, OSError):
                    pass
            raise


def set_esp32_mode(
    port_name: str,
    baudrate: int,
    mode: str,
) -> Mapping[str, Any]:
    """Change the persisted firmware mode and reboot when USB must re-enumerate."""
    if mode not in (
        "bridge", "standalone_hid"
    ):
        raise ValueError(f"Unsupported ESP32 mode: {mode}")
    with serial.Serial(
        port_name,
        baudrate,
        timeout=0.10,
        write_timeout=0.50,
    ) as port:
        port.reset_input_buffer()
        expected_identity = _read_verified_identity(port)
        capabilities = _send_and_expect(
            port, "capabilities", "capabilities"
        )
        features = capabilities.get("features", {})
        if not (
            features.get("standalone_usb_xinput")
            or features.get("standalone_usb_hid")
        ):
            raise StandaloneTransferError(
                "目前 ESP32 韌體不支援獨立 USB 手把模式。"
            )
        if (
            mode == "standalone_hid"
            and not features.get("standalone_usb_hid")
        ):
            raise StandaloneTransferError(
                "目前 ESP32 韌體不支援手機 USB HID 模式，請先更新韌體。"
            )
        return _change_esp32_mode(port, port_name, baudrate, expected_identity, mode)
