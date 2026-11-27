Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
Push-Location $project
try {
    $ready = $false
    try { $r = Invoke-WebRequest 'http://127.0.0.1:8200/health' -UseBasicParsing -TimeoutSec 3; $ready = ($r.StatusCode -eq 200) } catch { }
    if (-not $ready) {
        & (Join-Path $PSScriptRoot 'Start-Artemis.ps1')
        $r = Invoke-WebRequest 'http://127.0.0.1:8200/health' -UseBasicParsing -TimeoutSec 5
        if ($r.StatusCode -ne 200) { throw 'Artemis startup failed.' }
    }
    Invoke-RestMethod 'http://127.0.0.1:8200/voice-hub/start' -Method Post -ContentType 'application/json' -Body '{}' | Out-Null
} finally { Pop-Location }
