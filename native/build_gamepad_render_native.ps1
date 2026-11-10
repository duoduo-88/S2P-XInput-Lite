param(
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $repoRoot "src"
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$objectDirectory = Join-Path $repoRoot "build\native-render-objects"
New-Item -ItemType Directory -Force -Path $OutputDirectory, $objectDirectory | Out-Null
$vswhere = Join-Path ${env:ProgramFiles(x86)} `
    "Microsoft Visual Studio\Installer\vswhere.exe"
$installation = & $vswhere -latest -products * `
    -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
    -property installationPath
if (-not $installation) {
    throw "Visual Studio C++ Build Tools were not found."
}
$vcvars = Join-Path $installation "VC\Auxiliary\Build\vcvars64.bat"
$projection = Join-Path $PSScriptRoot "gamepad_aim_projection.cpp"
$trail = Join-Path $PSScriptRoot "gamepad_trail_raster.cpp"
$output = Join-Path $OutputDirectory "gamepad_render_native.dll"
$library = Join-Path $objectDirectory "gamepad_render_native.lib"
$compile = (
    "`"$vcvars`" >nul && cl.exe /nologo /std:c++17 /O2 /MT /EHsc /W4 /LD " +
    "`"$projection`" `"$trail`" /Fe:`"$output`" /link /IMPLIB:`"$library`""
)
Push-Location -LiteralPath $objectDirectory
try {
    & cmd.exe /d /s /c $compile
    if ($LASTEXITCODE -ne 0) {
        throw "gamepad_render_native.dll build failed with exit code $LASTEXITCODE"
    }
}
finally {
    Pop-Location
}
Write-Output "Built $output"
