# Session Handoff (2026-02-12)

## Update (2026-02-13)

1. Added task strategy controls:
   - `sandbox_mode` (default `1`)
   - `sandbox_workspace_path` (optional override, default `<project>-lab`)
   - `self_loop_mode` (`0` manual author decision default, `1` autonomous)
2. Added manual author decision flow:
   - start in manual mode -> `waiting_manual` + `author_confirmation_required` event
   - `POST /api/tasks/{task_id}/author-decision` with `approve=true|false`
   - CLI `decide` command added
   - Web monitor added `Approve + Queue`, `Approve + Start`, `Reject`
3. Added sandbox bootstrap behavior:
   - on first use, sandbox workspace auto-created and seeded from project (excluding runtime/cache/git dirs)
4. Auto-merge default now pairs naturally with sandbox mode:
   - in sandbox mode + auto-merge on, default merge target is the project root
5. Overnight scripts updated:
   - support `--sandbox-mode`, `--sandbox-workspace-path`, `--self-loop-mode`
   - launcher default uses `self_loop_mode=1` for unattended runs
6. Sandbox lifecycle hardened:
   - omitted sandbox path now allocates unique per-task sandbox (`<project>-lab/<timestamp>-<id>`)
   - generated sandbox auto-cleanup on `passed + auto_merge_completed`
7. Monitor UI improvements:
   - fixed tree `Expand` / `Collapse` controls
   - conversation rendered in chat-bubble style with avatars
   - upgraded role/message pixel avatars to 24x24 portraits with larger render size for readability
8. Documentation sync completed:
   - clarified current default `auto_merge=1` across README/RUNBOOK/ARCH docs
   - documented disable options: Web `Auto Merge=0`, CLI `--no-auto-merge`, API `auto_merge=false`
9. GitHub presentation refresh completed:
   - upgraded `docs/assets/dashboard-preview.svg` to terminal-pixel multi-role preview
   - redesigned `docs/assets/workflow-flow.svg` into clean-lane runtime flow (no arrow crossing through node bubbles)
   - added bilingual README updates for beginner-grade dashboard button guide
   - added star statistics section (badges + star history chart link) in both README files
10. About/narrative focus adjusted:
   - reduced sandbox-first emphasis in top README messaging
   - reframed product essence to multi-agent collaboration, bug-fix loops, and continuous self-evolution
   - synchronized hero tagline (`docs/assets/awe-agentcheck-hero.svg`) with new narrative
11. Low-risk brand rename + API exposure audit:
   - display-level brand updated to `AWE-AgentForge` in README/web/hero
   - internal package/runtime IDs intentionally unchanged (`awe-agentcheck` / `awe_agentcheck`) for compatibility
   - added `docs/API_EXPOSURE_AUDIT.md` with local listener/tunnel checks and exposure guardrails

## Pause Window Notice (Operator Directive)

1. Operator instruction: do not run overnight/auto-evolution before `2026-02-17 09:03` (local time).
2. Pause command executed:
   - `pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/stop_overnight.ps1" -All`
3. Verification after stop:
   - stopped one active process: `PID 28840`
   - no remaining overnight python process (`overnight_autoevolve.py`)
   - lock cleared: `C:/Users/hangw/awe-agentcheck/.agents/overnight/overnight.lock` is missing
   - API unreachable on `http://127.0.0.1:8000/api/health` (service not running)
   - no relevant scheduled task found for auto-restart
4. Resume command (after pause window only):
   - `pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1"`

## Goal

Keep `awe-agentcheck` running as a medium-engineering orchestrator where two CLIs can review each other continuously, with observability and resilient overnight operation.

## Current Runtime Status (Pre-Pause Snapshot)

1. API is running on `http://127.0.0.1:8000`.
2. Overnight loop is running until configured local deadline (current run: `2026-02-13 06:00`).
3. Single-instance lock is active at `.agents/overnight/overnight.lock`.
4. Latest session metadata is in `.agents/overnight/sessions/session-*.json`.
5. Current loop is producing terminal task states (not stuck in infinite `running` buildup).

## Architecture Flow

```text
start_overnight_until_7.ps1
  -> (optional) stop_overnight.ps1 -All
  -> start/reuse uvicorn API
  -> launch scripts/overnight_autoevolve.py
       -> create task (auto_start=true)
       -> API background worker calls OrchestratorService.start_task
            -> WorkflowEngine.run
                 discussion (author)
                 implementation (author)
                 review (reviewers)
                 verification (test/lint commands)
                 medium gate decision
            -> persist task/events/artifacts
       -> wait terminal status
       -> append overnight markdown log
       -> fallback switch if system failure indicates claude path
```

## Key Reliability Changes Applied

1. Added process lock (`acquire_single_instance`) for overnight loop.
2. Added startup duplicate guard in `start_overnight_until_7.ps1`.
3. Added full cleanup mode in `stop_overnight.ps1 -All`.
4. Added API restart mode in launcher: `-RestartApi`.
5. Added command path resolution for `claude`/`codex` to avoid PATH/profile issues.
6. Added unbuffered Python logs for background processes.
7. Added configurable workflow timeouts:
   - `AWE_PARTICIPANT_TIMEOUT_SECONDS` default `240`
   - `AWE_COMMAND_TIMEOUT_SECONDS` default `300`
