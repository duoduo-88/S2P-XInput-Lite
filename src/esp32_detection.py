"""Shared, fast detection of the compatible ESP32-S3 serial bridge."""

import json
import re
import time

import serial
import serial.tools.list_ports


ESPRESSIF_VID = 0x303A
S2P_STANDALONE_VID = 0xCAFE
S2P_STANDALONE_PIDS = frozenset((0x4020, 0x4021, 0x4022))
COMMON_USB_SERIAL_VIDS = frozenset((0x0403, 0x10C4, 0x1A86))
S2P_FIRMWARE_PRODUCT = "S2P-FW"
S2P_FIRMWARE_VERSION = "1.0.7"
S2P_PROTOCOL = "s2p_bridge"
S2P_PROTOCOL_VERSION = "1.0.0"
S2P_FIRMWARE_PROFILE = "s2p_usb_bridge"
SUPPORTED_FIRMWARE_BUILDS = frozenset(("standalone_diagnostics",))
LEGACY_S2P_RELEASE = {
    "version": "0.14.3",
    "profile": "tinyusb_direct",
    "build": "cdc_bridge_2_lowlatency",
}


def _port_priority(port_info):
    """Probe native Espressif ports first, then likely USB serial ports."""
    vid = getattr(port_info, "vid", None)
    pid = getattr(port_info, "pid", None)
    if (
        vid == ESPRESSIF_VID
        or (vid == S2P_STANDALONE_VID and pid in S2P_STANDALONE_PIDS)
    ):
        return (0, str(getattr(port_info, "device", "")))
    if vid in COMMON_USB_SERIAL_VIDS:
        return (1, str(getattr(port_info, "device", "")))
    if vid is not None:
        return (2, str(getattr(port_info, "device", "")))
    return (3, str(getattr(port_info, "device", "")))


def ordered_serial_ports(port_infos=None):
    if port_infos is None:
        port_infos = serial.tools.list_ports.comports()
    return sorted(list(port_infos), key=_port_priority)


def _is_bridge_status(data):
    if not isinstance(data, dict):
        return False
    current = (
        data.get("cmd") in ("status", "status lite")
        and data.get("product") == S2P_FIRMWARE_PRODUCT
        and data.get("protocol") == S2P_PROTOCOL
        and data.get("protocol_version") == S2P_PROTOCOL_VERSION
        and data.get("profile") == S2P_FIRMWARE_PROFILE
        and data.get("build") in SUPPORTED_FIRMWARE_BUILDS
    )
    # Accept only the last pre-1.0 S2P bundle as an upgrade path. Earlier
    # upstream releases are intentionally not treated as compatible firmware.
    legacy = (
        data.get("cmd") in ("status", "status lite")
        and all(
            data.get(key) == value
            for key, value in LEGACY_S2P_RELEASE.items()
        )
    )
    return current or legacy


def verified_firmware_status(data):
    """Require protocol identity and the compatible profile runtime contract."""
    if not isinstance(data, dict) or not _is_bridge_status(data):
        return False
    if data.get("product") != S2P_FIRMWARE_PRODUCT:
        return True  # Explicitly supported pre-1.0 upgrade identity.
    features = data.get("features", {})
    return isinstance(features, dict) and all(features.get(key) for key in (
        "standalone_profile_write", "standalone_profile_runtime"))


def stable_usb_serial(data):
    value = str(data.get("usb_serial", ""))
    return value if re.fullmatch(r"S2P-[0-9A-F]{12}", value) else None


def firmware_identity_matches(actual, expected):
    """Bind a re-enumerated CDC interface to the original physical device."""
    serial_number = stable_usb_serial(expected)
    return bool(serial_number and verified_firmware_status(actual)
                and stable_usb_serial(actual) == serial_number)


def usb_metadata_matches(port_info, data):
    """Verify shared IDs using the handshake and any available USB product."""
    if (getattr(port_info, "vid", None) == ESPRESSIF_VID
            and getattr(port_info, "pid", None) in (0x4001, 0x4005)):
        expected_product = ("S2P ESP32 Bridge" if port_info.pid == 0x4001
                            else "S2P Mobile Gamepad")
        usb_product = getattr(port_info, "product", None)
        if (usb_product is not None and usb_product != expected_product):
            return False
        if not verified_firmware_status(data):
            return False
        # The explicitly supported pre-1.0 bundle predates S2P's USB serial
        # and product fields. Its exact application signature is sufficient
        # only for Bridge upgrade discovery, never for device association.
        if port_info.pid == 0x4001 and all(
                data.get(key) == value for key, value in LEGACY_S2P_RELEASE.items()):
            return True
        # PySerial 3.5 on Windows does not populate ListPortInfo.product.
        # Missing USB product metadata needs the complete application identity;
        # it never makes a VID/PID or a serial number sufficient by itself.
        if usb_product is None and data.get("product") != S2P_FIRMWARE_PRODUCT:
            return False
        serial_number = stable_usb_serial(data)
        if serial_number:
            return serial_number == getattr(port_info, "serial_number", None)
        # The historical Bridge descriptor used a fixed serial. Keep its
        # verified application handshake as an upgrade path, never as a
        # cross-interface/device association or reconnect identity.
        old_version = data.get("version") in {
            "1.0.0", "1.0.1", "1.0.2", "1.0.3",
            "1.0.4", "1.0.5", "1.0.6",
        }
        return bool(port_info.pid == 0x4001 and old_version
                    and getattr(port_info, "serial_number", None) == "S2P-BRIDGE-DEV1")

    return True


def find_esp32_firmware(baudrate=2_000_000, port_infos=None):
    """Return identity for the first compatible bridge, including its port."""

    ordered = ordered_serial_ports(port_infos)
    for port_info in ordered:
        port = port_info.device
        # Native/identified USB devices get a full response window. Legacy COM
        # ports without VID metadata (commonly COM1) are only a fallback.
        probe_window = 1.0 if getattr(port_info, "vid", None) is not None else 0.20
        try:
            with serial.Serial(
                port,
                baudrate,
                timeout=min(0.10, probe_window),
                write_timeout=0.25,
            ) as ser:
                ser.reset_input_buffer()
                ser.write(b"status lite\n")
                ser.flush()
                deadline = time.monotonic() + probe_window
                while time.monotonic() < deadline:
                    line = ser.readline()
                    if not line:
                        continue
                    text = line.decode("utf-8", errors="ignore").strip()
                    if text.startswith("[") or "{" not in text or "}" not in text:
                        continue
                    try:
                        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
                    if (verified_firmware_status(data)
                            and usb_metadata_matches(port_info, data)):
                        result = dict(data)
                        result["port"] = port
                        return result
        except (serial.SerialException, OSError):
            continue
    return None


def find_esp32_port(baudrate=2_000_000, port_infos=None):
    """Return the first compatible bridge; avoid a one-second wait on COM1."""

    firmware = find_esp32_firmware(baudrate, port_infos)
    return firmware.get("port") if firmware else None
