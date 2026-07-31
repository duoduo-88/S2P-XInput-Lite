param(
    [string]$Python = "python",
    [string]$IdfPath = $env:IDF_PATH,
    [switch]$UseExistingBuildOutputs,
    [switch]$SkipTests,
    [switch]$SkipFirmwareBuild,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$repoRoot = Split-Path -Parent $PSScriptRoot
$buildRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "build"))
$distRoot = [System.IO.Path]::GetFullPath((Join-Path $repoRoot "dist"))

function Assert-BuildPath {
    param([string]$Path)

    $resolved = [System.IO.Path]::GetFullPath($Path)
    $prefix = $buildRoot.TrimEnd("\") + "\"
    if (-not $resolved.StartsWith(
        $prefix,
        [System.StringComparison]::OrdinalIgnoreCase
    )) {
        throw "Release staging path must stay under the repository build directory."
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

function Copy-ReleaseFile {
    param(
        [string]$Source,
        [string]$RelativePath,
        [string]$DestinationRoot
    )

    if (-not (Test-Path -LiteralPath $Source -PathType Leaf)) {
        throw "Release source file is missing: $Source"
    }
    $destination = Join-Path $DestinationRoot $RelativePath
    $parent = Split-Path -Parent $destination
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    Copy-Item -LiteralPath $Source -Destination $destination -Force
}

function Copy-CuratedTree {
    param(
        [string]$SourceRoot,
        [string]$RelativeRoot,
        [string[]]$Extensions,
        [string]$DestinationRoot
    )

    Get-ChildItem -LiteralPath $SourceRoot -File -Recurse |
        Where-Object {
            $_.Extension.ToLowerInvariant() -in $Extensions
        } |
        ForEach-Object {
            $child = Get-RelativePath -BasePath $SourceRoot -Path $_.FullName
            Copy-ReleaseFile `
                -Source $_.FullName `
                -RelativePath (Join-Path $RelativeRoot $child) `
                -DestinationRoot $DestinationRoot
        }
}

$versionFile = Join-Path $repoRoot "src\version.py"
$versionMatch = Select-String `
    -LiteralPath $versionFile `
    -Pattern '^\s*VERSION\s*=\s*"([^"]+)"\s*$'
if (-not $versionMatch) {
    throw "Could not read VERSION from src\version.py."
}
$version = $versionMatch.Matches[0].Groups[1].Value
$versionSlug = $version -replace "\s+", "-"
$packageName = "S2P-XInput-Lite-v$versionSlug"
$releaseNotes = Join-Path $repoRoot "RELEASE_NOTES_v$versionSlug.md"
if (-not (Test-Path -LiteralPath $releaseNotes -PathType Leaf)) {
    throw "Release notes are missing for ${version}: $releaseNotes"
}

$runtimeOutput = Join-Path $buildRoot "runtime"
$launcherOutput = Join-Path $buildRoot "launchers"
$launcherMetadataPath = Join-Path $launcherOutput "LAUNCHER_BUILD.json"
$nativeOutput = Join-Path $buildRoot "native"
if (-not $UseExistingBuildOutputs) {
    & (Join-Path $PSScriptRoot "build_runtime.ps1") `
        -Python $Python `
        -OutputDirectory $runtimeOutput
    & (Join-Path $PSScriptRoot "build_launchers.ps1") `
        -Python $Python `
        -OutputDirectory $launcherOutput
    & (Join-Path $PSScriptRoot "build_native_helpers.ps1") `
        -OutputDirectory $nativeOutput
}

foreach ($requiredBuild in @(
    (Join-Path $runtimeOutput "python.exe"),
    (Join-Path $launcherOutput "S2P-XInput-Lite.exe"),
    (Join-Path $launcherOutput "GamepadTester.exe"),
    $launcherMetadataPath,
    (Join-Path $nativeOutput "raw_hid_probe.exe"),
    (Join-Path $nativeOutput "raw_hid_stream_probe.exe")
)) {
    if (-not (Test-Path -LiteralPath $requiredBuild -PathType Leaf)) {
        throw "Required build output is missing: $requiredBuild"
    }
}

try {
    $launcherMetadata = Get-Content `
        -LiteralPath $launcherMetadataPath `
        -Raw `
        -Encoding utf8 | ConvertFrom-Json
}
catch {
    throw "Launcher build metadata is invalid: $($_.Exception.Message)"
}
if (
    $launcherMetadata.schema_version -ne 1 -or
    $launcherMetadata.source_version -ne $version
) {
    throw "Launcher build version does not match source VERSION $version. Rebuild the launchers."
}
foreach ($name in @("S2P-XInput-Lite.exe", "GamepadTester.exe")) {
    $entry = $launcherMetadata.launchers.PSObject.Properties[$name].Value
    if ($null -eq $entry -or $entry.sha256 -notmatch '^[0-9A-Fa-f]{64}$') {
        throw "Launcher build metadata is missing a valid hash for $name."
    }
    $actualHash = (
        Get-FileHash `
            -LiteralPath (Join-Path $launcherOutput $name) `
            -Algorithm SHA256
    ).Hash
    if ($actualHash -ne $entry.sha256) {
        throw "Launcher build hash does not match $name. Rebuild the launchers."
    }
}

if (-not $SkipTests) {
    $toolPython = Join-Path $buildRoot "build-tools\Scripts\python.exe"
    & $toolPython -m ruff check `
        (Join-Path $repoRoot "src") `
        (Join-Path $repoRoot "tests")
    if ($LASTEXITCODE -ne 0) {
        throw "Ruff correctness checks failed."
    }
    $testPython = Join-Path $runtimeOutput "python.exe"
    & $testPython (Join-Path $repoRoot "tests\run_tests.py")
    if ($LASTEXITCODE -ne 0) {
        throw "Automated tests failed."
    }
}

if (-not $SkipFirmwareBuild) {
    & (Join-Path $PSScriptRoot "build_firmware.ps1") -IdfPath $IdfPath
}

$stagingParent = Join-Path $buildRoot "release-staging"
$packageRoot = Join-Path $stagingParent $packageName
Assert-BuildPath $stagingParent
if (Test-Path -LiteralPath $stagingParent) {
    Remove-Item -LiteralPath $stagingParent -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $packageRoot | Out-Null

foreach ($name in @(
    "LICENSE",
    "README.md",
    "README_zh-TW.md",
    "THIRD_PARTY_NOTICES.md"
)) {
    Copy-ReleaseFile `
        -Source (Join-Path $repoRoot $name) `
        -RelativePath $name `
        -DestinationRoot $packageRoot
}
Copy-CuratedTree `
    -SourceRoot (Join-Path $repoRoot "third_party") `
    -RelativeRoot "third_party" `
    -Extensions @("", ".md", ".txt", ".zip") `
    -DestinationRoot $packageRoot
Copy-ReleaseFile `
    -Source $releaseNotes `
    -RelativePath (Split-Path -Leaf $releaseNotes) `
    -DestinationRoot $packageRoot

Copy-CuratedTree `
    -SourceRoot (Join-Path $repoRoot "driver") `
    -RelativeRoot "driver" `
    -Extensions @(".bat", ".ps1") `
    -DestinationRoot $packageRoot
Copy-CuratedTree `
    -SourceRoot (Join-Path $repoRoot "image") `
    -RelativeRoot "image" `
    -Extensions @(".png", ".jpg", ".jpeg", ".gif", ".ico") `
    -DestinationRoot $packageRoot
Copy-CuratedTree `
    -SourceRoot (Join-Path $repoRoot "manual\assets\annotated") `
    -RelativeRoot "manual\assets\annotated" `
    -Extensions @(".png") `
    -DestinationRoot $packageRoot
foreach ($name in @("USER_GUIDE.md", "USER_GUIDE_zh-TW.md")) {
    Copy-ReleaseFile `
        -Source (Join-Path $repoRoot "manual\$name") `
        -RelativePath "manual\$name" `
        -DestinationRoot $packageRoot
}
Copy-CuratedTree `
    -SourceRoot (Join-Path $repoRoot "esp32s3\firmware") `
    -RelativeRoot "esp32s3\firmware" `
    -Extensions @(".bin") `
    -DestinationRoot $packageRoot
Copy-ReleaseFile `
    -Source (Join-Path $repoRoot "esp32s3\tools\esptool.exe") `
    -RelativePath "esp32s3\tools\esptool.exe" `
    -DestinationRoot $packageRoot

Copy-CuratedTree `
    -SourceRoot (Join-Path $repoRoot "src") `
    -RelativeRoot "src" `
    -Extensions @(".py", ".c") `
    -DestinationRoot $packageRoot
foreach ($name in @("requirements.txt", "requirements-binary.txt")) {
    Copy-ReleaseFile `
        -Source (Join-Path $repoRoot "src\$name") `
        -RelativePath "src\$name" `
        -DestinationRoot $packageRoot
}
Copy-ReleaseFile `
    -Source (Join-Path $repoRoot "src\layers\mouse.json") `
    -RelativePath "src\layers\mouse.json" `
    -DestinationRoot $packageRoot

$packagedProfiles = Join-Path $packageRoot "src\profiles"
if (Test-Path -LiteralPath $packagedProfiles) {
    Remove-Item -LiteralPath $packagedProfiles -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $packagedProfiles | Out-Null
foreach ($name in @(
    "Action.ini",
    "Audio.ini",
    "FPS-COMP.ini",
    "FPS-IMM.ini",
    "General.ini",
    "Racing.ini",
    "Rhythm.ini",
    "System Default.ini"
)) {
    Copy-ReleaseFile `
        -Source (Join-Path $repoRoot "src\profiles\$name") `
        -RelativePath "src\profiles\$name" `
        -DestinationRoot $packageRoot
}

Copy-Item `
    -LiteralPath $runtimeOutput `
    -Destination (Join-Path $packageRoot "runtime") `
    -Recurse
Copy-ReleaseFile `
    -Source (Join-Path $launcherOutput "S2P-XInput-Lite.exe") `
    -RelativePath "S2P-XInput-Lite.exe" `
    -DestinationRoot $packageRoot
Copy-ReleaseFile `
    -Source (Join-Path $launcherOutput "GamepadTester.exe") `
    -RelativePath "GamepadTester.exe" `
    -DestinationRoot $packageRoot
Copy-ReleaseFile `
    -Source $launcherMetadataPath `
    -RelativePath "LAUNCHER_BUILD.json" `
    -DestinationRoot $packageRoot
Copy-ReleaseFile `
    -Source (Join-Path $nativeOutput "raw_hid_probe.exe") `
    -RelativePath "src\raw_hid_probe.exe" `
    -DestinationRoot $packageRoot
Copy-ReleaseFile `
    -Source (Join-Path $nativeOutput "raw_hid_stream_probe.exe") `
    -RelativePath "src\raw_hid_stream_probe.exe" `
    -DestinationRoot $packageRoot

Get-ChildItem -LiteralPath $packageRoot -Directory -Recurse -Force |
    Where-Object { $_.Name -eq "__pycache__" } |
    Sort-Object FullName -Descending |
    ForEach-Object { Remove-Item -LiteralPath $_.FullName -Recurse -Force }

$manifestPath = Join-Path $packageRoot "SHA256SUMS.txt"
$manifestLines = @(
    Get-ChildItem -LiteralPath $packageRoot -File -Recurse |
        Sort-Object FullName |
        ForEach-Object {
            $relative = (
                Get-RelativePath -BasePath $packageRoot -Path $_.FullName
            ).Replace("\", "/")
            $hash = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash
            "$hash  $relative"
        }
)
Set-Content `
    -LiteralPath $manifestPath `
    -Value $manifestLines `
    -Encoding utf8

New-Item -ItemType Directory -Force -Path $distRoot | Out-Null
$zipPath = Join-Path $distRoot "$packageName.zip"
$hashPath = "$zipPath.sha256"
if ((Test-Path -LiteralPath $zipPath) -or (Test-Path -LiteralPath $hashPath)) {
    if (-not $Force) {
        throw "Release output already exists. Pass -Force to replace it: $zipPath"
    }
    Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $hashPath -Force -ErrorAction SilentlyContinue
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem
$commitEpochText = & git -C $repoRoot log -1 --format=%ct
if ($LASTEXITCODE -eq 0 -and $commitEpochText -match '^\d+$') {
    $archiveTimestamp = [DateTimeOffset]::FromUnixTimeSeconds(
        [long]$commitEpochText
    )
}
else {
    $archiveTimestamp = [DateTimeOffset](
        (Get-Item -LiteralPath $versionFile).LastWriteTime
    )
}
$zipStream = [System.IO.File]::Open(
    $zipPath,
    [System.IO.FileMode]::CreateNew
)
$archive = New-Object System.IO.Compression.ZipArchive(
    $zipStream,
    [System.IO.Compression.ZipArchiveMode]::Create,
    $false
)
try {
    foreach ($file in (
        Get-ChildItem -LiteralPath $packageRoot -File -Recurse |
            Sort-Object FullName
    )) {
        $relative = (
            Get-RelativePath -BasePath $packageRoot -Path $file.FullName
        ).Replace("\", "/")
        $entry = $archive.CreateEntry(
            "$packageName/$relative",
            [System.IO.Compression.CompressionLevel]::Optimal
        )
        $entry.LastWriteTime = $archiveTimestamp
        $input = [System.IO.File]::OpenRead($file.FullName)
        $output = $entry.Open()
        try {
            $input.CopyTo($output)
        }
        finally {
            $output.Dispose()
            $input.Dispose()
        }
    }
}
finally {
    $archive.Dispose()
    $zipStream.Dispose()
}

$zipHash = (Get-FileHash -LiteralPath $zipPath -Algorithm SHA256).Hash
Set-Content `
    -LiteralPath $hashPath `
    -Value "$zipHash  $(Split-Path -Leaf $zipPath)" `
    -Encoding ascii

& (Join-Path $PSScriptRoot "verify_release.ps1") -ZipPath $zipPath
Write-Output "Release package: $zipPath"
Write-Output "SHA-256: $zipHash"
