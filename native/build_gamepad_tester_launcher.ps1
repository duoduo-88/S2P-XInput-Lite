param(
    [string]$Python = "python",
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) {
    $OutputDirectory = $repoRoot
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$entryPoint = Join-Path $repoRoot "src\gamepad_tester_launcher.py"
$buildRoot = Join-Path $repoRoot "build\pyinstaller\GamepadTester"
$distRoot = Join-Path $buildRoot "dist"
$workRoot = Join-Path $buildRoot "work"
$specRoot = Join-Path $buildRoot "spec"

New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
New-Item -ItemType Directory -Force -Path $distRoot | Out-Null
New-Item -ItemType Directory -Force -Path $workRoot | Out-Null
New-Item -ItemType Directory -Force -Path $specRoot | Out-Null

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name GamepadTester `
    --distpath $distRoot `
    --workpath $workRoot `
    --specpath $specRoot `
    --paths (Join-Path $repoRoot "src") `
    $entryPoint

if ($LASTEXITCODE -ne 0) {
    throw "GamepadTester launcher build failed with exit code $LASTEXITCODE"
}

$builtExe = Join-Path $distRoot "GamepadTester.exe"
$targetExe = Join-Path $OutputDirectory "GamepadTester.exe"
Copy-Item -LiteralPath $builtExe -Destination $targetExe -Force
Write-Output "Built $targetExe"
