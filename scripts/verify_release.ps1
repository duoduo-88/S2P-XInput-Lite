param(
    [string]$PackageDirectory,
    [string]$ZipPath
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$buildRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "build"))

function Assert-BuildPath {
    param([string]$Path)

    $resolved = [System.IO.Path]::GetFullPath($Path)
    $prefix = $buildRoot.TrimEnd("\") + "\"
    if (-not $resolved.StartsWith(
        $prefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Verification workspace must stay under the repository build directory."
    }
}

function Get-RelativePath {
    param(
        [string]$BasePath,
        [string]$Path
    )

    $baseUri = New-Object System.Uri(
        ([System.IO.Path]::GetFullPath($BasePath).TrimEnd("\") + "\")
    )
    $pathUri = New-Object System.Uri([System.IO.Path]::GetFullPath($Path))
    return [System.Uri]::UnescapeDataString(
        $baseUri.MakeRelativeUri($pathUri).ToString()
    ).Replace("/", "\")
}

if ([bool]$PackageDirectory -eq [bool]$ZipPath) {
    throw "Specify exactly one of -PackageDirectory or -ZipPath."
}

if ($ZipPath) {
    $ZipPath = [System.IO.Path]::GetFullPath($ZipPath)
    if (-not (Test-Path -LiteralPath $ZipPath -PathType Leaf)) {
        throw "Release ZIP was not found: $ZipPath"
    }
    $verificationRoot = Join-Path $buildRoot "verify-release"
    Assert-BuildPath $verificationRoot
    if (Test-Path -LiteralPath $verificationRoot) {
        Remove-Item -LiteralPath $verificationRoot -Recurse -Force
    }
    New-Item -ItemType Directory -Force -Path $verificationRoot | Out-Null
    Expand-Archive -LiteralPath $ZipPath -DestinationPath $verificationRoot
    $packageDirectories = @(
        Get-ChildItem -LiteralPath $verificationRoot -Directory
    )
    if ($packageDirectories.Count -ne 1) {
        throw "Release ZIP must contain exactly one top-level directory."
    }
    $PackageDirectory = $packageDirectories[0].FullName
}

$PackageDirectory = [System.IO.Path]::GetFullPath($PackageDirectory)
if (-not (Test-Path -LiteralPath $PackageDirectory -PathType Container)) {
    throw "Release package directory was not found: $PackageDirectory"
}

$versionFile = Join-Path $PackageDirectory "src\version.py"
if (-not (Test-Path -LiteralPath $versionFile -PathType Leaf)) {
    throw "Release package is missing src\version.py."
}
$versionMatch = Select-String `
    -LiteralPath $versionFile `
    -Pattern '^\s*VERSION\s*=\s*"([^"]+)"\s*$'
if (-not $versionMatch) {
    throw "Could not read VERSION from packaged src\version.py."
}
$version = $versionMatch.Matches[0].Groups[1].Value
$versionSlug = $version -replace "\s+", "-"
$expectedName = "S2P-XInput-Lite-v$versionSlug"
if ((Split-Path -Leaf $PackageDirectory) -ne $expectedName) {
    throw "Package directory name does not match version: expected $expectedName."
}

$required = @(
    "S2P-XInput-Lite.exe",
    "GamepadTester.exe",
    "LICENSE",
    "README.md",
    "README_zh-TW.md",
    "THIRD_PARTY_NOTICES.md",
    "runtime\python.exe",
    "runtime\pythonw.exe",
    "src\config_gui.py",
    "src\gamepad_test_app.py",
    "src\main.py",
    "src\layers\mouse.json",
    "src\requirements.txt",
    "src\requirements-binary.txt",
    "src\raw_hid_probe.exe",
    "src\raw_hid_stream_probe.exe",
    "src\profiles\System Default.ini",
    "esp32s3\firmware\bootloader.bin",
    "esp32s3\firmware\partition-table.bin",
    "esp32s3\firmware\esp32s3_bluedroid_bridge.bin",
    "SHA256SUMS.txt"
)
foreach ($relative in $required) {
    $path = Join-Path $PackageDirectory $relative
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Release package is missing required file: $relative"
    }
}

$forbiddenFiles = @(
    "src\config.ini",
    "src\controller_status.json",
    "src\profiles\ZZZ.ini"
)
foreach ($relative in $forbiddenFiles) {
    if (Test-Path -LiteralPath (Join-Path $PackageDirectory $relative)) {
        throw "Release package contains local state: $relative"
    }
}
$cacheDirectories = @(
    Get-ChildItem -LiteralPath $PackageDirectory -Directory -Recurse -Force |
        Where-Object { $_.Name -eq "__pycache__" }
)
if ($cacheDirectories.Count) {
    throw "Release package contains __pycache__ directories."
}

$expectedProfiles = @(
    "Action.ini",
    "Audio.ini",
    "FPS-COMP.ini",
    "FPS-IMM.ini",
    "General.ini",
    "Racing.ini",
    "Rhythm.ini",
    "System Default.ini"
)
$profileDirectory = Join-Path $PackageDirectory "src\profiles"
$actualProfiles = @(
    Get-ChildItem -LiteralPath $profileDirectory -File -Filter "*.ini" |
        Select-Object -ExpandProperty Name |
        Sort-Object
)
$profileDifference = Compare-Object `
    -ReferenceObject ($expectedProfiles | Sort-Object) `
    -DifferenceObject $actualProfiles
if ($profileDifference) {
    throw "Release package profile allowlist does not match the bundled profiles."
}

$manifestPath = Join-Path $PackageDirectory "SHA256SUMS.txt"
$manifestLines = Get-Content -LiteralPath $manifestPath -Encoding utf8
$manifestEntries = [System.Collections.Generic.Dictionary[string,string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
$packagePrefix = $PackageDirectory.TrimEnd("\") + "\"
foreach ($line in $manifestLines) {
    if ($line -notmatch '^([0-9A-F]{64})  (.+)$') {
        throw "Malformed SHA256SUMS entry: $line"
    }
    $expectedHash = $Matches[1]
    $relative = $Matches[2].Replace("/", "\")
    $segments = @($relative -split "\\")
    if (
        [System.IO.Path]::IsPathRooted($relative) -or
        $segments.Count -eq 0 -or
        @($segments | Where-Object { $_ -in @("", ".", "..") }).Count
    ) {
        throw "Unsafe SHA256SUMS path: $relative"
    }
    $path = [System.IO.Path]::GetFullPath(
        (Join-Path $PackageDirectory $relative)
    )
    if (-not $path.StartsWith(
        $packagePrefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "SHA256SUMS path escapes the package: $relative"
    }
    $normalizedRelative = Get-RelativePath `
        -BasePath $PackageDirectory `
        -Path $path
    if ($normalizedRelative -eq "SHA256SUMS.txt") {
        throw "SHA256SUMS must not contain a hash for itself."
    }
    if ($manifestEntries.ContainsKey($normalizedRelative)) {
        throw "Duplicate SHA256SUMS entry: $normalizedRelative"
    }
    $manifestEntries.Add($normalizedRelative, $expectedHash)
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "SHA256SUMS references a missing file: $normalizedRelative"
    }
    $actualHash = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    if ($actualHash -ne $expectedHash) {
        throw "SHA-256 mismatch for $normalizedRelative."
    }
}

$actualPayload = [System.Collections.Generic.HashSet[string]]::new(
    [System.StringComparer]::OrdinalIgnoreCase
)
foreach ($file in (
    Get-ChildItem -LiteralPath $PackageDirectory -File -Recurse
)) {
    if ($file.FullName -eq $manifestPath) {
        continue
    }
    $relative = Get-RelativePath `
        -BasePath $PackageDirectory `
        -Path $file.FullName
    [void]$actualPayload.Add($relative)
}
$unlistedPayload = @(
    $actualPayload |
        Where-Object { -not $manifestEntries.ContainsKey($_) } |
        Sort-Object
)
$missingPayload = @(
    $manifestEntries.Keys |
        Where-Object { -not $actualPayload.Contains($_) } |
        Sort-Object
)
if (
    $manifestEntries.Count -ne $actualPayload.Count -or
    $unlistedPayload.Count -or
    $missingPayload.Count
) {
    $details = @()
    if ($unlistedPayload.Count) {
        $details += "unlisted: $($unlistedPayload -join ', ')"
    }
    if ($missingPayload.Count) {
        $details += "missing: $($missingPayload -join ', ')"
    }
    throw "SHA256SUMS does not exactly cover the package payload ($($details -join '; '))."
}

$runtimePython = Join-Path $PackageDirectory "runtime\python.exe"
$importCheck = (
    "import config_gui, gamepad_test_window, main; " +
    "print('release import smoke test passed')"
)
& $runtimePython -B -c $importCheck
if ($LASTEXITCODE -ne 0) {
    throw "Packaged runtime import smoke test failed."
}

$fileCount = @(
    Get-ChildItem -LiteralPath $PackageDirectory -File -Recurse
).Count
$relativePackage = Get-RelativePath -BasePath $repoRoot -Path $PackageDirectory
Write-Output "Verified $expectedName ($fileCount files) at $relativePackage"
