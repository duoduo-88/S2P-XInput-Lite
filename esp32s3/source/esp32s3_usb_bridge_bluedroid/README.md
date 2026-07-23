# S2P-XInput-Lite ESP32-S3 BLE Bridge

This directory contains the stable bridge source plus the in-development
`0.14.0-dev` standalone compatibility stage. It targets an ESP32-S3. Bridge mode keeps
the existing USB CDC transport. Standalone mode can expose either a CDC +
XInput-compatible composite device for PCs or a CDC + standards-based
USB HID Gamepad for direct connection to phones.

## Standalone development status

The development firmware keeps the existing bridge protocol and adds:

- a machine-readable `capabilities` response;
- standalone profile schema version negotiation;
- chunked profile transfer with CRC32 validation;
- A/B NVS slots, read-back verification, and atomic active-slot switching.
- persisted `bridge` / `standalone` / `standalone_hid` mode selection with
  controlled reboot;
- a development XInput USB interface alongside the CDC configuration channel;
- profile-driven Switch input to XInput buttons, calibrated sticks, direction
  mappings, linear triggers, compatible mapping layers, and gyro-to-stick;
- XInput large/small motor commands translated to LF/HF controller rumble.
- mobile USB HID output driven by the same processed buttons, calibrated
  sticks, direction mappings, mapping layers and gyro-to-stick state.

The XInput path compiles and links successfully and has passed Windows
enumeration and 130+ Hz input tests. The mobile HID personality enumerates as
`S2P Mobile Gamepad` (VID/PID `CAFE:4021`) and produced 131 Hz hardware input
with 7.68/9.01 ms p50/p95 intervals. Profile data is validated, stored, and
applied at runtime. Keyboard/mouse output, Windows audio haptics, process-based
profile switching, phone rumble, and BLE HID output remain desktop-only or
unimplemented.
Do not publish this development binary as a stable standalone release until the
expanded profile runtime has completed controller-side regression testing.

The Windows client uses these CDC commands:

```text
capabilities
profile status
profile begin <schema> <length> <crc32-hex>
profile chunk <offset> <payload-hex>
profile commit
profile abort
mode standalone
mode standalone_hid
mode bridge
restart
```

An interrupted or rejected transfer leaves the previous active slot unchanged.
The current profile size limit is 8192 bytes.

The development XInput interface uses a non-retail development VID/PID plus a
Microsoft OS 2.0 `XUSB20` compatible-ID descriptor. It does not copy the
VID/PID of a retail Xbox controller.

“XInput-compatible” describes USB interface interoperability only. It does not
represent Microsoft licensing, certification, approval, or hardware identity.
The development VID/PID is intended for development and testing, is not a
formally assigned production identifier, and may conflict with other
development devices that use the same value.

## Build requirements

- ESP-IDF 5.5.4 (the committed dependency lock was generated with this version)
- An ESP32-S3 board with 16 MB flash
- The ESP-IDF Component Manager enabled

## Build

From an ESP-IDF command prompt:

```console
idf.py set-target esp32s3
idf.py build
```

`idf_component.yml`, `dependencies.lock`, and `sdkconfig.defaults` define the required managed components and project configuration. Do not commit the generated `build`, `managed_components`, or `sdkconfig` paths.

## Flash with ESP-IDF

```console
idf.py -p COM_PORT flash
```

Replace `COM_PORT` with the board's flashing port.

## Bundled application flash layout

S2P-XInput-Lite uses these release files and offsets:

| Offset | File |
| --- | --- |
| `0x0000` | `firmware/bootloader.bin` |
| `0x8000` | `firmware/partition-table.bin` |
| `0x10000` | `firmware/esp32s3_bluedroid_bridge.bin` |

The Windows application invokes `tools/esptool.exe` with DIO mode, an 80 MHz
flash frequency, and a 16 MB flash size. Existing `0.12.4` devices remain
usable in bridge mode, but standalone profile storage and direct USB controller
output require the current `0.14.0-dev` firmware.
