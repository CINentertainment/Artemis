# Artemis Backup and Recovery

## Purpose

This document defines how to preserve and restore the local Artemis installation.

Artemis uses three primary forms of persistent information:

1. Git-tracked application source code
2. The Artemis SQLite database
3. Chroma semantic-memory data

The local `.env` file must also be protected because it contains machine-specific configuration and credentials.

## Known-Good Release

The initial stable foundation is preserved as:

```text
v0.1.0
```

It points to commit:

```text
f82c1a8
```

Inspect the release with:

```powershell
git show v0.1.0 --no-patch
```

## Persistent Locations

### Source Code

```text
C:\Users\skate\OneDrive\Desktop\GitHub\artemis
```

### Artemis Database

```text
C:\Users\skate\AppData\Local\CIN Entertainment\Artemis\data\artemis.db
```

The SQLite database contains application records such as conversations, messages, preferences, approvals, identities, and audit events.

### Chroma Data

```text
C:\Users\skate\OneDrive\Desktop\GitHub\artemis\data\chroma
```

### Local Configuration

```text
C:\Users\skate\OneDrive\Desktop\GitHub\artemis\.env
```

The `.env` file may contain credentials and must not be committed to Git or shared publicly.

### Runtime Files

```text
C:\Users\skate\OneDrive\Desktop\GitHub\artemis\.runtime
```

Runtime process records are temporary and do not need to be included in a normal backup.

## Backup Requirements

Before copying the database or Chroma data:

1. Stop Artemis using the **Stop Artemis** desktop shortcut.
2. Confirm that Artemis-managed services have stopped.
3. Do not modify the database during the backup.
4. Store backups somewhere separate from the working repository.

Stopping the services first prevents partially written or inconsistent backup files.

## Manual Backup

Create a dated backup directory:

```powershell
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

$backupRoot = Join-Path `
    $env:USERPROFILE `
    "Documents\Artemis Backups\artemis-$timestamp"

New-Item `
    -ItemType Directory `
    -Force `
    -Path $backupRoot |
    Out-Null
```

Back up the Artemis database:

```powershell
Copy-Item `
    "C:\Users\skate\AppData\Local\CIN Entertainment\Artemis\data\artemis.db" `
    (Join-Path $backupRoot "artemis.db")
```

Back up Chroma:

```powershell
Copy-Item `
    ".\data\chroma" `
    (Join-Path $backupRoot "chroma") `
    -Recurse
```

Back up the local environment configuration:

```powershell
Copy-Item `
    ".\.env" `
    (Join-Path $backupRoot "artemis.env")
```

Record the current Git state:

```powershell
git rev-parse HEAD |
    Set-Content `
        (Join-Path $backupRoot "git-commit.txt")

git tag --points-at HEAD |
    Set-Content `
        (Join-Path $backupRoot "git-tags.txt")

git status --short |
    Set-Content `
        (Join-Path $backupRoot "git-status.txt")
```

Create file hashes:

```powershell
Get-ChildItem `
    $backupRoot `
    -Recurse `
    -File |
    Get-FileHash `
        -Algorithm SHA256 |
    Select-Object Path, Hash |
    Export-Csv `
        (Join-Path $backupRoot "sha256.csv") `
        -NoTypeInformation
```

## Backup Contents

A complete backup should contain:

```text
artemis-YYYYMMDD-HHMMSS/
├── artemis.db
├── artemis.env
├── chroma/
├── git-commit.txt
├── git-tags.txt
├── git-status.txt
└── sha256.csv
```

## Backup Verification

Confirm the database exists:

```powershell
Test-Path `
    (Join-Path $backupRoot "artemis.db")
```

Confirm the environment backup exists:

```powershell
Test-Path `
    (Join-Path $backupRoot "artemis.env")
```

Confirm the Chroma directory exists:

```powershell
Test-Path `
    (Join-Path $backupRoot "chroma")
```

Review the backup directory:

```powershell
Get-ChildItem `
    $backupRoot `
    -Recurse |
    Select-Object FullName, Length
```

## Source-Code Recovery

Do not overwrite an active working branch immediately.

Create a recovery branch from the known-good release:

```powershell
git switch -c recovery-v0.1.0 v0.1.0
```

Run the complete test suite:

```powershell
& "C:\Users\skate\.venvs\artemis\Scripts\python.exe" `
    -m pytest
```

Return to the main branch when finished:

```powershell
git switch main
```

## Database Recovery

Stop Artemis before restoring the database.

Preserve the current database before replacing it:

```powershell
$databasePath = (
    "C:\Users\skate\AppData\Local\" +
    "CIN Entertainment\Artemis\data\artemis.db"
)

Copy-Item `
    $databasePath `
    "$databasePath.before-restore" `
    -Force
```

Restore the selected backup:

```powershell
Copy-Item `
    "C:\path\to\backup\artemis.db" `
    $databasePath `
    -Force
```

Start Artemis and verify:

```powershell
Invoke-RestMethod `
    -Uri "http://127.0.0.1:8200/health"
```

Confirm that conversations and preferences appear correctly in the Command Deck.

## Chroma Recovery

Stop Artemis and Chroma before restoring semantic-memory data.

Preserve the current Chroma directory:

```powershell
$timestamp = Get-Date -Format "yyyyMMdd-HHmmss"

Rename-Item `
    ".\data\chroma" `
    "chroma-before-restore-$timestamp"
```

Restore the selected backup:

```powershell
Copy-Item `
    "C:\path\to\backup\chroma" `
    ".\data\chroma" `
    -Recurse
```

Restart Artemis and verify connected-service health.

## Environment Recovery

Restore `.env` only from a trusted backup:

```powershell
Copy-Item `
    "C:\path\to\backup\artemis.env" `
    ".\.env" `
    -Force
```

Never restore an environment file from an untrusted or public source.

After restoration, confirm:

- Service URLs use `127.0.0.1`
- External access remains disabled
- Telemetry remains disabled
- Connector credentials are read-only
- Credentials have not expired or changed

## Post-Recovery Validation

After any recovery:

1. Start Artemis.
2. Confirm the Command Deck opens.
3. Confirm system status is healthy.
4. Run the application health check.
5. Run connected-service health.
6. Run storage health.
7. Run the complete automated test suite.
8. Confirm the Git working tree is clean.

Application health:

```powershell
Invoke-RestMethod `
    -Uri "http://127.0.0.1:8200/health"
```

Connected-service health:

```powershell
Invoke-RestMethod `
    -Uri "http://127.0.0.1:8200/services/health" |
    ConvertTo-Json -Depth 6
```

Storage health:

```powershell
Invoke-RestMethod `
    -Uri "http://127.0.0.1:8200/storage/health" |
    ConvertTo-Json -Depth 6
```

Tests:

```powershell
& "C:\Users\skate\.venvs\artemis\Scripts\python.exe" `
    -m pytest
```

Repository state:

```powershell
git diff --check
git status
```

## Recovery Rule

Never delete or overwrite the current database, Chroma directory, or environment configuration without first creating a recoverable copy.

When uncertain, preserve the existing state and restore into a separate location for inspection.