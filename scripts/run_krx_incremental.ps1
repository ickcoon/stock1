param(
    [string]$Python,
    [string]$EnvFile,
    [string]$End,
    [string]$Start,
    [switch]$Execute,
    [switch]$RetryHistory,
    [switch]$RebuildAdr,
    [switch]$MasterOnly,
    [string]$MasterDate,
    [int]$MaxCalls = 120
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
if (-not $Python) { $Python = Join-Path $projectRoot '.venv\Scripts\python.exe' }
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'Project Python is missing. Create .venv and install requirements-krx-as-windows.txt first.'
}
if (-not $EnvFile) {
    $privateRoot = Join-Path (Split-Path -Parent $projectRoot) ((Split-Path -Leaf $projectRoot) + '-private')
    $EnvFile = Join-Path $privateRoot '.env.krx.local'
}
Push-Location -LiteralPath $projectRoot
try {
    $arguments = @('-B', '-m', 'kiwoom_collector.krx_incremental', '--max-calls', "$MaxCalls", '--env-file', $EnvFile)
    if ($End) { $arguments += @('--end', $End) }
    if ($Start) { $arguments += @('--start', $Start) }
    if ($Execute) { $arguments += '--execute' }
    if ($RetryHistory) { $arguments += '--retry-history' }
    if ($RebuildAdr) { $arguments += '--rebuild-adr' }
    if ($MasterOnly) { $arguments += '--master-only' }
    if ($MasterDate) { $arguments += @('--master-date', $MasterDate) }
    & $Python @arguments
    $resultCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $resultCode
