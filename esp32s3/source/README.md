# ESP32-S3 firmware source

`esp32s3_usb_bridge_bluedroid/` is the complete source for the bundled
ESP32-S3 bridge firmware.

- Upstream: https://github.com/TommyWabg/Switch2Connect
- Base commit: `d63b044e66cfb93f8377a3596e3f00c82715b029`
- Firmware protocol version: `0.12.4`
- Lite build: `cdc_bridge_2_lowlatency`
- Target: ESP32-S3
- Verified toolchain: official ESP-IDF `v5.5.4`, performance optimization

The Lite build keeps the upstream protocol and adds a latency-focused CDC
schedule:

- newest BLE input shadow is forwarded before command and diagnostic queues;
- BLE callbacks wake the CDC task immediately instead of waiting for its 2 ms
  maintenance timeout;
- each CDC report uses one combined header/payload write;
- rumble uses direct `wr` latest-only output from the host, avoiding the legacy
  five-packet `rs` FIFO.

The current images in `../firmware/` were built from this source and verified
on real hardware. The original upstream images remain in
`../firmware/upstream_0.12.4/` for rollback.

| Current file | SHA-256 |
|---|---|
| `esp32s3_bluedroid_bridge.bin` | `F845F1E506CB14EBBEE54DFA5340D746B1817EB526AD65F8D0E605A99C2C1D64` |
| `bootloader.bin` | `80A0436E931B24118F2E93BD99D67E6AD2AD0A66C337BD60DB8AAA8C4AA026EA` |
| `partition-table.bin` | `7F00B6C042A89B15B0CAC534F82ED988CAF29278FF5700B0C511EB1B5BB7C820` |

Real-controller A/B testing improved the normal host arrival interval from
7.986/8.059 ms p50/p95 to 7.500/7.595 ms. A 200 Hz rumble stress test sent
only the newest 668 of 2,003 submitted states, reported 1,334 intentional
overwrites and zero send failures, while input remained above 132 Hz with no
dispatcher backlog or dropped analog reports.

To rebuild:

```text
cd esp32s3_usb_bridge_bluedroid
idf.py set-target esp32s3
idf.py build
```
