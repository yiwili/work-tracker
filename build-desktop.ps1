<#
.SYNOPSIS
    AI Work Tracker (拾时) 一键式桌面端与绿色便携版打包脚本。

.DESCRIPTION
    所有后续桌面端 / 便携版正式构建（以及 AI 助手协助构建）请统一使用本脚本或 build-desktop.cmd：
      -Target all   : 构建纯绿色便携版 (Edge/托盘) + Tauri v2 原生安装版与 Tauri 便携版（默认）
      -Target green : 仅构建纯绿色免安装便携版（PyInstaller 内置前端 + 托盘/Edge独立窗口 + 同级 data/，无需 VS Build Tools）
      -Target tauri : 仅构建 Tauri v2 原生桌面壳 + NSIS 安装包 + Tauri 便携版

.EXAMPLE
    .\build-desktop.ps1
    .\build-desktop.ps1 -Target green
    .\build-desktop.ps1 -Setup
    .\build-desktop.ps1 -RunTests
    .\build-desktop.ps1 -SkipSidecar -SkipVerify
#>
param(
    [ValidateSet('all', 'green', 'tauri')]
    [string]$Target = 'all',
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

function Write-PortableReadme([string]$DestinationDir, [string]$EditionLabel) {
    $readme = @"
拾时 (AI Work Tracker) - $EditionLabel

1. 解压到任意英文或中文目录后，直接双击 ai-work-tracker.exe 即可运行，无需安装 Python 或其他运行库。
2. 同级目录下的 portable.flag 用于启用“真便携模式”：数据库 work.db、日志与浏览器缓存默认保存在本目录的 data\ 文件夹中（请勿删除 portable.flag）。
3. 如整包复制到同一台电脑的任意新位置，历史数据与设置完整保留；若复制到另一台电脑，work.db 中的任务与历史记录仍可完整读取，模型 API Key 与 Gitee Token 需在新电脑上重新保存一次（Windows DPAPI 加密绑定本机账户）。
4. 右下角系统托盘支持“打开主界面 / 打开数据目录 / 退出拾时”；如需备份，请先退出拾时，再直接备份整个文件夹或其中的 data\ 目录。
"@
    Set-Content -LiteralPath (Join-Path $DestinationDir 'README-PORTABLE.txt') -Value $readme -Encoding UTF8
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
    $viteBin = Join-Path $PSScriptRoot 'frontend\node_modules\.bin\vite.cmd'
    $needTauri = ($Target -ne 'green')
    $missingBin = (-not (Test-Path -LiteralPath $viteBin)) -or ($needTauri -and -not (Test-Path -LiteralPath $tauriBin))
    if ($Setup -or -not (Test-Path -LiteralPath 'frontend\node_modules') -or $missingBin) {
        Write-Host 'Installing frontend dependencies...'
        Push-Location frontend
        try {
            & $NpmCmd ci
            if ($LASTEXITCODE -ne 0) { throw 'Frontend dependency installation failed.' }
        } finally {
            Pop-Location
        }
    }
    if ($needTauri -and -not (Test-Path -LiteralPath $tauriBin)) {
        throw "Tauri CLI not found at $tauriBin. Run .\build-desktop.ps1 -Setup."
    }
    return $tauriBin
}

function Build-FrontendDist([string]$NpmCmd) {
    Write-Host 'Building frontend production bundle (frontend\dist)...'
    Push-Location frontend
    try {
        & $NpmCmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend production build failed.' }
    } finally {
        Pop-Location
    }
}

function Test-SidecarBinary([string]$VenvPy, [string]$SidecarExe) {
    if (-not (Test-Path -LiteralPath $SidecarExe)) {
        throw "Missing sidecar binary: $SidecarExe"
    }
    $verifyScript = Join-Path $PSScriptRoot 'packaging\verify_sidecar.py'
    & $VenvPy $verifyScript $SidecarExe
    if ($LASTEXITCODE -ne 0) { throw 'Sidecar smoke verification failed.' }
}

function Test-PortableGreenBinary([string]$VenvPy, [string]$PortableExe) {
    if (-not (Test-Path -LiteralPath $PortableExe)) {
        throw "Missing portable binary: $PortableExe"
    }
    $verifyScript = Join-Path $PSScriptRoot 'packaging\verify_portable.py'
    & $VenvPy $verifyScript $PortableExe
    if ($LASTEXITCODE -ne 0) { throw 'Portable green smoke verification failed.' }
}

$needGreen = ($Target -eq 'all') -or ($Target -eq 'green')
$needTauri = ($Target -eq 'all') -or ($Target -eq 'tauri')

Write-Step "[1/6] Checking build toolchain (Target=$Target)..."
$npmCmd = Resolve-NpmCmd
if ($needTauri) {
    Ensure-CargoPath
}
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

$outDir = Join-Path $PSScriptRoot 'dist-desktop'
New-Item -ItemType Directory -Path $outDir -Force | Out-Null

if ($needGreen) {
    Write-Step '[3/6] Building Pure-Green Portable Edition (no VS Build Tools required)...'
    Build-FrontendDist $npmCmd
    & $venvPy -m PyInstaller --noconfirm --clean --distpath build\portable-dist --workpath build\portable-work packaging\awt-portable.spec
    if ($LASTEXITCODE -ne 0) { throw 'PyInstaller pure-green portable build failed.' }

    $builtGreen = Join-Path $PSScriptRoot 'build\portable-dist\ai-work-tracker-portable'
    if (-not (Test-Path -LiteralPath (Join-Path $builtGreen 'ai-work-tracker.exe'))) {
        throw "Expected PyInstaller portable output missing under $builtGreen"
    }

    $greenDir = Join-Path $outDir 'portable-green'
    if (Test-Path -LiteralPath $greenDir) {
        Remove-Item -LiteralPath $greenDir -Recurse -Force
    }
    New-Item -ItemType Directory -Path $greenDir -Force | Out-Null
    Copy-Item -Path (Join-Path $builtGreen '*') -Destination $greenDir -Recurse -Force
    Set-Content -LiteralPath (Join-Path $greenDir 'portable.flag') -Value 'portable=1' -Encoding ASCII
    Write-PortableReadme $greenDir '纯绿色免安装便携版（内置前端 + 系统托盘 + Edge 独立应用窗口）'

    $greenExe = Join-Path $greenDir 'ai-work-tracker.exe'
    if (-not $SkipVerify) {
        Test-PortableGreenBinary $venvPy $greenExe
    }

    $greenZip = Join-Path $outDir 'ai-work-tracker-portable-win64.zip'
    if (Test-Path -LiteralPath $greenZip) {
        Remove-Item -LiteralPath $greenZip -Force
    }
    Compress-Archive -Path (Join-Path $greenDir '*') -DestinationPath $greenZip -Force
    Write-Host "  Pure-Green Portable Dir : dist-desktop\portable-green\ai-work-tracker.exe" -ForegroundColor Green
    Write-Host "  Pure-Green Portable Zip : dist-desktop\ai-work-tracker-portable-win64.zip" -ForegroundColor Green
}

if (-not $needTauri) {
    Write-Host ""
    Write-Host "Portable green build complete!" -ForegroundColor Green
    exit 0
}

$sidecarDir = Join-Path $PSScriptRoot 'src-tauri\resources\sidecar'
$sidecarExe = Join-Path $sidecarDir 'awt-sidecar.exe'

if (-not $SkipSidecar) {
    Write-Step '[4/6] Packaging Python backend sidecar (PyInstaller --onedir)...'
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
    Write-Step '[4/6] Reusing existing Python sidecar...'
}

if (-not (Test-Path -LiteralPath $sidecarExe)) {
    throw "Sidecar executable not found at $sidecarExe. Re-run without -SkipSidecar."
}

if (-not $SkipVerify) {
    Write-Host 'Running headless sidecar smoke verification...'
    Test-SidecarBinary $venvPy $sidecarExe
}

Write-Step '[5/6] Building Tauri v2 desktop executable and NSIS installer...'
Assert-MsvcToolchain
& $tauriBin build
if ($LASTEXITCODE -ne 0) { throw 'Tauri desktop build failed.' }

Write-Step '[6/6] Collecting Tauri release artifacts into dist-desktop\...'
Get-ChildItem -Path $outDir -Filter '*-setup.exe' -ErrorAction SilentlyContinue | Remove-Item -Force

$nsisDir = Join-Path $PSScriptRoot 'src-tauri\target\release\bundle\nsis'
$installers = @()
if (Test-Path -LiteralPath $nsisDir) {
    $installers = Get-ChildItem -Path $nsisDir -Filter '*.exe' -ErrorAction SilentlyContinue
    foreach ($inst in $installers) {
        Copy-Item -LiteralPath $inst.FullName -Destination (Join-Path $outDir $inst.Name) -Force
    }
}

$portableDir = Join-Path $outDir 'portable'
if (Test-Path -LiteralPath $portableDir) {
    Remove-Item -LiteralPath $portableDir -Recurse -Force
}
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
Set-Content -LiteralPath (Join-Path $portableDir 'portable.flag') -Value 'portable=1' -Encoding ASCII
Write-PortableReadme $portableDir 'Tauri 原生桌面便携版（WebView2 + 系统托盘 + 同级 data/）'

$tauriPortableZip = Join-Path $outDir 'ai-work-tracker-tauri-portable-win64.zip'
if (Test-Path -LiteralPath $tauriPortableZip) {
    Remove-Item -LiteralPath $tauriPortableZip -Force
}
Compress-Archive -Path (Join-Path $portableDir '*') -DestinationPath $tauriPortableZip -Force

Write-Host ""
Write-Host "Desktop build complete!" -ForegroundColor Green
if ($installers.Count -gt 0) {
    foreach ($inst in $installers) {
        Write-Host "  NSIS Installer           : dist-desktop\$($inst.Name)"
    }
}
Write-Host "  Tauri Portable Dir       : dist-desktop\portable\ai-work-tracker.exe"
Write-Host "  Tauri Portable Zip       : dist-desktop\ai-work-tracker-tauri-portable-win64.zip"
if ($needGreen) {
    Write-Host "  Pure-Green Portable Dir  : dist-desktop\portable-green\ai-work-tracker.exe"
    Write-Host "  Pure-Green Portable Zip  : dist-desktop\ai-work-tracker-portable-win64.zip"
}
