# S2P-XInput-Lite ESP32-S3 firmware

This directory contains the firmware images and complete corresponding source
used by S2P-XInput-Lite v0.6.0 development builds.

- `firmware/`: current flash images used by the desktop application
- `source/`: source, build instructions, checksums, and upstream revision
- `source/esp32s3_usb_bridge_bluedroid/`: ESP-IDF project

The current `0.14.0-dev` firmware retains bridge compatibility and adds
profile-backed standalone USB output:

- XInput-compatible output for Windows PCs
- standards-based USB HID Gamepad output for phones and other compatible hosts
- controller-side calibration, stick processing, mappings, gyro-to-stick, and
  rumble processing
- validated profile transfer with CRC32 and A/B storage slots

The firmware is an independent interoperability implementation and is not
licensed, certified, approved, or manufactured by Microsoft, Nintendo, or
Espressif Systems. Product and interface names are used only to describe
compatibility. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for
attribution and license details.
