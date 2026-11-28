param([string]$ClientJson)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
if (-not $ClientJson) {
    $ClientJson = Read-Host 'Full path to your Google Desktop OAuth client JSON'
}
$ClientJson = $ClientJson.Trim('"')
if (-not (Test-Path -LiteralPath $ClientJson -PathType Leaf)) { throw 'Google client JSON file not found.' }
& "$projectRoot\.venv\Scripts\python.exe" "$PSScriptRoot\connect_artemis_mailbox.py" --client $ClientJson
if ($LASTEXITCODE -ne 0) { throw 'Mailbox setup did not finish. Read the message above.' }
