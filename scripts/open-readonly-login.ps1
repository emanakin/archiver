param(
    [Parameter(Mandatory = $true)][ValidatePattern('^[a-f0-9-]{36}$')][string]$AccountId,
    [ValidateSet('Connect', 'Verify', 'Recover')][string]$Action = 'Connect',
    [string]$EnvFile = '.env.readonly',
    [ValidateRange(1, 20)][int]$MaxRequests = 12,
    [switch]$AcknowledgeRecovery,
    [switch]$ManualProfileVerification
)
$ErrorActionPreference = 'Stop'
if ($Action -eq 'Recover' -and !$AcknowledgeRecovery) {
    throw 'Review the account in Instagram, then explicitly supply -AcknowledgeRecovery.'
}
if ($AcknowledgeRecovery -and $Action -ne 'Recover') {
    throw '-AcknowledgeRecovery requires -Action Recover.'
}
if ($ManualProfileVerification -and $Action -ne 'Recover') { throw '-ManualProfileVerification requires -Action Recover.' }
$projectRoot = Split-Path -Parent $PSScriptRoot
$terminal = Get-Command wt.exe -ErrorAction SilentlyContinue
if (!$terminal) { throw 'Windows Terminal is unavailable. Run connect-readonly.ps1 directly in your own PowerShell terminal.' }
$helper = Join-Path $PSScriptRoot 'connect-readonly.ps1'
$environmentPath = if ([IO.Path]::IsPathRooted($EnvFile)) { $EnvFile } else { Join-Path $projectRoot $EnvFile }
$environmentPath = (Resolve-Path -LiteralPath $environmentPath).Path
$label = if ($Action -eq 'Connect') { 'Login' } else { $Action }
$title = 'Archive Desk - Private ' + $label
# Start a real GUI terminal. A background PowerShell/ConPTY process can lack
# a visible console even when Start-Process requested a normal window.
$arguments = @('-w', 'new', 'new-tab', '--title', ('"' + $title + '"'), '--suppressApplicationTitle',
    '-d', ('"' + $projectRoot + '"'), 'powershell.exe', '-NoProfile', '-NoExit', '-ExecutionPolicy', 'Bypass',
    '-File', ('"' + $helper + '"'), '-AccountId', $AccountId,
    '-EnvFile', ('"' + $environmentPath + '"'), '-MaxRequests', $MaxRequests)
if ($Action -eq 'Verify') { $arguments += '-VerifyOnly' }
if ($Action -eq 'Recover') { $arguments += @('-Recover', '-AcknowledgeRecovery') }
if ($ManualProfileVerification) { $arguments += '-ManualProfileVerification' }
Start-Process -FilePath $terminal.Source -ArgumentList $arguments -WorkingDirectory $projectRoot -WindowStyle Normal
Write-Host ('Requested visible Windows Terminal: ' + $title)
