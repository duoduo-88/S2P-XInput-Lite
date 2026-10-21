# USB HID standardization — v0.7.14 / S2P-FW 1.0.7

This report documents the v0.7.14 release and its locally verified package.
Hardware validation remains pending. The bridge protocol remains
`s2p_bridge 1.0.0`, with the existing standalone profile schema.

## USB identity and supported modes

| Mode | USB identifier | Interfaces | Product |
|---|---|---|---|
| Bridge | `303A:4001` | CDC control/data | S2P ESP32 Bridge |
| Android USB HID standalone | `303A:4005` | CDC control/data + HID | S2P Mobile Gamepad |

The locked esp_tinyusb 1.7.6~2 default PID formula is
`0x4000 | CDC<<0 | MSC<<1 | HID<<2 | MIDI<<3 | AUDIO<<4 | VENDOR<<5`.
CDC only yields `0x4001`; CDC+HID yields `0x4005`. These are shared
Espressif/TinyUSB class defaults, not dedicated PID allocations, USB
certification, trademark permission or Espressif endorsement. Official policy:
[Espressif VID/PID guidance](https://docs.espressif.com/projects/esp-iot-solution/en/latest/usb/usb_overview/usb_vid_pid.html),
[Espressif USB PID repository](https://github.com/espressif/usb-pids/blob/main/README.md).

VID/PID and product names are discovery hints. The desktop requires the
matching firmware product, protocol, profile/build identity and compatible
capabilities before S2P operations. Special HID parsing requires association
to the exact verified CDC device by USB parent/container or stable serial.
A different verified S2P device does not establish this association. If it cannot
be established, ordinary HID behavior is retained. The same device's stable
serial is preserved across Bridge/HID switches. Legacy `CAFE:4020/4021/4022`
are upgrade candidates only, with identity verification still required.

Windows pyserial does not expose the USB product string. When that field is
unavailable, discovery still requires the S2P firmware product/protocol/profile/
build, compatible capabilities and a stable serial matching the USB port.
Explicit conflicting USB product metadata is rejected. Verified older Bridge
firmware can be discovered for upgrade using its known version and historical
fixed serial; that fixed serial cannot establish a unique HID/reconnect identity.
Raw HID verification also requires its actual USB product string.

Physical USB XInput, XUSB20, Microsoft OS Auto probing and the old Auto mode
are removed. Windows desktop XInput continues through Bridge and ViGEmBus,
including mappings, game rumble and audio haptics. This preservation is an
implementation intention backed by automated regression checks where recorded;
it is not a claim of a new physical ViGEm/controller test.

## Upgrade and rescue

Existing NVS modes `0=Bridge` and `2=HID` keep their meanings. Old `1=XInput`
and `3=Auto` normalize to HID and are reported in `mode_migration` for a desktop
notice. Unknown values/read errors use Bridge. Legacy boolean mode keys are
migration inputs only. Migration does not itself reboot; storage failures retain
a safe in-memory mode for retry, and old data is cleared only after durable commit.

The sole profile activation action is **Write and enable Android USB HID
standalone** / **寫入並啟用 Android USB HID 獨立模式**. The desktop also offers
**Return to ESP32 bridge mode** / **切回 ESP32 橋接模式**. Profile reconnect verifies
the expected device identity before checking length and CRC.

- With a connected controller, hold **HOME+A for 3 seconds and release** for HID,
  or **HOME+X for 3 seconds and release** for Bridge. HOME+Y is removed.
  Chords use raw input before mapping/layers. Same-mode requests do not reboot;
  failed mode saves do not reboot and leave CDC available.
- Without a controller, CDC remains available in both modes. Close applications
  using the port, send `mode bridge`, and send `restart` after a successful save.
- Without working CDC, use the board's ROM Download Mode via its native USB
  port. Typically hold BOOT, press/release RESET/EN, then release BOOT. A board
  without RESET may require holding BOOT while reconnecting USB. Consult the
  board manufacturer's wiring/timing instructions. Use the normal flashing
  workflow with chip/16 MB flash checks; ROM does not speak the S2P application
  protocol. Release BOOT and reset/power-cycle after flashing.

Calibration, curves, deadzones, stick processing, mapping layers, gyro-to-stick,
idle disconnect and BLE reconnect reuse the shared runtime. HID retains the
13-byte input report and analog/digital trigger semantics. HID has no rumble
output report; Android game rumble is not promised. Windows keyboard/mouse,
Audio/Mix processing, process-based profiles and BLE HID are outside standalone
support. Android USB OTG and game compatibility must be checked per device.

## Evidence boundaries

The source is built using ESP-IDF 5.5.4 with reproducible builds and locked
esp_tinyusb 1.7.6~2 / tinyusb 0.19.0~3. Authoritative image hashes are in
[`esp32s3/firmware/SHA256SUMS.txt`](../esp32s3/firmware/SHA256SUMS.txt).
Build/descriptor extraction, automated tests and package checks must be reported
with their actual results, failures and skips. A source constant or mocked
Windows output test alone is not hardware descriptor/ViGEm validation.
The implementation plan in the source repository defines the integration checks.
Historical performance and compatibility results
predate this revision and are not proof for S2P-FW 1.0.7.

## Validation

### Portable ZIP follow-up (2026-10-20)

- The root contains only `S2P-XInput-Lite.exe`, `GamepadTester.exe`,
  `START_HERE.txt` and `app/`. All support files, licenses and firmware are
  retained under `app/`; the full manifest is `app/SHA256SUMS.txt` with paths
  relative to the installation root.
- The complete suite passed **676 tests, zero skips**, including enabled IDF
  compilation and exact firmware comparison. New regressions exercise both
  frozen launcher paths, older layouts and settings import from `app/src/`.
- Both rebuilt EXEs were launched from the extracted ZIP in a relocated path
  containing spaces and Chinese characters. Settings reached `gui_visible`;
  both child processes displayed their expected main windows. Only the test
  processes were closed. This verifies desktop startup, not controller hardware.
- The package verifier passed exact manifest coverage, launcher hashes/layout
  metadata, profiles, third-party licenses, firmware evidence and runtime imports.

### Local automated verification (2026-10-20)

- Complete suite: **672 tests passed, zero skips**, 26.307 seconds, using the
  bundled runtime with `S2P_RUN_IDF_BUILD=1` and `S2P_VERIFY_RELEASE_IMAGE=1`.
- ESP-IDF 5.5.4 build and exact bundled application/bootloader/partition comparison:
  passed. Compiled ELF descriptors were extracted and found in the corresponding
  application BIN; configuration lengths are 75 bytes (Bridge) and 100 bytes (HID).
- Bridge has classes CDC control/data (2/10), CDC IAD and endpoints 0x81/0x02/0x82.
  HID adds class 3 and interrupt IN 0x83. The retained Android report descriptor
  is 117 bytes and encodes a 13-byte input report; same-size incompatible layouts
  are rejected. Removed XUSB20 and old USB personality strings are absent.
- Source compilation and Ruff correctness checks passed. Both v0.7.14 launchers
  and the native Raw HID helpers were rebuilt.
- Executable production-C tests cover old modes/keys and NVS failures, mode commands,
  HID encoding, raw recovery under unconfigured/busy USB, queue preservation and
  cross-controller/disconnect cancellation. Python tests cover Windows metadata
  without product strings, exact legacy upgrade signatures, expected-device
  reconnect, handle cleanup, migration messages, translations and existing desktop
  mapping/rumble/audio paths. The desktop output checks use test backends.

Application SHA-256:
`b60e5fceca0867017102169aa3d6307e4654cbee7c2b68071cea11756aa469ca`.
Machine-readable descriptor evidence is in
[`usb-descriptors.json`](../esp32s3/firmware/usb-descriptors.json).

### Hardware validation checklist — all pending

No new controller hardware validation has occurred. Every item below is pending, including
where automated checks exercise related logic. Record board/host/OS/game,
firmware hash and observed results before marking any item passed.

| Check | Required observation | Status |
|---|---|---|
| Windows Bridge enumeration | `303A:4001`, CDC only, stable serial, correct interfaces/endpoints | Pending |
| Windows/Android HID enumeration | `303A:4005`, CDC + HID, correct report layout/serial | Pending |
| Shared PID and multiple devices | Non-S2P rejected for S2P operations; CDC/HID never cross-associated | Pending |
| Windows desktop XInput | ViGEmBus output, buttons, sticks, triggers, mappings and gyro | Pending |
| Windows feedback | Game rumble, stop frames, Audio and Mix haptics | Pending |
| Android input/game compatibility | Buttons, both sticks, hat, independent analog triggers, Home/Capture behavior | Pending |
| USB unplug/replug | Correct mode, identity and expected-device reconnect | Pending |
| Sleep / remote wake / host shutdown | USB/BLE lifecycle and Bridge lease behavior | Pending |
| Idle disconnect / BLE reconnect | Timeout, wake, reconnect and controller/ESP32 restart | Pending |
| Actual NVS upgrade | Old 0/1/2/3, old keys, notice, repeated reboot, persistence failures | Pending |
| Profile write/restart | A/B recovery, length/CRC and expected-device verification | Pending |
| Raw recovery chords | HOME+A / HOME+X hold/release, remapped profiles, same-mode behavior | Pending |
| CDC rescue | `mode bridge` + restart with no controller, both modes | Pending |
| ROM rescue | Board BOOT/RESET procedure, flashing, reboot to application | Pending |

## 繁體中文重點

本次只有 Bridge（`303A:4001`）與 Android USB HID（`303A:4005`），兩種模式
均保留 CDC。識別碼是 Espressif/TinyUSB 共享預設值，不是專屬 PID 或 USB 認證；
桌面仍須驗證韌體身分，不能因同名或另一台 S2P 存在而套用專用 HID 解析。
舊 PC XInput／Auto 升級會改成 HID 並提示；Windows 桌面 XInput 請切回 Bridge。
HOME+A 長按 3 秒放開切 HID；HOME+X 長按 3 秒放開切 Bridge；HOME+Y 已移除。
無手把仍可用 CDC `mode bridge`，無 CDC 時依板商 BOOT／RESET 說明進 ROM 救援。
Android 不承諾遊戲震動。上表全部實機項目仍待測，歷史量測及自動化測試不能代表
本文件隨 v0.7.14 正式發佈。新版硬體驗證仍待完成；本表狀態代表實機驗證進度。
