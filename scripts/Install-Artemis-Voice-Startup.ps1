param([switch]$Remove)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$startup = [Environment]::GetFolderPath('Startup')
$shortcutPath = Join-Path $startup 'Artemis Voice.lnk'
if ($Remove) {
    Remove-Item -LiteralPath $shortcutPath -Force -ErrorAction SilentlyContinue
    Write-Host 'Artemis voice sign-in startup removed. Running services were not stopped.'
    return
}
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcutPath)
$link.TargetPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$launcher = Join-Path $PSScriptRoot 'Start-Artemis-Voice.ps1'
$link.Arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $launcher + '"'
$link.WorkingDirectory = Split-Path -Parent $PSScriptRoot
$link.WindowStyle = 7
$link.Description = 'Start the local Artemis suite and microphone wake-phrase listener at Windows sign-in.'
$link.Save()
Write-Host 'Sign-in startup enabled. The microphone listener and desktop controls will open when you sign in. Use -Remove to disable.'
