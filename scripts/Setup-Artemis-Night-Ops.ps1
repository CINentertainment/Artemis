Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$project=Split-Path -Parent $PSScriptRoot
$python=Join-Path $project '.venv\Scripts\python.exe'
& $python -m pip install 'cryptography>=43,<51'
if ($LASTEXITCODE -ne 0) { throw 'Backup encryption dependency installation failed.' }
Write-Host 'Encryption dependency ready. Restart Artemis and its local listener.'
Write-Host 'Backups: http://127.0.0.1:8200/static/continuity.html'
Write-Host 'Specialists: http://127.0.0.1:8200/static/specialists.html'
Write-Host 'Remote portal remains disabled until you run scripts/Start-Artemis-Remote.ps1 with Tailscale installed and connected.'
