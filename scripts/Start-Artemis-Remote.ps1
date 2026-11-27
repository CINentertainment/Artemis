[CmdletBinding()]
param([string]$AllowedLogin = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$python = Join-Path $project '.venv\Scripts\python.exe'
$pythonw = Join-Path $project '.venv\Scripts\pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = $python }
$tailscale = (Get-Command tailscale.exe -ErrorAction SilentlyContinue)
if ($null -eq $tailscale) { throw 'Install Tailscale on this PC and your phone/office desktop, sign into the same account, then rerun this script. https://tailscale.com/download' }
$state = (& $tailscale.Source status --json | ConvertFrom-Json)
if ($LASTEXITCODE -ne 0 -or $state.BackendState -ne 'Running') { throw 'Sign into Tailscale and connect this PC first.' }
$dns = ([string]$state.Self.DNSName).TrimEnd('.').ToLowerInvariant()
if (-not $AllowedLogin) {
    $property = $state.User.PSObject.Properties[[string]$state.Self.UserID]
    if ($null -eq $property) { throw 'Could not resolve your Tailscale login. Rerun with -AllowedLogin your-login.' }
    $AllowedLogin = [string]$property.Value.LoginName
}
$serve = (& $tailscale.Source serve status --json | ConvertFrom-Json)
$tcpProperty = $serve.PSObject.Properties['TCP']
if ($null -ne $tcpProperty -and $null -ne $tcpProperty.Value.PSObject.Properties['8443']) {
    if (($serve | ConvertTo-Json -Depth 12) -notmatch '127\.0\.0\.1:8201') { throw 'Tailscale Serve port 8443 already serves another application. Nothing was changed.' }
}
Push-Location $project
try {
    & $python -m app.remote_gateway --configure --host "${dns}:8443" --login $AllowedLogin
    if ($LASTEXITCODE -ne 0) { throw 'Portal configuration failed.' }
    $listener = New-Object System.Net.Sockets.TcpClient
    try { $listener.Connect('127.0.0.1',8201); $running = $true } catch { $running = $false } finally { $listener.Dispose() }
    if ($running) {
        $pidFile = Join-Path $project '.runtime\remote-gateway.pid'
        if (-not (Test-Path $pidFile)) { throw 'Port 8201 is occupied by an unidentified service. Refusing to expose it.' }
        $gatewayId = [int](Get-Content $pidFile -Raw)
        $existing = Get-CimInstance Win32_Process -Filter "ProcessId=$gatewayId" -ErrorAction SilentlyContinue
        if ($null -eq $existing -or $existing.CommandLine -notmatch 'app\.remote_gateway:app') { throw 'Existing gateway identity could not be checked. Refusing to expose port 8201.' }
    }
    if (-not $running) {
        $process = Start-Process -FilePath $pythonw -ArgumentList @('-m','uvicorn','app.remote_gateway:app','--host','127.0.0.1','--port','8201','--no-proxy-headers') -WorkingDirectory $project -PassThru
        $runtime = Join-Path $project '.runtime'
        New-Item -ItemType Directory -Path $runtime -Force | Out-Null
        [string]$process.Id | Set-Content (Join-Path $runtime 'remote-gateway.pid')
    }
    & $tailscale.Source serve --bg --https=8443 http://127.0.0.1:8201
    if ($LASTEXITCODE -ne 0) { throw 'Tailscale Serve needs setup. Follow its displayed account/HTTPS instructions, then rerun.' }
    Write-Host "Private portal: https://${dns}:8443/"
    Write-Host 'Only the configured Tailscale user is accepted. Keep Tailscale connected on your phone/desktop. Artemis must be running.'
} finally { Pop-Location }
