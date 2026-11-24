Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$artemisPath = Split-Path -Parent $PSScriptRoot
$githubPath = Split-Path -Parent $artemisPath

$intelNotesPath = Join-Path $githubPath "intel-notes-manager"
$marketLensPath = Join-Path $githubPath "market-lens"
$riskTrackerPath = Join-Path $githubPath "protective-intelligence-risk-tracker"

$artemisPython = Join-Path $artemisPath ".venv\Scripts\python.exe"
$intelNotesPython = Join-Path $intelNotesPath ".venv\Scripts\python.exe"
$marketLensPython = Join-Path $marketLensPath ".venv\Scripts\python.exe"
$riskTrackerPython = Join-Path $riskTrackerPath ".venv\Scripts\python.exe"

# Keep the existing Chroma installation.
$chromaExecutable = "C:\Users\skate\.venvs\artemis\Scripts\chroma.exe"

$runtimePath = Join-Path $artemisPath ".runtime"
$logPath = Join-Path $runtimePath "logs"
$chromaDataPath = Join-Path $artemisPath "data\chroma"

$artemisPort = 8200
$dashboardUrl = "http://127.0.0.1:$artemisPort/dashboard"

foreach ($directory in @($runtimePath, $logPath, $chromaDataPath)) {
    New-Item -ItemType Directory -Path $directory -Force |
        Out-Null
}


function Test-LocalPort {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

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


function Get-LocalApplication {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    try {
        $response = Invoke-RestMethod `
            -Uri "http://127.0.0.1:$Port/" `
            -TimeoutSec 5

        $property = $response.PSObject.Properties["application"]

        if ($null -ne $property) {
            return [string]$property.Value
        }
    }
    catch {
        # The service may not expose an application identity.
    }

    return ""
}


function ConvertTo-NativeArgument {
    param(
        [AllowEmptyString()]
        [string]$Value
    )

    # Simple arguments need no Windows quoting.
    if ($Value -match '^[A-Za-z0-9_./:=+-]+$') {
        return $Value
    }

    # Windows native-process quoting, including embedded quotes
    # and trailing backslashes.
    $escaped = [regex]::Replace(
        $Value,
        '(\\*)"',
        '$1$1\"'
    )
    $escaped = [regex]::Replace(
        $escaped,
        '(\\+)$',
        '$1$1'
    )

    return '"' + $escaped + '"'
}


function Wait-LocalPort {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [int]$Port,

        [int]$TimeoutSeconds = 60
    )

    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $deadline) {
        if (Test-LocalPort -Port $Port) {
            Write-Host `
                "[READY] $Name on port $Port" `
                -ForegroundColor Green
            return
        }

        Start-Sleep -Milliseconds 500
    }

    throw (
        "$Name did not start on port $Port. " +
        "Review the logs in $logPath."
    )
}


function Start-ManagedProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [string]$Executable,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments,

        [Parameter(Mandatory = $true)]
        [string]$WorkingDirectory,

        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    if (Test-LocalPort -Port $Port) {
        Write-Host `
            "[RUNNING] $Name on port $Port" `
            -ForegroundColor Cyan
        return
    }

    if (-not (Test-Path -LiteralPath $Executable)) {
        throw "Executable for $Name was not found: $Executable"
    }

    if (-not (Test-Path -LiteralPath $WorkingDirectory)) {
        throw "Working directory for $Name was not found: $WorkingDirectory"
    }

    $safeName = (
        $Name.ToLowerInvariant() -replace "[^a-z0-9]+", "-"
    ).Trim("-")

    $pidFile = Join-Path $runtimePath "$safeName.pid"
    $standardOutput = Join-Path $logPath "$safeName.out.log"
    $standardError = Join-Path $logPath "$safeName.err.log"

    $argumentLine = (
        $Arguments | ForEach-Object {
            ConvertTo-NativeArgument -Value $_
        }
    ) -join " "

    Write-Host "[STARTING] $Name..." -ForegroundColor Yellow

    $process = Start-Process `
        -FilePath $Executable `
        -ArgumentList $argumentLine `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput $standardOutput `
        -RedirectStandardError $standardError `
        -PassThru

    Set-Content `
        -LiteralPath $pidFile `
        -Value $process.Id `
        -Encoding ASCII

    Wait-LocalPort -Name $Name -Port $Port
}


function Find-OllamaExecutable {
    $command = Get-Command "ollama.exe" -ErrorAction SilentlyContinue

    if ($null -ne $command) {
        return $command.Source
    }

    $fallback = Join-Path `
        $env:LOCALAPPDATA `
        "Programs\Ollama\ollama.exe"

    if (Test-Path -LiteralPath $fallback) {
        return $fallback
    }

    throw "Ollama was not found. Install or open Ollama first."
}


function Set-RiskWriteCredentials {
    $username = [Environment]::GetEnvironmentVariable(
        "ARTEMIS_RISK_WRITE_USERNAME",
        "Process"
    )
    $password = [Environment]::GetEnvironmentVariable(
        "ARTEMIS_RISK_WRITE_PASSWORD",
        "Process"
    )
    $role = [Environment]::GetEnvironmentVariable(
        "ARTEMIS_RISK_WRITE_ROLE",
        "Process"
    )

    if (
        [string]::IsNullOrWhiteSpace($username) -or
        [string]::IsNullOrEmpty($password)
    ) {
        $credential = Get-Credential `
            -UserName "cin" `
            -Message "Risk Tracker credentials for Artemis research"

        if ($null -eq $credential) {
            throw "Startup cancelled: Risk Tracker credentials were not supplied."
        }

        $env:ARTEMIS_RISK_WRITE_USERNAME = $credential.UserName
        $env:ARTEMIS_RISK_WRITE_PASSWORD = (
            $credential.GetNetworkCredential().Password
        )
        $env:ARTEMIS_RISK_WRITE_ROLE = "Administrator"
    }
    elseif ([string]::IsNullOrWhiteSpace($role)) {
        throw (
            "ARTEMIS_RISK_WRITE_ROLE is missing. Set it to the " +
            "actual role of the configured Risk Tracker account."
        )
    }
}


