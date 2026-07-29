# Build and release scripts

Run these commands from a Windows PowerShell prompt at the repository root.

## Validation

```powershell
python -m compileall -q src tests
ruff check src tests
python tests\run_tests.py
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

For a quick local packaging check after all build outputs already exist:

```powershell
.\scripts\package_release.ps1 `
    -UseExistingBuildOutputs `
    -SkipFirmwareBuild `
    -Force
```

Use `-SkipTests` or `-SkipFirmwareBuild` only for local diagnostics, not for
an official release.
