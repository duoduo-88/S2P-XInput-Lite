param(
    [string]$IdfPath = $env:IDF_PATH
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$firmwareRoot = Join-Path $repoRoot `
    "esp32s3\source\esp32s3_usb_bridge_bluedroid"

if (-not $IdfPath) {
    $IdfPath = "C:\Espressif\frameworks\esp-idf-v5.5.4"
}
$IdfPath = [System.IO.Path]::GetFullPath($IdfPath)
$exportScript = Join-Path $IdfPath "export.bat"
if (-not (Test-Path -LiteralPath $exportScript -PathType Leaf)) {
    throw "ESP-IDF 5.5.4 export script was not found: $exportScript"
}

$command = (
    "call `"$exportScript`" && " +
    "set `"IDF_CCACHE_ENABLE=0`" && " +
    "idf.py build"
)
Push-Location $firmwareRoot
try {
    & cmd.exe /d /s /c $command
    if ($LASTEXITCODE -ne 0) {
        throw "ESP-IDF build failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}

$pairs = @{
    (Join-Path $firmwareRoot "build\bootloader\bootloader.bin") =
        (Join-Path $repoRoot "esp32s3\firmware\bootloader.bin")
    (Join-Path $firmwareRoot "build\partition_table\partition-table.bin") =
        (Join-Path $repoRoot "esp32s3\firmware\partition-table.bin")
    (Join-Path $firmwareRoot "build\esp32s3_bluedroid_bridge.bin") =
        (Join-Path $repoRoot "esp32s3\firmware\esp32s3_bluedroid_bridge.bin")
}
foreach ($generated in $pairs.Keys) {
    $released = $pairs[$generated]
    $generatedHash = (Get-FileHash -LiteralPath $generated -Algorithm SHA256).Hash
    $releasedHash = (Get-FileHash -LiteralPath $released -Algorithm SHA256).Hash
    if ($generatedHash -ne $releasedHash) {
        throw (
            "Firmware output does not match bundled image: {0}: {1} != {2}" -f `
                (Split-Path -Leaf $generated),
                $generatedHash,
                $releasedHash
        )
    }
}

Write-Output "Firmware build matches all bundled release images."
