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

from idle_disconnect import normalize_idle_disconnect_minutes

import serial
from serial.tools import list_ports

from mapping_targets import XINPUT_BUTTON_TARGETS
from settings_schema import normalize_section_values


STANDALONE_PROFILE_SCHEMA = 1
STANDALONE_PROFILE_MAX_BYTES = 8192
STANDALONE_CHUNK_BYTES = 96
SUPPORTED_BUTTON_TARGETS = frozenset(XINPUT_BUTTON_TARGETS) | {"NONE"}


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


class StandaloneTransferError(RuntimeError):
    """Raised when the ESP32 rejects or fails to acknowledge a profile write."""


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
    if (
        str(gyro.get("activation_mode", "OFF")).upper() != "OFF"
        and str(gyro.get("target", "RIGHT_STICK")).upper() == "MOUSE"
    ):
        _issue(
            issues, "blocking", "陀螺儀滑鼠",
            "ESP32 不支援 Windows 滑鼠輸出；請改為左或右搖桿。",
        )
    for source, raw_target in settings.get("buttons", {}).items():
        target = str(raw_target).strip().upper()
        if target not in SUPPORTED_BUTTON_TARGETS:
            _issue(
                issues, "blocking", "按鍵 Windows 輸出",
                f"{str(source).upper()} → {target or '<空白>'}",
            )
    for side, mappings in settings.get("direction_mappings", {}).items():
        mode = str(
            sections.get(f"stick_direction_{side}", {}).get("mode", "4WAY")
        ).upper()
        if "MOUSE" in mode or mode not in (
            "4WAY", "8WAY", "XINPUT_LT_LINEAR", "XINPUT_RT_LINEAR",
        ):
            if "MOUSE" in mode or any(
                str(value).strip().upper() != "NONE"
                for value in mappings.values()
            ):
                _issue(
                    issues, "blocking", "搖桿 Windows 輸出",
                    f"{str(side).upper()} 使用 {mode}。",
                )
        for direction, raw_target in mappings.items():
            target = str(raw_target).strip().upper()
            if target not in SUPPORTED_BUTTON_TARGETS:
                _issue(
                    issues, "blocking", "搖桿方向映射",
                    f"{str(side).upper()} {str(direction).upper()} → {target}",
                )
    enabled_layers = [
        layer for layer in mapping_layers if layer.get("enabled", False)
    ]
    if len(enabled_layers) > 8:
        _issue(
            issues, "blocking", "映射層數量",
            f"ESP32 獨立模式目前最多支援 8 個啟用層；目前為 {len(enabled_layers)} 個。",
        )
    for index, layer in enumerate(enabled_layers):
        name = str(layer.get("name", "")).strip() or f"Layer {index + 1}"
        for source, raw_target in layer.get("buttons", {}).items():
            target = str(raw_target).strip().upper()
            if target not in SUPPORTED_BUTTON_TARGETS:
                _issue(
                    issues, "blocking", "映射層 Windows 輸出",
                    f"{name}: {str(source).upper()} → {target}",
                )
        for side in ("left", "right"):
            mode = str(
                layer.get(f"stick_{side}", {}).get("mode", "4WAY")
            ).upper()
            if "MOUSE" in mode or mode not in (
                "4WAY", "8WAY", "XINPUT_LT_LINEAR", "XINPUT_RT_LINEAR",
            ):
                _issue(
                    issues, "blocking", "映射層 Windows 輸出",
                    f"{name}: {side.upper()} 使用 {mode}。",
                )
    return tuple(issues)


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
            layer for layer in mapping_layers if layer.get("enabled", False)
        ],
    }
    payload = json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
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
        if "{" not in text or "}" not in text:
            continue
        try:
            return json.loads(text[text.find("{"):text.rfind("}") + 1])
        except (ValueError, TypeError, json.JSONDecodeError):
            continue
    raise StandaloneTransferError("ESP32 回應逾時。")


