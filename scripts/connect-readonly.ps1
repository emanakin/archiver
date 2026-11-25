param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9-]{36}$')][string]$AccountId,
    [string]$EnvFile = '.env.readonly',
    [ValidateRange(1, 20)][int]$MaxRequests = 12,
    [switch]$VerifyOnly,
    [switch]$Recover,
    [switch]$AcknowledgeRecovery,
    [switch]$ManualProfileVerification
)
$ErrorActionPreference = 'Stop'
if ($Recover -and $VerifyOnly) { throw 'Choose either verification or recovery.' }
if ($Recover -and !$AcknowledgeRecovery) { throw 'Review the account in Instagram, then explicitly supply -AcknowledgeRecovery.' }
if ($AcknowledgeRecovery -and !$Recover) { throw '-AcknowledgeRecovery requires -Recover.' }
if ($ManualProfileVerification -and !$Recover) { throw '-ManualProfileVerification requires explicit recovery.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
Push-Location -LiteralPath $projectRoot
$previousMode = $env:ADAPTER_MODE
try {
    if (!(Test-Path -LiteralPath $EnvFile -PathType Leaf)) { throw 'Create the private environment file first; see docs/INSTAGRAM-SETUP.md.' }
    if (!(Test-Path -LiteralPath 'secrets/instagram_session_key' -PathType Leaf)) { throw 'Initialize the separate worker session key first.' }
    # Even if the caller has a live-mode environment, this helper only starts reads.
    $env:ADAPTER_MODE = 'read_only'
    $composeArguments = @('compose', '--env-file', $EnvFile, '-f', 'compose.yaml', '-f', 'compose.instagram.yaml')
    & docker @composeArguments up -d --no-build
    if ($LASTEXITCODE -ne 0) { throw 'The reviewed images must be built before connecting.' }
    $operation = if ($VerifyOnly) { 'verify' } elseif ($Recover) { 'recover' } else { 'connect' }
    $operationArguments = @($operation, '--account-id', $AccountId, '--max-requests', $MaxRequests)
    if ($Recover) { $operationArguments += '--acknowledge-recovery' }
    if ($ManualProfileVerification) { $operationArguments += '--manual-profile-verification' }
    Write-Host 'Enter secrets only in the private prompt below. No follow/unfollow workflow is enabled.'
    & docker @composeArguments exec worker python -m app.manage @operationArguments
    if ($LASTEXITCODE -ne 0) { throw 'Connection did not complete. Review the safe error and official account recovery instructions.' }
} finally {
    if ($null -eq $previousMode) { Remove-Item Env:ADAPTER_MODE -ErrorAction SilentlyContinue } else { $env:ADAPTER_MODE = $previousMode }
    Pop-Location
}
