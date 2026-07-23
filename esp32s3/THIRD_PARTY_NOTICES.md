# Third-Party Software and Attribution Notices

S2P-XInput-Lite uses, modifies, or redistributes certain third-party open-source software and related files.

This document describes the sources and relationships of those third-party components.

Third-party components remain subject to their respective copyright and applicable license terms. The S2P-XInput-Lite license does not replace or alter the original licenses of third-party components.

---

## Switch2Connect

**Author:** TommyWabg  
**Project:** Switch2Connect  
**Upstream repository:** https://github.com/TommyWabg/Switch2Connect

Switch2Connect is licensed under the GNU General Public License v3.0 (GPL-3.0).

Parts of the Python code in S2P-XInput-Lite originate from or are based on Switch2Connect and have been substantially reduced, modified, reorganized, or reimplemented for the purposes of this project.

Modifications and additions include, but are not limited to:

- Controller input processing
- XInput virtual controller output
- HD Rumble 2 vibration conversion and parameter handling
- Independent LF / HF vibration curves and channel compensation
- Analog stick calibration and input processing
- Button mapping
- Stick direction mapping
- Graphical configuration interface
- ESP32 automatic detection and connection workflow integration
- Wired USB HID report translation, startup sequencing, and non-blocking rumble transport
- Low-latency ESP32 BLE-to-USB CDC scheduling and latest-state forwarding

S2P-XInput-Lite itself is distributed under the GNU General Public License v3.0 (GPL-3.0).

Special thanks to TommyWabg and the contributors to Switch2Connect for their open-source work.

The authors and contributors of Switch2Connect do not provide official support for, endorse, or sponsor S2P-XInput-Lite.

---

## Wired USB Python Libraries

Wired controller support bundles `hidapi`, `PyUSB`, and `libusb-package` in the
portable Python runtime. These components remain subject to their respective
upstream licenses and copyright notices.

- hidapi: https://github.com/trezor/cython-hidapi
- PyUSB: https://github.com/pyusb/pyusb
- libusb-package: https://github.com/pyocd/libusb-package

---

## ESP32-S3 Firmware Files

The bundled ESP32-S3 Bluedroid bridge and standalone firmware is a modified
build based on Switch2Connect commit
`d63b044e66cfb93f8377a3596e3f00c82715b029`. Its complete corresponding source
is distributed under `esp32s3/source/esp32s3_usb_bridge_bluedroid`. The current
development firmware reports version `0.14.0-dev` and retains compatibility
with the `0.12.4` bridge command protocol.

S2P-XInput-Lite modifications include low-latency BLE-to-USB CDC scheduling,
immediate CDC task wake-up, newest-input priority, combined CDC report writes,
and an updated build identifier. These modifications remain distributed under
the GNU General Public License terms stated in the firmware source.

**Upstream source:**  
https://github.com/TommyWabg/Switch2Connect

**Exact upstream revision:**  
https://github.com/TommyWabg/Switch2Connect/tree/d63b044e66cfb93f8377a3596e3f00c82715b029/ESP32-S3%20firmware/esp32s3_usb_bridge_bluedroid

The linked upstream files remain subject to their applicable copyright and
license terms. The firmware images distributed by this project are modified
builds and are not represented as unmodified upstream releases.

### ESP-IDF and USB firmware components

The current firmware binary was built with the following third-party
components:

- ESP-IDF 5.5.4 — Apache License 2.0  
  https://github.com/espressif/esp-idf
- Espressif ESP TinyUSB 1.7.6~2 — Apache License 2.0  
  https://components.espressif.com/components/espressif/esp_tinyusb
- TinyUSB 0.19.0~3 — MIT License, copyright TinyUSB contributors and
  hathach (tinyusb.org)  
  https://github.com/hathach/tinyusb

The development standalone XInput device interface is based in part on the
USB interface layout, Microsoft OS 2.0 compatible-ID approach, report format,
and rumble command handling documented or implemented by these MIT-licensed
projects:

- Adafruit TinyUSB XInput example, copyright Ha Thach for Adafruit Industries
  https://github.com/JonnyHaystack/Adafruit_TinyUSB_XInput
- GP2040-CE XInput implementation, copyright Jason Skuby and
  OpenStickCommunity contributors
  https://github.com/OpenStickCommunity/GP2040-CE
- tinyusb-xinput reference implementation
  https://github.com/fluffymadness/tinyusb-xinput

These components retain their respective copyright notices, disclaimers, and
license terms. Their inclusion in the firmware binary does not change the
license of the original components.

### Fusion AHRS

The standalone firmware embeds the Fusion AHRS C core from `imufusion` 1.2.11
to keep its gyroscope orientation processing aligned with the desktop runtime.

- Project: https://github.com/xioTechnologies/Fusion
- Python package release: https://pypi.org/project/imufusion/1.2.11/
- License: MIT
- Copyright: x-io Technologies

The complete Fusion license is retained at
`esp32s3/source/esp32s3_usb_bridge_bluedroid/main/fusion/LICENSE.md`.

---

## esptool

The ESP32 firmware flashing functionality included with this project uses
`esptool.exe` version 4.11.0.

esptool is an open-source utility developed by Espressif Systems for flashing and communicating with Espressif chips.

**Upstream project:**  
https://github.com/espressif/esptool

`esptool.exe` is distributed under the GNU General Public License version 2.
Its copyright and applicable license terms remain with its original authors
and contributors.

Distribution of `esptool.exe` alongside S2P-XInput-Lite or as part of an upstream firmware package does not change its original copyright or applicable license terms.

For the complete license terms applicable to a specific version of esptool, refer to the corresponding upstream source release and its accompanying license files.

---

## HidHide

S2P-XInput-Lite can integrate with a separately installed copy of HidHide by
invoking its official `HidHideCLI.exe`. HidHide is not bundled or redistributed
with this release and remains subject to its own copyright and license terms.

- Project: https://github.com/nefarius/HidHide
- Documentation: https://docs.nefarius.at/projects/HidHide/

The integration only extends the existing application and hidden-device lists;
it does not claim ownership of, clear, or replace settings created by HidHide or
other controller tools.

---

## Third-Party Trademarks

Nintendo, Nintendo Switch, Switch 2, Xbox, Microsoft, Espressif, and other third-party product names, project names, and trademarks are the property of their respective owners.

These names are mentioned solely for compatibility, interoperability, software attribution, and open-source acknowledgement purposes.

S2P-XInput-Lite is not affiliated with, endorsed by, sponsored by, or officially associated with the companies or third-party projects mentioned above.

“XInput-compatible” describes software and USB interface interoperability only.
It does not represent Microsoft licensing, certification, approval, or hardware
identity. The ESP32 development firmware uses a non-retail development VID/PID
and does not copy the USB VID/PID of a retail Microsoft or Xbox controller.
