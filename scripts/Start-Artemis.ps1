Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$artemisPath = Split-Path -Parent $PSScriptRoot
$githubPath = Split-Path -Parent $artemisPath

$intelNotesPath = Join-Path `
    $githubPath `
    "intel-notes-manager"

$marketLensPath = Join-Path `
    $githubPath `
    "market-lens"

$riskTrackerPath = Join-Path `
    $githubPath `
    "protective-intelligence-risk-tracker"

$artemisPython = (
    "C:\Users\skate\.venvs" +
    "\artemis\Scripts\python.exe"
)

$chromaExecutable = (
    "C:\Users\skate\.venvs" +
    "\artemis\Scripts\chroma.exe"
)

$intelNotesPython = Join-Path `
    $intelNotesPath `
    ".venv\Scripts\python.exe"

$marketLensPython = Join-Path `
    $marketLensPath `
    ".venv\Scripts\python.exe"

$riskTrackerPython = Join-Path `
    $riskTrackerPath `
    ".venv\Scripts\python.exe"

$runtimePath = Join-Path `
    $artemisPath `
    ".runtime"

$logPath = Join-Path `
    $runtimePath `
    "logs"

$chromaDataPath = Join-Path `
    $artemisPath `
    "data\chroma"

$dashboardUrl = (
    "http://127.0.0.1:8200/dashboard"
)

New-Item `
    -ItemType Directory `
    -Path $runtimePath `
    -Force |
    Out-Null

New-Item `
    -ItemType Directory `
    -Path $logPath `
    -Force |
    Out-Null

New-Item `
    -ItemType Directory `
    -Path $chromaDataPath `
    -Force |
    Out-Null


function Test-LocalPort {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port,

        [int]$TimeoutMilliseconds = 500
    )

    $client = New-Object `
        System.Net.Sockets.TcpClient

    try {
        $connection = $client.BeginConnect(
            "127.0.0.1",
            $Port,
            $null,
            $null
        )

        $connected = (
            $connection.AsyncWaitHandle.WaitOne(
                $TimeoutMilliseconds,
                $false
            )
        )

        if (-not $connected) {
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


function Wait-LocalPort {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,

        [Parameter(Mandatory = $true)]
        [int]$Port,

        [int]$TimeoutSeconds = 45
    )

    $deadline = (
        Get-Date
    ).AddSeconds($TimeoutSeconds)

    while ((Get-Date) -lt $deadline) {
        if (Test-LocalPort -Port $Port) {
            Write-Host (
                (
                    "[READY] {0} on port {1}" -f
                    $Name,
                    $Port
                )
            ) -ForegroundColor Green

            return
        }

        Start-Sleep -Milliseconds 500
    }

    throw (
        (
            "{0} did not start on port {1}. " +
            "Review the logs in {2}."
        ) -f $Name, $Port, $logPath
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
        Write-Host (
            (
                "[RUNNING] {0} is already available " +
                "on port {1}."
            ) -f $Name, $Port
        ) -ForegroundColor Cyan

        return
    }

    if (-not (Test-Path $Executable)) {
        throw (
            (
                "The executable for {0} was not " +
                "found: {1}"
            ) -f $Name, $Executable
        )
    }

    if (-not (Test-Path $WorkingDirectory)) {
        throw (
            (
                "The working directory for {0} was " +
                "not found: {1}"
            ) -f $Name, $WorkingDirectory
        )
    }

    $safeName = (
        $Name.ToLowerInvariant() -replace
        "[^a-z0-9]+",
        "-"
    ).Trim("-")

    $pidFile = Join-Path `
        $runtimePath `
        "$safeName.pid"

    $standardOutput = Join-Path `
        $logPath `
        "$safeName.out.log"

    $standardError = Join-Path `
        $logPath `
        "$safeName.err.log"

    Remove-Item `
        $pidFile `
        -Force `
        -ErrorAction SilentlyContinue

    Write-Host (
        (
            "[STARTING] {0}..." -f $Name
        )
    ) -ForegroundColor Yellow

    $process = Start-Process `
        -FilePath $Executable `
        -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory `
        -WindowStyle Hidden `
        -RedirectStandardOutput $standardOutput `
        -RedirectStandardError $standardError `
        -PassThru

    Set-Content `
        -Path $pidFile `
        -Value $process.Id `
        -Encoding ASCII

    Wait-LocalPort `
        -Name $Name `
        -Port $Port
}


function Find-OllamaExecutable {
    $ollamaCommand = Get-Command `
        "ollama.exe" `
        -ErrorAction SilentlyContinue

    if ($null -ne $ollamaCommand) {
        return $ollamaCommand.Source
    }

    $fallbackPath = (
        "C:\Users\skate\AppData\Local" +
        "\Programs\Ollama\ollama.exe"
    )

    if (Test-Path $fallbackPath) {
        return $fallbackPath
    }

    throw (
        "Ollama was not found. Install or open " +
        "Ollama before starting Artemis."
    )
}


try {
    Write-Host ""

    Write-Host `
        "Starting Artemis..." `
        -ForegroundColor Magenta

    Write-Host ""

    if (Test-LocalPort -Port 11434) {
        Write-Host (
            "[RUNNING] Ollama is already available " +
            "on port 11434."
        ) -ForegroundColor Cyan
    }
    else {
        $ollamaExecutable = (
            Find-OllamaExecutable
        )

        Start-ManagedProcess `
            -Name "Ollama" `
            -Executable $ollamaExecutable `
            -Arguments @("serve") `
            -WorkingDirectory $artemisPath `
            -Port 11434
    }

    Start-ManagedProcess `
        -Name "Chroma" `
        -Executable $chromaExecutable `
        -Arguments @(
            "run",
            "--path",
            $chromaDataPath,
            "--host",
            "127.0.0.1",
            "--port",
            "8100"
        ) `
        -WorkingDirectory $artemisPath `
        -Port 8100

    Start-ManagedProcess `
        -Name "Intel Notes" `
        -Executable $intelNotesPython `
        -Arguments @(
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8001"
        ) `
        -WorkingDirectory $intelNotesPath `
        -Port 8001

    Start-ManagedProcess `
        -Name "Risk Tracker" `
        -Executable $riskTrackerPython `
        -Arguments @(
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8000"
        ) `
        -WorkingDirectory $riskTrackerPath `
        -Port 8000

    Start-ManagedProcess `
        -Name "Market Lens" `
        -Executable $marketLensPython `
        -Arguments @(
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8003"
        ) `
        -WorkingDirectory $marketLensPath `
        -Port 8003

    Start-ManagedProcess `
        -Name "Artemis" `
        -Executable $artemisPython `
        -Arguments @(
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8200"
        ) `
        -WorkingDirectory $artemisPath `
        -Port 8200

    Write-Host ""

    Write-Host (
        "Artemis is ready. Opening the dashboard..."
    ) -ForegroundColor Green

    Start-Process $dashboardUrl
}
catch {
    Write-Host ""

    Write-Host `
        "Artemis could not start." `
        -ForegroundColor Red

    Write-Host `
        $_.Exception.Message `
        -ForegroundColor Red

    Write-Host ""

    Write-Host (
        "Press Enter to close this window."
    )

    Read-Host | Out-Null

    exit 1
}