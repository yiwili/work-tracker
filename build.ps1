param([switch]$Install)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskNpm = 'npm.cmd'
if (-not (Get-Command $taskNpm -ErrorAction SilentlyContinue)) {
    $taskNpm = Join-Path $env:ProgramFiles 'nodejs\npm.cmd'
    if (-not (Test-Path -LiteralPath $taskNpm)) {
        throw 'Node.js 20.19+ is required. Install it and run this script again.'
    }
}
Push-Location frontend
try {
    if ($Install -or -not (Test-Path -LiteralPath 'node_modules')) {
        Write-Host 'Installing frontend dependencies...'
        & $taskNpm ci
        if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
    }
    Write-Host 'Building frontend...'
    & $taskNpm run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally {
    Pop-Location
}
Write-Host 'Frontend build complete: frontend\dist'
Write-Host 'A running service serves the new files directly; restart it after backend changes.'
