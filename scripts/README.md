# Build and release scripts

Run these commands from a Windows PowerShell prompt at the repository root.

## Validation

```powershell
python -m compileall -q src tests
ruff check src tests
runtime\python.exe tests\run_tests.py
```

## Individual build outputs

All generated files are written below the ignored `build\` directory.

```powershell
.\scripts\build_runtime.ps1
.\scripts\build_launchers.ps1
.\scripts\build_native_helpers.ps1
.\scripts\build_firmware.ps1
```

The portable runtime requires 64-bit Python 3.11.9 and Visual Studio C++ Build
Tools. The two dependencies available only as source are pinned and verified
by SHA-256; vgamepad is safely extracted without executing its MSI-launching
setup.py, while PyBluez is built at an exact commit. The firmware script uses
ESP-IDF 5.5.4 and verifies that all three compiled images match the bundled
firmware byte for byte.

## Release package

```powershell
.\scripts\package_release.ps1
```

The package version is read from `src\version.py`; the matching
`RELEASE_NOTES_v<version>.md` must exist. The script builds the runtime and
native executables, runs lint and tests, verifies firmware, creates a curated
ZIP in `dist\`, writes SHA-256 manifests, and performs an import smoke test
against the extracted package.

The ZIP root contains only `S2P-XInput-Lite.exe`, `GamepadTester.exe`,
`START_HERE.txt` and `app/`. The support tree keeps its relative paths under
`app/`, including firmware, runtime, profiles, licenses and documentation.
`app/SHA256SUMS.txt` covers the whole package with paths relative to the ZIP
installation root. Launcher metadata records portable layout 2 to reject old
same-version launchers. Settings import accepts both `src/` and `app/src/`.

For a quick local packaging check after all build outputs already exist:

```powershell
.\scripts\package_release.ps1 `
    -UseExistingBuildOutputs `
    -SkipFirmwareBuild `
    -Force
```

Use `-SkipTests` or `-SkipFirmwareBuild` only for local diagnostics, not for
an official release.

## v0.7.14 firmware and evidence

The desktop version is 0.7.14 and bundled firmware is S2P-FW 1.0.7; protocol
s2p_bridge 1.0.0 and the existing profile schema remain. ESP-IDF 5.5.4 builds
use reproducible-build configuration with esp_tinyusb 1.7.6~2 and tinyusb 0.19.0~3.
Synchronize bootloader, partition table and application from the same build before
byte-for-byte verification; firmware hashes belong in
[`esp32s3/firmware/SHA256SUMS.txt`](../esp32s3/firmware/SHA256SUMS.txt).

Descriptor evidence must come from this build's actual ELF/BIN, including the
CDC-only 303A:4001 Bridge descriptor and CDC+HID 303A:4005 descriptor. Metadata
alone does not verify S2P identity. Record actual automated/build/package results
and skips, and retain the [hardware checklist](../docs/usb-hid-standardization.md).
All hardware checks remain pending; local package verification and mock Windows
tests do not demonstrate physical input, ViGEmBus, rumble or audio operation.
The v0.7.14 release includes the verified firmware and descriptor evidence.

By default `build_firmware.ps1` builds and verifies against the existing bundle.
When deliberately updating the bundle from the current source, maintainers run:

```powershell
.\scripts\build_firmware.ps1 -UpdateBundledImages -Python .\runtime\python.exe
```

`-Python` selects the Python interpreter for descriptor inspection. The script
inspects the actual compiled ELF before copying the three images, then writes
`esp32s3/firmware/usb-descriptors.json` and `SHA256SUMS.txt`. Review descriptor
and hash evidence with the source change; normal verification should not use
`-UpdateBundledImages` to hide unexpected bundle differences.
