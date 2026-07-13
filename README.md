# S2P-XInput-Lite

S2P-XInput-Lite is a lightweight Windows controller interoperability project that connects a Switch 2 Pro Controller to a Windows PC over **Bluetooth through an ESP32-S3 bridge**, and converts the controller input into an Xbox 360-compatible XInput virtual controller.

The ESP32-S3 handles the Bluetooth connection with the Switch 2 Pro Controller and forwards controller data to the Windows application through USB CDC serial. The Windows application then processes the controller input and outputs a standard Xbox 360-compatible XInput virtual controller.

The project provides Bluetooth controller connectivity, automatic reconnection for previously paired controllers, configurable button and stick-direction mapping, analog stick calibration and processing, keyboard mapping, and XInput rumble conversion to HD Rumble 2.

> S2P-XInput-Lite is an unofficial community project and is not affiliated with, endorsed by, sponsored by, or officially associated with Nintendo, Microsoft, Espressif Systems, or any other third party.

## Project Scope

This project is intentionally focused on a lightweight:

**Switch 2 Pro Controller → Bluetooth → ESP32-S3 → USB CDC → Windows → XInput virtual controller**

workflow.

Instead of connecting the controller directly to Windows over Bluetooth, the ESP32-S3 acts as the Bluetooth communication bridge. It connects to the Switch 2 Pro Controller wirelessly and forwards controller input to the Windows application over USB CDC serial.

S2P-XInput-Lite then processes the controller data and outputs a standard Xbox 360-compatible XInput virtual controller through ViGEmBus.

The project provides configurable input processing and mapping features on top of this workflow, including:

* Bluetooth connection through the ESP32-S3 bridge
* Automatic reconnection for previously paired controllers
* Button mapping
* Stick-direction mapping
* Keyboard key mapping
* Analog stick calibration
* Inner and outer deadzone adjustment
* Stick response curve adjustment
* Stick input debounce / jitter filtering
* XInput rumble conversion to HD Rumble 2

Because the virtual controller output is based on XInput, this version does **not** expose gyroscope or accelerometer motion input through the virtual controller.

If you need broader controller features or a more feature-complete implementation, please refer to the upstream **Switch2Connect** project by TommyWabg:

https://github.com/TommyWabg/Switch2Connect

## Features

### Bluetooth Controller Connection

* Wireless Switch 2 Pro Controller connection through an ESP32-S3 Bluetooth bridge
* The ESP32-S3 handles Bluetooth communication with the controller
* Automatic connection to previously paired controllers
* Automatic reconnection when the controller becomes available again
* Controller pairing support through the ESP32-S3 bridge
* Automatic detection of compatible ESP32 serial devices on Windows
* USB CDC communication between the ESP32-S3 and the Windows application
* Compatible firmware identification through bridge status responses
* Player 1 LED setup after connection
* Short vibration notification after a successful connection
* Pin function for locating the connected controller with a vibration alert

### Connection Architecture

```text
Switch 2 Pro Controller
        │
        │ Bluetooth
        ▼
     ESP32-S3
        │
        │ USB CDC Serial
        ▼
S2P-XInput-Lite
        │
        │ vgamepad / ViGEmBus
        ▼
Virtual Xbox 360 Controller
        │
        ▼
    XInput Games
```

### XInput Output

* Virtual Xbox 360 controller output through `vgamepad` / ViGEmBus
* Button and D-pad input
* Left and right analog stick input
* XInput vibration output converted back to HD Rumble 2
* One virtual controller at a time
