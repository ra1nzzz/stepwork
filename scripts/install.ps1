<#
.SYNOPSIS
  STEPWORK Worker 安装脚本（Windows Alpha）。
.DESCRIPTION
  W9 L.44：MVP Windows Alpha 安装脚本。
  流程：检查 Python 3.12+ → 创建 .venv → pip install -e ".[dev]" → 打印快捷方式指引。
  不创建桌面快捷方式（Tauri 桌面应用尚未发布；仅装 Python worker 侧）。
  保留用户数据 $STEPWORK_HOME（卸载脚本也不删）。
.PARAMETER SkipVenv
  跳过 .venv 创建（已有 venv 时用）。默认会创建 .venv。
.EXAMPLE
  .\scripts\install.ps1
.NOTES
  W9_PLAN D7：仅 Windows PowerShell；macOS/Linux 推 V0.2。
  W9_PLAN R7：前置检查 Python 版本，缺失时给明确指引。
#>

[CmdletBinding()]
param(
    [switch]$SkipVenv
)

$ErrorActionPreference = "Stop"
$RepoRoot = Split-Path -Parent $PSScriptRoot

function Write-Step([string]$msg) {
    Write-Host ""
    Write-Host "==> $msg" -ForegroundColor Cyan
}

function Write-Ok([string]$msg) {
    Write-Host "  [OK] $msg" -ForegroundColor Green
}

function Write-Err([string]$msg) {
    Write-Host "  [ERR] $msg" -ForegroundColor Red
}

# ---------------------------------------------------------------------------
# 1. 前置检查：Python 3.12+
# ---------------------------------------------------------------------------
Write-Step "检查 Python 版本（要求 3.12+）"

$pythonCmd = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $ver = & $cmd --version 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver -match "Python (\d+)\.(\d+)") {
            $pythonCmd = $cmd
            break
        }
    } catch { }
}

if (-not $pythonCmd) {
    Write-Err "未找到 Python。请安装 Python 3.12+ 后重试："
    Write-Host "  https://www.python.org/downloads/"
    Write-Host "  安装时勾选 'Add Python to PATH'"
    exit 1
}

$major = [int]$Matches[1]
$minor = [int]$Matches[2]
$verStr = "$major.$minor"
Write-Host "  检测到: $ver via $pythonCmd"

if ($major -lt 3 -or ($major -eq 3 -and $minor -lt 12)) {
    Write-Err "Python $verStr 版本过低，要求 3.12+。"
    Write-Host "  请从 https://www.python.org/downloads/ 下载 3.12 或更新版本。"
    exit 1
}
Write-Ok "Python $verStr 满足要求"

# ---------------------------------------------------------------------------
# 2. 创建虚拟环境（.venv）
# ---------------------------------------------------------------------------
$VenvPath = Join-Path $RepoRoot ".venv"
if (-not $SkipVenv) {
    if (Test-Path $VenvPath) {
        Write-Step ".venv 已存在，跳过创建（用 -SkipVenv 可显式跳过）"
    } else {
        Write-Step "创建虚拟环境 .venv"
        & $pythonCmd -m venv $VenvPath
        if ($LASTEXITCODE -ne 0) {
            Write-Err "venv 创建失败"
            exit 1
        }
        Write-Ok ".venv 已创建"
    }
} else {
    Write-Step "跳过 .venv 创建（-SkipVenv）"
}

# ---------------------------------------------------------------------------
# 3. 安装依赖（pip install -e ".[dev]"）
# ---------------------------------------------------------------------------
Write-Step "安装依赖（pip install -e .[dev]）"
$Pip = Join-Path $VenvPath "Scripts\pip.exe"
if (-not (Test-Path $Pip)) {
    # 某些环境下 pip.exe 不存在，回退到 python -m pip
    $Pyp = Join-Path $VenvPath "Scripts\python.exe"
    & $Pyp -m pip install --upgrade pip
    & $Pyp -m pip install -e ".[dev]"
} else {
    & $Pip install --upgrade pip
    & $Pip install -e ".[dev]"
}
if ($LASTEXITCODE -ne 0) {
    Write-Err "依赖安装失败"
    exit 1
}
Write-Ok "依赖已安装"

# ---------------------------------------------------------------------------
# 4. 验证安装（跑一次 --version / 模块导入）
# ---------------------------------------------------------------------------
Write-Step "验证安装"
$Pyp = Join-Path $VenvPath "Scripts\python.exe"
& $Pyp -c "import worker.runtime; print('worker.runtime OK')"
if ($LASTEXITCODE -ne 0) {
    Write-Err "worker.runtime 导入失败，请检查日志"
    exit 1
}
Write-Ok "worker.runtime 可导入"

# ---------------------------------------------------------------------------
# 5. 打印后续指引
# ---------------------------------------------------------------------------
$CliExe = Join-Path $VenvPath "Scripts\stepwork-cli.exe"

Write-Step "安装完成"
Write-Host "  仓库根目录: $RepoRoot"
Write-Host "  虚拟环境:   $VenvPath"
Write-Host "  CLI 入口:   $CliExe"
Write-Host ""
Write-Host "  后续操作："
Write-Host "    1. 激活 venv:  .venv\Scripts\Activate.ps1"
Write-Host "    2. 跑测试:     python -m pytest worker\tests -q"
Write-Host "    3. 注入种子:   python scripts\seed_demo.py"
Write-Host "    4. 启动 CLI:   stepwork-cli --help"
Write-Host ""
Write-Host "  数据目录: `$env:STEPWORK_HOME（默认 ~/STEPWORK）"
Write-Host "  卸载:       .\scripts\uninstall.ps1"
Write-Host ""
