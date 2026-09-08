[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'
$repo = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$runDir = Join-Path $repo 'server\state\run'

function Stop-AirPaintProcess {
    param(
        [Parameter(Mandatory=$true)][string]$Name,
        [Parameter(Mandatory=$true)][string]$PidFile,
        [Parameter(Mandatory=$true)][string]$ExpectedPattern,
        [string]$GracefulMarker = ''
    )
    if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
        Write-Host "$Name PID file is absent; nothing to stop."
        return
    }
    $savedPid = [int](Get-Content -LiteralPath $PidFile -Raw).Trim()
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$savedPid" -ErrorAction SilentlyContinue
    if (-not $process) {
        Remove-Item -LiteralPath $PidFile -Force
        Write-Host "$Name is already stopped."
        return
    }
    $command = [string]$process.CommandLine
    if ($command -notmatch $ExpectedPattern) {
        throw "$Name PID $savedPid belongs to another command; refusing to stop it."
    }
    if ($GracefulMarker) {
        Set-Content -LiteralPath $GracefulMarker -Value 'stop' -Encoding ascii
        for ($attempt = 0; $attempt -lt 100; $attempt++) {
            Start-Sleep -Milliseconds 200
            if (-not (Get-Process -Id $savedPid -ErrorAction SilentlyContinue)) {
                Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
                Remove-Item -LiteralPath $GracefulMarker -Force -ErrorAction SilentlyContinue
                Write-Host "$Name stopped cleanly (PID $savedPid)."
                return
            }
        }
        Write-Warning "$Name did not stop within 20 seconds; forcing termination."
    }
    Stop-Process -Id $savedPid
    try { Wait-Process -Id $savedPid -Timeout 15 -ErrorAction Stop } catch {}
    Remove-Item -LiteralPath $PidFile -Force
    Write-Host "$Name stopped (PID $savedPid)."
}

Stop-AirPaintProcess -Name 'Cloudflare tunnel' `
    -PidFile (Join-Path $runDir 'tunnel.pid') `
    -ExpectedPattern 'cloudflared(.exe)?"?\s+tunnel\s+run\s+airpaint'
Stop-AirPaintProcess -Name 'AirPaint backend' `
    -PidFile (Join-Path $runDir 'backend.pid') `
    -ExpectedPattern '(python(.exe)?).*?-m\s+server\.main' `
    -GracefulMarker (Join-Path $runDir 'stop.request')

Write-Host 'ComfyUI was not started by AirPaint and was left running.'
