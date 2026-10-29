# Local credential validation and message-only preview. This script cannot send.
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if ((Get-Location).Path -ne $projectRoot) {
    throw 'Run this script from the stock1-dev project root.'
}
$python = Join-Path $projectRoot '.venv\Scripts\python.exe'
& $python -B -c 'import sys; sys.exit(0)'
if ($LASTEXITCODE -ne 0) { throw 'Repair the development Python environment before entering credentials.' }
if (-not (Test-Path -LiteralPath 'reports/telegram-once-preflight.json')) {
    & $python -B -m kiwoom_collector.am_momentum --fixture tests/fixtures/am_momentum_synthetic.json --name telegram-once-preflight
    if ($LASTEXITCODE -ne 0) { throw 'Synthetic report generation failed.' }
}
$names = @('ARVIS_TELEGRAM_BOT_TOKEN', 'ARVIS_TELEGRAM_CHAT_ID', 'ARVIS_TELEGRAM_ALLOWED_CHAT_IDS')
$previous = @{}
foreach ($name in $names) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
try {
    $tokenInput = Read-Host 'Telegram Bot Token (hidden)' -AsSecureString
    $chatInput = Read-Host 'Private Chat ID (hidden)' -AsSecureString
    $env:ARVIS_TELEGRAM_BOT_TOKEN = [System.Net.NetworkCredential]::new('', $tokenInput).Password
    $env:ARVIS_TELEGRAM_CHAT_ID = [System.Net.NetworkCredential]::new('', $chatInput).Password
    $env:ARVIS_TELEGRAM_ALLOWED_CHAT_IDS = $env:ARVIS_TELEGRAM_CHAT_ID
    $validateConfiguration = @'
from kiwoom_collector.telegram_briefing import Configuration
import sys
try:
    Configuration.from_environment()
except Exception:
    print('configuration_rejected')
    sys.exit(1)
print('configuration_valid_local_only')
'@
    & $python -B -c $validateConfiguration
    if ($LASTEXITCODE -ne 0) { throw 'Local configuration validation failed.' }
    & $python -B -m kiwoom_collector.telegram_briefing --report reports/telegram-once-preflight.json
    if ($LASTEXITCODE -ne 0) { throw 'Synthetic message preview rejected.' }
    Write-Host 'Preparation complete. No network call or delivery reservation. Credentials will now be cleared/restored.'
} finally {
    foreach ($name in $names) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
    if ($null -ne $tokenInput) { $tokenInput.Dispose() }
    if ($null -ne $chatInput) { $chatInput.Dispose() }
}
