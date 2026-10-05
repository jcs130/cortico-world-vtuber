param(
    [Parameter(Mandatory = $true)][string]$ConfigPath,
    [Parameter(Mandatory = $true)][string]$PythonExe,
    [string]$GatewayScript = '',
    [switch]$Once
)

$ErrorActionPreference = 'Stop'
if (-not $GatewayScript) { $GatewayScript = Join-Path (Split-Path $MyInvocation.MyCommand.Path) '..\src\music-generation-gateway.py' }
$ConfigPath = (Resolve-Path -LiteralPath $ConfigPath).Path
$PythonExe = (Resolve-Path -LiteralPath $PythonExe).Path
$GatewayScript = (Resolve-Path -LiteralPath $GatewayScript).Path
foreach ($path in @($ConfigPath, $PythonExe, $GatewayScript)) {
    if ($path.Contains('"') -or $path.Contains("`r") -or $path.Contains("`n")) { throw 'Invalid process argument path.' }
}
$config = Get-Content -LiteralPath $ConfigPath -Raw | ConvertFrom-Json
$port = if ($null -ne $config.port) { [int]$config.port } else { 8195 }
$hostName = if ($config.host) { [string]$config.host } else { '127.0.0.1' }
if ($hostName -notin @('127.0.0.1', 'localhost', '::1') -or $port -lt 1 -or $port -gt 65535) { throw 'Expected a local gateway address.' }
$address = if ($hostName -eq '::1') { '[::1]' } else { $hostName }
$stateDir = if ([IO.Path]::IsPathRooted([string]$config.stateDir)) { [string]$config.stateDir } else { Join-Path (Split-Path $ConfigPath) ([string]$config.stateDir) }
New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
$logPath = Join-Path $stateDir 'watchdog.jsonl'
$mutex = [Threading.Mutex]::new($false, "Local\CorticoOriginalSongGateway-$port")
$locked = $false
function Write-WatchLog([string]$event, $details) {
    @{ at = [DateTimeOffset]::Now.ToString('o'); event = $event; details = $details } |
        ConvertTo-Json -Compress -Depth 4 | Add-Content -LiteralPath $logPath -Encoding UTF8
}
function Read-GatewayHealth {
    try {
        $health = Invoke-RestMethod -Uri "http://${address}:$port/health" -TimeoutSec 3
        if ($health.ok -eq $true -and $health.service -eq 'original-song-gateway') { return $true }
    } catch { }
    return $false
}
function Test-GatewayPort {
    $client = [Net.Sockets.TcpClient]::new()
    try {
        $pending = $client.BeginConnect($hostName, $port, $null, $null)
        if (-not $pending.AsyncWaitHandle.WaitOne(500)) { return $false }
        $client.EndConnect($pending)
        return $client.Connected
    } catch { return $false } finally { $client.Dispose() }
}
try {
    try { $locked = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $locked = $true }
    if (-not $locked) { return }
    Write-WatchLog 'watch_started' @{ port = $port }
    $lastHealthy = $null
    $nextLaunch = [DateTimeOffset]::MinValue
    $backoff = 10
    do {
        $healthy = Read-GatewayHealth
        if ($healthy) {
            if ($lastHealthy -ne $true) { Write-WatchLog 'healthy' @{ port = $port } }
            $backoff = 10
        } elseif ([DateTimeOffset]::Now -ge $nextLaunch) {
            if (Test-GatewayPort) {
                if ($lastHealthy -ne $false) { Write-WatchLog 'port_busy_unhealthy' @{ port = $port; action = 'wait_without_interrupting_existing_process' } }
                $nextLaunch = [DateTimeOffset]::Now.AddSeconds(30)
            } else {
                try {
                    $argsList = @('-u', ('"' + $GatewayScript + '"'), '--config', ('"' + $ConfigPath + '"'))
                    $child = Start-Process -FilePath $PythonExe -ArgumentList $argsList -WindowStyle Hidden -PassThru -WorkingDirectory (Split-Path $GatewayScript) -RedirectStandardOutput (Join-Path $stateDir 'gateway.stdout.log') -RedirectStandardError (Join-Path $stateDir 'gateway.stderr.log')
                    Write-WatchLog 'gateway_started' @{ pid = $child.Id; retryAfterSec = $backoff }
                } catch { Write-WatchLog 'launch_failed' @{ reason = $_.Exception.GetType().Name } }
                $nextLaunch = [DateTimeOffset]::Now.AddSeconds($backoff)
                $backoff = [Math]::Min(300, $backoff * 2)
            }
        }
        $lastHealthy = $healthy
        if (-not $Once) { Start-Sleep -Seconds 5 }
    } while (-not $Once)
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    $mutex.Dispose()
}
