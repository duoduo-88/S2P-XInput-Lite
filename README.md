# S2P-XInput-Lite

S2P-XInput-Lite converts a Switch 2 Pro Controller into an Xbox 360-compatible XInput controller on Windows. It supports wired USB, an ESP32-S3 USB bridge, and native Windows BLE.

Current release: **v0.4.0**

> This is an unofficial community project and is not affiliated with Nintendo, Microsoft, or Espressif Systems.

## Features

- Automatic wired USB priority, followed by ESP32-S3 or native BLE, with live 6-axis/9-axis sensor status
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
- A USB-C data cable, Bluetooth, or a compatible ESP32-S3 bridge

The packaged release includes a portable Python runtime and all required packages. A separate Python installation is not required.

## Installation and Use

1. Install ViGEmBus with `Install-ViGEmBus.bat` if it is not already installed.
2. For wired USB, connect the controller before opening Steam or another controller tool. For native BLE, **do not pair it through Windows Bluetooth settings**; turn Bluetooth on and let this application establish the connection. For ESP32, connect a device running compatible firmware.
3. Run `S2P-XInput-Lite.exe`.
4. Adjust and save settings in the GUI.
5. Start the connection program and wake the controller.

The application checks wired USB first, then ESP32, and finally native BLE. Keep the connection program running while playing.

For first-time native BLE pairing, start the connection program and hold the controller's **SYNC** button. A previously paired controller can normally be woken with any button.

Settings and per-controller calibration data are stored in `config.ini`.

## Notes

- Gyro or stick calibration should be performed from the GUI by following its on-screen instructions.
- Save mapping changes and restart the connection program before testing them.
- Set both LF and HF vibration strength to zero to disable vibration.
- ESP32 firmware flashing requires reconnecting or restarting the ESP32-S3 afterward.
- The status line reports 9-axis when sustained magnetometer data is available, or 6-axis when only gyro and accelerometer data are active.

## Limitations

- Windows only
- One controller at a time
- No Joy-Con pairing or PS5 controller emulation
- Motion is mapped to a stick or mouse; XInput does not expose raw motion sensors
- The ESP32 connection requires compatible bridge firmware
- The release includes the complete ESP32-S3 source corresponding to the bundled `0.12.4` firmware under `esp32s3/source/esp32s3_usb_bridge_bluedroid`
- Wired mode may require HidHide if a game sees both the physical HID and virtual XInput controller
- If wired status shows Basic mode, fully exit Steam or other controller tools, reconnect the controller, and start this application first

## Upstream and License

Parts of this project are based on or derived from [Switch2Connect](https://github.com/TommyWabg/Switch2Connect) by TommyWabg and have been modified and reorganized for this project.

S2P-XInput-Lite is licensed under the [GNU General Public License v3.0](LICENSE). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for third-party attribution and license information.
