Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$artemisPath = Split-Path -Parent $PSScriptRoot
$runtimePath = Join-Path $artemisPath ".runtime"
$script:hadWarnings = $false


function Test-LocalPort {
    param([int]$Port)

    $client = New-Object System.Net.Sockets.TcpClient

    try {
        $connection = $client.BeginConnect(
            "127.0.0.1", $Port, $null, $null
        )

        if (-not $connection.AsyncWaitHandle.WaitOne(500, $false)) {
            return $false
        }

        $client.EndConnect($connection)
        return $true
    }
    catch {
        return $false
    }
    finally {
        $client.Dispose()
    }
}


function Stop-TrackedProcess {
    param(
        [string]$Name,
        [string]$PidFileName,
        [string]$ExpectedCommandPattern,
        [switch]$SearxNG
    )

    $pidFile = Join-Path $runtimePath $PidFileName

    if (-not (Test-Path -LiteralPath $pidFile)) {
        Write-Host `
            "[SKIPPED] $Name has no launcher process record." `
            -ForegroundColor DarkGray
        return
    }

    $rawProcessId = Get-Content -LiteralPath $pidFile |
        Select-Object -First 1

    $trackedProcessId = 0
    if (
        -not [int]::TryParse(
            [string]$rawProcessId,
            [ref]$trackedProcessId
        ) -or
        $trackedProcessId -le 0
    ) {
        Write-Host `
            "[STALE] Invalid process record for $Name." `
            -ForegroundColor Yellow

        Remove-Item -LiteralPath $pidFile -Force
        return
    }

    $details = Get-CimInstance `
        -ClassName Win32_Process `
        -Filter "ProcessId = $trackedProcessId" `
        -ErrorAction SilentlyContinue

    if ($null -eq $details) {
        Write-Host `
            "[STOPPED] $Name launcher process is already stopped." `
            -ForegroundColor DarkGray

        Remove-Item -LiteralPath $pidFile -Force

        if ($SearxNG -and (Test-LocalPort -Port 8888)) {
            $script:hadWarnings = $true
            Write-Host `
                "[WARNING] Search is still running; its ownership could not be verified." `
                -ForegroundColor Yellow
        }
        return
    }

    $commandLine = [string]$details.CommandLine

    if (
        [string]::IsNullOrWhiteSpace($commandLine) -or
        $commandLine -notmatch $ExpectedCommandPattern
    ) {
        $script:hadWarnings = $true
        Write-Host `
            "[SKIPPED] $Name process identity does not match its record." `
            -ForegroundColor Yellow
        return
    }

    Write-Host "[STOPPING] $Name..." -ForegroundColor Yellow

    if ($SearxNG) {
        # Stop only a matching Python service in the configured
        # SearxNG directory. Do not terminate the WSL distribution.
        $stopSearch = @'
import os
import signal
from pathlib import Path

root = (Path.home() / "artemis-searxng").resolve()
matches = []

for entry in Path("/proc").iterdir():
    if not entry.name.isdigit():
        continue
    try:
        if (entry / "cwd").resolve() != root:
            continue
        args = (entry / "cmdline").read_bytes().split(b"\0")
        if any(
            args[index:index + 2] == [b"-m", b"searx.webapp"]
            for index in range(len(args) - 1)
        ):
            matches.append(int(entry.name))
    except (OSError, RuntimeError):
        continue

if len(matches) > 1:
    raise SystemExit("Multiple matching search processes found; no processes stopped.")

for process_id in matches:
    os.kill(process_id, signal.SIGTERM)
'@

        $encodedSearch = [Convert]::ToBase64String(
            [Text.Encoding]::UTF8.GetBytes($stopSearch)
        )

        $launcher = (
            "import base64;exec(base64.b64decode('" +
            $encodedSearch +
            "'))"
        )

        & "$env:WINDIR\System32\wsl.exe" `
            -d Ubuntu --exec python3 -c $launcher

        if ($LASTEXITCODE -ne 0) {
            throw "The SearxNG shutdown command failed."
        }

        $deadline = (Get-Date).AddSeconds(15)
        while (
            (Test-LocalPort -Port 8888) -and
            (Get-Date) -lt $deadline
        ) {
            Start-Sleep -Milliseconds 500
        }

        if (Test-LocalPort -Port 8888) {
            $script:hadWarnings = $true
            Write-Host `
                "[WARNING] Search is still listening on port 8888." `
                -ForegroundColor Yellow
            return
        }

        # The tracked WSL command should exit with the Linux service.
        $deadline = (Get-Date).AddSeconds(5)
        while (
            (Get-Process -Id $trackedProcessId -ErrorAction SilentlyContinue) -and
            (Get-Date) -lt $deadline
        ) {
            Start-Sleep -Milliseconds 250
        }
    }
    else {
        Stop-Process -Id $trackedProcessId -ErrorAction Stop
    }

    $deadline = (Get-Date).AddSeconds(5)
    while (
        (Get-Process -Id $trackedProcessId -ErrorAction SilentlyContinue) -and
        (Get-Date) -lt $deadline
    ) {
        Start-Sleep -Milliseconds 250
    }

    if (Get-Process -Id $trackedProcessId -ErrorAction SilentlyContinue) {
        $script:hadWarnings = $true
        Write-Host `
            "[WARNING] $Name process is still running." `
            -ForegroundColor Yellow
        return
    }

    Remove-Item -LiteralPath $pidFile -Force
    Write-Host "[STOPPED] $Name" -ForegroundColor Green
}


