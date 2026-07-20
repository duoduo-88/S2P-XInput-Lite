# S2P-XInput-Lite

S2P-XInput-Lite converts a Switch 2 Pro Controller into an Xbox 360-compatible XInput controller on Windows. It supports wired USB, an ESP32-S3 USB bridge, and native Windows BLE.

Current release: **v0.5.0**  
[Release notes](RELEASE_NOTES_v0.5.0.md)

> This is an unofficial community project and is not affiliated with Nintendo, Microsoft, or Espressif Systems.

## Features

- Automatic wired USB priority, followed by ESP32-S3 or native BLE, with live 6-axis/9-axis sensor status
- Xbox 360 virtual controller output through ViGEmBus
- Low-latency input dispatch that preserves button edges while using the newest stick and motion report
- Button, keyboard, mouse, and stick-direction mapping
- Global mapping layers with hold/toggle activation, per-layer button and stick overrides, ordering, and import/export
- Per-controller stick and motion calibration
- Stick curves, deadzones, smoothing, stabilization, and output-shape adjustment
- Consistent XInput-to-HD Rumble 2 conversion across USB, ESP32, and native BLE, with latest-only pacing, priority stop frames, and audio-reactive vibration
- Gyroscope mapping to an Xbox stick or mouse
- Full game profiles for switching stick, gyro, rumble, audio-haptics, and mapping settings together, with automatic UI refresh
- Traditional Chinese and English interface
- Live connection, battery, ESP32, ViGEmBus, WASAPI, and HidHide status

Detailed setting descriptions are available from the `?` buttons in the application.

## Requirements

- Windows 10 or 11
- Switch 2 Pro Controller
- ViGEmBus 1.22.0
- Optional for wired USB: the current [HidHide release](https://github.com/nefarius/HidHide/releases/latest), used to hide the physical HID from games
- A USB-C data cable, Bluetooth, or a compatible ESP32-S3 bridge

The packaged release includes a portable Python runtime and all required packages. A separate Python installation is not required.

## Installation and Use

1. Install ViGEmBus with `driver\Install-ViGEmBus.bat` if it is not already installed.
2. For wired USB, install HidHide if you want games to see only the virtual Xbox controller. HidHide is optional for ESP32 and native BLE.
3. Connect the controller before opening Steam or another controller tool. For native BLE, **do not pair it through Windows Bluetooth settings**; turn Bluetooth on and let this application establish the connection. For ESP32, connect a device running compatible firmware.
4. Run `S2P-XInput-Lite.exe`.
5. Choose a game profile or adjust settings, then use **Save Profile** or **Save New Profile**.
6. Saving or switching a profile applies gameplay settings immediately while connected. Use **Restart** only after changing the connection method, device, or serial settings.

The application checks wired USB first, then ESP32, and finally native BLE. Keep the connection program running while playing.

If HidHide is missing, the application offers to open its official download page; skipping does not block the connection. When a wired controller is detected and HidHide is installed but not configured, the application asks before adding the portable `runtime\python.exe` and the selected physical Nintendo HID to HidHide. Accepting may enable global cloaking; existing HidHide entries belonging to other applications are preserved. **Restore Defaults** removes this application and the selected controller from HidHide while preserving unrelated entries; reconnect the USB controller first if it is currently hidden.

For first-time native BLE pairing, start the connection program and hold the controller's **SYNC** button. A previously paired controller can normally be woken with any button.

Settings and per-controller calibration are stored in `src/config.ini`. A missing file is created automatically from `src/profiles/System Default.ini`, and missing keys in an older file are added without overwriting existing values. Keep `System Default.ini` in every release package; `config.ini` may be omitted from a clean distribution.

The first launch provides General, Audio, FPS-COMP, FPS-IMM, Racing, Action, and Rhythm profiles. Selecting a profile refreshes the GUI and applies its saved settings to a running connection without reconnecting. Built-in profiles start from canonical button and stick-direction mappings. Custom profiles can be saved, renamed, or deleted. **System Default** is a protected read-only baseline and always appears at the bottom of the list. **Restore Defaults** resets the current controls without deleting saved profiles or calibration.

Mapping layers are global and can temporarily override the active profile while a button chord is held or toggled. A layer can remap buttons and stick directions, swap sticks, use a stick as a mouse, or produce linear XInput trigger/stick output. Layer priority follows the order shown in the editor. Layer files are stored under `src/layers` and can be imported or exported.

## Verified Low-Latency Paths

- Wired USB input: 250 Hz, 4 ms report interval, zero queue/drop in concurrent rumble stress testing.
- ESP32 input: about 132 Hz; the low-latency firmware reduced normal host arrival p50/p95 from 7.986/8.059 ms to 7.500/7.595 ms.
- Native Windows BLE: 66.7 Hz / 15 ms, which matches WinRT's minimum `throughput_optimized` interval.
- Rumble is latest-only on every transport. Verified priority pacing is about 8 ms on ESP32 and wired USB, 15 ms on native BLE, while wired audio/Mix updates use 25 ms.

Reusable hardware probes and automated tests are maintained in the development repository.

## Notes

- Gyro or stick calibration should be performed from the GUI by following its on-screen instructions.
- Save mapping changes before testing them; a running connection applies the saved settings automatically.
- Set both LF and HF vibration strength to zero to disable vibration.
- The default HD Rumble 2 commands are LF `225` and HF `481`; connection and Pin feedback use the same two-pulse pattern on every transport.
- ESP32 firmware flashing requires reconnecting or restarting the ESP32-S3 afterward.
- The status line reports 9-axis when sustained magnetometer data is available, or 6-axis when only gyro and accelerometer data are active.
- If HidHide setup reports an error, close HidHide Configuration Client and retry. Restart Steam or the game after changing hiding, and reconnect USB if the physical controller remains visible.

## Limitations

- Windows only
- One controller at a time
- No Joy-Con pairing or PS5 controller emulation
- Motion is mapped to a stick or mouse; XInput does not expose raw motion sensors
- The ESP32 connection requires compatible bridge firmware
- The release includes the complete ESP32-S3 source for the bundled `0.12.4` protocol / `cdc_bridge_2_lowlatency` build under `esp32s3/source/esp32s3_usb_bridge_bluedroid`
- HidHide is not bundled and is not required for ESP32 or BLE. Without it, wired input still works, but games may detect both the physical HID and virtual XInput controller.
- If wired status shows Basic mode, fully exit Steam or other controller tools, reconnect the controller, and start this application first
- Wired USB always requests the controller's complete sensor report; the 6-axis/9-axis label reflects the data actually received rather than a selectable polling mode

## Upstream and License

Parts of this project are based on or derived from [Switch2Connect](https://github.com/TommyWabg/Switch2Connect) by TommyWabg and have been modified and reorganized for this project.

S2P-XInput-Lite is licensed under the [GNU General Public License v3.0](LICENSE). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for third-party attribution and license information.
