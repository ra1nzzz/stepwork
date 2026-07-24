<#
.SYNOPSIS
  STEPWORK Worker 卸载脚本（Windows Alpha）。
.DESCRIPTION
  W9 L.44：卸载 Python worker 侧（删除 .venv）。
  保留用户数据 $STEPWORK_HOME（数据库、日志、备份），仅删代码环境。
  如需彻底清除用户数据，用 -RemoveData 显式指定。
.PARAMETER RemoveData
  同时删除 $STEPWORK_HOME（数据库、日志、备份）。默认不删。
.EXAMPLE
  .\scripts\uninstall.ps1
  .\scripts\uninstall.ps1 -RemoveData
.NOTES
  W9_PLAN D7：仅 Windows PowerShell。
#>

[CmdletBinding()]
param(
    [switch]$RemoveData
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

# ---------------------------------------------------------------------------
# 1. 删除 .venv
# ---------------------------------------------------------------------------
$VenvPath = Join-Path $RepoRoot ".venv"
Write-Step "删除虚拟环境 .venv"
if (Test-Path $VenvPath) {
    # 先尝试 deactivate（若用户当前 shell 激活着 venv）
    try {
        if (Get-Command deactivate -ErrorAction SilentlyContinue) {
            deactivate
        }
    } catch { }

    Remove-Item -Recurse -Force $VenvPath
    Write-Ok ".venv 已删除"
} else {
    Write-Ok ".venv 不存在，跳过"
}

# ---------------------------------------------------------------------------
# 2. 可选：删除用户数据
# ---------------------------------------------------------------------------
$StepworkHome = $env:STEPWORK_HOME
if (-not $StepworkHome) {
    $StepworkHome = Join-Path $env:USERPROFILE "STEPWORK"
}

if ($RemoveData) {
    Write-Step "删除用户数据 $StepworkHome"
    if (Test-Path $StepworkHome) {
        Remove-Item -Recurse -Force $StepworkHome
        Write-Ok "用户数据已删除"
    } else {
        Write-Ok "用户数据目录不存在，跳过"
    }
} else {
    Write-Step "保留用户数据"
    Write-Host "  数据目录: $StepworkHome"
    Write-Host "  如需彻底清除，重跑: .\scripts\uninstall.ps1 -RemoveData"
}

# ---------------------------------------------------------------------------
# 3. 清理 pip 缓存（可选，留作指引）
# ---------------------------------------------------------------------------
Write-Step "完成"
Write-Host "  STEPWORK Worker 已卸载。"
Write-Host "  代码仓库本身未删除（如需删除，手动 rm -rf $RepoRoot）。"
Write-Host ""
