# Runbook (Operator)

Date: 2026-02-19

## Purpose

Operate `awe-agentcheck` end-to-end with either real CLI participants or safe dry-run mode.

## 1) Start service (safe smoke mode)

```powershell
cd C:/Users/hangw/awe-agentcheck
py -m pip install -e .[dev]
$env:AWE_DRY_RUN="true"
$env:AWE_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/awe_agentcheck?connect_timeout=2"
$env:AWE_ARTIFACT_ROOT="C:/Users/hangw/awe-agentcheck/.agents"
$env:PYTHONPATH="C:/Users/hangw/awe-agentcheck/src"
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_api.ps1" -ForceRestart
```

Linux/macOS equivalent:

```bash
cd /path/to/awe-agentforge
pip install -e .[dev]
export PYTHONPATH="src"
export AWE_DRY_RUN="true"
export AWE_DATABASE_URL="sqlite+pysqlite:///./.agents/runtime/awe-agentcheck.sqlite3"
export AWE_ARTIFACT_ROOT=".agents"
bash scripts/start_api.sh --force-restart
```

If PostgreSQL is unavailable, `start_api.ps1` defaults to local persistent SQLite (`.agents/runtime/awe-agentcheck.sqlite3`) so task history survives restarts.

Health check:

```powershell
(Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8000/healthz").Content
```

## 2) Start service (real participant mode)

```powershell
$env:AWE_DRY_RUN="false"
$env:AWE_CLAUDE_COMMAND="claude -p --dangerously-skip-permissions --effort low --model claude-opus-4-6"
$env:AWE_CODEX_COMMAND="codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=xhigh"
$env:AWE_GEMINI_COMMAND="gemini --yolo"
$env:AWE_PARTICIPANT_TIMEOUT_SECONDS="3600"
$env:AWE_COMMAND_TIMEOUT_SECONDS="300"
$env:AWE_PARTICIPANT_TIMEOUT_RETRIES="1"
$env:AWE_MAX_CONCURRENT_RUNNING_TASKS="1"
$env:AWE_WORKFLOW_BACKEND="langgraph"
# Optional: architecture audit enforcement (off|warn|hard). Default follows evolution level.
$env:AWE_ARCH_AUDIT_MODE=""
# Optional: architecture audit threshold overrides
$env:AWE_ARCH_PYTHON_FILE_LINES_MAX="1200"
$env:AWE_ARCH_FRONTEND_FILE_LINES_MAX="2500"
$env:AWE_ARCH_RESPONSIBILITY_KEYWORDS_MAX="10"
$env:AWE_ARCH_SERVICE_FILE_LINES_MAX="4500"
$env:AWE_ARCH_WORKFLOW_FILE_LINES_MAX="2600"
$env:AWE_ARCH_DASHBOARD_JS_LINES_MAX="3800"
$env:AWE_ARCH_PROMPT_BUILDER_COUNT_MAX="14"
$env:AWE_ARCH_ADAPTER_RUNTIME_RAISE_MAX="0"
$env:AWE_PROMOTION_GUARD_ENABLED="true"
$env:AWE_PROMOTION_ALLOWED_BRANCHES=""
$env:AWE_PROMOTION_REQUIRE_CLEAN="false"
# Optional: extra provider adapters (JSON map, provider -> command template)
$env:AWE_PROVIDER_ADAPTERS_JSON='{"qwen":"qwen-cli --yolo"}'
# Optional: set to 1/true only if you explicitly want shared/public sandbox base
$env:AWE_SANDBOX_USE_PUBLIC_BASE="false"
```

Then restart API with:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_api.ps1" -ForceRestart
```

```bash
bash scripts/start_api.sh --force-restart
```

Stop API with:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/stop_api.ps1"
```

```bash
bash scripts/stop_api.sh
```

