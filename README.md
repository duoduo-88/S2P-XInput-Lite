# S2P-XInput-Lite

S2P-XInput-Lite converts a Switch 2 Pro Controller into an Xbox 360-compatible XInput controller on Windows. It supports an ESP32-S3 USB bridge and native Windows BLE.

> This is an unofficial community project and is not affiliated with Nintendo, Microsoft, or Espressif Systems.

## Features

- Automatic ESP32-S3 detection with native BLE fallback
- Xbox 360 virtual controller output through ViGEmBus
- Button, keyboard, mouse, and stick-direction mapping
- Per-controller stick and motion calibration
- Stick curves, deadzones, smoothing, stabilization, and output-shape adjustment
- XInput-to-HD Rumble 2 conversion and audio-reactive vibration
- Gyroscope mapping to an Xbox stick or mouse
- Traditional Chinese and English interface
- Live connection, battery, ESP32, and ViGEmBus status

Detailed setting descriptions are available from the `?` buttons in the application.

## Requirements

- Windows 10 or 11
- Switch 2 Pro Controller
- ViGEmBus 1.22.0
- Bluetooth, or a compatible ESP32-S3 bridge (optional)

The packaged release includes a portable Python runtime and all required packages. A separate Python installation is not required.

## Installation and Use

1. Install ViGEmBus with `Install-ViGEmBus.bat` if it is not already installed.
2. For native BLE, pair the controller in Windows. For ESP32, connect a device running compatible firmware.
3. Run `S2P-XInput-Lite.exe`.
4. Adjust and save settings in the GUI.
5. Start the connection program and wake the controller.

The application checks for ESP32 first and falls back to native BLE automatically. Keep the connection program running while playing.

Settings and per-controller calibration data are stored in `config.ini`.

## Notes

- Gyro or stick calibration should be performed from the GUI by following its on-screen instructions.
- Save mapping changes and restart the connection program before testing them.
- Set both LF and HF vibration strength to zero to disable vibration.
- ESP32 firmware flashing requires reconnecting or restarting the ESP32-S3 afterward.

## Limitations

- Windows only
- One controller at a time
- No Joy-Con pairing or PS5 controller emulation
- Motion is mapped to a stick or mouse; XInput does not expose raw motion sensors
- The ESP32 connection requires compatible bridge firmware

## Upstream and License

Parts of this project are based on or derived from [Switch2Connect](https://github.com/TommyWabg/Switch2Connect) by TommyWabg and have been modified and reorganized for this project.

S2P-XInput-Lite is licensed under the [GNU General Public License v3.0](LICENSE). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for third-party attribution and license information.

