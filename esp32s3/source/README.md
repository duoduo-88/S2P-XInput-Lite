# ESP32-S3 firmware source

`esp32s3_usb_bridge_bluedroid/` contains the complete bundled firmware source.

- Product/version: `S2P-FW 1.0.7`
- Protocol: `s2p_bridge 1.0.0`; firmware profile: `s2p_usb_bridge`
- Build: `standalone_diagnostics`; target: ESP32-S3 with 16 MB flash
- Locked toolchain: ESP-IDF `5.5.4`, esp_tinyusb `1.7.6~2`, tinyusb `0.19.0~3`
- Derived from [Switch2Connect](https://github.com/TommyWabg/Switch2Connect)
- Historical base commit: `d63b044e66cfb93f8377a3596e3f00c82715b029`

S2P-FW is an independent firmware line, not protocol-compatible with upstream
Switch2Connect. The historical source reference records provenance.

## USB modes and runtime

Bridge exposes CDC as `303A:4001`, product `S2P ESP32 Bridge`. Android standalone
exposes CDC + standard HID as `303A:4005`, product `S2P Mobile Gamepad`.
These are Espressif/TinyUSB shared defaults, not dedicated PID allocations or
USB certification. Physical USB XInput, XUSB20 and Auto probing are removed.
Desktop Windows XInput through ViGEmBus, mapping, rumble and audio haptics remain
on the Bridge path. HID provides input only; Android rumble is not promised.

The shared runtime preserves calibration, stick curves/deadzones, mapping layers,
gyro-to-stick, battery LEDs, idle disconnect and reconnect. Bounded button/trigger
edge queues coexist with newest-state stick/IMU samples. CDC control/lifecycle
queues retain priority over scan/debug traffic. Host rumble uses latest-only
`wr` output, with one GATT write in flight and one pending state.

Bridge retains its explicitly established host lease: 8 seconds without a host
heartbeat or 3 seconds of continuous CDC loss starts sequential BLE teardown.
HID never arms this lease and retains independent idle/reconnect behavior.
See [mode migration, recovery and hardware checklist](../../docs/usb-hid-standardization.md).

## Build and bundle verification

```text
cd esp32s3_usb_bridge_bluedroid
idf.py set-target esp32s3
idf.py build
```

Use ESP-IDF 5.5.4 and the committed dependency lock/configuration. Release builds
enable the reproducible-build option; matching source, configuration and locked
toolchain should reproduce the images. The authoritative bundled image hashes
are in [`../firmware/SHA256SUMS.txt`](../firmware/SHA256SUMS.txt). The firmware
build script checks all three images against the bundle. The optional full-build
test uses `S2P_RUN_IDF_BUILD=1`; set `S2P_VERIFY_RELEASE_IMAGE=1` after synchronizing
all three bundle files from that same build.

## Historical hardware baseline

Earlier firmware A/B tests measured host input p50/p95 improving from
7.986/8.059 ms to 7.500/7.595 ms. A 200 Hz rumble stress run sent the newest
668 of 2,003 submitted states, intentionally overwrote 1,334, and reported zero
send failures while input remained above 132 Hz. Earlier HID input measured
131 Hz with 7.68/9.01 ms p50/p95 intervals.

These are historical results, not evidence for S2P-FW 1.0.7, its shared USB IDs,
mode migration or descriptors. No new hardware validation has occurred. Every
hardware check in the linked checklist remains pending. This revision is prepared
for a Draft PR and local verification, not a formal release.
