# Runbook (Operator)

Date: 2026-02-11

## Purpose

Operate `awe-agentcheck` end-to-end with either real CLI participants or safe dry-run mode.

## 1) Start service (safe smoke mode)

```powershell
cd C:/Users/hangw/awe-agentcheck
py -m pip install -e .[dev]
$env:AWE_DRY_RUN="true"
$env:AWE_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/awe_agentcheck"
$env:AWE_ARTIFACT_ROOT="C:/Users/hangw/awe-agentcheck/.agents"
$env:PYTHONPATH="C:/Users/hangw/awe-agentcheck/src"
py -m uvicorn awe_agentcheck.main:app --reload --port 8000
```

If PostgreSQL is unavailable, service falls back to in-memory repo automatically.

## 2) Start service (real participant mode)

```powershell
$env:AWE_DRY_RUN="false"
$env:AWE_CLAUDE_COMMAND="claude -p --dangerously-skip-permissions --effort low"
$env:AWE_CODEX_COMMAND="codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=low"
$env:AWE_GEMINI_COMMAND="gemini -p --yolo"
$env:AWE_PARTICIPANT_TIMEOUT_SECONDS="240"
$env:AWE_COMMAND_TIMEOUT_SECONDS="300"
$env:AWE_PARTICIPANT_TIMEOUT_RETRIES="1"
$env:AWE_MAX_CONCURRENT_RUNNING_TASKS="1"
```

Then restart the same `uvicorn` command.

## 3) Create and run task by CLI

```powershell
$env:PYTHONPATH="C:/Users/hangw/awe-agentcheck/src"
py -m awe_agentcheck.cli run `
  --task "Implement parser" `
  --author "claude#author-A" `
  --reviewer "codex#review-B" `
  --reviewer "gemini#review-C" `
  --evolution-level 1 `
  --sandbox-mode 1 `
  --self-loop-mode 0 `
  --evolve-until "2026-02-13 06:00" `
  --workspace-path "C:/Users/hangw/awe-agentcheck" `
  --max-rounds 3 `
  --test-command "py -m pytest -q" `
  --lint-command "py -m ruff check ." `
  --auto-start
```

Default policy:

1. `sandbox_mode=1` uses `<workspace>-lab` as execution workspace.
2. If `sandbox_workspace_path` is omitted, system creates unique per-task sandbox under `<workspace>-lab/<timestamp>-<id>`.
3. Generated sandbox is auto-cleaned after `passed + auto_merge_completed`.
4. User-specified sandbox path is preserved by default.
5. `self_loop_mode=0` enters `waiting_manual` after discussion/proposal review.
6. Author must approve before implementation starts.
7. `auto_merge=1` is enabled by default; disable per task with CLI `--no-auto-merge`, API `auto_merge=false`, or Web `Auto Merge=0`.

## 4) Inspect status and timeline

```powershell
py -m awe_agentcheck.cli tasks --limit 20
py -m awe_agentcheck.cli status <task_id>
py -m awe_agentcheck.cli events <task_id>
py -m awe_agentcheck.cli stats
py -m awe_agentcheck.cli tree --workspace-path "C:/Users/hangw/awe-agentcheck" --max-depth 3
```

## 5) Manual controls

```powershell
py -m awe_agentcheck.cli start <task_id>
py -m awe_agentcheck.cli decide <task_id> --approve --auto-start
py -m awe_agentcheck.cli decide <task_id> --note "not now"
py -m awe_agentcheck.cli cancel <task_id>
py -m awe_agentcheck.cli force-fail <task_id> --reason "watchdog_timeout: operator forced fail"
```

## 6) Web operations

Open: `http://localhost:8000/`

Capabilities:

1. Left top: project structure tree (directory + file nodes) for selected project.
2. Tree controls: `Expand` and `Collapse` for all folder nodes in current view.
3. Left bottom: role/session monitor grouped by participant id (`provider#alias`).
4. Right: conversation stream in chat-bubble style with role avatars and role filtering.
5. Start/cancel/force-fail actions for selected task.
6. Author controls for `waiting_manual`: `Approve + Queue`, `Approve + Start`, `Reject`.
7. Create task includes `sandbox_mode`, `sandbox_workspace_path`, `self_loop_mode`, `evolution_level`, optional `evolve_until`.
8. Auto polling and extended stats with reason/provider breakdown.