try {
    Write-Host ""
    Write-Host "Starting Artemis..." -ForegroundColor Magenta
    Write-Host ""

    # Prevent a second Artemis scheduler from starting alongside
    # the manually launched testing server.
    if (Test-LocalPort -Port 7000) {
        $application = Get-LocalApplication -Port 7000

        if ($application -eq "Artemis") {
            throw (
                "Artemis is already running on testing port 7000. " +
                "Press Ctrl+C in that server's PowerShell window, " +
                "wait for shutdown, then use this launcher again."
            )
        }

        if ([string]::IsNullOrWhiteSpace($application)) {
            throw (
                "Port 7000 is occupied but its application could not " +
                "be identified. Check that it is not another Artemis " +
                "server before starting the desktop launcher."
            )
        }
    }

    if (-not (Test-Path -LiteralPath $artemisPython)) {
        throw "Artemis Python was not found: $artemisPython"
    }

    # Verify the tested environment can import the current application.
    Push-Location $artemisPath
    try {
        & $artemisPython -c "from app.main import app"
        if ($LASTEXITCODE -ne 0) {
            throw "Artemis could not load. Review the Python error above."
        }
    }
    finally {
        Pop-Location
    }

    if (Test-LocalPort -Port $artemisPort) {
        if ((Get-LocalApplication -Port $artemisPort) -ne "Artemis") {
            throw "Port $artemisPort is occupied by an unverified application."
        }
    }
    else {
        Set-RiskWriteCredentials
    }

    if (Test-LocalPort -Port 11434) {
        Write-Host `
            "[RUNNING] Ollama on port 11434" `
            -ForegroundColor Cyan
    }
    else {
        Start-ManagedProcess `
            -Name "Ollama" `
            -Executable (Find-OllamaExecutable) `
            -Arguments @("serve") `
            -WorkingDirectory $artemisPath `
            -Port 11434
    }

    Start-ManagedProcess `
        -Name "Chroma" `
        -Executable $chromaExecutable `
        -Arguments @(
            "run",
            "--path", $chromaDataPath,
            "--host", "127.0.0.1",
            "--port", "8100"
        ) `
        -WorkingDirectory $artemisPath `
        -Port 8100

    Start-ManagedProcess `
        -Name "Intel Notes" `
        -Executable $intelNotesPython `
        -Arguments @(
            "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1",
            "--port", "8001"
        ) `
        -WorkingDirectory $intelNotesPath `
        -Port 8001

    Start-ManagedProcess `
        -Name "Risk Tracker" `
        -Executable $riskTrackerPython `
        -Arguments @(
            "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1",
            "--port", "8000"
        ) `
        -WorkingDirectory $riskTrackerPath `
        -Port 8000

    Start-ManagedProcess `
        -Name "Market Lens" `
        -Executable $marketLensPython `
        -Arguments @(
            "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1",
            "--port", "8003"
        ) `
        -WorkingDirectory $marketLensPath `
        -Port 8003

    # Direct SearxNG startup without nested Bash
    $wslExecutable = Join-Path $env:WINDIR "System32\wsl.exe"
    $searxLinuxPath = "/home/skate/artemis-searxng"

    Start-ManagedProcess `
        -Name "SearxNG" `
        -Executable $wslExecutable `
        -Arguments @(
            "--distribution", "Ubuntu",
            "--cd", $searxLinuxPath,
            "--exec", "/usr/bin/env",
            "SEARXNG_SETTINGS_PATH=$searxLinuxPath/artemis-settings.yml",
            "$searxLinuxPath/.venv/bin/python",
            "-m", "searx.webapp"
        ) `
        -WorkingDirectory $artemisPath `
        -Port 8888

    # Check that the search service supports the JSON interface.
    $searchCheck = Invoke-RestMethod `
        -Uri "http://127.0.0.1:8888/search?q=Artemis&format=json" `
        -TimeoutSec 45

    if ($null -eq $searchCheck.PSObject.Properties["results"]) {
        throw "SearxNG did not return the expected JSON search response."
    }

    Start-ManagedProcess `
        -Name "Artemis" `
        -Executable $artemisPython `
        -Arguments @(
            "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1",
            "--port", "$artemisPort"
        ) `
        -WorkingDirectory $artemisPath `
        -Port $artemisPort

    if ((Get-LocalApplication -Port $artemisPort) -ne "Artemis") {
        throw "The Artemis application identity check failed."
    }

    Write-Host ""
    Write-Host `
        "Artemis is ready. Opening the dashboard..." `
        -ForegroundColor Green

    Start-Process $dashboardUrl
}
catch {
    Write-Host ""
    Write-Host "Artemis could not start." -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Write-Host "Services already started remain running."
    Write-Host "Logs: $logPath"
    Write-Host "Press Enter to close this window."
    Read-Host | Out-Null
    exit 1
}