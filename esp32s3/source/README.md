# ESP32-S3 firmware source

The complete source corresponding to the bundled Bluedroid bridge firmware is
included in `esp32s3_usb_bridge_bluedroid/`.

- Upstream: https://github.com/TommyWabg/Switch2Connect
- Commit: `d63b044e66cfb93f8377a3596e3f00c82715b029`
- Firmware version: `0.12.4`
- Target: ESP32-S3
- Framework: ESP-IDF

The bundled files were copied without modification from:

`ESP32-S3 firmware/esp32s3_usb_bridge_bluedroid`

The application binary and boot files were copied without modification from
the same commit's `firmware_bin` directory. Verified SHA-256 values:

| File | SHA-256 |
|---|---|
| `esp32s3_bluedroid_bridge.bin` | `859F32988A2EBEE793626B732B7A00AD661742EEE4FA18E36BD98DCFE1627BB7` |
| `bootloader.bin` | `F8835057B41828302C3C8B9B92E07C7773DF9FF978FF6869D4058824EBF2EE97` |
| `partition-table.bin` | `7F00B6C042A89B15B0CAC534F82ED988CAF29278FF5700B0C511EB1B5BB7C820` |

To build with a compatible ESP-IDF environment:

```text
cd esp32s3_usb_bridge_bluedroid
idf.py set-target esp32s3
idf.py build
```
