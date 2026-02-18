param(
  [switch]$DryRun,
  [switch]$ForceRestart,
  [switch]$RestartApi,
  [bool]$ResetActiveAutoEvolve = $true,
  [switch]$NoAutoMerge,
  [switch]$NoSandbox,
  [string]$Until = '',
  [string]$MergeTargetPath = '',
  [string]$SandboxWorkspacePath = '',
  [string]$WorkspacePath = 'C:/Users/hangw/awe-agentcheck',
  [string]$Author = 'claude#author-A',
  [string[]]$Reviewers = @('codex#review-B','claude#review-C'),
  [string]$FallbackAuthor = 'codex#author-A',
  [string[]]$FallbackReviewers = @('codex#review-B'),
  [ValidateRange(0,2)][int]$EvolutionLevel = 0,
  [ValidateRange(0,1)][int]$SelfLoopMode = 1,
  [ValidateRange(0,1)][int]$PlainMode = 1,
  [ValidateRange(0,1)][int]$StreamMode = 0,
  [ValidateRange(0,1)][int]$DebateMode = 0,
  [ValidateSet('minimal','balanced','structural')][string]$RepairMode = 'balanced',
  [int]$MaxRounds = 3,
  [int]$ParticipantTimeoutSeconds = 240,
  [int]$CommandTimeoutSeconds = 300,
  [int]$TaskTimeoutSeconds = 1800,
  [int]$PrimaryDisableSeconds = 3600,
  [string]$GeminiCommand = '',
  [string]$TestCommand = 'py -m pytest -q',
  [string]$LintCommand = 'py -m ruff check .',
  [string]$ApiBase = 'http://127.0.0.1:8000'
)

$repo = 'C:/Users/hangw/awe-agentcheck'
$src = "$repo/src"
$overnightDir = "$repo/.agents/overnight"
$runtimeDir = "$repo/.agents/runtime"
$sessionsDir = "$overnightDir/sessions"
$lockFile = "$overnightDir/overnight.lock"
New-Item -ItemType Directory -Path $sessionsDir -Force | Out-Null
New-Item -ItemType Directory -Path $runtimeDir -Force | Out-Null

$now = Get-Date
if ([string]::IsNullOrWhiteSpace($Until)) {
  throw "Missing required -Until value. Example: -Until '2026-02-18 07:00'."
}
try {
  $target = Get-Date $Until
} catch {
  throw "Invalid -Until value: $Until. Expected parseable datetime like '2026-02-13 06:00'."
}
if ($target -le $now) {
  throw "Until must be in the future. Received: $Until"
}
$until = $target.ToString('yyyy-MM-dd HH:mm')

$dryValue = if ($DryRun) { 'true' } else { 'false' }
$apiUri = [Uri]$ApiBase
$apiHost = $apiUri.Host
$apiPort = $apiUri.Port
$defaultDbPath = "$runtimeDir/awe-agentcheck.sqlite3"
$defaultDbUrl = "sqlite+pysqlite:///$($defaultDbPath -replace '\\','/')"
$effectiveDbUrl = if ([string]::IsNullOrWhiteSpace($env:AWE_DATABASE_URL)) { $defaultDbUrl } else { $env:AWE_DATABASE_URL }

$apiStdout = "$overnightDir/api-stdout.log"
$apiStderr = "$overnightDir/api-stderr.log"
$nightStdout = "$overnightDir/night-stdout.log"
$nightStderr = "$overnightDir/night-stderr.log"

function Test-PidAlive([int]$ProcId) {
  if ($ProcId -le 0) {
    return $false
  }
  $proc = Get-Process -Id $ProcId -ErrorAction SilentlyContinue
  return $null -ne $proc
}

function Get-LockPid([string]$Path) {
  if (-not (Test-Path $Path)) {
    return $null
  }
  try {
    $raw = Get-Content -Path $Path -TotalCount 1 -ErrorAction Stop
    $value = 0
    if ([int]::TryParse(($raw | Out-String).Trim(), [ref]$value)) {
      return $value
    }
  } catch {
  }
  return $null
}

