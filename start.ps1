param([switch]$Setup, [int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
try {
    $taskHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
    if ($taskHealth.application -eq 'ai-work-tracker') {
        Write-Host "AI Work Tracker is already running: http://127.0.0.1:$Port"
        exit 0
    }
} catch { }
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    $taskRuntime = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (-not (Test-Path -LiteralPath $taskRuntime)) { $taskRuntime = 'python' }
    & $taskRuntime -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required.' }
    $Setup = $true
}
if ($Setup) {
    & $taskPython -m pip install -e '.[test]'
    if ($LASTEXITCODE -ne 0) { throw 'Backend dependency installation failed.' }
}
if ($Setup -or -not (Test-Path -LiteralPath 'frontend\dist\index.html')) {
    Push-Location frontend
    try {
        npm.cmd ci
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
        npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
}
Write-Host "AI Work Tracker: http://127.0.0.1:$Port"
& $taskPython -m awt.cli serve --port $Port
