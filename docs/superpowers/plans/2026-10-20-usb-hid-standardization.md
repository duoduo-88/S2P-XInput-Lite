# USB HID Standardization Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development for scoped implementation and independent review. Track the work below.

**Goal:** Ship v0.7.14 with CDC Bridge and standard CDC+HID firmware, safe migration, verified identity, and preserved desktop XInput.
**Architecture:** Keep the integrated firmware and shared mapping runtime. Select complete USB descriptors at boot. Treat USB metadata as discovery hints and require firmware identity for S2P operations.
**Tech Stack:** Python/unittest, ESP-IDF 5.5.4, esp_tinyusb 1.7.6~2, tinyusb 0.19.0~3, PowerShell packaging.
**Spec:** ../specs/2026-10-20-usb-hid-standardization-design.md

## Global Constraints

- Desktop v0.7.14; firmware S2P-FW 1.0.7; existing protocol/profile schema retained.
- Bridge 303A:4001; HID+CDC 303A:4005, product S2P Mobile Gamepad; no vendor USB class or XUSB20.
- Preserve desktop ViGEmBus, mapping, rumble/audio haptics and standalone profile algorithms.
- No hardware writes, merge, deployment, or formal Release. Create a Draft PR.
- User explicitly requested execution after reviewing the spec; proceed continuously with scoped agents. Root owns integration/commits to avoid shared-index races.

## Review Focus

- Unresponsive shared PID devices and same-name devices must not acquire S2P identity or special HID interpretation.
- Multiple USB controllers must not cross-associate CDC/HID or recover a profile on the wrong controller.
- NVS read/write/commit errors and repeated boots must preserve USB and avoid reboot loops.
- Bridge must have CDC-only descriptors despite HID support compiled into the same image.
- Removing USB XInput must preserve shared report calculations and desktop rumble paths.

### Task 1: Firmware USB modes and migration

Files: firmware main/standalone_xinput.c/.h, main/main.c, targeted firmware tests and C harnesses.
Interfaces: mode values 0 Bridge, 2 HID; old 1/3 migrate to 2. Status/capabilities expose `mode_migration` as old mode string or empty string, `usb_serial` as S2P-<12 uppercase MAC hex>. Serial matches USB string across modes.
- [ ] Add failing executable tests for descriptors/migration/error recovery and update obsolete mode contracts.
- [ ] Implement the two descriptors, remove physical XUSB/Auto transport, preserve shared input processing.
- [ ] Implement idempotent NVS migration and durable notification; reject removed mode commands.
- [ ] HOME+A 3 seconds selects HID, HOME+X 3 seconds selects Bridge on release; independent pending flag.
- [ ] Run scoped firmware tests and inspect diffs for shared-runtime deletion.

### Task 2: Desktop identity, UI, and reconnect

Files: src/esp32_detection.py, standalone_profile.py, config_gui.py, gui_sections/footer.py, gamepad_devices.py, gamepad_test_window.py, localization.py, related tests.
Consumes: Task 1 status identity and migration fields; old S2P responses remain upgrade candidates only after identity verification.
- [ ] Add failing behavior tests for unresponsive/shared PID, wrong identity, reconnect, and removed options.
- [ ] Remove identity fallback, require metadata plus firmware handshake for shared PID; include legacy Auto candidate.
- [ ] Validate profile reconnect identity and stable serial; avoid cross-device association.
- [ ] Simplify UI and default target, display migration message in both languages, retain Bridge rescue.
- [ ] Conservatively keep generic HID behavior if WinMM cannot link exact device to verified CDC; no PID/name-only special handling.
- [ ] Run scoped detection/profile/tester/UI/translation tests.

### Task 3: Version, docs, build, and packaging

Files: src/version.py, README.md/README_zh-TW.md, manual guides, firmware/source/scripts docs, RELEASE_NOTES_v0.7.14.md, tests/test_release_baseline.py, bundled firmware, build/verify scripts as necessary.
- [ ] Diagnose the pre-existing baseline test stall with tracebacks, then run full suite to completion.
- [ ] Update versions and current docs without rewriting historical Release Notes.
- [ ] Compile ESP-IDF and inspect descriptor bytes from the actual ELF/BIN; export a parseable descriptor evidence artifact.
- [ ] Update three bundled images, rebuild launchers/native helpers as required, package v0.7.14 and verify SHA-256.
- [ ] Run complete automated suite, lint and packaging checks; record skips and hardware pending items.

### Task 4: Independent review and Draft PR

- [ ] Review whole branch against spec, especially the five Review Focus items; fix material findings with regression tests.
- [ ] Verify clean diff, bundle hashes and final test evidence, then commit scoped changes.
- [ ] Push task branch, create Draft PR, attach it to this chat, and report differences/results/hardware pending.