## 7) Artifacts

Task outputs are written to:

- `.agents/threads/<task_id>/discussion.md`
- `.agents/threads/<task_id>/summary.md`
- `.agents/threads/<task_id>/events.jsonl`
- `.agents/threads/<task_id>/final_report.md`
- `.agents/threads/<task_id>/state.json`
- `.agents/threads/<task_id>/artifacts/pending_proposal.json` (manual mode only)
- `.agents/threads/<task_id>/artifacts/auto_merge_summary.json` (auto-merge on passed)

Import lab self-evolution markdown plans into main docs:

```powershell
py scripts/import_lab_evolution_docs.py
```

## 8) Overnight continuous improvement loop (until 2026-02-12 07:00 local)

With API already running:

```powershell
cd C:/Users/hangw/awe-agentcheck
$env:PYTHONPATH="C:/Users/hangw/awe-agentcheck/src"
py scripts/overnight_autoevolve.py `
  --until "2026-02-12 07:00" `
  --workspace-path "C:/Users/hangw/awe-agentcheck" `
  --sandbox-mode 1 `
  --self-loop-mode 1 `
  --author "claude#author-A" `
  --reviewer "codex#review-B" `
  --reviewer "gemini#review-C" `
  --fallback-author "codex#author-A" `
  --fallback-reviewer "codex#review-B" `
  --evolution-level 0 `
  --evolve-until "2026-02-12 07:00" `
  --max-rounds 3 `
  --task-timeout-seconds 1800 `
  --test-command "py -m pytest -q" `
  --lint-command "py -m ruff check ."
```

Notes:

1. The script creates tasks continuously until the specified local deadline.
2. If system failures indicate Claude command issues, it switches to fallback Codex participants automatically.
3. Results are logged to `.agents/overnight/overnight-*.md`.
4. A single-instance lock file is used: `.agents/overnight/overnight.lock`.
5. If Claude returns `provider_limit`, primary participants are temporarily disabled (cooldown window) to avoid provider thrashing.
6. If a task exceeds `--task-timeout-seconds`, overnight watcher force-fails the task (`watchdog_timeout`) so the loop does not stall.
7. Overnight default sets `--self-loop-mode 1` for unattended autonomous execution.

## 9) One-command background launch + stop

Start in background (next local 07:00 deadline):

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1"
```

Force replace any existing overnight process:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -ForceRestart
```

Force replace overnight process and restart API listener:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -ForceRestart -RestartApi
```

Customize primary cooldown window after provider-limit:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -ForceRestart -PrimaryDisableSeconds 5400
```

Customize per-task watchdog timeout:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -ForceRestart -TaskTimeoutSeconds 2400
```

Set an explicit stop time (for example, until next day 06:00):

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -ForceRestart -Until "2026-02-13 06:00"
```

Set evolution intensity for auto-created overnight tasks:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -ForceRestart -EvolutionLevel 2
```

Run overnight in direct-main mode (disable sandbox):

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -ForceRestart -NoSandbox
```

Start in safe dry-run mode:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -DryRun
```

Stop latest background session:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/stop_overnight.ps1"
```

Stop all overnight/session-managed processes (cleanup mode):

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/stop_overnight.ps1" -All
```

## 10) Stats interpretation

`GET /api/stats` now includes:

1. `status_counts`: lifecycle state totals
2. `reason_bucket_counts`: grouped failure reasons (`command_timeout`, `provider_limit`, `watchdog_timeout`, `review_blocker`, etc.)
3. `provider_error_counts`: provider-attributed failures extracted from reason strings (`claude`, `codex`, `gemini`)
4. `pass_rate_50` / `failed_gate_rate_50` / `failed_system_rate_50`: terminal outcome ratios over recent 50 tasks
5. `mean_task_duration_seconds_50`: average terminal duration over recent 50 tasks

## 11) Self-test (program tests itself)

This starts an isolated dry-run API, creates a real task against this repo, waits for terminal status, and asserts `passed`.

```powershell
cd C:/Users/hangw/awe-agentcheck
py scripts/selftest_local_smoke.py --port 8011
```

Outputs include:

1. `task_id`, `status`, `events`, `pass_rate_50`
2. log paths under `.agents/selftest/`