function Resolve-CommandPath([string]$Name) {
  $found = Get-Command $Name -All -ErrorAction SilentlyContinue
  if (-not $found) {
    return $null
  }
  $preferredExt = @('.exe', '.cmd', '.bat', '.com')
  foreach ($ext in $preferredExt) {
    $candidate = $found | Where-Object {
      $_.Source -and ([System.IO.Path]::GetExtension($_.Source).ToLowerInvariant() -eq $ext)
    } | Select-Object -First 1
    if ($candidate) {
      return $candidate.Source
    }
  }
  return ($found | Select-Object -First 1).Source
}

function Wait-ApiReady([string]$BaseUrl, [int]$MaxAttempts = 25, [int]$DelaySeconds = 1) {
  $healthUrl = "$($BaseUrl.TrimEnd('/'))/healthz"
  for ($i = 1; $i -le [Math]::Max(1, $MaxAttempts); $i++) {
    try {
      $resp = Invoke-RestMethod -Uri $healthUrl -Method Get -TimeoutSec 5
      if ($resp -and $resp.status -eq 'ok') {
        return $true
      }
    } catch {
    }
    Start-Sleep -Seconds ([Math]::Max(1, $DelaySeconds))
  }
  return $false
}

function Reset-ActiveAutoEvolveTasks([string]$BaseUrl) {
  $apiRoot = $BaseUrl.TrimEnd('/')
  try {
    $tasks = Invoke-RestMethod -Uri "$apiRoot/api/tasks?limit=400" -Method Get -TimeoutSec 20
  } catch {
    Write-Output '[launch] skip reset_active_autoevolve: cannot list tasks'
    return
  }

  $active = @()
  foreach ($t in @($tasks)) {
    $status = [string]($t.status)
    $title = [string]($t.title)
    $isAuto = $title -like 'AutoEvolve:*'
    $isActive = $status -in @('running', 'queued', 'waiting_manual')
    if ($isAuto -and $isActive) {
      $active += $t
    }
  }

  if ($active.Count -eq 0) {
    Write-Output '[launch] no active AutoEvolve tasks to reset'
    return
  }

  $reason = 'operator_restart_cleanup'
  foreach ($t in $active) {
    $taskId = [string]($t.task_id)
    if ([string]::IsNullOrWhiteSpace($taskId)) { continue }
    try {
      Invoke-RestMethod -Uri "$apiRoot/api/tasks/$taskId/cancel" -Method Post -TimeoutSec 20 | Out-Null
    } catch {
    }
    try {
      Invoke-RestMethod -Uri "$apiRoot/api/tasks/$taskId/force-fail" -Method Post -ContentType 'application/json' -Body (@{reason=$reason} | ConvertTo-Json -Compress) -TimeoutSec 20 | Out-Null
      Write-Output "[launch] reset active task: $taskId"
    } catch {
      Write-Output "[launch] reset task failed (best-effort): $taskId"
    }
  }
}

if ($ForceRestart) {
  Write-Output '[launch] ForceRestart enabled, cleaning previous overnight processes'
  & pwsh -NoProfile -ExecutionPolicy Bypass -File "$repo/scripts/stop_overnight.ps1" -All | Out-Null
  Start-Sleep -Seconds 1
} else {
  $lockPid = Get-LockPid $lockFile
  if ($lockPid -and (Test-PidAlive $lockPid)) {
    Write-Output "[launch] overnight already running with lock pid=$lockPid. Use -ForceRestart to replace."
    exit 0
  }

  $runningNight = Get-CimInstance Win32_Process |
    Where-Object { $_.CommandLine -like '*overnight_autoevolve.py*' -and $_.CommandLine -like '*awe-agentcheck*' } |
    Select-Object -First 1
  if ($runningNight) {
    Write-Output "[launch] overnight process already detected (pid=$($runningNight.ProcessId)). Use -ForceRestart to replace."
    exit 0
  }
}

