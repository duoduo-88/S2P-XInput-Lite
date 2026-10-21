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
$source = Join-Path $repoRoot "src\raw_hid_stream_probe.c"
$output = Join-Path $OutputDirectory "raw_hid_stream_probe.exe"
$object = Join-Path $OutputDirectory "raw_hid_stream_probe.obj"
$vcvars = Join-Path $installation "VC\Auxiliary\Build\vcvars64.bat"
$compile = (
    "`"$vcvars`" >nul && cl.exe /nologo /TC /O2 /Oi- /W4 /GS- " +
    "/DUNICODE /D_UNICODE `"$source`" /Fo:`"$object`" " +
    "/Fe:`"$output`" /link /NODEFAULTLIB /ENTRY:wmainCRTStartup " +
    "/SUBSYSTEM:CONSOLE kernel32.lib hid.lib"
)
& cmd.exe /d /s /c $compile
if ($LASTEXITCODE -ne 0) {
    throw "raw_hid_stream_probe.exe build failed with exit code $LASTEXITCODE"
}
Remove-Item -LiteralPath $object -Force -ErrorAction SilentlyContinue
Write-Output "Built $output"