If you see `Unable to connect` on `127.0.0.1:8000`, it means API is not listening yet (or startup failed). Use:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_api.ps1" -ForceRestart
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/stop_api.ps1"
```

Cross-platform helper scripts available:
1. `scripts/start_api.sh`
2. `scripts/stop_api.sh`
3. `scripts/start_overnight_until_7.sh`
4. `scripts/stop_overnight.sh`
5. `scripts/supervise_until.sh`

## 3) Create and run task by CLI

```powershell
$env:PYTHONPATH="C:/Users/hangw/awe-agentcheck/src"
py -m awe_agentcheck.cli run `
  --task "Implement parser" `
  --author "codex#author-A" `
  --reviewer "claude#review-B" `
  --reviewer "gemini#review-C" `
  --provider-model "claude=claude-opus-4-6" `
  --provider-model "codex=gpt-5.3-codex" `
  --provider-model "gemini=gemini-3-pro-preview" `
  --provider-model-param "codex=-c model_reasoning_effort=xhigh" `
  --conversation-language "en" `
  --claude-team-agents 0 `
  --evolution-level 0 `
  --repair-mode "balanced" `
  --memory-mode "strict" `
  --phase-timeout "proposal=1800" `
  --phase-timeout "review=1800" `
  --plain-mode `
  --stream-mode `
  --debate-mode `
  --sandbox-mode 1 `
  --self-loop-mode 0 `
  --evolve-until "2026-02-13 06:00" `
  --workspace-path "C:/Users/hangw/awe-agentcheck" `
  --max-rounds 1 `
  --test-command "py -m pytest -q" `
  --lint-command "py -m ruff check ." `
  --auto-start
```

Default policy:

1. `sandbox_mode=1` uses `<workspace>-lab` as execution workspace.
2. If `sandbox_workspace_path` is omitted, system creates unique per-task sandbox under `<workspace>-lab/<timestamp>-<id>`.
3. Generated sandbox is auto-cleaned after `passed + auto_merge_completed`.
4. User-specified sandbox path is preserved by default.
5. `self_loop_mode=0` runs proposal-consensus rounds first, then enters `waiting_manual`.
6. With `debate_mode=1`, proposal stage is reviewer-first (`proposal_precheck_review` -> author revision -> `proposal_review`).
7. One consensus round is counted only when required reviewers align; same-round retries now include a stall guard:
   - 10+ unresolved retries in one round -> `waiting_manual` (`proposal_consensus_stalled_in_round`)
   - repeated same-issue signature across 4+ consensus rounds -> `waiting_manual` (`proposal_consensus_stalled_across_rounds`)
   - reviewer outputs fully unavailable still fail fast (`proposal_precheck_unavailable` / `proposal_review_unavailable`).
8. Author must approve before full implementation loop starts.
9. In full loop, author is still the implementation actor and reviewers remain evaluators.
10. Proposal phase now enforces structured issue contract:
   - reviewer `BLOCKER/UNKNOWN` must provide explicit `issues[].issue_id` (`ISSUE-xxx`).
   - author must provide `issue_responses[]` for required issue ids.
   - author `reject` requires `reason + alternative_plan + validation_commands + evidence_paths`.
11. Contract-incomplete discussion does not advance to proposal-review execution; it retries in-round and can end in `proposal_consensus_stalled_in_round`.
12. Pending proposal artifact now stores `proposal_contract` and `author_issue_validation` for operator audit.
13. If proposal contract contains required issue ids, review stage must return `issue_checks[]` covering all required ids.
14. Missing/unresolved issue checks hard-fail gate with `review_issue_checks_missing` / `review_issue_unresolved`.
15. `repair_mode` defaults to `balanced`; choose `minimal` or `structural` per risk appetite.
16. `plain_mode=1`, `stream_mode=1`, `debate_mode=1` are default-friendly for readable and observable runs.
17. `auto_merge=1` is enabled by default; disable per task with CLI `--no-auto-merge`, API `auto_merge=false`, or Web `Auto Merge=0`.
18. Optional model pinning via `--provider-model provider=model` applies per provider for this task.
19. Optional per-provider args via `--provider-model-param provider=args` are forwarded as-is.
20. Optional language control via `--conversation-language en|zh` influences prompt output language.
21. Optional Claude `--agents` behavior via `--claude-team-agents 1` applies to Claude participants only.
22. Memory recall/persistence can be tuned with `memory_mode=off|basic|strict` (default `basic`).
23. Optional per-phase timeout overrides are accepted via `phase_timeout_seconds` (`proposal|discussion|implementation|review|command`).
24. `max_rounds` is used only when `evolve_until` is empty; if `evolve_until` is set, deadline takes priority.
25. If `max_rounds>1` and `auto_merge=0`, runtime forces fresh sandbox isolation and captures per-round artifacts (`round-N.patch`, `round-N.md`, round snapshots).
26. Promotion back to target path is then a separate explicit action via `promote-round` (guarded by promotion policy checks).
27. Before a task can become `passed`, `PreCompletionChecklist` must pass:
   - verification stage executed
   - evidence paths present in implementation/review/verification outputs.
28. Checklist failures emit explicit reasons (for example `precompletion_evidence_missing`) and block completion.
29. Each checklist result is persisted as `artifacts/evidence_bundle_round_<n>.json` for auditability.
30. Pass + auto-merge path performs an additional evidence-bundle validation (`No evidence, no merge`).
31. Task start/resume validates a stored workspace fingerprint; mismatches are blocked as `workspace_resume_guard_mismatch`.
32. Repeated no-progress rounds trigger `strategy_shifted` with remediation hints.
33. Multiple strategy shifts without progress end as `failed_gate` with `loop_no_progress`.
34. Task start now runs a preflight risk-policy gate before consensus/execution.
35. Preflight hard-fail reason: `preflight_risk_gate_failed` (prevents expensive empty runs).
36. Auto-merge path enforces merge-target head SHA stability; drift during run fails with `head_sha_mismatch`.

## 4) Inspect status and timeline

```powershell
py -m awe_agentcheck.cli tasks --limit 20
py -m awe_agentcheck.cli status <task_id>
py -m awe_agentcheck.cli events <task_id>
py -m awe_agentcheck.cli stats
py -m awe_agentcheck.cli analytics --limit 300
py -m awe_agentcheck.cli policy-templates --workspace-path "C:/Users/hangw/awe-agentcheck"
py -m awe_agentcheck.cli benchmark --workspace-path "C:/Users/hangw/awe-agentcheck" --variant-a-name "baseline" --variant-b-name "candidate" --reviewer "claude#review-B" --include-regression --regression-file ".agents/regressions/failure_tasks.json"
py -m awe_agentcheck.cli github-summary <task_id>
py -m awe_agentcheck.cli tree --workspace-path "C:/Users/hangw/awe-agentcheck" --max-depth 3
```

## 5) Manual controls

```powershell
py -m awe_agentcheck.cli start <task_id>
py -m awe_agentcheck.cli decide <task_id> --approve --auto-start
py -m awe_agentcheck.cli decide <task_id> --note "not now"
py -m awe_agentcheck.cli cancel <task_id>
py -m awe_agentcheck.cli force-fail <task_id> --reason "watchdog_timeout: operator forced fail"
py -m awe_agentcheck.cli promote-round <task_id> --round 2 --merge-target-path "C:/Users/hangw/awe-agentcheck"
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
7. `Custom Reply + Re-run` lets operator send a free-text manual note (`decision=revise`) and immediately rerun proposal consensus.
8. Manual reply text box is enabled only when task status is `waiting_manual`.
9. Create task includes `sandbox_mode`, `sandbox_workspace_path`, `self_loop_mode`, `evolution_level`, optional `evolve_until`, `conversation_language`, `memory_mode`, `phase_timeout_seconds`, provider-level settings (`provider_models`, `provider_model_params`), participant-level overrides (`participant_models`, `participant_model_params`), `claude_team_agents`, and `codex_multi_agents`.
10. Create task advanced controls include `repair_mode`, `plain_mode`, `stream_mode`, `debate_mode`.
11. Auto polling and extended stats with reason/provider breakdown.
12. Project history card shows cross-task records for selected project: core findings, revisions, disputes, next steps.
13. `Project History` card supports scoped `Clear` (can optionally clear matching live tasks in scope).
14. `GitHub / PR Summary` card provides PR-ready markdown and artifact links for selected task.
15. `Advanced Analytics` card visualizes failure taxonomy trends and reviewer drift signals.
16. `Promote Round` control is enabled only for terminal tasks with `max_rounds>1` and `auto_merge=0`.
17. When proposal consensus stalls, `Custom Reply + Re-run` is the intended recovery path; stall details are saved in `artifacts/consensus_stall.json`.

