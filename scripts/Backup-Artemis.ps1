$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path `
    -Parent `
    $PSScriptRoot

$databasePath = Join-Path `
    $env:LOCALAPPDATA `
    "CIN Entertainment\Artemis\data\artemis.db"

$chromaPath = Join-Path `
    $repositoryRoot `
    "data\chroma"

$environmentPath = Join-Path `
    $repositoryRoot `
    ".env"

$backupParent = Join-Path `
    ([Environment]::GetFolderPath("MyDocuments")) `
    "Artemis Backups"

$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

$backupRoot = Join-Path `
    $backupParent `
    "artemis-$timestamp"

function Write-BackupStatus {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Status,

        [Parameter(Mandatory = $true)]
        [string]$Message,

        [ConsoleColor]$Color = (
            [ConsoleColor]::Gray
        )
    )

    Write-Host "[$Status] " `
        -ForegroundColor $Color `
        -NoNewline

    Write-Host $Message
}

function Test-ListeningPort {
    param(
        [Parameter(Mandatory = $true)]
        [int]$Port
    )

    $connection = Get-NetTCPConnection `
        -State Listen `
        -LocalPort $Port `
        -ErrorAction SilentlyContinue

    return $null -ne $connection
}

function Copy-BackupItem {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Source,

        [Parameter(Mandatory = $true)]
        [string]$Destination,

        [Parameter(Mandatory = $true)]
        [string]$Name,

        [switch]$Recurse
    )

    if (-not (Test-Path $Source)) {
        Write-BackupStatus `
            -Status "SKIPPED" `
            -Message "$Name was not found." `
            -Color Yellow

        return
    }

    if ($Recurse) {
        Copy-Item `
            -Path $Source `
            -Destination $Destination `
            -Recurse `
            -Force
    } else {
        Copy-Item `
            -Path $Source `
            -Destination $Destination `
            -Force
    }

    Write-BackupStatus `
        -Status "BACKED UP" `
        -Message $Name `
        -Color Green
}

try {
    Write-Host ""
    Write-Host "Artemis Backup" `
        -ForegroundColor Magenta
    Write-Host ""

    $activeServices = @()

    if (Test-ListeningPort -Port 8200) {
        $activeServices += "Artemis (8200)"
    }

    if (Test-ListeningPort -Port 8100) {
        $activeServices += "Chroma (8100)"
    }

    if ($activeServices.Count -gt 0) {
        $serviceList = (
            $activeServices -join ", "
        )

        throw (
            "Stop Artemis before creating a backup. " +
            "Still running: $serviceList"
        )
    }

    New-Item `
        -ItemType Directory `
        -Force `
        -Path $backupRoot |
        Out-Null

    Write-BackupStatus `
        -Status "CREATED" `
        -Message $backupRoot `
        -Color Cyan

    Copy-BackupItem `
        -Source $databasePath `
        -Destination (
            Join-Path $backupRoot "artemis.db"
        ) `
        -Name "Artemis database"

    Copy-BackupItem `
        -Source $chromaPath `
        -Destination (
            Join-Path $backupRoot "chroma"
        ) `
        -Name "Chroma data" `
        -Recurse

    Copy-BackupItem `
        -Source $environmentPath `
        -Destination (
            Join-Path $backupRoot "artemis.env"
        ) `
        -Name "Local environment configuration"

    $commit = (
        git `
            -C $repositoryRoot `
            rev-parse HEAD
    )

    $branch = (
        git `
            -C $repositoryRoot `
            branch `
            --show-current
    )

    $tags = @(
        git `
            -C $repositoryRoot `
            tag `
            --points-at HEAD
    )

    $status = @(
        git `
            -C $repositoryRoot `
            status `
            --short
    )

    $commit |
        Set-Content `
            -Path (
                Join-Path `
                    $backupRoot `
                    "git-commit.txt"
            ) `
            -Encoding UTF8

    $branch |
        Set-Content `
            -Path (
                Join-Path `
                    $backupRoot `
                    "git-branch.txt"
            ) `
            -Encoding UTF8

    $tags |
        Set-Content `
            -Path (
                Join-Path `
                    $backupRoot `
                    "git-tags.txt"
            ) `
            -Encoding UTF8

    $status |
        Set-Content `
            -Path (
                Join-Path `
                    $backupRoot `
                    "git-status.txt"
            ) `
            -Encoding UTF8

    $manifest = [ordered]@{
        application = "Artemis"
        created_at = (
            Get-Date
        ).ToString("o")
        repository = $repositoryRoot
        branch = $branch
        commit = $commit
        tags = $tags
        database_source = $databasePath
        chroma_source = $chromaPath
        environment_source = (
            $environmentPath
        )
    }

    $manifest |
        ConvertTo-Json -Depth 5 |
        Set-Content `
            -Path (
                Join-Path `
                    $backupRoot `
                    "manifest.json"
            ) `
            -Encoding UTF8

    $hashPath = Join-Path `
        $backupRoot `
        "sha256.csv"

    Get-ChildItem `
        -Path $backupRoot `
        -Recurse `
        -File |
        Where-Object {
            $_.FullName -ne $hashPath
        } |
        Get-FileHash `
            -Algorithm SHA256 |
        Select-Object Path, Hash |
        Export-Csv `
            -Path $hashPath `
            -NoTypeInformation `
            -Encoding UTF8

    Write-BackupStatus `
        -Status "VERIFIED" `
        -Message "SHA-256 manifest created." `
        -Color Green

    Write-Host ""
    Write-Host "Artemis backup completed." `
        -ForegroundColor Green

    Write-Host ""
    Write-Host "Backup location:"
    Write-Host $backupRoot `
        -ForegroundColor Cyan
    Write-Host ""
} catch {
    Write-Host ""
    Write-Host "Artemis backup failed." `
        -ForegroundColor Red

    Write-Host $_.Exception.Message `
        -ForegroundColor Red

    Write-Host ""
    exit 1
}