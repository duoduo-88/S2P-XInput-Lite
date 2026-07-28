"""Shared, fast detection of the compatible ESP32-S3 serial bridge."""

import json
import time

import serial
import serial.tools.list_ports


ESPRESSIF_VID = 0x303A
S2P_STANDALONE_VID = 0xCAFE
S2P_STANDALONE_PIDS = frozenset((0x4020, 0x4021))
COMMON_USB_SERIAL_VIDS = frozenset((0x0403, 0x10C4, 0x1A86))
S2P_FIRMWARE_PRODUCT = "S2P-FW"
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


def find_esp32_port(baudrate=2_000_000, port_infos=None):
    """Return the first compatible bridge; avoid a one-second wait on COM1."""
    ordered = ordered_serial_ports(port_infos)
    standalone_fallback = None
    for port_info in ordered:
        port = port_info.device
        if (
            getattr(port_info, "vid", None) == S2P_STANDALONE_VID
            and getattr(port_info, "pid", None) in S2P_STANDALONE_PIDS
        ):
            # These VID/PIDs belong to this firmware's standalone USB
            # personalities. Keep the CDC port as a safe fallback even if the
            # first status reply is lost during USB re-enumeration.
            standalone_fallback = standalone_fallback or port
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
                    if "{" not in text or "}" not in text:
                        continue
                    try:
                        data = json.loads(text[text.find("{"):text.rfind("}") + 1])
                    except (ValueError, TypeError, json.JSONDecodeError):
                        continue
                    if _is_bridge_status(data):
                        return port
        except (serial.SerialException, OSError):
            continue
    return standalone_fallback