$claudePath = Resolve-CommandPath 'claude'
if ($claudePath) {
  $resolvedClaudeCommand = "`"$claudePath`" -p --dangerously-skip-permissions --strict-mcp-config --effort low --model claude-opus-4-6"
} else {
  $resolvedClaudeCommand = 'claude -p --dangerously-skip-permissions --strict-mcp-config --effort low --model claude-opus-4-6'
}

$codexPath = Resolve-CommandPath 'codex'
if ($codexPath) {
  if ([System.IO.Path]::GetExtension($codexPath).ToLowerInvariant() -eq '.ps1') {
    $resolvedCodexCommand = "pwsh -NoProfile -File `"$codexPath`" exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=xhigh"
  } else {
    $resolvedCodexCommand = "`"$codexPath`" exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=xhigh"
  }
} else {
  $resolvedCodexCommand = 'codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=xhigh'
}

if (-not [string]::IsNullOrWhiteSpace($GeminiCommand)) {
  $resolvedGeminiCommand = $GeminiCommand
} else {
  $geminiPath = Resolve-CommandPath 'gemini'
  if ($geminiPath) {
    if ([System.IO.Path]::GetExtension($geminiPath).ToLowerInvariant() -eq '.ps1') {
      $resolvedGeminiCommand = "pwsh -NoProfile -File `"$geminiPath`" --yolo"
    } else {
      $resolvedGeminiCommand = "`"$geminiPath`" --yolo"
    }
  } else {
    $resolvedGeminiCommand = 'gemini --yolo'
  }
}

$apiCmd = @"
`$env:PYTHONPATH='$src';
`$env:PYTHONUNBUFFERED='1';
`$env:AWE_DRY_RUN='$dryValue';
`$env:AWE_DATABASE_URL='$effectiveDbUrl';
`$env:AWE_ARTIFACT_ROOT='$repo/.agents';
`$env:AWE_CLAUDE_COMMAND='$resolvedClaudeCommand';
`$env:AWE_CODEX_COMMAND='$resolvedCodexCommand';
`$env:AWE_GEMINI_COMMAND='$resolvedGeminiCommand';
`$env:AWE_PARTICIPANT_TIMEOUT_SECONDS='$ParticipantTimeoutSeconds';
`$env:AWE_COMMAND_TIMEOUT_SECONDS='$CommandTimeoutSeconds';
`$env:AWE_PARTICIPANT_TIMEOUT_RETRIES='1';
`$env:AWE_MAX_CONCURRENT_RUNNING_TASKS='1';
Set-Location '$repo';
py -m uvicorn awe_agentcheck.main:app --host '$apiHost' --port $apiPort
"@

$reviewerArgs = $Reviewers | ForEach-Object { "--reviewer '$($_)'" }
$fallbackReviewerArgs = $FallbackReviewers | ForEach-Object { "--fallback-reviewer '$($_)'" }

$nightCmd = @"
`$env:PYTHONPATH='$src';
`$env:PYTHONUNBUFFERED='1';
Set-Location '$repo';
py scripts/overnight_autoevolve.py --api-base '$ApiBase' --until '$until' --workspace-path '$WorkspacePath' --author '$Author' $($reviewerArgs -join ' ') --fallback-author '$FallbackAuthor' $($fallbackReviewerArgs -join ' ') --evolution-level $EvolutionLevel --self-loop-mode $SelfLoopMode --plain-mode $PlainMode --stream-mode $StreamMode --debate-mode $DebateMode --repair-mode '$RepairMode' --evolve-until '$until' --max-rounds $MaxRounds --test-command '$TestCommand' --lint-command '$LintCommand' --task-timeout-seconds $TaskTimeoutSeconds --lock-file '$lockFile' --primary-disable-seconds $PrimaryDisableSeconds
"@

if ($NoAutoMerge) {
  $nightCmd += " --no-auto-merge"
} else {
  $nightCmd += " --auto-merge"
}
if ($NoSandbox) {
  $nightCmd += " --sandbox-mode 0"
} else {
  $nightCmd += " --sandbox-mode 1"
}
if (-not [string]::IsNullOrWhiteSpace($SandboxWorkspacePath)) {
  $nightCmd += " --sandbox-workspace-path '$SandboxWorkspacePath'"
}
if (-not [string]::IsNullOrWhiteSpace($MergeTargetPath)) {
  $nightCmd += " --merge-target-path '$MergeTargetPath'"
}
$existingListener = Get-NetTCPConnection -LocalPort $apiPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
$apiProc = $null
$apiStartedByScript = $false

if ($RestartApi -and $existingListener) {
  Write-Output "[launch] RestartApi enabled, stopping existing API pid=$($existingListener.OwningProcess)"
  try {
    Stop-Process -Id $existingListener.OwningProcess -Force -ErrorAction Stop
    Start-Sleep -Seconds 1
  } catch {
    Write-Output "[launch] could not stop existing API pid=$($existingListener.OwningProcess)"
  }
  $existingListener = Get-NetTCPConnection -LocalPort $apiPort -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
}

if ($existingListener) {
  Write-Output "[launch] Reusing existing API on port $apiPort (pid=$($existingListener.OwningProcess))"
} else {
  $apiProc = Start-Process -FilePath 'pwsh' -ArgumentList '-NoProfile','-Command',$apiCmd -PassThru -WindowStyle Hidden -RedirectStandardOutput $apiStdout -RedirectStandardError $apiStderr
  $apiStartedByScript = $true
}

if (-not (Wait-ApiReady -BaseUrl $ApiBase -MaxAttempts 25 -DelaySeconds 1)) {
  throw "[launch] API did not become healthy at $ApiBase/healthz"
}

if ($ResetActiveAutoEvolve) {
  Reset-ActiveAutoEvolveTasks -BaseUrl $ApiBase
}

$nightProc = Start-Process -FilePath 'pwsh' -ArgumentList '-NoProfile','-Command',$nightCmd -PassThru -WindowStyle Hidden -RedirectStandardOutput $nightStdout -RedirectStandardError $nightStderr

$apiPid = if ($apiProc) { $apiProc.Id } else { $existingListener.OwningProcess }

$session = [ordered]@{
  started_at = (Get-Date).ToString('s')
  until_input = $Until
  until = $until
  api_base = $ApiBase
  api_host = $apiHost
  api_port = $apiPort
  database_url = $effectiveDbUrl
  dry_run = $dryValue
  lock_file = $lockFile
  api_pid = $apiPid
  api_started_by_script = $apiStartedByScript
  overnight_pid = $nightProc.Id
  evolution_level = $EvolutionLevel
  participant_timeout_seconds = $ParticipantTimeoutSeconds
  command_timeout_seconds = $CommandTimeoutSeconds
  task_timeout_seconds = $TaskTimeoutSeconds
  primary_disable_seconds = $PrimaryDisableSeconds
  sandbox_mode = (-not $NoSandbox)
  sandbox_workspace_path = if ([string]::IsNullOrWhiteSpace($SandboxWorkspacePath)) { $null } else { $SandboxWorkspacePath }
  self_loop_mode = $SelfLoopMode
  plain_mode = $PlainMode
  stream_mode = $StreamMode
  debate_mode = $DebateMode
  repair_mode = $RepairMode
  auto_merge = (-not $NoAutoMerge)
  merge_target_path = if ([string]::IsNullOrWhiteSpace($MergeTargetPath)) { $null } else { $MergeTargetPath }
  claude_command = $resolvedClaudeCommand
  codex_command = $resolvedCodexCommand
  gemini_command = $resolvedGeminiCommand
  api_stdout = $apiStdout
  api_stderr = $apiStderr
  overnight_stdout = $nightStdout
  overnight_stderr = $nightStderr
}
$sessionPath = "$sessionsDir/session-$(Get-Date -Format 'yyyyMMdd-HHmmss').json"
$session | ConvertTo-Json -Depth 4 | Set-Content -Path $sessionPath -Encoding utf8

Write-Output "[launch] API PID: $apiPid"
Write-Output "[launch] Overnight PID: $($nightProc.Id)"
Write-Output "[launch] Until: $until"
Write-Output "[launch] Session file: $sessionPath"
