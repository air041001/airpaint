[CmdletBinding()]
param(
    [switch]$NoTunnel
)

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$state = Join-Path $repo 'server\state'
$runDir = Join-Path $state 'run'
$logDir = Join-Path $state 'logs'
$stopMarker = Join-Path $runDir 'stop.request'
$config = Join-Path $repo 'server\config.yaml'
$cloudflared = 'E:\cloudflared\cloudflared.exe'

New-Item -ItemType Directory -Force -Path $runDir, $logDir | Out-Null

if (-not (Test-Path -LiteralPath $config -PathType Leaf)) {
    throw "Missing config: $config. Copy server/config.example.yaml first."
}
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw 'Python was not found in PATH.' }

try {
    $null = Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8188/system_stats' -TimeoutSec 3
} catch {
    throw 'ComfyUI is not reachable at http://127.0.0.1:8188. Start ComfyUI first.'
}
if (-not $NoTunnel -and -not (Test-Path -LiteralPath $cloudflared -PathType Leaf)) {
    throw "cloudflared was not found: $cloudflared. Use -NoTunnel for local-only startup."
}

$env:PYTHONUTF8 = '1'
$backendHealthy = $false
try {
    $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2
    $backendHealthy = [bool]$health.ok
} catch {}

if (-not $backendHealthy) {
    Remove-Item -LiteralPath $stopMarker -Force -ErrorAction SilentlyContinue
    $backend = Start-Process -FilePath $python.Source `
        -ArgumentList @('-m', 'server.main') `
        -WorkingDirectory $repo `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir 'backend.out.log') `
        -RedirectStandardError (Join-Path $logDir 'backend.err.log') `
        -PassThru
    Set-Content -LiteralPath (Join-Path $runDir 'backend.pid') -Value $backend.Id -Encoding ascii

    $ready = $false
    for ($attempt = 0; $attempt -lt 30; $attempt++) {
        Start-Sleep -Milliseconds 500
        if ($backend.HasExited) {
            $tail = Get-Content -LiteralPath (Join-Path $logDir 'backend.err.log') -Tail 12 -ErrorAction SilentlyContinue
            throw "AirPaint backend exited during startup.`n$($tail -join [Environment]::NewLine)"
        }
        try {
            $health = Invoke-RestMethod -Uri 'http://127.0.0.1:8000/api/health' -TimeoutSec 2
            if ($health.ok -and $health.database) { $ready = $true; break }
        } catch {}
    }
    if (-not $ready) {
        throw "AirPaint backend did not become ready. See $logDir\backend.err.log"
    }
    Write-Host "AirPaint backend started (PID $($backend.Id))."
} else {
    Write-Host 'AirPaint backend is already running.'
}

if (-not $NoTunnel) {
    $tunnelPidFile = Join-Path $runDir 'tunnel.pid'
    $tunnelRunning = $false
    if (Test-Path -LiteralPath $tunnelPidFile -PathType Leaf) {
        try {
            $savedTunnelPid = [int](Get-Content -LiteralPath $tunnelPidFile -Raw).Trim()
            $existingTunnel = Get-CimInstance Win32_Process -Filter "ProcessId=$savedTunnelPid" -ErrorAction SilentlyContinue
            $tunnelRunning = $existingTunnel -and ([string]$existingTunnel.CommandLine -match 'cloudflared(.exe)?"?\s+tunnel\s+run\s+airpaint')
        } catch {}
        if (-not $tunnelRunning) {
            Remove-Item -LiteralPath $tunnelPidFile -Force -ErrorAction SilentlyContinue
        }
    }
    if ($tunnelRunning) {
        Write-Host "Cloudflare tunnel is already running (PID $savedTunnelPid)."
    } else {
    $tunnel = Start-Process -FilePath $cloudflared `
        -ArgumentList @('tunnel', 'run', 'airpaint') `
        -WorkingDirectory $repo `
        -WindowStyle Hidden `
        -RedirectStandardOutput (Join-Path $logDir 'tunnel.out.log') `
        -RedirectStandardError (Join-Path $logDir 'tunnel.err.log') `
        -PassThru
    Set-Content -LiteralPath $tunnelPidFile -Value $tunnel.Id -Encoding ascii
    Write-Host "Cloudflare tunnel started (PID $($tunnel.Id))."
    }
}

Write-Host 'Local:  http://127.0.0.1:8000'
if (-not $NoTunnel) { Write-Host 'Public: https://airpaint.xyz' }
Write-Host "Logs:   $logDir"
Write-Host 'Use .tools\stop_airpaint.bat for a normal shutdown.'
