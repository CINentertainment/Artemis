param([switch]$SkipOCR,[switch]$SkipBackups)
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$python = Join-Path $project '.venv\Scripts\python.exe'
$base = 'http://127.0.0.1:8200'
Invoke-RestMethod "$base/capabilities/status" -TimeoutSec 30 | Out-Null
if (-not $SkipOCR) {
    $check = Invoke-RestMethod "$base/capabilities/check-ocr" -Method Post -TimeoutSec 60
    if (-not $check.passed) {
        if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { throw 'Windows App Installer (winget) is required for automatic OCR installation. Install Tesseract from https://github.com/tesseract-ocr/tesseract and rerun this script.' }
        Write-Host 'Installing the Tesseract OCR package through Windows Package Manager. Windows may request administrator approval.'
        & winget install --id tesseract-ocr.tesseract --exact --source winget --accept-package-agreements --accept-source-agreements
        if ($LASTEXITCODE -ne 0) { throw 'OCR installation did not finish. Resolve the winget message and rerun; backups have not been changed.' }
        $check = Invoke-RestMethod "$base/capabilities/check-ocr" -Method Post -TimeoutSec 60
        if (-not $check.passed) { throw 'OCR installation did not pass the known-input check. Verify English language data, restart Artemis, and rerun. Backups have not been changed.' }
    }
    Write-Host 'PASS: local OCR recognized the known-input image. This is not a guarantee for every scan.'
}
if (-not $SkipBackups) {
    $status = Invoke-RestMethod "$base/continuity/status"
    if (-not $status.configured) {
        $discovery = Invoke-RestMethod "$base/continuity/discover"
        $paths = @{}
        foreach ($label in @('artemis','notes','risk_tracker','market_lens')) {
            $candidates = @($discovery.candidates.$label)
            if ($candidates.Count -eq 1) { $paths[$label] = $candidates[0] }
            else { Write-Host "$label database candidates:"; $candidates | ForEach-Object { Write-Host $_ }; $paths[$label] = Read-Host "Full path to the LIVE $label SQLite database" }
            Write-Host "$label selected: $($paths[$label])"
        }
        $destination = Read-Host "Backup directory (Enter uses $($discovery.default_destination); another drive is preferable)"
        if ([string]::IsNullOrWhiteSpace($destination)) { $destination = $discovery.default_destination }
        Write-Host 'Save the recovery passphrase independently in your password manager. It must have at least 16 characters.'
        $secure = Read-Host 'New recovery passphrase' -AsSecureString
        $confirm = Read-Host 'Repeat recovery passphrase' -AsSecureString
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        $confirmPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($confirm)
        try {
            $password = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
            $repeat = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($confirmPointer)
            if ($password -cne $repeat -or $password.Length -lt 16) { throw 'Passphrases must match and contain at least 16 characters.' }
            $body = @{ enabled=$true; passphrase=$password; destination=$destination; databases=$paths; interval_hours=24; retain=7; include_workspace=$true; include_reports=$true; include_settings=$true } | ConvertTo-Json -Depth 5
            Invoke-RestMethod "$base/continuity/settings" -Method Put -ContentType 'application/json' -Body ([System.Text.Encoding]::UTF8.GetBytes($body)) | Out-Null
        } finally {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($confirmPointer)
            $password=$null; $repeat=$null; $body=$null; $secure.Dispose(); $confirm.Dispose()
        }
        Write-Host 'Backups configured for every 24 hours, retaining seven verified archives. The first backup is due now.'
    } elseif (-not $status.settings.enabled) {
        throw 'An existing backup configuration is paused. Enable its schedule in Backups & recovery; this script preserved your saved policy.'
    } else {
        Write-Host 'Existing enabled backup configuration preserved.'
        if ($status.status.latest) {
            Invoke-RestMethod "$base/continuity/rehearse" -Method Post -TimeoutSec 300 | Out-Null
            Write-Host 'PASS: latest saved backup passed a staged restore rehearsal. No live databases were replaced.'
        }
    }
    Start-Process "$base/static/continuity.html"
    Write-Host 'The backup page shows completion or errors. New backups include an automatic staged restore rehearsal before success is recorded.'
}
Invoke-RestMethod "$base/capabilities/review" -Method Post -TimeoutSec 60 | Out-Null
Write-Host 'Readiness setup finished. Ask Artemis: What capabilities do you need that you do not have, and why would they help my goals?'
