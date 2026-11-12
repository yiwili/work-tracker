<#
.SYNOPSIS
    AI Work Tracker (拾时) Tauri v2 + Python Sidecar 一键式桌面端打包脚本。

.DESCRIPTION
    所有后续桌面端正式构建（以及 AI 助手协助构建桌面端）请统一使用本脚本或 build-desktop.cmd：
      1. 校验 Node.js / Python .venv (PyInstaller) / Rust / VS 2022 C++ Build Tools
      2. 可选运行完整 pytest (-RunTests)
      3. 使用 PyInstaller (packaging\awt-sidecar.spec) 打包独立 Python 后台并执行冒烟验证
      4. 构建前端 (frontend\dist) 与 Tauri v2 原生桌面壳 + NSIS 安装包
      5. 将安装包与便携版输出收集到 dist-desktop\ 目录

.EXAMPLE
    .\build-desktop.ps1
    .\build-desktop.ps1 -Setup
    .\build-desktop.ps1 -RunTests
    .\build-desktop.ps1 -SkipSidecar -SkipVerify
#>
param(
    [switch]$Setup,
    [switch]$SkipSidecar,
    [switch]$SkipVerify,
    [switch]$RunTests
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
Set-Location -LiteralPath $PSScriptRoot
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host "==> $Message" -ForegroundColor Cyan
}

function Resolve-NpmCmd {
    $cmd = Get-Command npm.cmd -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    foreach ($candidate in @(
        (Join-Path $env:ProgramFiles 'nodejs\npm.cmd'),
        'C:\nvm4w\nodejs\npm.cmd'
    )) {
        if (Test-Path -LiteralPath $candidate) {
            $dir = Split-Path -Parent $candidate
            $env:Path = "$dir;$env:Path"
            return $candidate
        }
    }
    throw 'Node.js 20.19+ is required (npm.cmd not found). Install Node.js and retry.'
}

function Ensure-CargoPath {
    $cargoBin = Join-Path $env:USERPROFILE '.cargo\bin'
    if ((Test-Path -LiteralPath $cargoBin) -and -not ($env:Path -split ';' | Where-Object { $_ -ieq $cargoBin })) {
        $env:Path = "$cargoBin;$env:Path"
    }
    $rustc = Get-Command rustc.exe -ErrorAction SilentlyContinue
    $cargo = Get-Command cargo.exe -ErrorAction SilentlyContinue
    if (-not $rustc -or -not $cargo) {
        throw "Rust toolchain (rustc/cargo) not found. Install Rustup via: winget install Rustlang.Rustup"
    }
}

function Assert-MsvcToolchain {
    $cl = Get-Command cl.exe -ErrorAction SilentlyContinue
    $hasSdk = (Test-Path -LiteralPath "${env:ProgramFiles(x86)}\Windows Kits\10\Include") -or (Test-Path -LiteralPath "${env:ProgramFiles(x86)}\Windows Kits\11\Include")
    if ($cl -and $hasSdk) { return }

    $vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
    $vcInstall = $null
    if (Test-Path -LiteralPath $vswhere) {
        $vcInstall = & $vswhere -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath 2>$null
    }
    if (-not $vcInstall -or -not $hasSdk) {
        $cached = Get-ChildItem -Path (Join-Path $env:LOCALAPPDATA 'Temp\WinGet\Microsoft.VisualStudio.2022.BuildTools*\vs_BuildTools.exe') -ErrorAction SilentlyContinue | Select-Object -First 1
        $installHint = if ($cached) {
            "Detected cached installer at:`n  $($cached.FullName)`nRun in an interactive Windows terminal (approve UAC once):`n  `"$($cached.FullName)`" --wait --passive --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended"
        } else {
            "Run in an interactive Windows terminal (approve UAC once):`n  winget install --id Microsoft.VisualStudio.2022.BuildTools -e --source winget --override `"--wait --passive --norestart --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended`""
        }
        throw "Visual Studio 2022 C++ Build Tools (VCTools + Windows SDK) are required for Tauri native compilation.`n$installHint"
    }
}

function Ensure-PythonVenv {
    $venvPy = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPy)) {
        $basePy = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        if (-not (Test-Path -LiteralPath $basePy)) {
            $cmdPy = Get-Command python -ErrorAction SilentlyContinue
            if (-not $cmdPy) { throw 'Python 3.12+ is required.' }
            $basePy = $cmdPy.Source
        }
        Write-Host "Creating Python virtual environment at .venv..."
        & $basePy -m venv .venv
        if ($LASTEXITCODE -ne 0) { throw 'Failed to create .venv.' }
        $script:Setup = $true
    }
    & $venvPy -c "import PyInstaller" 2>$null
    if ($LASTEXITCODE -ne 0 -or $Setup) {
        Write-Host "Installing Python backend & desktop packaging dependencies (.[test,desktop])..."
        & $venvPy -m pip install -e '.[test,desktop]'
        if ($LASTEXITCODE -ne 0) { throw 'Failed to install Python dependencies (.[test,desktop]).' }
    }
    return $venvPy
}

function Ensure-FrontendDeps([string]$NpmCmd) {
    $tauriBin = Join-Path $PSScriptRoot 'frontend\node_modules\.bin\tauri.cmd'
    if ($Setup -or -not (Test-Path -LiteralPath 'frontend\node_modules') -or -not (Test-Path -LiteralPath $tauriBin)) {
        Write-Host 'Installing frontend & Tauri CLI dependencies...'
        Push-Location frontend
        try {
            & $NpmCmd ci
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
        } finally {
            Pop-Location
        }
    }
    if (-not (Test-Path -LiteralPath $tauriBin)) {
        throw "Tauri CLI not found at $tauriBin. Run .\build-desktop.ps1 -Setup."
    }
    return $tauriBin
}

