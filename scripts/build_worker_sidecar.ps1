# 打包 worker sidecar 为独立 EXE（W9 增补）
#
# 产物：dist/stepwork-worker/stepwork-worker.exe（单文件）
# 后续：复制到 src-tauri/binaries/ 并按 Tauri target triple 重命名
#
# 用法：在 repo root 执行
#   .\.venv\Scripts\python.exe scripts\build_worker_sidecar.ps1
# 或直接：
#   pwsh scripts\build_worker_sidecar.ps1

$ErrorActionPreference = "Stop"

$RepoRoot = Resolve-Path "$PSScriptRoot\.."
$VenvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"

if (-not (Test-Path $VenvPython)) {
    Write-Error "未找到 .venv，请先执行 scripts\install.ps1"
    exit 1
}

Write-Host "=== STEPWORK Worker Sidecar 打包 ===" -ForegroundColor Cyan
Write-Host "Repo: $RepoRoot"
Write-Host "Python: $VenvPython"

# PyInstaller 打包
$EntryScript = Join-Path $RepoRoot "scripts\worker_entry.py"
$MigrationsDir = Join-Path $RepoRoot "migrations"
$DistDir = Join-Path $RepoRoot "dist\worker-sidecar"
$WorkDir = Join-Path $RepoRoot "build\worker-sidecar"

Write-Host "`n--- PyInstaller ---" -ForegroundColor Yellow
& $VenvPython -m PyInstaller `
    --onefile `
    --name stepwork-worker `
    --distpath $DistDir `
    --workpath $WorkDir `
    --specpath $WorkDir `
    --add-data "$MigrationsDir;migrations" `
    --collect-submodules worker `
    --hidden-import pydantic `
    --hidden-import sqlalchemy `
    --noconfirm `
    --clean `
    $EntryScript

if ($LASTEXITCODE -ne 0) {
    Write-Error "PyInstaller 打包失败 (exit $LASTEXITCODE)"
    exit $LASTEXITCODE
}

$WorkerExe = Join-Path $DistDir "stepwork-worker.exe"
if (-not (Test-Path $WorkerExe)) {
    Write-Error "产物未找到: $WorkerExe"
    exit 1
}

$SizeMB = [math]::Round((Get-Item $WorkerExe).Length / 1MB, 2)
Write-Host "`n=== 打包成功 ===" -ForegroundColor Green
Write-Host "产物: $WorkerExe ($SizeMB MB)"

# 复制到 Tauri sidecar 目录，按 target triple 命名
$TargetTriple = "x86_64-pc-windows-msvc"
$SidecarDir = Join-Path $RepoRoot "apps\desktop\src-tauri\binaries"
$SidecarName = "stepwork-worker-$TargetTriple.exe"
$SidecarPath = Join-Path $SidecarDir $SidecarName

New-Item -ItemType Directory -Force -Path $SidecarDir | Out-Null
Copy-Item -Force $WorkerExe $SidecarPath

Write-Host "已复制到: $SidecarPath" -ForegroundColor Green

# 同时同步到 target/release 目录（避免 Tauri build 产物中的 sidecar 旧版本导致 worker 崩溃）
$ReleaseDir = Join-Path $RepoRoot "apps\desktop\src-tauri\target\release"
$ReleaseBinariesDir = Join-Path $ReleaseDir "binaries"
if (Test-Path $ReleaseDir) {
    New-Item -ItemType Directory -Force -Path $ReleaseBinariesDir | Out-Null
    Copy-Item -Force $WorkerExe (Join-Path $ReleaseBinariesDir $SidecarName)
    # flat 副本（无 target triple 后缀）作为兜底，覆盖某些 Tauri 配置下的查找路径
    Copy-Item -Force $WorkerExe (Join-Path $ReleaseDir "stepwork-worker.exe")
    Write-Host "已同步到: $ReleaseBinariesDir\$SidecarName" -ForegroundColor Green
    Write-Host "已同步到: $ReleaseDir\stepwork-worker.exe (flat)" -ForegroundColor Green
}

Write-Host "`n下一步: 在 src-tauri 目录执行 cargo tauri build" -ForegroundColor Cyan
