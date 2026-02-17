param(
  [string]$ApiHost = '127.0.0.1',
  [int]$Port = 8000,
  [int]$StartTimeoutSeconds = 20,
  [switch]$ForceRestart
)

$ErrorActionPreference = 'Stop'

$repo = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$runtimeDir = Join-Path $repo '.agents/runtime'
$stdoutLog = Join-Path $runtimeDir 'api-stdout.log'
$stderrLog = Join-Path $runtimeDir 'api-stderr.log'
$pidFile = Join-Path $runtimeDir 'api.pid'
$dbPath = Join-Path $runtimeDir 'awe-agentcheck.sqlite3'
New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null

function Resolve-PythonExe {
  $candidates = @('py', 'python')
  foreach ($name in $candidates) {
    $found = Get-Command $name -All -ErrorAction SilentlyContinue
    if (-not $found) {
      continue
    }
    foreach ($cmd in $found) {
      $src = $cmd.Source
      if (-not $src) {
        continue
      }
      $ext = [System.IO.Path]::GetExtension($src).ToLowerInvariant()
      if ($src -like '*\WindowsApps\*') {
        continue
      }
      if ($ext -in @('.exe', '.cmd', '.bat', '.com')) {
        return $src
      }
    }
    $first = $found | Select-Object -First 1
    if ($first -and $first.Source -and $first.Source -notlike '*\WindowsApps\*') {
      return $first.Source
    }
  }
  return $null
}

function Stop-ExistingApiIfNeeded {
  param([switch]$OnlyWhenForced)
  $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
  if ($listener -and (-not $ForceRestart) -and $OnlyWhenForced) {
    Write-Output "[api] already listening on $ApiHost`:$Port (pid=$($listener.OwningProcess))"
    exit 0
  }
  if ($listener -and $ForceRestart) {
    Stop-Process -Id $listener.OwningProcess -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
  }
}

Stop-ExistingApiIfNeeded -OnlyWhenForced

$pythonExe = Resolve-PythonExe
if (-not $pythonExe) {
  throw 'No python launcher found (python/py).'
}

$env:PYTHONPATH = (Join-Path $repo 'src')
$env:AWE_ARTIFACT_ROOT = (Join-Path $repo '.agents')
if ([string]::IsNullOrWhiteSpace($env:AWE_DATABASE_URL)) {
  # Persist history across restarts when PostgreSQL is absent.
  $sqlitePath = ($dbPath -replace '\\', '/')
  $env:AWE_DATABASE_URL = "sqlite+pysqlite:///$sqlitePath"
}

Set-Location $repo
$proc = Start-Process -FilePath $pythonExe `
  -ArgumentList '-m','uvicorn','awe_agentcheck.main:app','--host',$ApiHost,'--port',"$Port" `
  -PassThru -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog

Set-Content -Path $pidFile -Value "$($proc.Id)" -Encoding ascii

$deadline = (Get-Date).AddSeconds([Math]::Max(3, $StartTimeoutSeconds))
$healthy = $false
while ((Get-Date) -lt $deadline) {
  $alive = Get-Process -Id $proc.Id -ErrorAction SilentlyContinue
  if (-not $alive) {
    break
  }
  try {
    $resp = Invoke-WebRequest -Uri ("http://{0}:{1}/healthz" -f $ApiHost, $Port) -UseBasicParsing -TimeoutSec 2
    if ($resp.StatusCode -eq 200 -and $resp.Content -like '*"status":"ok"*') {
      $healthy = $true
      break
    }
  } catch {
  }
  Start-Sleep -Milliseconds 700
}

if ($healthy) {
  Write-Output "[api] started pid=$($proc.Id) url=http://$ApiHost`:$Port"
  exit 0
}

Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
Remove-Item -Path $pidFile -ErrorAction SilentlyContinue
Write-Output '[api] failed to start within timeout.'
if (Test-Path $stderrLog) {
  Write-Output '--- stderr (tail) ---'
  Get-Content -Path $stderrLog -Tail 80
}
if (Test-Path $stdoutLog) {
  Write-Output '--- stdout (tail) ---'
  Get-Content -Path $stdoutLog -Tail 80
}
exit 1
