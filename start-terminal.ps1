param([switch]$Setup, [string]$Date = '', [int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) {
    $taskRuntime = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (-not (Test-Path -LiteralPath $taskRuntime)) { $taskRuntime = 'python' }
    & $taskRuntime -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Python 3.12+ is required.' }
    $Setup = $true
}
if ($Setup) {
    & $taskPython -m pip install -e .
    if ($LASTEXITCODE -ne 0) { throw 'Terminal dependencies could not be installed.' }
}
$taskArguments = @('-X','utf8','-m','awt.cli','terminal','--url',"http://127.0.0.1:$Port")
if ($Date) { $taskArguments += @('--date',$Date) }
& $taskPython @taskArguments
if ($LASTEXITCODE -ne 0) { throw 'Work Tracker terminal exited with an error.' }
