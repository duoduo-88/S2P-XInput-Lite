# ESP32-S3 firmware source

`esp32s3_usb_bridge_bluedroid/` is the complete source for the bundled
ESP32-S3 bridge firmware.

- Product: `S2P-FW`
- Firmware version: `1.0.2`
- S2P bridge protocol: `1.0.0`
- Firmware profile: `s2p_usb_bridge`
- Build: `standalone_diagnostics`
- Derived from: https://github.com/TommyWabg/Switch2Connect
- Historical base commit: `d63b044e66cfb93f8377a3596e3f00c82715b029`
- Target: ESP32-S3
- Verified toolchain: official ESP-IDF `v5.5.4`, performance optimization

S2P-FW is an independent firmware line. It does not claim runtime or command
compatibility with upstream Switch2Connect firmware. The historical source
and commit above are retained for attribution and code provenance.

The S2P bridge protocol uses a latency-focused CDC schedule:

- newest BLE input shadow is forwarded before command and diagnostic queues;
- BLE callbacks wake the CDC task immediately instead of waiting for its 2 ms
  maintenance timeout;
- BLE connection setup has a watchdog, checked scan transitions and complete
  failure recovery;
- channels become ready only after the input CCCD write succeeds, and queued
  input, ACK and rumble packets are generation-scoped across reconnects;
- each CDC report uses one combined header/payload write;
- host control commands and lifecycle events use dedicated priority queues, so
  scan/debug floods cannot delay connection control or discard connection state;
- rumble uses direct `wr` latest-only output from the host, avoiding the legacy
  five-packet `rs` FIFO;
- each controller has at most one GATT rumble write in flight and one latest
  pending state; congestion, queue-full and write completion are counted, and
  only the latest state is retried after the stack becomes writable;
- standalone XInput OUT rumble wakes the output task immediately instead of
  waiting for the 2 ms maintenance timeout.
- standalone mode derives the same four battery levels as the desktop client
  from controller voltage reports and shows them on the four player LEDs;
  hysteresis prevents motor-load voltage sag from making the LEDs flicker.

The current images in `../firmware/` were built from this source. The existing
transport and reconnect baseline was verified on real hardware; the
`S2P-FW 1.0.2` battery-LED build has passed compilation and automated contract
tests, but its new standalone LED behavior still requires a post-flash hardware
check. The exact upstream revision is linked above for comparison.

| Current file | SHA-256 |
|---|---|
| `esp32s3_bluedroid_bridge.bin` | `3A5623604D865D3E7D3233B5CD8C9477FCA54F31548341A54A83BF7C58EAF71F` |
| `bootloader.bin` | `0674EE7D6721269BFF482811B4441F6A85D6F590AFDE9F7F71F6E7DB39C68E94` |
| `partition-table.bin` | `7F00B6C042A89B15B0CAC534F82ED988CAF29278FF5700B0C511EB1B5BB7C820` |

Real-controller A/B testing improved the normal host arrival interval from
7.986/8.059 ms p50/p95 to 7.500/7.595 ms. A 200 Hz rumble stress test sent
only the newest 668 of 2,003 submitted states, reported 1,334 intentional
overwrites and zero send failures, while input remained above 132 Hz with no
dispatcher backlog or dropped analog reports.

The standalone firmware also provides mutually exclusive USB
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

The optional full-build test can also verify that a controlled release
packaging run copied the exact bootloader, partition table, and application
images into `esp32s3/firmware`. Set both `S2P_RUN_IDF_BUILD=1` and
`S2P_VERIFY_RELEASE_IMAGE=1` only after synchronizing those outputs from the
same build. Normal CI does not enable the byte comparison because ESP-IDF
currently embeds the compile date in the application image.