8. Improved adapter failure diagnostics:
   - `command_not_found provider=... command=...`
   - `command_timeout provider=... command=... timeout_seconds=...`
9. Added bidirectional participant routing in overnight driver:
   - Claude-side system failures route to fallback (Codex).
   - Codex command timeouts/not-found route back to primary (Claude).
10. Added consecutive system-failure cooldown in overnight driver.
11. Tuned command profiles for unattended execution:
   - Claude: `--dangerously-skip-permissions --effort low`
   - Codex: `--dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=low`
12. Added workflow prompt clipping and anti-follow-up constraints to reduce long-turn stalls.
13. Added round-to-round convergence signal: previous gate failure reason is injected into next-round discussion prompt.
14. Extended observability stats:
   - `reason_bucket_counts`
   - `provider_error_counts`
15. Added recent-window quality metrics in `/api/stats`:
   - `recent_terminal_total`
   - `pass_rate_50`
   - `failed_gate_rate_50`
   - `failed_system_rate_50`
   - `mean_task_duration_seconds_50`
16. Added service-level running concurrency gate (`AWE_MAX_CONCURRENT_RUNNING_TASKS`, default `1`) with `start_deferred` events.
17. Added participant timeout retry policy (`AWE_PARTICIPANT_TIMEOUT_RETRIES`, default `1`) with retry-time prompt clipping.
18. Added provider-limit detection in participant adapter:
   - converts CLI quota messages into `provider_limit ...` system failures
19. Added overnight anti-thrashing cooldown:
   - when Claude hits provider limit, primary participants are disabled for a cooldown window before switching back.
20. Added queued-concurrency auto-retry in overnight waiter:
   - if task is `queued` with `concurrency_limit`, overnight driver retries `POST /start` automatically.
21. Hardened overnight HTTP polling against transient transport resets:
   - `wait_terminal` retries on `httpx.HTTPError`
   - driver logs now keep actual `task_id` on post-create failures (no more blind `n/a` when known).
22. Added watchdog timeout handling in overnight runner:
   - new `--task-timeout-seconds` guard enforces per-task terminal deadline
   - timed-out tasks are force-failed via API with `watchdog_timeout` reason.
23. Added operator fail-safe API/CLI path:
   - `POST /api/tasks/{task_id}/force-fail`
   - `py -m awe_agentcheck.cli force-fail <task_id> --reason "..."`
24. Fixed launcher API binding behavior:
   - `start_overnight_until_7.ps1` now binds Uvicorn host/port from `-ApiBase` (not hardcoded `8000`).
25. Rebuilt monitor UI to match operator mental model:
   - left top = projects
   - left bottom = roles/sessions
   - right = dialogue stream + task controls
26. Added self-test runner:
   - `scripts/selftest_local_smoke.py`
   - launches isolated dry-run API and validates end-to-end pass status automatically.
27. Added task strategy controls:
   - `evolution_level` (`0|1|2`) on task create
   - `evolve_until` datetime deadline for discussion/evolution phase.
28. Added workspace structure endpoint:
   - `GET /api/workspace-tree` for directory/file tree rendering.
29. Updated monitor UI:
   - left top now shows project structure tree (not just project list).
30. Added launcher deadline and intensity controls:
   - `start_overnight_until_7.ps1 -Until "..."`
   - `start_overnight_until_7.ps1 -EvolutionLevel 0|1|2`
31. Added monitor UI multi-theme support:
   - `Neon Grid` (existing hacker style)
   - `Terminal Pixel` (new pixel-terminal style via toolbar switch)
32. Added deterministic pixel avatars for each role/session card in the left role panel.

## Verification Evidence

1. `py -m pytest -q` passed.
2. `py -m ruff check .` passed.
3. Runtime smoke:
   - `/healthz` returns `{"status":"ok"}`
   - `/api/stats` shows tasks transitioning to terminal statuses
   - overnight logs continuously append in `.agents/overnight/night-stdout.log`

## Operator Commands

Start/restart loop with fresh API:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -ForceRestart -RestartApi
```

Stop only latest session:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/stop_overnight.ps1"
```

Hard cleanup (all overnight/session-managed processes):

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/stop_overnight.ps1" -All
```

Check live status:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/stats
py -m awe_agentcheck.cli --api-base http://127.0.0.1:8000 stats
Get-Content C:/Users/hangw/awe-agentcheck/.agents/overnight/night-stdout.log -Tail 80
```

## Remaining Caveats

1. Repository currently runs with in-memory store if DB is unavailable (`AWE_DATABASE_URL` invalid fallback mode).
2. Terminal results may often be `failed_gate` due reviewer verdict policy and CLI output format; this is expected behavior under strict medium gate.
3. If CLI command signatures/flags change, update launcher-resolved command templates accordingly.
4. With strict gate policy ("all reviewers no-blocker"), throughput is lower but quality bar is intentionally high.
5. If both providers degrade simultaneously, overnight loop may still progress slowly despite cooldown and retries; inspect `/api/stats` and `night-stdout.log` for rate/quality trends.
