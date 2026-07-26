# ESP32-S3 firmware source

`esp32s3_usb_bridge_bluedroid/` is the complete source for the bundled
ESP32-S3 bridge firmware.

- Upstream: https://github.com/TommyWabg/Switch2Connect
- Base commit: `d63b044e66cfb93f8377a3596e3f00c82715b029`
- Firmware build version: `0.14.0`
- Bridge command compatibility: `0.12.4`
- Lite build: `cdc_bridge_2_lowlatency`
- Target: ESP32-S3
- Verified toolchain: official ESP-IDF `v5.5.4`, performance optimization

The Lite build keeps the upstream protocol and adds a latency-focused CDC
schedule:

- newest BLE input shadow is forwarded before command and diagnostic queues;
- BLE callbacks wake the CDC task immediately instead of waiting for its 2 ms
  maintenance timeout;
- BLE connection setup has a watchdog, checked scan transitions and complete
  failure recovery;
- channels become ready only after the input CCCD write succeeds, and queued
  input, ACK and rumble packets are generation-scoped across reconnects;
- each CDC report uses one combined header/payload write;
- rumble uses direct `wr` latest-only output from the host, avoiding the legacy
  five-packet `rs` FIFO.

The current images in `../firmware/` were built from this source and verified
on real hardware. The exact upstream revision is linked above for comparison.

| Current file | SHA-256 |
|---|---|
| `esp32s3_bluedroid_bridge.bin` | `92E8C914E8A49381F00F53AA37D94B93EA3379668508F8E8255F667BAA68BD77` |
| `bootloader.bin` | `9F1BE89EECD1C24A562C0C570894F6F625405041508E55A2B1AA3875B74D237A` |
| `partition-table.bin` | `7F00B6C042A89B15B0CAC534F82ED988CAF29278FF5700B0C511EB1B5BB7C820` |

Real-controller A/B testing improved the normal host arrival interval from
7.986/8.059 ms p50/p95 to 7.500/7.595 ms. A 200 Hz rumble stress test sent
only the newest 668 of 2,003 submitted states, reported 1,334 intentional
overwrites and zero send failures, while input remained above 132 Hz with no
dispatcher backlog or dropped analog reports.

The development standalone firmware also provides mutually exclusive USB
output personalities: XInput-compatible output for PCs and a standards-based HID
Gamepad for direct USB connection to phones. The HID path reuses the same
calibration, curves, mappings and gyro runtime as XInput. Host-side hardware
validation measured 131 Hz input with 7.68/9.01 ms p50/p95 intervals. Both
standalone personalities play the same two-pulse ready cue as the desktop
application after the controller's full initialization sequence is acknowledged.
Mobile HID triggers are exposed as both analog axes and digital L2/R2 buttons
for compatibility with games that consume only one representation.

The firmware is an independent interoperability implementation and is not
licensed, certified, approved, or manufactured by Microsoft, Nintendo, or
Espressif Systems. Product and interface names are used only to describe
compatibility.

To rebuild:

```text
cd esp32s3_usb_bridge_bluedroid
idf.py set-target esp32s3
idf.py build
```
