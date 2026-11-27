Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$python = Join-Path $project '.venv\Scripts\python.exe'
Push-Location $project
try {
    & $python -m pip install 'vosk>=0.3.45,<0.4' 'sounddevice>=0.5,<0.6' 'pypdf>=5,<7' soundfile numpy httpx
    if ($LASTEXITCODE -ne 0) { throw 'Voice dependency installation failed.' }
    & $python (Join-Path $PSScriptRoot 'setup_voice_activation.py')
    if ($LASTEXITCODE -ne 0) { throw 'Local model setup failed. Check the displayed error.' }
    Write-Host 'Setup complete. Start Artemis and open /static/voice.html. Click Start local listener when ready to activate the microphone.'
} finally { Pop-Location }