try {
    Write-Host ""
    Write-Host "Stopping Artemis-managed services..." -ForegroundColor Magenta
    Write-Host ""

    if (-not (Test-Path -LiteralPath $runtimePath)) {
        Write-Host "No launcher process records found." -ForegroundColor DarkGray
        exit 0
    }

    Stop-TrackedProcess `
        -Name "Artemis" `
        -PidFileName "artemis.pid" `
        -ExpectedCommandPattern 'uvicorn.*"?--port"?\s+"?8200"?(?:\s|$)'

    Stop-TrackedProcess `
        -Name "SearxNG" `
        -PidFileName "searxng.pid" `
        -ExpectedCommandPattern 'searx\.webapp' `
        -SearxNG

    Stop-TrackedProcess `
        -Name "Market Lens" `
        -PidFileName "market-lens.pid" `
        -ExpectedCommandPattern 'uvicorn.*"?--port"?\s+"?8003"?(?:\s|$)'

    Stop-TrackedProcess `
        -Name "Risk Tracker" `
        -PidFileName "risk-tracker.pid" `
        -ExpectedCommandPattern 'uvicorn.*"?--port"?\s+"?8000"?(?:\s|$)'

    Stop-TrackedProcess `
        -Name "Intel Notes" `
        -PidFileName "intel-notes.pid" `
        -ExpectedCommandPattern 'uvicorn.*"?--port"?\s+"?8001"?(?:\s|$)'

    Stop-TrackedProcess `
        -Name "Chroma" `
        -PidFileName "chroma.pid" `
        -ExpectedCommandPattern 'chroma.*"?--port"?\s+"?8100"?(?:\s|$)'

    Stop-TrackedProcess `
        -Name "Ollama" `
        -PidFileName "ollama.pid" `
        -ExpectedCommandPattern 'ollama.*serve'

    Write-Host ""

    if ($script:hadWarnings) {
        Write-Host `
            "Shutdown finished with warnings. Review the messages above." `
            -ForegroundColor Yellow
        Read-Host "Press Enter to close" | Out-Null
    }
    else {
        Write-Host `
            "Tracked services stopped. Untracked services were left running." `
            -ForegroundColor Green
    }
}
catch {
    Write-Host ""
    Write-Host "Artemis shutdown encountered an error." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Read-Host "Press Enter to close" | Out-Null
    exit 1
}