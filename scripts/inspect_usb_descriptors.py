"""Extract and validate the two USB personalities in an ESP32-S3 ELF.

Uses only the Python standard library. This verifies compiled descriptor bytes;
host enumeration, suspend/resume and Android interpretation still require hardware.
"""

import argparse
import hashlib
import json
import struct
from pathlib import Path

# Retained Android layout: semantic button usages, null hat, four signed axes,
# and independent Brake/Accelerator triggers. Pin the descriptor as well as its
# byte count so a different 13-byte HID layout cannot pass this release gate.
ANDROID_GAMEPAD_REPORT_SHA256 = "9cc08b0e8f254b14f528da596adb2c50ec0e2ceba4ca3461104eccd1c0232971"


def read_elf_symbols(path):
    """Return byte contents of file-backed symbols in a little-endian ELF32."""
    data = Path(path).read_bytes()
    if data[:6] != b"\x7fELF\x01\x01":
        raise ValueError("Expected a little-endian ELF32 firmware")
    header = struct.unpack_from("<16sHHIIIIIHHHHHH", data)
    shoff, shsize, shcount = header[6], header[11], header[12]
    if shsize != 40 or not shcount or shoff + shcount * shsize > len(data):
        raise ValueError("Invalid ELF section table")
    sections = [
        struct.unpack_from("<10I", data, shoff + index * shsize)
        for index in range(shcount)
    ]
    symbols = {}
    for section in sections:
        if section[1] != 2:  # SHT_SYMTAB
            continue
        if section[9] != 16 or section[6] >= len(sections):
            raise ValueError("Invalid ELF symbol table")
        names_section = sections[section[6]]
        names = data[names_section[4]:names_section[4] + names_section[5]]
        for offset in range(section[4], section[4] + section[5], 16):
            name_offset, address, size, _, _, index = struct.unpack_from(
                "<IIIBBH", data, offset
            )
            if not size or index == 0 or index >= len(sections):
                continue
            storage = sections[index]
            if storage[1] == 8:  # SHT_NOBITS
                continue
            relative = address - storage[3]
            if relative < 0 or relative + size > storage[5]:
                continue
            end = names.find(b"\0", name_offset)
            name = names[name_offset:end].decode("ascii", errors="strict")
            start = storage[4] + relative
            symbols[name] = data[start:start + size]
    return symbols


def decode_configuration(data):
    if len(data) < 9 or data[:2] != b"\x09\x02":
        raise ValueError("Missing USB configuration header")
    if int.from_bytes(data[2:4], "little") != len(data):
        raise ValueError("Configuration total length does not match its bytes")
    interfaces, classes, iads, endpoints, details = [], [], [], [], []
    declared_endpoints, actual_endpoints = [], []
    hid_report_length = None
    offset = 0
    while offset < len(data):
        length = data[offset]
        if length < 2 or offset + length > len(data):
            raise ValueError("Truncated USB descriptor")
        item = data[offset:offset + length]
        kind = item[1]
        if kind == 11:
            if length != 8:
                raise ValueError("Invalid IAD")
            iads.append([item[2], item[3], item[4]])
        elif kind == 4:
            if length != 9 or item[3] != 0:
                raise ValueError("Unexpected interface or alternate setting")
            interfaces.append(item[2])
            classes.append(item[5])
            declared_endpoints.append(item[4])
            actual_endpoints.append(0)
        elif kind == 5:
            if length != 7 or not interfaces or item[2] in endpoints:
                raise ValueError("Invalid or duplicate endpoint")
            endpoints.append(item[2])
            actual_endpoints[-1] += 1
            details.append({"address": item[2], "transfer": item[3] & 3,
                            "packet_size": int.from_bytes(item[4:6], "little"),
                            "interval": item[6]})
        elif kind == 0x21:
            if length != 9 or not classes or classes[-1] != 3 or item[6] != 0x22:
                raise ValueError("Invalid HID descriptor")
            hid_report_length = int.from_bytes(item[7:9], "little")
        elif kind not in (2, 0x24):
            raise ValueError(f"Unexpected descriptor type: {kind:#x}")
        offset += length
    if interfaces != list(range(data[4])) or declared_endpoints != actual_endpoints:
        raise ValueError("Configuration interface/endpoint counts disagree")
    return {"classes": classes, "iads": iads, "endpoints": endpoints,
            "endpoint_details": details, "hid_report_length": hid_report_length}


