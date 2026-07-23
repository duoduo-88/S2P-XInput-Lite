# S2P-XInput-Lite ESP32-S3 BLE Bridge

This directory contains the source for bundled firmware `0.12.4`. It targets an ESP32-S3 and uses a USB CDC connection to bridge controller input and rumble between the Windows host and BLE.

The firmware is an independent interoperability implementation and is not
licensed, certified, approved, or manufactured by Microsoft, Nintendo, or
Espressif Systems. Product and interface names are used only to describe
compatibility.

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

The Windows application invokes `tools/esptool.exe` with DIO mode, an 80 MHz flash frequency, and a 16 MB flash size. Existing devices already running firmware `0.12.4` do not need to be flashed again for S2P-XInput-Lite v0.5.2.

