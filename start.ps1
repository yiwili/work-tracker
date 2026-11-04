param([switch]$Setup, [int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Get-TaskListener {
    $connection = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($connection) { return [int]$connection.OwningProcess }
    return 0
}

$taskOldPid = Get-TaskListener
if ($taskOldPid) {
    $taskIsOurs = $false
    try {
        $taskHealth = Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/health" -TimeoutSec 2
        $taskIsOurs = $taskHealth.application -eq 'ai-work-tracker'
    } catch { }
    if (-not $taskIsOurs) {
        throw "Port $Port is used by another process (PID $taskOldPid). Stop it or choose another -Port."
    }
    Write-Host "Stopping the running AI Work Tracker (PID $taskOldPid)..."
    & taskkill.exe /PID $taskOldPid /T /F 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "Could not stop process $taskOldPid." }
    $taskDeadline = (Get-Date).AddSeconds(10)
    while ((Get-TaskListener) -and (Get-Date) -lt $taskDeadline) {
        Start-Sleep -Milliseconds 200
    }
    if (Get-TaskListener) { throw "Port $Port is still occupied after stopping the old process." }
}

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
