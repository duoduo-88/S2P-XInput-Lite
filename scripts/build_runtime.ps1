param(
    [string]$Python = "python",
    [string]$OutputDirectory
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$buildRoot = [System.IO.Path]::GetFullPath(
    (Join-Path $repoRoot "build")
)
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $buildRoot "runtime"
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)

function Assert-BuildPath {
    param([string]$Path)

    $resolved = [System.IO.Path]::GetFullPath($Path)
    $prefix = $buildRoot.TrimEnd("\") + "\"
    if (-not $resolved.StartsWith(
        $prefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Runtime output must stay under the repository build directory: $resolved"
    }
}

Assert-BuildPath $OutputDirectory

$pythonInfoText = & $Python -c (
    "import json, platform, sys; " +
    "print(json.dumps({" +
    "'version': platform.python_version(), " +
    "'architecture': platform.architecture()[0], " +
    "'base_prefix': sys.base_prefix" +
    "}))"
)
if ($LASTEXITCODE -ne 0) {
    throw "Could not inspect the requested Python interpreter."
}
$pythonInfo = $pythonInfoText | ConvertFrom-Json
if ($pythonInfo.version -ne "3.11.9") {
    throw "Release runtime requires Python 3.11.9; received $($pythonInfo.version)."
}
if ($pythonInfo.architecture -ne "64bit") {
    throw "Release runtime requires 64-bit Python; received $($pythonInfo.architecture)."
}

$basePrefix = [System.IO.Path]::GetFullPath($pythonInfo.base_prefix)
if (-not (Test-Path -LiteralPath (Join-Path $basePrefix "python.exe"))) {
    throw "Python base installation is incomplete: $basePrefix"
}

if (Test-Path -LiteralPath $OutputDirectory) {
    Assert-BuildPath $OutputDirectory
    Remove-Item -LiteralPath $OutputDirectory -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $OutputDirectory | Out-Null

$pythonDownloadDirectory = Join-Path $buildRoot "downloads\python"
Assert-BuildPath $pythonDownloadDirectory
if (Test-Path -LiteralPath $pythonDownloadDirectory) {
    Remove-Item -LiteralPath $pythonDownloadDirectory -Recurse -Force
}
New-Item `
    -ItemType Directory `
    -Force `
    -Path $pythonDownloadDirectory | Out-Null
$pythonArchive = Join-Path `
    $pythonDownloadDirectory "python-3.11.9-embed-amd64.zip"
$pythonArchiveUrl = (
    "https://www.python.org/ftp/python/3.11.9/" +
    "python-3.11.9-embed-amd64.zip"
)
Invoke-WebRequest `
    -UseBasicParsing `
    -Uri $pythonArchiveUrl `
    -OutFile $pythonArchive
$expectedPythonHash = (
    "009D6BF7E3B2DDCA3D784FA09F90FE54336D5B60F0E0F305C37F400BF83CFD3B"
)
$actualPythonHash = (
    Get-FileHash -LiteralPath $pythonArchive -Algorithm SHA256
).Hash
if ($actualPythonHash -ne $expectedPythonHash) {
    throw (
        "Python embeddable package hash mismatch: $actualPythonHash " +
        "!= $expectedPythonHash"
    )
}
Expand-Archive -LiteralPath $pythonArchive -DestinationPath $OutputDirectory

$tkinterSources = @(
    (Join-Path $basePrefix "DLLs\_tkinter.pyd"),
    (Join-Path $basePrefix "DLLs\tcl86t.dll"),
    (Join-Path $basePrefix "DLLs\tk86t.dll"),
    (Join-Path $basePrefix "Lib\tkinter"),
    (Join-Path $basePrefix "tcl")
)
foreach ($source in $tkinterSources) {
    if (-not (Test-Path -LiteralPath $source)) {
        throw "Required Tk source is missing from Python 3.11.9: $source"
    }
}
New-Item `
    -ItemType Directory `
    -Force `
    -Path (Join-Path $OutputDirectory "DLLs") | Out-Null
New-Item `
    -ItemType Directory `
    -Force `
    -Path (Join-Path $OutputDirectory "Lib") | Out-Null
Copy-Item `
    -LiteralPath (Join-Path $basePrefix "DLLs\_tkinter.pyd") `
    -Destination (Join-Path $OutputDirectory "DLLs\_tkinter.pyd")
Copy-Item `
    -LiteralPath (Join-Path $basePrefix "DLLs\tcl86t.dll") `
    -Destination $OutputDirectory
Copy-Item `
    -LiteralPath (Join-Path $basePrefix "DLLs\tk86t.dll") `
    -Destination $OutputDirectory
Copy-Item `
    -LiteralPath (Join-Path $basePrefix "Lib\tkinter") `
    -Destination (Join-Path $OutputDirectory "Lib") `
    -Recurse
Copy-Item `
    -LiteralPath (Join-Path $basePrefix "tcl") `
    -Destination $OutputDirectory `
    -Recurse

$sitePackages = Join-Path $OutputDirectory "Lib\site-packages"
New-Item -ItemType Directory -Force -Path $sitePackages | Out-Null

$toolEnvironment = Join-Path $buildRoot "build-tools"
$toolPython = Join-Path $toolEnvironment "Scripts\python.exe"
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

& $toolPython -m pip install `
    --disable-pip-version-check `
    --no-deps `
    --only-binary=:all: `
    --target $sitePackages `
    -r (Join-Path $repoRoot "src\requirements-binary.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Could not install the pinned binary runtime dependencies."
}

$downloadDirectory = Join-Path $buildRoot "downloads\vgamepad"
Assert-BuildPath $downloadDirectory
if (Test-Path -LiteralPath $downloadDirectory) {
    Remove-Item -LiteralPath $downloadDirectory -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $downloadDirectory | Out-Null

$vgamepadArchive = Join-Path $downloadDirectory "vgamepad-0.1.0.tar.gz"
$vgamepadUrl = (
    "https://files.pythonhosted.org/packages/8a/54/" +
    "0eaddc33f84247963af078f364b37153d09fcd6cdc398f243ec3e8842c56/" +
    "vgamepad-0.1.0.tar.gz"
)
Invoke-WebRequest `
    -UseBasicParsing `
    -Uri $vgamepadUrl `
    -OutFile $vgamepadArchive
$expectedVgamepadHash = (
    "57F6BD01AEC0C172947517FB782D150EF9B285F7F4D524C317374FA5C24A89DE"
)
$actualVgamepadHash = (
    Get-FileHash -LiteralPath $vgamepadArchive -Algorithm SHA256
).Hash
if ($actualVgamepadHash -ne $expectedVgamepadHash) {
    throw (
        "vgamepad source hash mismatch: $actualVgamepadHash " +
        "!= $expectedVgamepadHash"
    )
}

$vgamepadExtract = Join-Path $downloadDirectory "vgamepad-extracted"
New-Item -ItemType Directory -Force -Path $vgamepadExtract | Out-Null
& tar.exe -xf $vgamepadArchive -C $vgamepadExtract
if ($LASTEXITCODE -ne 0) {
    throw "Could not extract the verified vgamepad source distribution."
}
$vgamepadSource = Join-Path $vgamepadExtract "vgamepad-0.1.0"
$vgamepadPackage = Join-Path $vgamepadSource "vgamepad"
if (-not (Test-Path -LiteralPath $vgamepadPackage -PathType Container)) {
    throw "The verified vgamepad archive has an unexpected layout."
}
Copy-Item -LiteralPath $vgamepadPackage -Destination $sitePackages -Recurse

# setup.py launches an MSI even while pip is only preparing wheel metadata.
# Copy the verified pure-Python package directly and exclude those installers;
# the application already owns an explicit, integrity-checked driver workflow.
$embeddedInstallers = Join-Path `
    $sitePackages "vgamepad\win\vigem\install"
if (Test-Path -LiteralPath $embeddedInstallers) {
    Assert-BuildPath $embeddedInstallers
    Remove-Item -LiteralPath $embeddedInstallers -Recurse -Force
}
$vgamepadDistInfo = Join-Path $sitePackages "vgamepad-0.1.0.dist-info"
New-Item -ItemType Directory -Force -Path $vgamepadDistInfo | Out-Null
Copy-Item `
    -LiteralPath (Join-Path $vgamepadSource "PKG-INFO") `
    -Destination (Join-Path $vgamepadDistInfo "METADATA")
Copy-Item `
    -LiteralPath (Join-Path $vgamepadSource "LICENSE") `
    -Destination (Join-Path $vgamepadDistInfo "LICENSE")
Set-Content `
    -LiteralPath (Join-Path $vgamepadDistInfo "INSTALLER") `
    -Value "s2p-verified-source" `
    -Encoding ascii
$vgamepadWheelMetadata = @"
Wheel-Version: 1.0
Generator: S2P-XInput-Lite verified source extraction
Root-Is-Purelib: true
Tag: py3-none-any
"@
Set-Content `
    -LiteralPath (Join-Path $vgamepadDistInfo "WHEEL") `
    -Value $vgamepadWheelMetadata `
    -Encoding ascii `
    -NoNewline

$vgamepadVersion = & $toolPython -c (
    "import importlib.metadata as m, sys; " +
    "sys.path.insert(0, r'$sitePackages'); print(m.version('vgamepad'))"
)
if ($LASTEXITCODE -ne 0 -or $vgamepadVersion -ne "0.1.0") {
    throw "The safely extracted vgamepad package failed metadata validation."
}

$pybluezCommit = "82cbba8a1ebd4c1e3442dfafd8581d58c50fa39e"
$pybluezArchive = Join-Path $downloadDirectory "pybluez-$pybluezCommit.tar.gz"
$pybluezUrl = "https://github.com/pybluez/pybluez/archive/$pybluezCommit.tar.gz"
Invoke-WebRequest `
    -UseBasicParsing `
    -Uri $pybluezUrl `
    -OutFile $pybluezArchive
$expectedPybluezHash = (
    "3B80237F87B51AD7D4F06ACBE7071D43B5093FD724D957F782008A17F9A4E64A"
)
$actualPybluezHash = (
    Get-FileHash -LiteralPath $pybluezArchive -Algorithm SHA256
).Hash
if ($actualPybluezHash -ne $expectedPybluezHash) {
    throw (
        "PyBluez source hash mismatch: $actualPybluezHash " +
        "!= $expectedPybluezHash"
    )
}

$vswhere = Join-Path ${env:ProgramFiles(x86)} `
    "Microsoft Visual Studio\Installer\vswhere.exe"
if (-not (Test-Path -LiteralPath $vswhere -PathType Leaf)) {
    throw "Visual Studio Installer's vswhere.exe was not found."
}
$installation = & $vswhere -latest -products * `
    -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 `
    -property installationPath
if (-not $installation) {
    throw "Visual Studio C++ Build Tools were not found."
}
$vcvars = Join-Path $installation "VC\Auxiliary\Build\vcvars64.bat"
$pybluezInstall = (
    "call `"$vcvars`" >nul && " +
    "`"$toolPython`" -m pip install --disable-pip-version-check " +
    "--no-deps --no-build-isolation --target `"$sitePackages`" " +
    "`"$pybluezArchive`""
)
& cmd.exe /d /s /c $pybluezInstall
if ($LASTEXITCODE -ne 0) {
    throw "Could not build the verified PyBluez source distribution."
}

$pathConfiguration = @"
python311.zip
.
Lib
DLLs
Lib\site-packages
..\src

import site
"@
Set-Content `
    -LiteralPath (Join-Path $OutputDirectory "python311._pth") `
    -Value $pathConfiguration `
    -Encoding ascii `
    -NoNewline

$siteCustomization = @"
"""Keep the portable runtime free of generated bytecode cache files."""

import sys


sys.dont_write_bytecode = True
"@
Set-Content `
    -LiteralPath (Join-Path $sitePackages "sitecustomize.py") `
    -Value $siteCustomization `
    -Encoding utf8 `
    -NoNewline

Get-ChildItem -LiteralPath $OutputDirectory -Directory -Recurse -Force |
    Where-Object { $_.Name -eq "__pycache__" } |
    Sort-Object FullName -Descending |
    ForEach-Object {
        Assert-BuildPath $_.FullName
        Remove-Item -LiteralPath $_.FullName -Recurse -Force
    }
Get-ChildItem -LiteralPath $OutputDirectory -File -Recurse -Force |
    Where-Object { $_.Extension -in @(".pyc", ".pyo") } |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Force }

$runtimePython = Join-Path $OutputDirectory "python.exe"
# Importing vgamepad itself immediately connects to ViGEmBus. A clean build
# runner does not have that driver, so validate the package metadata and load
# its native client DLL without constructing a virtual controller.
$importCheck = (
    "import ctypes, importlib.metadata as metadata, " +
    "importlib.util as util, pathlib, tkinter; " +
    "import serial, bleak, bluetooth, pyaudiowpatch, numpy, " +
    "imufusion, hid, usb, libusb_package; " +
    "spec = util.find_spec('vgamepad'); " +
    "assert spec is not None and spec.submodule_search_locations; " +
    "package = pathlib.Path(next(iter(spec.submodule_search_locations))); " +
    "client = package / 'win' / 'vigem' / 'client' / 'x64' / " +
    "'ViGEmClient.dll'; " +
    "assert metadata.version('vgamepad') == '0.1.0' and client.is_file(); " +
    "ctypes.CDLL(str(client)); " +
    "print('portable runtime imports passed')"
)
& $runtimePython -c $importCheck
if ($LASTEXITCODE -ne 0) {
    throw "The generated portable runtime failed its import check."
}

Write-Output "Portable runtime is ready in $OutputDirectory"