function Test-SidecarBinary([string]$VenvPy, [string]$SidecarExe) {
    if (-not (Test-Path -LiteralPath $SidecarExe)) {
        throw "Missing sidecar binary: $SidecarExe"
    }
    $verifyScript = Join-Path $PSScriptRoot 'packaging\verify_sidecar.py'
    & $VenvPy $verifyScript $SidecarExe
    if ($LASTEXITCODE -ne 0) { throw 'Sidecar smoke verification failed.' }
}

Write-Step '[1/6] Checking build toolchain...'
$npmCmd = Resolve-NpmCmd
Ensure-CargoPath
$venvPy = Ensure-PythonVenv
$tauriBin = Ensure-FrontendDeps $npmCmd

if (-not (Test-Path -LiteralPath 'src-tauri\icons\icon.ico')) {
    Write-Host 'Generating application icons...'
    & $venvPy packaging\generate_icons.py
    if ($LASTEXITCODE -ne 0) { throw 'Icon generation failed.' }
}

if ($RunTests) {
    Write-Step '[2/6] Running backend test suite...'
    & $venvPy -m pytest -q
    if ($LASTEXITCODE -ne 0) { throw 'Backend pytest suite failed.' }
} else {
    Write-Step '[2/6] Skipping full pytest suite (pass -RunTests to run all tests)...'
}

$sidecarDir = Join-Path $PSScriptRoot 'src-tauri\resources\sidecar'
$sidecarExe = Join-Path $sidecarDir 'awt-sidecar.exe'

if (-not $SkipSidecar) {
    Write-Step '[3/6] Packaging Python backend sidecar (PyInstaller --onedir)...'
    & $venvPy -m PyInstaller --noconfirm --clean --distpath build\sidecar-dist --workpath build\sidecar-work packaging\awt-sidecar.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller sidecar build failed.' }

    $builtSidecar = Join-Path $PSScriptRoot 'build\sidecar-dist\awt-sidecar'
    if (-not (Test-Path -LiteralPath (Join-Path $builtSidecar 'awt-sidecar.exe'))) {
        throw "Expected PyInstaller output missing under $builtSidecar"
    }
    if (Test-Path -LiteralPath $sidecarDir) {
        Remove-Item -LiteralPath $sidecarDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $sidecarDir -Force | Out-Null
    Copy-Item -Path (Join-Path $builtSidecar '*') -Destination $sidecarDir -Recurse -Force
} else {
    Write-Step '[3/6] Reusing existing Python sidecar...'
}

if (-not (Test-Path -LiteralPath $sidecarExe)) {
    throw "Sidecar executable not found at $sidecarExe. Re-run without -SkipSidecar."
}

if (-not $SkipVerify) {
    Write-Step '[4/6] Running headless sidecar smoke verification...'
    Test-SidecarBinary $venvPy $sidecarExe
} else {
    Write-Step '[4/6] Skipping sidecar smoke verification (-SkipVerify)...'
}

Write-Step '[5/6] Building Tauri v2 desktop executable and NSIS installer...'
Assert-MsvcToolchain
& $tauriBin build
if ($LASTEXITCODE -ne 0) { throw 'Tauri desktop build failed.' }

Write-Step '[6/6] Collecting release artifacts into dist-desktop\...'
$outDir = Join-Path $PSScriptRoot 'dist-desktop'
if (Test-Path -LiteralPath $outDir) {
    Remove-Item -LiteralPath $outDir -Recurse -Force
}
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

$nsisDir = Join-Path $PSScriptRoot 'src-tauri\target\release\bundle\nsis'
$installers = @()
if (Test-Path -LiteralPath $nsisDir) {
    $installers = Get-ChildItem -Path $nsisDir -Filter '*.exe' -ErrorAction SilentlyContinue
    foreach ($inst in $installers) {
        Copy-Item -LiteralPath $inst.FullName -Destination (Join-Path $outDir $inst.Name) -Force
    }
}

$portableDir = Join-Path $outDir 'portable'
New-Item -ItemType Directory -Path $portableDir -Force | Out-Null
$releaseDir = Join-Path $PSScriptRoot 'src-tauri\target\release'
$mainExe = Join-Path $releaseDir 'ai-work-tracker.exe'
if (-not (Test-Path -LiteralPath $mainExe)) {
    throw "Compiled Tauri binary not found at $mainExe"
}
Copy-Item -LiteralPath $mainExe -Destination (Join-Path $portableDir 'ai-work-tracker.exe') -Force
$portableSidecar = Join-Path $portableDir 'resources\sidecar'
New-Item -ItemType Directory -Path $portableSidecar -Force | Out-Null
Copy-Item -Path (Join-Path $sidecarDir '*') -Destination $portableSidecar -Recurse -Force

Write-Host ""
Write-Host "Desktop build complete!" -ForegroundColor Green
if ($installers.Count -gt 0) {
    foreach ($inst in $installers) {
        Write-Host "  NSIS Installer : dist-desktop\$($inst.Name)"
    }
}
Write-Host "  Portable App   : dist-desktop\portable\ai-work-tracker.exe"
