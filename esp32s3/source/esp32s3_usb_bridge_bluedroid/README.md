# S2P-XInput-Lite ESP32-S3 BLE Bridge

This directory contains the stable bridge and standalone `S2P-FW 1.0.1`
source. It targets an ESP32-S3. Bridge mode keeps
the existing USB CDC transport. Standalone mode can expose either a CDC +
XInput-compatible composite device for PCs or a CDC + standards-based
USB HID Gamepad for direct connection to phones.

## Bridge and standalone status

The firmware uses the independent `s2p_bridge 1.0.0` protocol and includes:

- a machine-readable `capabilities` response;
- live bridge/controller MAC, BLE connection interval, and RSSI link status;
- standalone profile schema version negotiation;
- chunked profile transfer with CRC32 validation;
- A/B NVS slots, read-back verification, and atomic active-slot switching;
- persisted `bridge` / `standalone` / `standalone_hid` mode selection with
  controlled reboot;
- a development XInput USB interface alongside the CDC configuration channel;
- profile-driven Switch input to XInput buttons, calibrated sticks, direction
  mappings, linear triggers, compatible mapping layers, and gyro-to-stick;
- XInput large/small motor commands translated to LF/HF controller rumble;
- mobile USB HID output driven by the same processed buttons, calibrated
  sticks, direction mappings, mapping layers and gyro-to-stick state;
- resumable CDC transmission with a shared per-loop time budget, preventing
  slow hosts from producing partial frames or starving controller processing;
- priority-isolated CDC control, lifecycle-event and scan/debug queues, keeping
  connection commands and state changes responsive during scan-result floods;
- standalone scanning waits for GAP scan-parameter setup, recognizes the
  controller `reconnect_mac`, and completes the SET_MAC/LTK1/LTK2/FINISH
  sequence after a first-time SYNC connection so later wakeups and ESP32
  restarts reconnect without pressing SYNC again.

The XInput path compiles and links successfully and has passed Windows
enumeration and 130+ Hz input tests. The mobile HID personality enumerates as
`S2P Mobile Gamepad` (VID/PID `CAFE:4021`) and produced 131 Hz hardware input
with 7.68/9.01 ms p50/p95 intervals. Profile data is validated, stored, and
applied at runtime. Keyboard/mouse output, Windows audio haptics, process-based
profile switching, phone rumble, and BLE HID output remain desktop-only or
unimplemented.
The standalone runtime has completed automated, full ESP-IDF build, and
controller-side reconnect regression testing for the current release.

The Windows client uses these CDC commands:

```text
capabilities
runtime status
profile status
profile begin <schema> <length> <crc32-hex>
profile chunk <offset> <payload-hex>
profile commit
profile abort
latency status
latency reset
ble timing
link status
rumble status
rumble reset
mode standalone
mode standalone_hid
mode bridge
restart
```

An interrupted or rejected transfer leaves the previous active slot unchanged.
The current profile size limit is 8192 bytes.

`latency reset` clears the transport counters before a controlled run.
`latency status` reports received BLE input, source timestamp gaps, input
shadow overwrites, notify-queue drops, USB endpoint busy episodes, pending USB
state overwrites, and completed USB wait average/maximum time. A source gap
with zero shadow, queue, and USB counters indicates that the skipped interval
already existed before the standalone output path. Source-gap detection scales
with each channel's configured 7.5 or 15 ms BLE interval, and USB disconnect or
re-enumeration time is excluded from endpoint-wait measurements.

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
flash frequency, and a 16 MB flash size. Upstream Switch2Connect firmware is
not treated as protocol-compatible. The previous bundled S2P `0.14.3` build is
recognized only so the desktop application can guide an upgrade; standalone
profile storage, diagnostics, and direct USB controller output require the
current `S2P-FW 1.0.1` firmware.
