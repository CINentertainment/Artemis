Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$artemisPath = Split-Path -Parent $PSScriptRoot

$startScript = Join-Path `
    $PSScriptRoot `
    "Start-Artemis.ps1"

$stopScript = Join-Path `
    $PSScriptRoot `
    "Stop-Artemis.ps1"

$iconPath = Join-Path `
    $artemisPath `
    "app\static\artemis.ico"

$powershellPath = Join-Path `
    $env:SystemRoot `
    "System32\WindowsPowerShell\v1.0\powershell.exe"

$desktopPath = (
    [Environment]::GetFolderPath(
        [Environment+SpecialFolder]::Desktop
    )
)


function New-ArtemisShortcut {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Shell,

        [Parameter(Mandatory = $true)]
        [string]$ShortcutName,

        [Parameter(Mandatory = $true)]
        [string]$ScriptPath,

        [Parameter(Mandatory = $true)]
        [string]$Description,

        [Parameter(Mandatory = $true)]
        [string]$IconPath
    )

    $shortcutPath = Join-Path `
        $desktopPath `
        "$ShortcutName.lnk"

    $shortcut = $Shell.CreateShortcut(
        $shortcutPath
    )

    $shortcut.TargetPath = $powershellPath

    $shortcut.Arguments = (
        '-NoProfile ' +
        '-ExecutionPolicy Bypass ' +
        '-WindowStyle Minimized ' +
        '-File "{0}"'
    ) -f $ScriptPath

    $shortcut.WorkingDirectory = (
        $artemisPath
    )

    $shortcut.Description = $Description

    $shortcut.IconLocation = (
        "$IconPath,0"
    )

    $shortcut.WindowStyle = 7

    $shortcut.Save()

    return $shortcutPath
}


try {
    if (-not (Test-Path $startScript)) {
        throw (
            "The Artemis startup script was not " +
            "found: $startScript"
        )
    }

    if (-not (Test-Path $stopScript)) {
        throw (
            "The Artemis shutdown script was not " +
            "found: $stopScript"
        )
    }

    if (-not (Test-Path $iconPath)) {
        throw (
            "The Artemis icon was not found: " +
            $iconPath
        )
    }

    if (-not (Test-Path $powershellPath)) {
        throw (
            "Windows PowerShell was not found: " +
            $powershellPath
        )
    }

    if (-not (Test-Path $desktopPath)) {
        throw (
            "The Windows desktop folder was not " +
            "found: $desktopPath"
        )
    }

    $shell = New-Object `
        -ComObject WScript.Shell

    $startShortcutPath = New-ArtemisShortcut `
        -Shell $shell `
        -ShortcutName "Artemis" `
        -ScriptPath $startScript `
        -Description (
            "Start Artemis and open the dashboard"
        ) `
        -IconPath $iconPath

    $stopShortcutPath = New-ArtemisShortcut `
        -Shell $shell `
        -ShortcutName "Stop Artemis" `
        -ScriptPath $stopScript `
        -Description (
            "Stop services launched by Artemis"
        ) `
        -IconPath $iconPath

    Write-Host ""

    Write-Host `
        "Artemis desktop shortcuts installed." `
        -ForegroundColor Green

    Write-Host ""
    Write-Host "Icon:"
    Write-Host $iconPath

    Write-Host ""
    Write-Host "Start shortcut:"
    Write-Host $startShortcutPath

    Write-Host ""
    Write-Host "Stop shortcut:"
    Write-Host $stopShortcutPath
}
catch {
    Write-Host ""

    Write-Host `
        "The Artemis shortcuts could not be installed." `
        -ForegroundColor Red

    Write-Host `
        $_.Exception.Message `
        -ForegroundColor Red

    exit 1
}