## 7) Artifacts

Task outputs are written to:

- `.agents/threads/<task_id>/discussion.md`
- `.agents/threads/<task_id>/summary.md`
- `.agents/threads/<task_id>/events.jsonl`
- `.agents/threads/<task_id>/final_report.md`
- `.agents/threads/<task_id>/state.json`
- `.agents/threads/<task_id>/artifacts/pending_proposal.json` (manual mode only)
- `.agents/threads/<task_id>/artifacts/auto_merge_summary.json` (auto-merge on passed)
- `.agents/threads/<task_id>/artifacts/evidence_bundle_round_<n>.json` (precompletion evidence bundle per round)
- `.agents/threads/<task_id>/artifacts/evidence_manifest.json` (structured evidence manifest for passed workflow runs)
- `.agents/threads/<task_id>/artifacts/workspace_resume_guard.json` (written when resume guard blocks start)
- `.agents/threads/<task_id>/artifacts/precompletion_guard_failed.json` (written when evidence guard blocks completion)
- `.agents/threads/<task_id>/artifacts/preflight_risk_gate.json` (preflight risk-policy gate result)
- `.agents/threads/<task_id>/artifacts/regression_case.json` (failure->regression mapping payload)
- `.agents/threads/<task_id>/artifacts/rounds/round-<n>.patch` (multi-round manual promote mode)
- `.agents/threads/<task_id>/artifacts/rounds/round-<n>.md` (multi-round manual promote mode)
- `.agents/threads/<task_id>/artifacts/rounds/round-<nnn>-snapshot/` (per-round workspace snapshot)
- `.agents/threads/<task_id>/artifacts/round-<n>-artifact.json` (round metadata)
- `.agents/threads/<task_id>/artifacts/round-<n>-promote-summary.json` (written after promote-round)
- `.agents/regressions/failure_tasks.json` (auto-built regression tasks fed into benchmark harness)

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
  --adaptive-policy 1 `
  --adaptive-interval 1 `
  --analytics-limit 300 `
  --policy-template "balanced-default" `
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
8. With `--adaptive-policy 1`, trace analytics automatically adjust next-task policy template and key knobs.

