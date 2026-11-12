param(
    [string]$BaseUrl = (
        "http://127.0.0.1:8200"
    )
)

$ErrorActionPreference = "Stop"

$failureCount = 0
$warningCount = 0

function Write-TestResult {
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

function Add-Failure {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $script:failureCount += 1

    Write-TestResult `
        -Status "FAILED" `
        -Message $Message `
        -Color Red
}

function Add-Warning {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Message
    )

    $script:warningCount += 1

    Write-TestResult `
        -Status "WARNING" `
        -Message $Message `
        -Color Yellow
}

function Invoke-HealthRequest {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string]$Name
    )

    try {
        $response = Invoke-RestMethod `
            -Method Get `
            -Uri "$BaseUrl$Path" `
            -TimeoutSec 15

        Write-TestResult `
            -Status "PASSED" `
            -Message "$Name responded." `
            -Color Green

        return $response
    } catch {
        Add-Failure `
            -Message (
                "$Name could not be reached. " +
                $_.Exception.Message
            )

        return $null
    }
}

function Test-LocalListeners {
    $ports = @(
        8000,
        8001,
        8003,
        8100,
        8200,
        11434
    )

    $allowedAddresses = @(
        "127.0.0.1",
        "::1"
    )

    foreach ($port in $ports) {
        $listeners = @(
            Get-NetTCPConnection `
                -State Listen `
                -LocalPort $port `
                -ErrorAction SilentlyContinue
        )

        if ($listeners.Count -eq 0) {
            Write-TestResult `
                -Status "NOT RUNNING" `
                -Message "No listener on port $port." `
                -Color DarkGray

            continue
        }

        foreach ($listener in $listeners) {
            if (
                $listener.LocalAddress `
                -notin $allowedAddresses
            ) {
                Add-Failure `
                    -Message (
                        "Port $port is listening on " +
                        "$($listener.LocalAddress), " +
                        "not exclusively on localhost."
                    )
            } else {
                Write-TestResult `
                    -Status "LOCAL" `
                    -Message (
                        "Port $port is bound to " +
                        "$($listener.LocalAddress)."
                    ) `
                    -Color Green
            }
        }
    }
}

Write-Host ""
Write-Host "Artemis System Test" `
    -ForegroundColor Magenta
Write-Host ""

Write-Host "Checking network listeners..." `
    -ForegroundColor Cyan
Write-Host ""

Test-LocalListeners

Write-Host ""
Write-Host "Checking application..." `
    -ForegroundColor Cyan
Write-Host ""

$application = Invoke-HealthRequest `
    -Path "/" `
    -Name "Application endpoint"

if ($null -ne $application) {
    if (
        $application.application `
        -ne "Artemis"
    ) {
        Add-Failure `
            -Message (
                "Application identity did not " +
                "report Artemis."
            )
    } else {
        Write-TestResult `
            -Status "PASSED" `
            -Message (
                "Application identity is Artemis."
            ) `
            -Color Green
    }

    if (
        $application.external_access_enabled `
        -eq $true
    ) {
        Add-Failure `
            -Message (
                "External access is enabled."
            )
    } else {
        Write-TestResult `
            -Status "PASSED" `
            -Message (
                "External access is disabled."
            ) `
            -Color Green
    }

    if (
        $application.telemetry_enabled `
        -eq $true
    ) {
        Add-Failure `
            -Message "Telemetry is enabled."
    } else {
        Write-TestResult `
            -Status "PASSED" `
            -Message "Telemetry is disabled." `
            -Color Green
    }
}

$coreHealth = Invoke-HealthRequest `
    -Path "/health" `
    -Name "Core health endpoint"

if ($null -ne $coreHealth) {
    if ($coreHealth.status -eq "healthy") {
        Write-TestResult `
            -Status "PASSED" `
            -Message "Artemis reports healthy." `
            -Color Green
    } else {
        Add-Failure `
            -Message (
                "Artemis reported status: " +
                "$($coreHealth.status)"
            )
    }
}

$storageHealth = Invoke-HealthRequest `
    -Path "/storage/health" `
    -Name "Storage health endpoint"

if ($null -ne $storageHealth) {
    Write-TestResult `
        -Status "PASSED" `
        -Message "Storage response received." `
        -Color Green
}

Write-Host ""
Write-Host "Checking connected services..." `
    -ForegroundColor Cyan
Write-Host ""

$serviceHealth = Invoke-HealthRequest `
    -Path "/services/health" `
    -Name "Service health endpoint"

if ($null -ne $serviceHealth) {
    foreach (
        $service
        in $serviceHealth.services
    ) {
        if ($service.status -eq "available") {
            Write-TestResult `
                -Status "AVAILABLE" `
                -Message $service.name `
                -Color Green
        } elseif (
            $service.required `
            -eq $true
        ) {
            Add-Failure `
                -Message (
                    "$($service.name) is required " +
                    "but reported " +
                    "$($service.status)."
                )
        } else {
            Add-Warning `
                -Message (
                    "$($service.name) reported " +
                    "$($service.status)."
                )
        }
    }

    if (
        $serviceHealth.overall_status `
        -eq "unavailable"
    ) {
        Add-Failure `
            -Message (
                "Overall service status is " +
                "unavailable."
            )
    } elseif (
        $serviceHealth.overall_status `
        -eq "degraded"
    ) {
        Add-Warning `
            -Message (
                "Overall service status is degraded."
            )
    } else {
        Write-TestResult `
            -Status "PASSED" `
            -Message (
                "Overall service status is healthy."
            ) `
            -Color Green
    }
}

Write-Host ""
Write-Host "Artemis Test Summary" `
    -ForegroundColor Magenta
Write-Host ""

Write-Host "Failures: $failureCount"
Write-Host "Warnings: $warningCount"
Write-Host ""

if ($failureCount -gt 0) {
    Write-Host "Artemis failed the system test." `
        -ForegroundColor Red

    Write-Host ""
    exit 1
}

if ($warningCount -gt 0) {
    Write-Host (
        "Artemis passed with warnings."
    ) `
        -ForegroundColor Yellow

    Write-Host ""
    exit 0
}

Write-Host "Artemis passed all system tests." `
    -ForegroundColor Green

Write-Host ""
exit 0