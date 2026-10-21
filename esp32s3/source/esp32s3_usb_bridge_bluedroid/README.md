# S2P-XInput-Lite ESP32-S3 BLE Bridge

This is `S2P-FW 1.0.7` for ESP32-S3 with 16 MB flash, using the independent
`s2p_bridge 1.0.0` protocol and existing standalone profile schema.

## Bridge and Android USB HID

| Boot mode | VID:PID | Interfaces | Product |
|---|---|---|---|
| `bridge` | `303A:4001` | CDC control 0, data 1 | S2P ESP32 Bridge |
| `standalone_hid` | `303A:4005` | CDC control 0, data 1, HID 2 | S2P Mobile Gamepad |

Both use CDC IAD and device Misc/Common/IAD classes. Endpoints are CDC notification
`0x81`, CDC OUT/IN `0x02/0x82`, and HID interrupt IN `0x83` in HID mode only.
HID retains its 13-byte input report, button usages, hat null value, four axes,
and separate analog trigger semantics. There is no HID rumble output report.

Physical USB XInput, vendor XUSB interface, XUSB20 and Auto host probing are
removed. These VID/PIDs are Espressif/TinyUSB shared defaults, not a dedicated
allocation or USB certification. The PID formula in locked esp_tinyusb is
`0x4000 | CDC<<0 | MSC<<1 | HID<<2 | MIDI<<3 | AUDIO<<4 | VENDOR<<5`:
CDC only is `0x4001`; CDC+HID is `0x4005`. Bridge uses its CDC-only descriptor
even though the image compiles HID support. See
[Espressif's VID/PID guidance](https://docs.espressif.com/projects/esp-iot-solution/en/latest/usb/usb_overview/usb_vid_pid.html)
and [USB PID policy](https://github.com/espressif/usb-pids/blob/main/README.md).

USB metadata locates candidates only. S2P operations require matching firmware
product, protocol, profile/build identity and compatible capabilities. HID special
interpretation requires association to that exact verified CDC device by USB
parent/container or stable serial `S2P-<12 uppercase MAC hex>`. Otherwise the
desktop retains generic HID behavior. `CAFE:4020/4021/4022` are legacy upgrade
candidates only and must also pass identity verification.

## Profiles, migration and recovery

Chunked profile transfer validates CRC32 and length, uses A/B NVS slots with
read-back verification and atomic active-slot switching, and limits profiles to
8192 bytes. Interrupted/rejected transfers retain the previous valid profile.
HID reuses calibration, curves/deadzones, output shape, button/direction mappings,
linear triggers, compatible Mapping Layers, gyro-to-stick, battery LEDs,
idle disconnect and reconnect. Desktop Bridge XInput through ViGEmBus, game
rumble and audio haptics remain available. Standalone Windows keyboard/mouse,
Audio/Mix haptics, process-based profiles, phone rumble and BLE HID are unsupported.

NVS mode `0=Bridge` and `2=HID` retain their meanings; old `1=XInput` and `3=Auto`
migrate to HID. Unknown/read-error modes use Bridge. Legacy `standalone`/`usb_hid`
keys are decoded for migration. Migration does not reboot, and failed persistence
keeps a safe in-memory mode for retry. Old sources are cleared only after durable
commit. `mode_migration` exposes the removed source mode to the desktop notice.

Hold HOME+A for 3 seconds and release to select HID; HOME+X for 3 seconds and
release selects Bridge. HOME+Y is removed. Raw-input chords bypass profile
mappings/layers. Same-mode requests do not reboot; failed persistence does not
reboot and leaves CDC available. New mode commands accept only `bridge` and
`standalone_hid`; old `standalone`/`standalone_auto` return unsupported errors.

CDC recovery remains available without a controller in either mode: `mode bridge`,
then `restart` after success. If CDC is unavailable, use board-specific ROM
Download Mode: typically hold BOOT, press/release RESET/EN, then release BOOT;
boards without RESET may require holding BOOT during USB reconnect. Follow the
board manufacturer's instructions and use the native USB port. After flashing,
release BOOT and reset/power-cycle. ROM rescue uses esptool chip/16 MB flash
verification, not the application's identity protocol.

## Bridge host lease

Only configured **and** active `bridge` mode accepts `host begin <session>`.
After `host_session` with `state=ready`, the desktop sends `host ping <session>`
using its existing status worker. Eight seconds without a matching ping, or
three seconds of uninterrupted CDC/DTR loss, blocks reconnects and releases
BLE links sequentially. `host end <session>` starts the same release immediately.
`host_session` with `state=stopping` acknowledges acceptance;
`host_released` is emitted only after all channel slots and disconnect work
are gone and scanning is stopped. A ready-channel status mask is not proof
that a connecting link has disappeared.

A new session must wait for the previous owner to expire/release; the desktop
allows 14 seconds during startup and can cancel that wait on GUI exit. Pings
or begin commands carrying the released session ID cannot re-arm it. Closing
the GUI releases its connector through the existing signal path; a Windows
process-handle wait also handles a GUI-only crash. Legacy desktop clients
that never send `host begin` do not arm the watchdog. Use the updated desktop
source together with the 1.0.7 image for crash protection.

Android USB HID do not accept these host commands and
retain their existing idle disconnect, button wake and reconnect behavior
with CDC/DTR absent. Mode commands persist the next boot mode; switching
personality takes effect on restart, as before.

## CDC commands

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
mode standalone_hid
mode bridge
restart
```

`latency reset` clears the transport counters before a controlled run.
`latency status` reports received BLE input, source timestamp gaps, intentional
newest-state shadow coalescing, input edge-FIFO drops, USB endpoint busy
episodes, pending USB state overwrites, and completed USB wait average/maximum
time. Bridge and standalone modes both preserve button and trigger transitions
in the FIFO while coalescing continuous stick and IMU samples. Standalone mode
also applies USB pending-slot backpressure so the edge FIFO is not drained
faster than those transitions can be submitted. A source gap with zero queue
and USB counters indicates that the skipped interval already existed before
the output path. Source-gap detection scales with each channel's configured
7.5 or 15 ms BLE interval, and USB disconnect or re-enumeration time is
excluded from endpoint-wait measurements.

## Build and flash

Requirements: ESP-IDF 5.5.4, Component Manager, esp_tinyusb 1.7.6~2 and tinyusb
0.19.0~3 from `dependencies.lock`. Release builds enable reproducible builds.
Use the same locked toolchain and configuration to reproduce bundle hashes.

```console
idf.py set-target esp32s3
idf.py build
idf.py -p COM_PORT flash
```

Replace `COM_PORT` with the board's flashing port. `idf_component.yml`,
`dependencies.lock` and `sdkconfig.defaults` define dependencies/configuration;
do not commit generated `build`, `managed_components` or `sdkconfig` paths.

| Offset | Bundled file (under `esp32s3/firmware`) |
|---|---|
| `0x0000` | `bootloader.bin` |
| `0x8000` | `partition-table.bin` |
| `0x10000` | `esp32s3_bluedroid_bridge.bin` |

The Windows flasher uses DIO, 80 MHz and verified 16 MB flash. Hashes are in
[`../../firmware/SHA256SUMS.txt`](../../firmware/SHA256SUMS.txt). Upstream firmware
is not protocol-compatible. Older S2P firmware is recognized for upgrade only.

## Validation status

No new hardware validation has occurred for this revision. Historical input and
rumble measurements in the [source overview](../README.md) do not prove the new
firmware works on hardware. Windows/Android enumeration and input, desktop
ViGEmBus/rumble/audio, unplug/sleep/remote wake, idle/reconnect, NVS migration,
chords and ROM rescue all remain pending. See the
[validation checklist](../../../docs/usb-hid-standardization.md). Local build and
automated evidence are recorded separately by the integration process. This is
Draft PR work, not a formal release or USB certification.