def _send_and_expect(port, command: str, expected_cmd: str, timeout=2.0):
    port.write((command + "\n").encode("ascii"))
    port.flush()
    deadline = time.monotonic() + timeout
    while True:
        response = _read_json_response(port, deadline)
        if response.get("cmd") != expected_cmd:
            if time.monotonic() >= deadline:
                raise StandaloneTransferError("ESP32 回應逾時。")
            continue
        if not response.get("ok", 0):
            error = response.get("error", "unknown")
            raise StandaloneTransferError(f"ESP32 拒絕設定：{error}")
        return response


def _recover_committed_profile(
    port_name: str,
    baudrate: int,
    compiled: CompiledStandaloneProfile,
    *,
    timeout: float = 12.0,
) -> Mapping[str, Any]:
    """Confirm a commit whose CDC acknowledgement was lost during re-enumeration."""
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    expected_crc = f"{compiled.crc32:08x}"
    while time.monotonic() < deadline:
        detected = [
            info.device
            for info in list_ports.comports()
            if (
                getattr(info, "serial_number", None) in (
                    "S2P-XI-DEV1", "S2P-HID-DEV1"
                )
                or (
                    getattr(info, "vid", None) == 0xCAFE
                    and getattr(info, "pid", None) in (0x4020, 0x4021)
                )
            )
        ]
        candidates = list(dict.fromkeys((port_name, *detected)))
        for candidate in candidates:
            try:
                with serial.Serial(
                    candidate,
                    baudrate,
                    timeout=0.10,
                    write_timeout=0.50,
                ) as retry_port:
                    retry_port.reset_input_buffer()
                    status = _send_and_expect(
                        retry_port,
                        "profile status",
                        "profile_status",
                        timeout=1.0,
                    )
                if (
                    status.get("valid", 0)
                    and int(status.get("length", -1)) == len(compiled.payload)
                    and str(status.get("crc32", "")).lower() == expected_crc
                ):
                    return {
                        "cmd": "profile_commit",
                        "ok": 1,
                        "port": candidate,
                        "slot": status.get("slot", "?"),
                        "length": len(compiled.payload),
                        "crc32": expected_crc,
                        "runtime_applied": 1,
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
        None, "bridge", "standalone", "standalone_hid"
    ):
        raise ValueError(f"Unsupported ESP32 mode: {target_mode}")
    with serial.Serial(
        port_name,
        baudrate,
        timeout=0.10,
        write_timeout=0.50,
    ) as port:
        port.reset_input_buffer()
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
                StandaloneTransferError,
                serial.SerialException,
                OSError,
            ):
                # Applying a profile can briefly re-enumerate USB CDC on some
                # ESP32-S3/Windows combinations. The commit is atomic, so a
                # matching active slot is sufficient to recover the lost ACK.
                port.close()
                result = _recover_committed_profile(
                    port_name, baudrate, compiled
                )
            if not result.get("runtime_applied", 0):
                raise StandaloneTransferError(
                    "ESP32 已儲存設定，但執行期解析失敗；原有有效設定仍保留。"
                )
            if target_mode is not None:
                mode_port = port
                reopened_port = None
                if not port.is_open:
                    reopened_port = serial.Serial(
                        result.get("port", port_name),
                        baudrate,
                        timeout=0.10,
                        write_timeout=0.50,
                    )
                    reopened_port.reset_input_buffer()
                    mode_port = reopened_port
                try:
                    mode_result = _send_and_expect(
                        mode_port, f"mode {target_mode}", "mode"
                    )
                    restart_required = bool(
                        mode_result.get("restart_required", 0)
                    )
                    if restart_required:
                        _send_and_expect(mode_port, "restart", "restart")
                finally:
                    if reopened_port is not None:
                        reopened_port.close()
                result = dict(result)
                result["mode"] = mode_result.get("mode", target_mode)
                result["restart_required"] = restart_required
            return result
        except Exception:
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
    if mode not in ("bridge", "standalone", "standalone_hid"):
        raise ValueError(f"Unsupported ESP32 mode: {mode}")
    with serial.Serial(
        port_name,
        baudrate,
        timeout=0.10,
        write_timeout=0.50,
    ) as port:
        port.reset_input_buffer()
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
        result = _send_and_expect(port, f"mode {mode}", "mode")
        if result.get("restart_required", 0):
            _send_and_expect(port, "restart", "restart")
        return result
