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

The bundled ESP32-S3 Bluedroid bridge binary, boot files, and complete matching
source are copied without modification from Switch2Connect commit
`d63b044e66cfb93f8377a3596e3f00c82715b029`. The corresponding source is
distributed under `esp32s3/source/esp32s3_usb_bridge_bluedroid`. Firmware and
source report version `0.12.4`.

**Upstream source:**  
https://github.com/TommyWabg/Switch2Connect

**Exact upstream revision:**  
https://github.com/TommyWabg/Switch2Connect/tree/d63b044e66cfb93f8377a3596e3f00c82715b029/ESP32-S3%20firmware/esp32s3_usb_bridge_bluedroid

These files remain subject to their applicable upstream copyright and license
terms. They are not an unmodified upstream ZIP archive.

---

## esptool

The ESP32 firmware flashing functionality included with this project uses `esptool.exe`.

esptool is an open-source utility developed by Espressif Systems for flashing and communicating with Espressif chips.

**Upstream project:**  
https://github.com/espressif/esptool

`esptool.exe` is a third-party component. Its copyright and applicable license terms remain with its original authors and contributors.

Distribution of `esptool.exe` alongside S2P-XInput-Lite or as part of an upstream firmware package does not change its original copyright or applicable license terms.

For the complete license terms applicable to a specific version of esptool, refer to the corresponding upstream source release and its accompanying license files.

---

## Third-Party Trademarks

Nintendo, Nintendo Switch, Switch 2, Xbox, Microsoft, Espressif, and other third-party product names, project names, and trademarks are the property of their respective owners.

These names are mentioned solely for compatibility, interoperability, software attribution, and open-source acknowledgement purposes.

S2P-XInput-Lite is not affiliated with, endorsed by, sponsored by, or officially associated with the companies or third-party projects mentioned above.
