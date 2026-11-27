Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$project=Split-Path -Parent $PSScriptRoot
Push-Location $project
try {
    & (Join-Path $project '.venv\Scripts\python.exe') -m app.remote_gateway --disable
    if ($LASTEXITCODE -ne 0) { throw 'Could not disable the portal configuration.' }
    $tailscale=Get-Command tailscale.exe -ErrorAction SilentlyContinue
    if ($null -ne $tailscale) { & $tailscale.Source serve --https=8443 off }
    $pidFile=Join-Path $project '.runtime\remote-gateway.pid'
    if (Test-Path $pidFile) {
        $gatewayId=[int](Get-Content $pidFile -Raw)
        $process=Get-CimInstance Win32_Process -Filter "ProcessId=$gatewayId" -ErrorAction SilentlyContinue
        if ($null -ne $process -and $process.CommandLine -match 'app\.remote_gateway:app') { Stop-Process -Id $gatewayId -ErrorAction Stop }
        Remove-Item $pidFile -ErrorAction SilentlyContinue
    }
    Write-Host 'Private portal disabled. No other Tailscale Serve ports were reset.'
} finally { Pop-Location }
