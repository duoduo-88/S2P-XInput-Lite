param(
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repoRoot "src"
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null
$vswhere = Join-Path ${env:ProgramFiles(x86)} `
    "Microsoft Visual Studio\Installer\vswhere.exe"
$installation = & $vswhere -latest -products * `
    -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
    -property installationPath
if (-not $installation) {
    throw "Visual Studio C++ Build Tools were not found."
}
$source = Join-Path $PSScriptRoot "raw_hid_probe.cpp"
$output = Join-Path $OutputDirectory "raw_hid_probe.exe"
$object = Join-Path $OutputDirectory "raw_hid_probe.obj"
$vcvars = Join-Path $installation "VC\Auxiliary\Build\vcvars64.bat"
$compile = (
    "`"$vcvars`" >nul && cl.exe /nologo /std:c++17 /O2 /MT " +
    "/EHsc /W4 /DUNICODE /D_UNICODE `"$source`" " +
    "/Fo:`"$object`" /Fe:`"$output`" hid.lib"
)
& cmd.exe /d /s /c $compile
if ($LASTEXITCODE -ne 0) {
    throw "raw_hid_probe.exe build failed with exit code $LASTEXITCODE"
}
Remove-Item -LiteralPath $object -Force -ErrorAction SilentlyContinue
Write-Output "Built $output"
