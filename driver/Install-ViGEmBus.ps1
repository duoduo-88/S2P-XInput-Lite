$ErrorActionPreference = "Stop"

$FileName = "ViGEmBus_1.22.0_x64_x86_arm64.exe"
$DownloadUrl = "https://github.com/nefarius/ViGEmBus/releases/download/v1.22.0/ViGEmBus_1.22.0_x64_x86_arm64.exe"
$InstallerPath = "$env:TEMP\$FileName"

Write-Host ""
Write-Host "========================================"
Write-Host "        ViGEmBus 1.22.0 Installer"
Write-Host "========================================"
Write-Host ""
Write-Host "This script will download ViGEmBus 1.22.0"
Write-Host "from the official Nefarius GitHub release."
Write-Host ""
Write-Host "Download URL:"
Write-Host $DownloadUrl
Write-Host ""
Write-Host "Temporary file:"
Write-Host $InstallerPath
Write-Host ""

try {
    Write-Host "Downloading ViGEmBus..."

    Invoke-WebRequest `
        -Uri $DownloadUrl `
        -OutFile $InstallerPath `
        -UseBasicParsing

    if (-not (Test-Path $InstallerPath)) {
        throw "The installer was not downloaded."
    }

    Write-Host ""
    Write-Host "Download completed."
    Write-Host "Starting the ViGEmBus installer..."
    Write-Host ""

    Start-Process `
        -FilePath $InstallerPath `
        -Verb RunAs `
        -Wait

    Write-Host ""
    Write-Host "ViGEmBus installer has finished."
}
catch {
    Write-Host ""
    Write-Host "Failed to download or start ViGEmBus."
    Write-Host $_.Exception.Message
}
finally {
    if (
        $InstallerPath -and
        (Test-Path $InstallerPath)
    ) {
        Remove-Item `
            $InstallerPath `
            -Force `
            -ErrorAction SilentlyContinue
    }
}

Write-Host ""
Read-Host "Press Enter to close"