## 9) One-command background launch + stop

Start in background (explicit deadline required):

```powershell
$until = "2026-02-18 07:00"
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -Until "$until"
```

Force replace any existing overnight process:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -ForceRestart -Until "$until"
```

Force replace overnight process and restart API listener:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -ForceRestart -RestartApi -Until "$until"
```

Customize primary cooldown window after provider-limit:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -ForceRestart -PrimaryDisableSeconds 5400 -Until "$until"
```

Customize per-task watchdog timeout:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -ForceRestart -TaskTimeoutSeconds 2400 -Until "$until"
```

Set an explicit stop time (for example, until next day 06:00):

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -ForceRestart -Until "2026-02-13 06:00"
```

Set evolution intensity for auto-created overnight tasks:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -ForceRestart -EvolutionLevel 2 -Until "$until"
```

Run overnight in direct-main mode (disable sandbox):

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -ForceRestart -NoSandbox -Until "$until"
```

Start in safe dry-run mode:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -DryRun -Until "$until"
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

Project-level history endpoint:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/project-history?limit=50"
Invoke-RestMethod ("http://127.0.0.1:8000/api/project-history?project_path=" + [uri]::EscapeDataString("C:/Users/hangw/awe-agentcheck"))
```

Clear scoped history:

```powershell
Invoke-RestMethod -Method Post "http://127.0.0.1:8000/api/project-history/clear" `
  -ContentType "application/json" `
  -Body '{"project_path":"C:/Users/hangw/awe-agentcheck","include_live_tasks":true}'
```

Advanced analytics / policy / PR summary endpoints:

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/analytics?limit=300"
Invoke-RestMethod ("http://127.0.0.1:8000/api/policy-templates?workspace_path=" + [uri]::EscapeDataString("C:/Users/hangw/awe-agentcheck"))
Invoke-RestMethod "http://127.0.0.1:8000/api/tasks/<task_id>/github-summary"
```

## 11) Self-test (program tests itself)

This starts an isolated dry-run API, creates a real task against this repo, waits for terminal status, and asserts `passed`.

```powershell
cd C:/Users/hangw/awe-agentcheck
py scripts/selftest_local_smoke.py --port 8011
```

Outputs include:

1. `task_id`, `status`, `events`, `pass_rate_50`
2. log paths under `.agents/selftest/`

## 12) Benchmark A/B Harness

Run fixed benchmark tasks against two policy variants and generate comparable reports:

```powershell
cd C:/Users/hangw/awe-agentcheck
$env:PYTHONPATH="C:/Users/hangw/awe-agentcheck/src"
py scripts/benchmark_harness.py `
  --api-base "http://127.0.0.1:8000" `
  --workspace-path "C:/Users/hangw/awe-agentcheck" `
  --tasks-file "ops/benchmark_tasks.json" `
  --regression-file ".agents/regressions/failure_tasks.json" `
  --include-regression `
  --variant-a-name "baseline" `
  --variant-a-template "balanced-default" `
  --variant-b-name "candidate" `
  --variant-b-template "safe-review" `
  --author "codex#author-A" `
  --reviewer "claude#review-B"
```

Reports are written to `.agents/benchmarks/` as:

1. `benchmark-<timestamp>.json`
2. `benchmark-<timestamp>.md`

Shortcut via CLI wrapper:

```powershell
py -m awe_agentcheck.cli benchmark --workspace-path "C:/Users/hangw/awe-agentcheck" --variant-a-name "baseline" --variant-b-name "candidate" --reviewer "claude#review-B" --include-regression --regression-file ".agents/regressions/failure_tasks.json"
```
