<#
.SYNOPSIS
    Live test of the packaged sidecar worker EXE via .NET Process API.
    Sends a health_check RPC frame and prints the response.
#>
$ErrorActionPreference = "Stop"

$exe = "D:\Code\STEPWORK\apps\desktop\src-tauri\binaries\stepwork-worker-x86_64-pc-windows-msvc.exe"
if (-not (Test-Path $exe)) { Write-Error "EXE not found: $exe"; exit 1 }

# Use a temp STEPWORK_HOME to avoid sandbox conflicts on the user's real home
$tmpHome = Join-Path $env:TEMP "sw_test_home_$(Get-Random)"
New-Item -ItemType Directory -Path $tmpHome -Force | Out-Null

# Build a JSON-RPC health_check frame: [4-byte BE length][JSON payload]
$payload = '{"jsonrpc":"2.0","id":"1","method":"runtime.health_check","params":{}}'
$bytes = [System.Text.Encoding]::UTF8.GetBytes($payload)
$lenBytes = [BitConverter]::GetBytes([uint32]$bytes.Length)
[Array]::Reverse($lenBytes)  # to big-endian
$frame = $lenBytes + $bytes

$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = $exe
$psi.UseShellExecute = $false
$psi.RedirectStandardInput = $true
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.CreateNoWindow = $true
$psi.EnvironmentVariables["STEPWORK_HOME"] = $tmpHome
$psi.EnvironmentVariables["STEPWORK_LOG_LEVEL"] = "DEBUG"

$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi

$errLines = New-Object System.Collections.Generic.List[string]
$procSync = New-Object object
$proc.Add_ErrorDataReceived({
    param($sender, $e)
    if ($null -ne $e.Data) {
        [System.Threading.Monitor]::Enter($script:procSync)
        try { $script:errLines.Add($e.Data) } finally { [System.Threading.Monitor]::Exit($script:procSync) }
    }
})

$null = $proc.Start()
$proc.BeginErrorReadLine()

# Give worker a moment to initialize (configure_logging + bootstrap_db + open stdin/stdout)
Start-Sleep -Milliseconds 800

if ($proc.HasExited) {
    Write-Host "WORKER EXITED EARLY: code=$($proc.ExitCode)"
    Start-Sleep -Milliseconds 200
    Write-Host "--- STDERR ---"
    foreach ($l in $errLines) { Write-Host "  $l" }
    exit 1
}

Write-Host "WORKER ALIVE: PID=$($proc.Id)"

# Send the health_check frame
try {
    $proc.StandardInput.BaseStream.Write($frame, 0, $frame.Length)
    $proc.StandardInput.BaseStream.Flush()
} catch {
    Write-Host "WRITE FAILED: $_"
    if (-not $proc.HasExited) { $proc.Kill() }
    exit 1
}

# Read the response frame directly from stdout.BaseStream (binary)
$stdout = $proc.StandardOutput.BaseStream
$header = New-Object byte[] 4

$deadline = [DateTime]::Now.AddSeconds(5)
$read = 0
while ($read -lt 4 -and [DateTime]::Now -lt $deadline) {
    $n = $stdout.Read($header, $read, 4 - $read)
    if ($n -le 0) { break }
    $read += $n
}

if ($read -lt 4) {
    Write-Host "NO RESPONSE: read $read header bytes (worker may have crashed)"
    Start-Sleep -Milliseconds 200
    Write-Host "--- STDERR ---"
    foreach ($l in $errLines) { Write-Host "  $l" }
    if (-not $proc.HasExited) { $proc.Kill() }
    exit 1
}

if ([BitConverter]::IsLittleEndian) { [Array]::Reverse($header) }
$len = [BitConverter]::ToUInt32($header, 0)
Write-Host "RESPONSE LENGTH: $len bytes"

$body = New-Object byte[] $len
$read = 0
while ($read -lt $len -and [DateTime]::Now -lt $deadline) {
    $n = $stdout.Read($body, $read, $len - $read)
    if ($n -le 0) { break }
    $read += $n
}
$text = [System.Text.Encoding]::UTF8.GetString($body, 0, $read)
Write-Host "RESPONSE BODY: $text"

Start-Sleep -Milliseconds 300
Write-Host "--- STDERR (last 30 lines) ---"
$tail = $errLines | Select-Object -Last 30
foreach ($l in $tail) { Write-Host "  $l" }

# Check log file
$logFile = Join-Path $tmpHome "logs\worker.log"
if (Test-Path $logFile) {
    Write-Host "--- worker.log (last 15) ---"
    Get-Content $logFile -Tail 15
}

if (-not $proc.HasExited) { $proc.Kill() }
Write-Host "DONE"
