param(
    [string]$Python = "python",
    [string]$OutputDirectory,
    [switch]$SkipBootstrap
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repoRoot "build\launchers"
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$toolEnvironment = Join-Path $repoRoot "build\build-tools"
$toolPython = Join-Path $toolEnvironment "Scripts\python.exe"

if (-not $SkipBootstrap) {
    & $Python -m venv $toolEnvironment
    if ($LASTEXITCODE -ne 0) {
        throw "Could not create the build-tools virtual environment."
    }
    & $toolPython -m pip install `
        --disable-pip-version-check `
        -r (Join-Path $repoRoot "requirements-build.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install the pinned build dependencies."
    }
}
elseif (-not (Test-Path -LiteralPath $toolPython -PathType Leaf)) {
    throw "Build-tools environment is missing: $toolPython"
}

& (Join-Path $repoRoot "native\build_launcher.ps1") `
    -Python $toolPython `
    -OutputDirectory $OutputDirectory
if ($LASTEXITCODE -ne 0) {
    throw "Main launcher build failed."
}

& (Join-Path $repoRoot "native\build_gamepad_tester_launcher.ps1") `
    -Python $toolPython `
    -OutputDirectory $OutputDirectory
if ($LASTEXITCODE -ne 0) {
    throw "GamepadTester launcher build failed."
}

$versionFile = Join-Path $repoRoot "src\version.py"
$versionMatch = Select-String `
    -LiteralPath $versionFile `
    -Pattern '^\s*VERSION\s*=\s*"([^"]+)"\s*$'
if (-not $versionMatch) {
    throw "Could not read VERSION from src\version.py."
}
$version = $versionMatch.Matches[0].Groups[1].Value
$launcherMetadata = [ordered]@{
    schema_version = 1
    source_version = $version
    launchers = [ordered]@{}
}
foreach ($name in @("S2P-XInput-Lite.exe", "GamepadTester.exe")) {
    $path = Join-Path $OutputDirectory $name
    $launcherMetadata.launchers[$name] = [ordered]@{
        sha256 = (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash
    }
}
$launcherMetadataPath = Join-Path $OutputDirectory "LAUNCHER_BUILD.json"
$launcherMetadata |
    ConvertTo-Json -Depth 4 |
    Set-Content -LiteralPath $launcherMetadataPath -Encoding utf8

Write-Output "Launchers are ready in $OutputDirectory"
