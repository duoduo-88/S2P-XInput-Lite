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

S2P-XInput-Lite itself is distributed under the GNU General Public License v3.0 (GPL-3.0).

Special thanks to TommyWabg and the contributors to Switch2Connect for their open-source work.

The authors and contributors of Switch2Connect do not provide official support for, endorse, or sponsor S2P-XInput-Lite.

---

## ESP32-S3 Firmware Files

The following file distributed with this project:

`esp32s3.zip`

is provided directly from the Switch2Connect project by TommyWabg.

**Source:**  
https://github.com/TommyWabg/Switch2Connect

S2P-XInput-Lite has not modified this ZIP archive.

The file is redistributed as an upstream project file. Its original copyright and applicable license terms remain in effect.

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
