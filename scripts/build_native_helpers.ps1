param(
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repoRoot "build\native"
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)

& (Join-Path $repoRoot "native\build_raw_hid_probe.ps1") `
    -OutputDirectory $OutputDirectory
if ($LASTEXITCODE -ne 0) {
    throw "Raw HID probe build failed."
}

& (Join-Path $repoRoot "native\build_raw_hid_stream_probe.ps1") `
    -OutputDirectory $OutputDirectory
if ($LASTEXITCODE -ne 0) {
    throw "Raw HID stream probe build failed."
}

Write-Output "Native helpers are ready in $OutputDirectory"