def hid_input_size(report):
    size = count = bits = 0
    offset = 0
    while offset < len(report):
        prefix = report[offset]
        length = (0, 1, 2, 4)[prefix & 3]
        if prefix == 0xfe or offset + 1 + length > len(report):
            raise ValueError("Unsupported or truncated HID report item")
        value = int.from_bytes(report[offset + 1:offset + 1 + length], "little")
        kind, tag = (prefix >> 2) & 3, prefix >> 4
        if kind == 1 and tag == 7:
            size = value
        elif kind == 1 and tag == 9:
            count = value
        elif kind == 1 and tag in (8, 10, 11):
            raise ValueError("Unexpected HID report ID or global stack")
        elif kind == 0 and tag == 8:
            bits += size * count
        elif kind == 0 and tag in (9, 11):
            raise ValueError("Unexpected HID output/feature report")
        offset += 1 + length
    if bits % 8:
        raise ValueError("HID input report is not byte-aligned")
    return bits // 8


def validate_personality(mode, device, configuration, report=b""):
    if len(device) != 18 or device[:2] != b"\x12\x01":
        raise ValueError("Invalid device descriptor")
    expected_pid = {"bridge": 0x4001, "hid": 0x4005}[mode]
    vid, pid = struct.unpack_from("<HH", device, 8)
    if (vid, pid) != (0x303a, expected_pid):
        raise ValueError("Device VID/PID does not match its USB personality")
    if device[2:8] != bytes.fromhex("00 02 ef 02 01 40") or device[17] != 1:
        raise ValueError("Unexpected USB version, IAD class, EP0, or configuration count")
    result = decode_configuration(configuration)
    expected_classes = [2, 10, 3] if mode == "hid" else [2, 10]
    expected_endpoints = [0x81, 0x02, 0x82, 0x83] if mode == "hid" else [0x81, 0x02, 0x82]
    if result["classes"] != expected_classes or result["iads"] != [[0, 2, 2]]:
        raise ValueError("Expected CDC IAD and standard CDC/HID interfaces only")
    if result["endpoints"] != expected_endpoints:
        raise ValueError("Unexpected USB endpoint allocation")
    expected_packets = [8, 64, 64, 32] if mode == "hid" else [8, 64, 64]
    expected_transfers = [3, 2, 2, 3] if mode == "hid" else [3, 2, 2]
    if ([item["packet_size"] for item in result["endpoint_details"]] != expected_packets
            or [item["transfer"] for item in result["endpoint_details"]] != expected_transfers):
        raise ValueError("Unexpected endpoint packet size or transfer type")
    if mode == "hid":
        if result["hid_report_length"] != len(report) or hid_input_size(report) != 13:
            raise ValueError("HID report descriptor length or input size is inconsistent")
        if hashlib.sha256(report).hexdigest() != ANDROID_GAMEPAD_REPORT_SHA256:
            raise ValueError("HID controls do not match the retained Android gamepad layout")
        result["input_report_bytes"] = 13
        result["report_hex"] = report.hex()
    elif result["hid_report_length"] is not None:
        raise ValueError("Bridge unexpectedly advertises HID")
    return {"vid_pid": f"{vid:04X}:{pid:04X}", "device_hex": device.hex(),
            "configuration_hex": configuration.hex(), **result}


def inspect(elf, application):
    symbols = read_elf_symbols(elf)
    image = Path(application).read_bytes()
    personalities = {}
    for mode in ("bridge", "hid"):
        for suffix in ("device_descriptor", "configuration_descriptor"):
            if symbols[f"s_{mode}_{suffix}"] not in image:
                raise ValueError("ELF descriptors are absent from the supplied application image")
        personalities[mode] = validate_personality(
            mode, symbols[f"s_{mode}_device_descriptor"],
            symbols[f"s_{mode}_configuration_descriptor"],
            symbols["s_hid_report_descriptor"] if mode == "hid" else b"",
        )
    if symbols["s_hid_report_descriptor"] not in image:
        raise ValueError("ELF HID report is absent from the supplied application image")
    for marker in (b"XUSB20", b"S2P Standalone XInput", b"S2P Auto Gamepad"):
        if marker in image or marker in Path(elf).read_bytes():
            raise ValueError(f"Removed USB personality remains in compiled firmware: {marker!r}")
    if b"S2P Mobile Gamepad\0" not in image:
        raise ValueError("Missing HID product string")
    return {"schema": 1, "evidence": "compiled ELF descriptor bytes; hardware enumeration pending",
            "application_sha256": hashlib.sha256(image).hexdigest(),
            "personalities": personalities}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path)
    parser.add_argument("application", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    evidence = json.dumps(inspect(args.elf, args.application), indent=2) + "\n"
    if args.output:
        args.output.write_text(evidence, encoding="utf-8")
    else:
        print(evidence, end="")


if __name__ == "__main__":
    main()
