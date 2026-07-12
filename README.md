# S2P-XInput-Lite

S2P-XInput-Lite is a lightweight Windows controller interoperability project that bridges a Switch 2 Pro Controller to an Xbox 360-compatible XInput virtual controller through an ESP32-S3 USB CDC bridge.

The project provides controller input conversion, configurable button and stick-direction mapping, analog stick calibration, and XInput rumble conversion to HD Rumble 2.

> S2P-XInput-Lite is an unofficial community project and is not affiliated with, endorsed by, sponsored by, or officially associated with Nintendo, Microsoft, Espressif Systems, or any other third party.

## Project Scope

This release is intentionally focused on a lightweight **ESP32-S3 bridge → XInput output** workflow.

S2P-XInput-Lite uses an ESP32-S3 as the controller communication bridge and outputs a standard XInput-compatible virtual Xbox 360 controller on Windows. The project then provides its own configurable controls and processing on top of the XInput-based workflow, including button mapping, stick-direction mapping, analog stick calibration, and custom vibration conversion and tuning.

Because the output is based on XInput, this version does **not** provide gyroscope or accelerometer motion input through the virtual controller.

If you need broader controller features or a more feature-complete implementation, please refer to the upstream **Switch2Connect** project by TommyWabg:

https://github.com/TommyWabg/Switch2Connect


## Features

- Switch 2 Pro Controller input through an ESP32-S3 USB CDC bridge
- Automatic detection of a compatible ESP32 serial device
- Virtual Xbox 360 controller output through `vgamepad` / ViGEmBus
- Button and D-pad mapping
- Left and right analog stick input
- Custom analog stick calibration
- Configurable left and right stick-direction mapping
- Keyboard key mapping
- XInput rumble conversion to HD Rumble 2
- Independent LF and HF vibration curves
- Independent LF and HF vibration strength
- LF → HF and HF → LF vibration compensation
- Configurable vibration frequencies and maximum amplitude
- Graphical configuration interface
- ViGEmBus installation status display
- Connection program launch and restart controls
- Pin function for locating the connected controller with a vibration alert
- Player 1 LED setup after connection
- Settings stored in `config.ini`

## Current Limitations

- One controller at a time
- Windows only
- Requires a compatible ESP32-S3 bridge firmware
- Gyroscope and accelerometer motion input is not supported in this XInput-focused version
- No Joy-Con pairing support
- No PS5 controller emulation
- No audio haptics

## Requirements

-   Windows

### Hardware

- Switch 2 Pro Controller
- Compatible ESP32-S3 device running the required USB CDC bridge firmware
- Windows PC

### Required Driver

- **ViGEmBus 1.22.0 (`ViGEmBus_1.22.0_x64_x86_arm64`)**

ViGEmBus is required for the virtual Xbox 360 / XInput controller output.

If ViGEmBus is not already installed, install `ViGEmBus_1.22.0_x64_x86_arm64` before using S2P-XInput-Lite.

> **Important:** ViGEmBus is a third-party system driver and is not part of S2P-XInput-Lite itself. Administrator privileges may be required during driver installation. After installation, restart Windows if requested by the installer.

The S2P-XInput-Lite configuration GUI includes a ViGEmBus installation status check.

### Packaged Release

The packaged release includes its own portable Python runtime and required Python packages.

You do **not** need to:

- Install Python separately
- Run `pip install`
- Manually install `pyserial` or `vgamepad`

## Installation

1. Install **ViGEmBus 1.22.0 (`ViGEmBus_1.22.0_x64_x86_arm64`)** if ViGEmBus is not already installed.
2. Connect the ESP32-S3 to the PC.
3. Make sure the ESP32-S3 is running the compatible bridge firmware.
4. Launch `S2P-XInput-Lite.exe`.
5. Use the configuration GUI to configure the controller and start the connection program.

The application automatically searches for a compatible ESP32 device, so manual COM port configuration is normally not required.

## Basic Usage

1. Open the configuration GUI.
2. Configure button mapping, stick-direction mapping, and vibration settings as needed.
3. Save the configuration.
4. Start or restart the connection program from the GUI.
5. Press any button on the controller to wake it and wait for the connection to complete.
6. Keep the connection program window open while using the controller.

When the controller connects successfully, the program sets the Player 1 LED and plays a short vibration notification.

## Configuration

Settings are stored in:

```text
config.ini
```

The GUI is the recommended way to edit these settings.

### Button Mapping

Controller buttons can be mapped to Xbox / XInput buttons or keyboard keys.

### Stick Direction Mapping

The left and right stick directions can be configured independently, including cardinal and diagonal directions.

### Stick Calibration

Use the included calibration function if the stick center or range is inaccurate.

Calibration data is stored in `config.ini` and used automatically by the main program.

For best results, close the active controller connection program before starting calibration.

## Vibration

The project converts Xbox XInput rumble into Switch 2 Pro Controller HD Rumble 2 vibration data.

Available vibration settings include:

- LF frequency
- HF frequency
- LF strength
- HF strength
- LF response curve
- HF response curve
- Maximum amplitude
- LF → HF compensation
- HF → LF compensation

The GUI also includes a **Pin** function that sends a short vibration notification to the currently connected controller.

## ESP32 Communication

The bridge communicates with the ESP32-S3 over USB CDC serial at:

```text
2,000,000 baud
```

The program automatically identifies compatible firmware using the bridge status response.

## Upstream Project and Acknowledgements

Parts of S2P-XInput-Lite are based on or derived from code from **Switch2Connect** by TommyWabg. These portions have been substantially reduced, modified, and reorganized for the purposes of this project.

The `esp32s3.zip` file distributed with this project is provided directly from Switch2Connect and has not been modified by S2P-XInput-Lite.

Special thanks to TommyWabg and the contributors to Switch2Connect for their open-source work.

For detailed information about third-party software and upstream sources, see `THIRD_PARTY_NOTICES.md`.

## License

S2P-XInput-Lite is licensed under the **GNU General Public License v3.0 (GPL-3.0)**.

You may use, study, modify, and redistribute this project under the terms of the GPL-3.0.

This project contains code derived from upstream open-source software and third-party components distributed alongside the project. Third-party components remain subject to their respective copyright and license terms.

See the `LICENSE` file in the project root for the full license text.

## Trademark Notice

S2P-XInput-Lite is an unofficial controller interoperability project and is not affiliated with, endorsed by, sponsored by, or officially associated with Nintendo, Microsoft, Espressif Systems, or any other third party.

Nintendo, Nintendo Switch, Switch 2, and related names and trademarks are the property of their respective owners. Xbox, Microsoft, and other related names and trademarks are also the property of their respective owners.

Third-party product and project names are mentioned solely for compatibility, interoperability, software attribution, and open-source acknowledgement purposes.
