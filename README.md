# S2P-XInput-Lite

S2P-XInput-Lite is a lightweight Windows controller interoperability project that connects a Switch 2 Pro Controller to an Xbox 360-compatible XInput virtual controller through an ESP32-S3 USB CDC bridge.

The project provides controller input conversion, configurable button and stick-direction mapping, analog stick calibration and tuning, keyboard mapping, and XInput rumble conversion to HD Rumble 2.

> S2P-XInput-Lite is an unofficial community project and is not affiliated with, endorsed by, sponsored by, or officially associated with Nintendo, Microsoft, Espressif Systems, or any other third party.

## Project Scope

This project is intentionally focused on a lightweight:

**Switch 2 Pro Controller → ESP32-S3 bridge → Windows → XInput virtual controller**

workflow.

The ESP32-S3 handles communication with the controller and forwards controller data to the Windows application through USB CDC serial. S2P-XInput-Lite then processes the controller input and outputs a standard Xbox 360-compatible XInput virtual controller.

On top of the basic XInput workflow, the project provides configurable input processing and mapping features, including:

* Button mapping
* Stick-direction mapping
* Keyboard key mapping
* Analog stick calibration
* Inner and outer deadzone adjustment
* Stick response curve adjustment
* Stick input debounce / jitter filtering
* Custom vibration conversion and tuning

Because the virtual controller output is based on XInput, this version does **not** expose gyroscope or accelerometer motion input through the virtual controller.

If you need broader controller features or a more feature-complete implementation, please refer to the upstream **Switch2Connect** project by TommyWabg:

https://github.com/TommyWabg/Switch2Connect

## Features

### Controller Connection

* Switch 2 Pro Controller input through an ESP32-S3 USB CDC bridge
* Automatic detection of compatible ESP32 serial devices
* Automatic connection and reconnection support for previously paired controllers
* USB CDC serial communication at 2,000,000 baud
* Compatible firmware identification through bridge status responses
* Player 1 LED setup after connection
* Short vibration notification after a successful connection
* Pin function for locating the connected controller with a vibration alert

### XInput Output

* Virtual Xbox 360 controller output through `vgamepad` / ViGEmBus
* Button and D-pad mapping
* Left and right analog stick input
* One virtual controller at a time

### Input Mapping

* Configurable button mapping
* Configurable D-pad mapping
* Independent left and right stick-direction mapping
* Cardinal and diagonal stick-direction mapping
* Keyboard key mapping

### Analog Stick Processing

* Custom analog stick calibration
* Configurable inner deadzone
* Configurable outer deadzone
* Adjustable stick response curve
* Stick input debounce / jitter filtering
* Independent processing for the left and right analog sticks

### Vibration

* XInput rumble conversion to HD Rumble 2
* Independent LF and HF vibration processing
* Independent LF and HF vibration response curves
* Independent LF and HF vibration strength
* LF → HF vibration compensation
* HF → LF vibration compensation
* Configurable LF and HF frequencies
* Configurable maximum vibration amplitude

### Configuration Interface

* Graphical configuration interface
* ViGEmBus installation status display
* Connection program launch and restart controls
* Controller Pin function
* Analog stick calibration
* Stick processing configuration
* Settings stored in `config.ini`

## Current Limitations

* One controller at a time
* Windows only
* Requires a compatible ESP32-S3 bridge firmware
* Gyroscope and accelerometer motion input are not supported through the XInput virtual controller
* No Joy-Con pairing support
* No PS5 controller emulation
* No audio haptics

## Requirements

### Operating System

* Windows

### Hardware

* Switch 2 Pro Controller
* Compatible ESP32-S3 device running the required USB CDC bridge firmware
* Windows PC

### Required Driver

* **ViGEmBus 1.22.0 (`ViGEmBus_1.22.0_x64_x86_arm64`)**

ViGEmBus is required for the virtual Xbox 360 / XInput controller output.

If ViGEmBus is not already installed, install `ViGEmBus_1.22.0_x64_x86_arm64` before using S2P-XInput-Lite.

