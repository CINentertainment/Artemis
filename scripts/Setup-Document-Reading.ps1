$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $PSScriptRoot
$python = Join-Path $project '.venv\Scripts\python.exe'
& $python -m pip install 'pypdf>=5,<7' 'pypdfium2>=4,<6' 'Pillow>=11,<13'
if ($LASTEXITCODE -ne 0) { throw 'Document dependency installation failed.' }
Write-Host 'Document libraries installed. OCR also requires local Tesseract with English language data.'
Write-Host 'If Tesseract is already installed in Program Files\Tesseract-OCR, Artemis detects it automatically.'
Write-Host 'Restart Artemis normally, then open http://127.0.0.1:8200/static/documents.html'
