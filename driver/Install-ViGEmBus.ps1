$ErrorActionPreference = "Stop"

$FileName = "ViGEmBus_1.22.0_x64_x86_arm64.exe"
$DownloadUrl = "https://github.com/nefarius/ViGEmBus/releases/download/v1.22.0/ViGEmBus_1.22.0_x64_x86_arm64.exe"
$ExpectedSha256 = "89220A7865076B342892F98865F3499FB7C4CFD673159E89D352C360FD014C6A"
$ExpectedSignerName = "Nefarius Software Solutions e.U."
$InstallerPath = "$env:TEMP\$FileName"
$InstallerExitCode = 1

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
    Write-Host "Verifying SHA-256..."

    $ActualSha256 = (
        Get-FileHash `
            -LiteralPath $InstallerPath `
            -Algorithm SHA256
    ).Hash.ToUpperInvariant()

    if ($ActualSha256 -ne $ExpectedSha256) {
        throw (
            "SHA-256 verification failed. Expected {0}, received {1}." -f `
                $ExpectedSha256,
                $ActualSha256
        )
    }

    Write-Host "Verifying Authenticode signature..."

    $Signature = Get-AuthenticodeSignature `
        -LiteralPath $InstallerPath

    if (
        $Signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid
    ) {
        throw (
            "Authenticode verification failed: {0}" -f `
                $Signature.StatusMessage
        )
    }

    $SignerSubject = $Signature.SignerCertificate.Subject
    if (
        [string]::IsNullOrWhiteSpace($SignerSubject) -or
        $SignerSubject -notlike "*CN=$ExpectedSignerName,*"
    ) {
        throw (
            "Unexpected Authenticode signer: {0}" -f `
                $SignerSubject
        )
    }

    Write-Host "Installer integrity and publisher verified."
    Write-Host "Starting the ViGEmBus installer..."
    Write-Host ""

    $InstallerProcess = Start-Process `
        -FilePath $InstallerPath `
        -Verb RunAs `
        -Wait `
        -PassThru

    $InstallerExitCode = $InstallerProcess.ExitCode
    if ($InstallerExitCode -notin @(0, 3010)) {
        throw "ViGEmBus installer returned exit code $InstallerExitCode."
    }

    Write-Host ""
    if ($InstallerExitCode -eq 3010) {
        Write-Host "ViGEmBus installer has finished. Windows must be restarted."
    }
    else {
        Write-Host "ViGEmBus installer has finished."
    }
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
exit $InstallerExitCode
