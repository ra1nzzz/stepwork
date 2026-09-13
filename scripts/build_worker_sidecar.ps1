# 打包 worker sidecar 为独立 EXE（Tauri `bundle.externalBin` 的目标）
#
# 产物：
#   dist/worker-sidecar/stepwork-worker.exe                              单文件 exe
#   apps/desktop/src-tauri/binaries/stepwork-worker-<triple>.exe         Tauri 要的文件名
#
# 用法（在 repo root）：
#   pwsh scripts\build_worker_sidecar.ps1
#
# 打包逻辑全在 packaging/stepwork-worker.spec（datas 由
# worker.runtime.assets.bundled_datas() 生成、hiddenimports 整包收集 worker），
# 本脚本只管「用哪个 Python / 产物投递到哪」。
#
# 可选引擎默认关，需要时用环境变量开（会显著增大体积）：
#   $env:STEPWORK_BUNDLE_RENDER = "1"   # 连 playwright（浏览器二进制仍需另装）
#   $env:STEPWORK_BUNDLE_ASR    = "1"   # 连 faster-whisper
#   $env:STEPWORK_BUNDLE_TTS    = "1"   # 连 edge-tts

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path "$PSScriptRoot\..").Path
$SpecFile = Join-Path $RepoRoot "packaging\stepwork-worker.spec"

Write-Host "=== STEPWORK Worker Sidecar 打包 ===" -ForegroundColor Cyan
Write-Host "Repo: $RepoRoot"

# --- 1. 选一个**同时有 PyInstaller** 的解释器 -------------------------------
#
# 不硬依赖 .venv：本机 python -m venv 建出来的目录是空的（等效 no-op），
# 旧脚本那句 `未找到 .venv，请先执行 install.ps1` 会把人堵死在这里。
# 改为候选列表逐个探测，且把「没有 PyInstaller」和「解释器不存在」分开报。
function Get-BuildPython {
    param([string]$Root)

    $candidates = New-Object System.Collections.Generic.List[string]
    $venv = Join-Path $Root ".venv\Scripts\python.exe"
    if (Test-Path $venv) { $candidates.Add($venv) }
    if (Get-Command py -ErrorAction SilentlyContinue) {
        $found = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $found) {
            $candidates.Add(($found | Select-Object -Last 1).Trim())
        }
    }
    foreach ($name in @("python", "python3.12")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd) { $candidates.Add($cmd.Source) }
    }

    $seenNoPyInstaller = @()
    foreach ($c in $candidates) {
        if (-not (Test-Path $c)) { continue }
        & $c -c "import PyInstaller, sys; sys.exit(0 if int(PyInstaller.__version__.split('.')[0]) >= 6 else 3)" 2>$null
        $code = $LASTEXITCODE
        if ($code -eq 0) { return $c }
        if ($code -eq 3) {
            throw "PyInstaller 版本过低（需 >= 6）：$c"
        }
        $seenNoPyInstaller += $c
    }

    $where = if ($seenNoPyInstaller.Count) { $seenNoPyInstaller -join " / " } else { "(没找到任何 python)" }
    throw @"
找不到可用的构建解释器。已探测：$where
这些解释器里没有 PyInstaller。装它（PyInstaller 只是构建期依赖，不随运行期分发）：
    & "$(if ($seenNoPyInstaller.Count) { $seenNoPyInstaller[0] } else { 'python' })" -m pip install "pyinstaller>=6"
或按 pyproject 的可选依赖组一次装齐：
    pip install -e ".[package]"
"@
}

$BuildPython = Get-BuildPython -Root $RepoRoot
$PyInstallerVersion = (& $BuildPython -c "import PyInstaller; print(PyInstaller.__version__)").Trim()
Write-Host "Python: $BuildPython"
Write-Host "PyInstaller: $PyInstallerVersion"

if (-not (Test-Path $SpecFile)) {
    throw "打包配置缺失：$SpecFile"
}
if (-not (Test-Path (Join-Path $RepoRoot "scripts\worker_entry.py"))) {
    throw "打包入口缺失：scripts\worker_entry.py"
}

# --- 2. PyInstaller ---------------------------------------------------------
$DistDir = Join-Path $RepoRoot "dist\worker-sidecar"
$WorkDir = Join-Path $RepoRoot "build\worker-sidecar"

Write-Host "`n--- PyInstaller ---" -ForegroundColor Yellow
& $BuildPython -m PyInstaller `
    --noconfirm `
    --clean `
    --log-level WARN `
    --distpath $DistDir `
    --workpath $WorkDir `
    $SpecFile

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller 打包失败 (exit $LASTEXITCODE)"
}

$WorkerExe = Join-Path $DistDir "stepwork-worker.exe"
if (-not (Test-Path $WorkerExe)) {
    throw "产物未找到: $WorkerExe"
}

$SizeMB = [math]::Round((Get-Item $WorkerExe).Length / 1MB, 2)
Write-Host "`n=== 打包成功 ===" -ForegroundColor Green
Write-Host "产物: $WorkerExe ($SizeMB MB)"

# --- 3. 投递到 Tauri sidecar 目录（文件名必须带 target triple）--------------
#
# Tauri 要求 externalBin 的**实际文件名**带 target triple 后缀，
# tauri.conf.json 里声明的 "binaries/stepwork-worker" 只是前缀。
$TargetTriple = "x86_64-pc-windows-msvc"
if (Get-Command rustc -ErrorAction SilentlyContinue) {
    $hostLine = (& rustc -vV) | Where-Object { $_ -like "host:*" } | Select-Object -First 1
    if ($hostLine) { $TargetTriple = ($hostLine -replace "^host:\s*", "").Trim() }
}
Write-Host "Target triple: $TargetTriple"

$SidecarDir = Join-Path $RepoRoot "apps\desktop\src-tauri\binaries"
$SidecarName = "stepwork-worker-$TargetTriple.exe"
New-Item -ItemType Directory -Force -Path $SidecarDir | Out-Null
Copy-Item -Force $WorkerExe (Join-Path $SidecarDir $SidecarName)
Write-Host "已复制到: $(Join-Path $SidecarDir $SidecarName)" -ForegroundColor Green

# 同步到 target/release：历史上撞过「cargo tauri build 之后跑起来的还是旧 sidecar」
# —— release 目录里留着一份过期副本。这里覆盖它，并额外放一份 flat 名字兜底
# 某些 Tauri 配置下的查找路径。
$ReleaseDir = Join-Path $RepoRoot "apps\desktop\src-tauri\target\release"
if (Test-Path $ReleaseDir) {
    $ReleaseBinariesDir = Join-Path $ReleaseDir "binaries"
    New-Item -ItemType Directory -Force -Path $ReleaseBinariesDir | Out-Null
    Copy-Item -Force $WorkerExe (Join-Path $ReleaseBinariesDir $SidecarName)
    Copy-Item -Force $WorkerExe (Join-Path $ReleaseDir "stepwork-worker.exe")
    Write-Host "已同步到: $ReleaseBinariesDir\$SidecarName" -ForegroundColor Green
}

Write-Host "`n下一步:" -ForegroundColor Cyan
Write-Host "  1) 冒烟: python scripts\test_sidecar.py `"$WorkerExe`""
Write-Host "  2) 出包: cd apps\desktop\src-tauri; cargo tauri build"
