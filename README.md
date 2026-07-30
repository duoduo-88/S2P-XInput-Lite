# S2P-XInput-Lite

[繁體中文](README_zh-TW.md) · [User Guide](manual/USER_GUIDE.md) · [繁體中文使用手冊](manual/USER_GUIDE_zh-TW.md)

![S2P-XInput-Lite banner](image/S2P-XInput-Lite-banner.jpg)

<p align="center">
  <img src="image/GUI.gif" alt="S2P-XInput-Lite settings interface" height="360">
  <img src="image/test.gif" alt="S2P-XInput-Lite gamepad tester" height="360">
</p>

S2P-XInput-Lite provides XInput-compatible controller output for a Switch 2 Pro Controller on Windows. It supports wired USB, an ESP32-S3 USB bridge, and native Windows BLE.

Current version: **v0.7.2**

[v0.7.2 release notes](RELEASE_NOTES_v0.7.2.md) ·
[Source](https://github.com/duoduo-88/S2P-XInput-Lite/tree/main) ·
[Latest published release](https://github.com/duoduo-88/S2P-XInput-Lite/releases/latest)

> This is an independent, unofficial community project. It is not affiliated with, endorsed by, sponsored by, or certified by Nintendo, Microsoft, Espressif Systems, Apple, or Google.
>
> Nintendo Switch is a trademark of Nintendo. Windows and Xbox are trademarks of Microsoft. ESP32 is a trademark of Espressif Systems. Apple, macOS, iOS, and iPadOS are trademarks of Apple Inc. Android is a trademark of Google LLC. All other trademarks are the property of their respective owners.

## Features

- Automatic wired USB priority, followed by ESP32-S3 or native BLE, with live 6-axis/9-axis sensor status
- XInput-compatible virtual controller output through ViGEmBus
- Low-latency input dispatch that preserves button edges while using the newest stick and motion report
- Button, keyboard, mouse, stick-direction, linear-trigger, and linear-scroll mapping with shared target validation
- Global mapping-layer files with hold/toggle activation, per-layer button and stick overrides, import/export, and per-profile enabled/order state
- Per-controller stick calibration and connection-time gyro zero-bias initialization
- Stick curves, deadzones, smoothing, stabilization, and output-shape adjustment
- Consistent XInput-to-HD Rumble 2 conversion across USB, ESP32, and native BLE, with latest-only pacing, priority stop frames, and audio-reactive vibration
- ESP32 standalone profile writing for PC XInput-compatible output or
  standards-based mobile USB HID without keeping the Windows application open
- Gyroscope mapping to an XInput stick or mouse
- Independently launchable gamepad tester designed primarily for a Switch 2
  Pro Controller connected and processed by S2P-XInput-Lite, where it can show
  complete physical/processed input, mapping, sensor, transport, and ESP32
  diagnostics. Other XInput, WinMM, and Raw HID controllers can also be
  tested, but S2P-specific telemetry is unavailable, so some information will
  be missing. The report-rate page still shows report count, P50/P95/P99
  intervals, distribution statistics, and a timeline.
- Built-in ESP32 diagnostics with timed tests, a clear pass/warn/fail verdict,
  and exportable text reports
- Startup update notifications plus a manual check and shared automatic-check
  toggle on the Gamepad Tester About page. Updates always open the official
  GitHub Release page for manual download; the application never downloads,
  runs, or replaces files automatically.
- About page with project links, the software license, and third-party notices
- Full game profiles for switching stick, gyro, rumble, audio-haptics, and mapping settings together, with automatic UI refresh
- Traditional Chinese and English interface
- Live connection, battery, ESP32, ViGEmBus, WASAPI, and HidHide status

Detailed setting descriptions are available from the `?` buttons in the application.

## Requirements

- Windows 10 or 11
- Switch 2 Pro Controller
- ViGEmBus 1.22.0
- Optional for wired USB: the current [HidHide release](https://github.com/nefarius/HidHide/releases/latest), used to hide the physical HID from games
- A USB-C data cable, Bluetooth, or a compatible ESP32-S3 N16R8 bridge

The packaged release includes a portable Python runtime and all required packages. A separate Python installation is not required.

## Installation and Use

1. Install ViGEmBus with `driver\Install-ViGEmBus.bat` if it is not already installed.
2. For wired USB, install HidHide if you want games to see only the virtual XInput controller. HidHide is optional for ESP32 and native BLE.
3. Connect the controller before opening Steam or another controller tool. For native BLE, **do not pair it through Windows Bluetooth settings**; turn Bluetooth on and let this application establish the connection. For ESP32, connect a device running compatible firmware.
4. Run `S2P-XInput-Lite.exe`.
5. Choose a game profile or adjust settings, then use **Save Profile** or **Save New Profile**.
6. Saving the active profile updates its stored settings. Switching profiles while connected automatically stops the connection program, applies the selected profile, refreshes the GUI, and reconnects. Use **Restart** only after changing the connection method, device, or serial settings.

The application checks wired USB first, then ESP32, and finally native BLE. Keep the connection program running while playing.

If HidHide is missing, the application offers to open its official download page. Declining suppresses the reminder on later launches and does not block the connection; click **HidHide: Missing** at the bottom of the window to open the download page later. When a wired controller is detected and HidHide is installed but not configured, the application asks before adding the portable `runtime\python.exe`, `src\raw_hid_probe.exe`, and the selected physical Nintendo HID to HidHide. Declining also suppresses later reminders; click **HidHide: Off/Setup** to reopen the setup confirmation. Accepting may enable global cloaking; existing HidHide entries belonging to other applications are preserved. **Restore Defaults** removes these two application entries and the selected controller from HidHide while preserving unrelated entries; reconnect the USB controller first if it is currently hidden.

For first-time native BLE pairing, start the connection program and hold the controller's **SYNC** button. A previously paired controller can normally be woken with any button.

Settings and per-controller stick calibration are stored in `src/config.ini`. Gyro zero-bias is initialized again after every controller connection and is not stored as permanent calibration. A missing file is created automatically from `src/profiles/System Default.ini`, and missing keys in an older file are added without overwriting existing values. Default and restore operations also use `System Default.ini` as their baseline. Keep this file in every release package; `config.ini` may be omitted from a clean distribution.

The first launch provides General, Audio, FPS-COMP, FPS-IMM, Racing, Action, and Rhythm profiles. Selecting a profile refreshes the GUI. When the connection program is running, it is stopped and reconnected automatically so the newly selected profile is loaded completely. Built-in profiles start from canonical button and stick-direction mappings. Custom profiles can be saved, renamed, or deleted. **System Default** is a protected read-only baseline and always appears at the bottom of the list. **Restore Defaults** resets the current controls without deleting saved profiles or calibration.

Mapping Layer files are global and can temporarily override the active profile while a button chord is held or toggled. A Layer can remap buttons and stick directions, swap sticks, use a stick as a mouse, or produce linear XInput trigger, stick-direction, or scroll output. Layer files are stored under `src/layers` and can be imported or exported. Each game profile stores which Layers are enabled and their priority order; switching profiles changes that state without deleting or replacing the Layer files. Only the highest-priority matching Layer is active: held Layers take priority over toggled Layers, then the editor order is used.

## Verified Low-Latency Paths

- Wired USB input: 250 Hz, 4 ms report interval, zero queue/drop in concurrent rumble stress testing.
- ESP32 input: about 132 Hz; the low-latency firmware reduced normal host arrival p50/p95 from 7.986/8.059 ms to 7.500/7.595 ms.
- Native Windows BLE: 66.7 Hz / 15 ms, which matches WinRT's minimum `throughput_optimized` interval.
- Rumble is latest-only on every transport. Game changes and zero frames use 7.5 ms priority pacing on ESP32 and wired USB (about 8 ms measured), while native BLE uses 15 ms. Wired audio/Mix updates use 16.6 ms (about 60 Hz), and ordinary refreshes use 15 ms.

Reusable hardware probes and automated tests are maintained in the development repository.

## Notes

- Perform stick calibration from the GUI by following its on-screen instructions. After each connection, keep the controller still for about 0.5 seconds; gyro output begins after at least 16 stable samples and zero-bias sampling continues up to 64 samples.
- Save or apply mapping changes before testing them; invalid or damaged mapping data is reported and is not silently applied.
- Each HD Rumble 2 vibration frame packs a 9-bit LF frequency, 10-bit LF amplitude, 9-bit HF frequency, and 10-bit HF amplitude. Frequency values range from `0` to `511` and can be interpreted approximately in Hz; actuator and enclosure response still affect the result.
- Testing with multiple bridge implementations and controllers found that higher vibration amplitudes make a light mechanical tapping sound more likely when the output changes rapidly. The effect is usually minor during normal use.
- The configurable amplitude range of `0` to `1023` is the protocol field range, not a published, certified safe limit for continuous hardware output. The system default maximum amplitude is `800`, about 78% of the field maximum (approximately 80%), to retain strong feedback while reducing the chance of excessive-output noise. Adjust it for the individual controller and personal preference, and lower it if tapping or other unusual sounds occur.
- Set both LF and HF amplitudes to zero to stop vibration. Connection and PIN feedback use the same two short pulses as v0.5.1 (LF `225`, HF `481`, amplitude `800`) on every transport. HF `481` is retained only for this established cue and is not a recommended general-game frequency.
- ESP32 firmware flashing requires reconnecting or restarting the ESP32-S3 afterward.
- The status line reports 9-axis when sustained magnetometer data is available, or 6-axis when only gyro and accelerometer data are active.
- If HidHide setup reports an error, close HidHide Configuration Client and retry. Restart Steam or the game after changing hiding, and reconnect USB if the physical controller remains visible.

## Limitations

- Windows only
- One controller at a time
- No Joy-Con pairing or PS5 controller emulation
- The ESP32-S3 standalone USB HID mode is not currently compatible with
  macOS, iOS, or iPadOS.
- Mobile use is currently limited to Android devices that support USB OTG and
  correctly recognize this HID game-controller mode. Compatibility varies by
  device, Android version, and game, so plug-and-play operation cannot be
  guaranteed on every device.
- Mobile mode supports controller input only; game rumble feedback is not
  supported. Apple-device compatibility and mobile rumble support are future
  development directions.
- ESP32 standalone mode does not support Windows keyboard/mouse output, audio
  haptics, process-based profile switching, phone rumble, or BLE HID output
- Motion is mapped to a stick or mouse; XInput does not expose raw motion sensors
- The ESP32 connection requires compatible firmware. Standalone profile
  writing, direct USB controller output, and the diagnostic page require the
  bundled `S2P-FW 1.0.1`; older S2P builds and upstream firmware cannot provide
  the complete `s2p_bridge 1.0.0` feature set.
- The GitHub repository includes the complete ESP32 source. Packaged releases
  contain only the flashing tool and required firmware images under `esp32s3`.
- HidHide is not bundled and is not required for ESP32 or BLE. Without it, wired input still works, but games may detect both the physical HID and virtual XInput controller.
- If wired status shows Basic mode, fully exit Steam or other controller tools, reconnect the controller, and start this application first
- Wired USB always requests the controller's complete sensor report; the 6-axis/9-axis label reflects the data actually received rather than a selectable polling mode

## Upstream and License

Parts of this project are based on or derived from [Switch2Connect](https://github.com/TommyWabg/Switch2Connect) by TommyWabg and have been modified and reorganized for this project.

S2P-XInput-Lite is licensed under the [GNU General Public License v3.0](LICENSE). See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for third-party attribution and license information.

## Compatibility and Trademarks

Product names and trademarks are used only to identify compatible hardware,
software, and interfaces. “XInput-compatible” describes interoperability with
software that accepts XInput controller input; it does not mean that this
project or its ESP32 firmware is licensed, certified, approved, or manufactured
by Microsoft.

The ESP32 development firmware uses a non-retail development VID/PID. It does
not claim the USB identity of a retail Microsoft or Xbox controller. That
identifier is intended for development and testing, is not a formally assigned
production identifier, and may conflict with other development devices that
use the same value.