> **Important:** ViGEmBus is a third-party system driver and is not part of S2P-XInput-Lite itself. Administrator privileges may be required during driver installation. Restart Windows if requested by the installer.

The S2P-XInput-Lite configuration GUI includes a ViGEmBus installation status check.

### Packaged Release

The packaged release includes its own portable Python runtime and the required Python packages.

You do **not** need to:

* Install Python separately
* Run `pip install`
* Manually install Python dependencies such as `pyserial` or `vgamepad`

## Installation

1. Download and extract the S2P-XInput-Lite release package.
2. Install **ViGEmBus 1.22.0 (`ViGEmBus_1.22.0_x64_x86_arm64`)** if ViGEmBus is not already installed.
3. Connect the ESP32-S3 to the PC.
4. Make sure the ESP32-S3 is running compatible bridge firmware.
5. Launch `S2P-XInput-Lite.exe`.
6. Configure the controller as needed.
7. Start the connection program from the configuration GUI.

The application automatically searches for a compatible ESP32 device, so manual COM port configuration is normally not required.

## Basic Usage

1. Open the S2P-XInput-Lite configuration GUI.
2. Configure button mapping, stick-direction mapping, analog stick processing, and vibration settings as needed.
3. Save the configuration.
4. Start or restart the connection program from the GUI.
5. Press any button on the controller to wake it.
6. Wait for the controller connection to complete.
7. Keep the connection program running while using the controller.

When the controller connects successfully, the program sets the Player 1 LED and plays a short vibration notification.

For a previously paired controller, the ESP32-S3 bridge can automatically reconnect when the controller is available.

## Configuration

Settings are stored in:

```text
config.ini
```

The graphical configuration interface is the recommended way to edit these settings.

### Button Mapping

Controller buttons can be mapped to Xbox / XInput buttons or keyboard keys.

### Stick Direction Mapping

The left and right stick directions can be configured independently.

Supported direction mapping includes:

* Up
* Down
* Left
* Right
* Diagonal directions

This allows the physical stick directions to be reassigned independently from the normal analog stick output.

### Stick Calibration

Use the included calibration function if the stick center or range is inaccurate.

Calibration data is stored in `config.ini` and automatically applied by the main controller program.

For best results, close the active controller connection program before starting calibration.

### Stick Deadzones

The analog stick processing includes configurable deadzone controls.

**Inner deadzone** can be used to suppress unwanted movement near the stick center.

**Outer deadzone** can be used to control how close the physical stick must move toward its maximum range before full virtual stick output is reached.

### Stick Response Curve

The stick response curve can be adjusted to change how physical stick movement is translated into virtual XInput stick output.

This can be used to make the stick response more precise near the center or more aggressive toward the outer range.

### Stick Debounce / Jitter Filtering

A configurable stick input filter is available to reduce small unwanted fluctuations in analog stick input.

The filter is intended to reduce minor jitter while keeping additional input latency as low as possible.

## Vibration

S2P-XInput-Lite converts Xbox XInput rumble into Switch 2 Pro Controller HD Rumble 2 vibration data.

Available vibration settings include:

* LF frequency
* HF frequency
* LF strength
* HF strength
* LF response curve
* HF response curve
* Maximum amplitude
* LF → HF compensation
* HF → LF compensation

The LF and HF vibration channels can be adjusted independently to tune how conventional XInput rumble is represented through HD Rumble 2.

The GUI also includes a **Pin** function that sends a short vibration notification to the currently connected controller.

## ESP32 Communication

The bridge communicates with the ESP32-S3 over USB CDC serial at:

```text
2,000,000 baud
```

The Windows application automatically searches for compatible serial devices and identifies compatible bridge firmware using the bridge status response.

Manual COM port selection is normally not required.

The ESP32-S3 bridge is responsible for communication with the Switch 2 Pro Controller, while the Windows application handles input processing, mapping, virtual XInput output, and vibration conversion.

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
