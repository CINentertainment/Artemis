Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$artemisPath = Split-Path -Parent $PSScriptRoot

$runtimePath = Join-Path `
    $artemisPath `
    ".runtime"


function Stop-TrackedProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$PidFileName,

        [Parameter(Mandatory = $true)]
        [string]$ExpectedCommandText
    )

    $pidFile = Join-Path `
        $runtimePath `
        $PidFileName

    if (-not (Test-Path $pidFile)) {
        Write-Host `
            "[SKIPPED] $Name was not started by the Artemis launcher." `
            -ForegroundColor DarkGray

        return
    }

    $rawProcessId = (
        Get-Content `
            $pidFile `
            -ErrorAction SilentlyContinue |
        Select-Object -First 1
    )

    $trackedProcessId = 0

    $validProcessId = [int]::TryParse(
        $rawProcessId,
        [ref]$trackedProcessId
    )

    if (
        -not $validProcessId -or
        $trackedProcessId -le 0
    ) {
        Write-Host `
            "[STALE] $Name has an invalid process record." `
            -ForegroundColor Yellow

        Remove-Item `
            $pidFile `
            -Force `
            -ErrorAction SilentlyContinue

        return
    }

    $processDetails = Get-CimInstance `
        -ClassName Win32_Process `
        -Filter "ProcessId = $trackedProcessId" `
        -ErrorAction SilentlyContinue

    if ($null -eq $processDetails) {
        Write-Host `
            "[STOPPED] $Name is already stopped." `
            -ForegroundColor DarkGray

        Remove-Item `
            $pidFile `
            -Force `
            -ErrorAction SilentlyContinue

        return
    }

    $commandLine = [string](
        $processDetails.CommandLine
    )

    if (
        [string]::IsNullOrWhiteSpace(
            $commandLine
        ) -or
        $commandLine -notlike (
            "*$ExpectedCommandText*"
        )
    ) {
        Write-Host `
            "[SKIPPED] $Name process identity could not be verified safely." `
            -ForegroundColor Yellow

        Remove-Item `
            $pidFile `
            -Force `
            -ErrorAction SilentlyContinue

        return
    }

    Write-Host `
        "[STOPPING] $Name..." `
        -ForegroundColor Yellow

    Stop-Process `
        -Id $trackedProcessId `
        -ErrorAction SilentlyContinue

    $deadline = (
        Get-Date
    ).AddSeconds(5)

    do {
        Start-Sleep -Milliseconds 250

        $processStillRunning = Get-Process `
            -Id $trackedProcessId `
            -ErrorAction SilentlyContinue
    }
    while (
        $null -ne $processStillRunning -and
        (Get-Date) -lt $deadline
    )

    if ($null -ne $processStillRunning) {
        Write-Host `
            "[WARNING] $Name did not stop within the expected time." `
            -ForegroundColor Red

        return
    }

    Remove-Item `
        $pidFile `
        -Force `
        -ErrorAction SilentlyContinue

    Write-Host `
        "[STOPPED] $Name" `
        -ForegroundColor Green
}


try {
    Write-Host ""

    Write-Host `
        "Stopping Artemis services..." `
        -ForegroundColor Magenta

    Write-Host ""

    if (-not (Test-Path $runtimePath)) {
        Write-Host `
            "No Artemis-managed processes were found." `
            -ForegroundColor DarkGray

        exit 0
    }

    Stop-TrackedProcess `
        -Name "Artemis" `
        -PidFileName "artemis.pid" `
        -ExpectedCommandText "--port 8200"

    Stop-TrackedProcess `
        -Name "Market Lens" `
        -PidFileName "market-lens.pid" `
        -ExpectedCommandText "--port 8003"

    Stop-TrackedProcess `
        -Name "Risk Tracker" `
        -PidFileName "risk-tracker.pid" `
        -ExpectedCommandText "--port 8000"

    Stop-TrackedProcess `
        -Name "Intel Notes" `
        -PidFileName "intel-notes.pid" `
        -ExpectedCommandText "--port 8001"

    Stop-TrackedProcess `
        -Name "Chroma" `
        -PidFileName "chroma.pid" `
        -ExpectedCommandText "--port 8100"

    Stop-TrackedProcess `
        -Name "Ollama" `
        -PidFileName "ollama.pid" `
        -ExpectedCommandText "ollama"

    Write-Host ""

    Write-Host `
        "Artemis-managed services are stopped." `
        -ForegroundColor Green
}
catch {
    Write-Host ""

    Write-Host `
        "Artemis shutdown encountered an error." `
        -ForegroundColor Red

    Write-Host `
        $_.Exception.Message `
        -ForegroundColor Red

    Write-Host ""

    Write-Host "Press Enter to close this window."

    Read-Host | Out-Null

    exit 1
}