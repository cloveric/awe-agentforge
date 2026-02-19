<p align="center">
  <img src="docs/assets/awe-agentcheck-hero.svg" alt="AWE-AgentForge" width="100%" />
</p>

<p align="center">
  <a href="https://github.com/cloveric/awe-agentforge"><img alt="GitHub" src="https://img.shields.io/badge/GitHub-awe--agentforge-0f172a?style=for-the-badge&logo=github"></a>&nbsp;
  <a href="https://github.com/cloveric/awe-agentforge/stargazers"><img alt="GitHub Repo stars" src="https://img.shields.io/github/stars/cloveric/awe-agentforge?style=for-the-badge&logo=github&label=Stars&color=fbbf24"></a>&nbsp;
  <a href="#"><img alt="Version" src="https://img.shields.io/badge/version-0.1.0-f59e0b?style=for-the-badge"></a>&nbsp;
  <a href="#"><img alt="Python" src="https://img.shields.io/badge/Python-3.10+-3b82f6?style=for-the-badge&logo=python&logoColor=white"></a>&nbsp;
  <a href="#"><img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white"></a>&nbsp;
  <a href="https://github.com/cloveric/awe-agentforge/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/cloveric/awe-agentforge/ci.yml?style=for-the-badge&label=CI"></a>&nbsp;
  <a href="#"><img alt="License" src="https://img.shields.io/badge/License-MIT-22c55e?style=for-the-badge"></a>
</p>

<p align="center">
  <a href="#"><img alt="Multi-Agent Collaboration" src="https://img.shields.io/badge/core-multi_agent_collaboration-06b6d4?style=flat-square"></a>&nbsp;
  <a href="#"><img alt="Bugfix and Review Loops" src="https://img.shields.io/badge/core-bugfix_%2B_review_loops-22c55e?style=flat-square"></a>&nbsp;
  <a href="#"><img alt="Self Evolution" src="https://img.shields.io/badge/core-self_evolution-8b5cf6?style=flat-square"></a>&nbsp;
  <a href="#"><img alt="Policy Guardrails" src="https://img.shields.io/badge/safety-policy_guardrails-f97316?style=flat-square"></a>&nbsp;
  <a href="#"><img alt="Ruff" src="https://img.shields.io/badge/code_style-ruff-d4aa00?style=flat-square"></a>
</p>

<br/>

<p align="center">
  <b>Reviewer-first control tower for vibe coders: multi-agent review, repair, and evolution in one place.</b><br/>
  <sub>Run Claude, Codex, Gemini, and other CLI agents in auditable consensus loops to find bugs, ship fixes, and continuously evolve your codebase.</sub>
</p>
<p align="center">
  <sub><b>Brand mode (low-risk rename):</b> display name = <code>AWE-AgentForge</code>, runtime/package IDs stay <code>awe-agentcheck</code> / <code>awe_agentcheck</code>.</sub>
</p>

<p align="center">
  <a href="README.zh-CN.md">&#127464;&#127475; 中文文档</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="docs/RUNBOOK.md">Runbook</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="docs/ARCHITECTURE_FLOW.md">Architecture</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#beginner-dashboard-guide-button-by-button">Dashboard Guide</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#project-pulse-stars">Stars</a>&nbsp;&nbsp;·&nbsp;&nbsp;
  <a href="#quick-start">Quick Start</a>
</p>

<br/>

---

<br/>

## Latest Update (2026-02-20)

1. Provider adapter architecture is now strategy/factory based:
   - added `ProviderAdapter` + provider-specific adapters (`ClaudeAdapter`, `CodexAdapter`, `GeminiAdapter`)
   - added `ProviderFactory` and switched `ParticipantRunner` to use adapter dispatch instead of provider branching.
2. Service layers were fully package-split for maintainability:
   - replaced monolithic `src/awe_agentcheck/service_layers.py` with `src/awe_agentcheck/service_layers/`
   - split into `analytics.py`, `history.py`, `task_management.py`, and package export `__init__.py`.
   - `HistoryService` now consumes a single `HistoryDeps` dependency object instead of many callback args.
3. Sandbox/fingerprint logic deduplicated:
   - workspace fingerprint and sandbox bootstrap/ignore logic now live in `TaskManagementService`.
   - `OrchestratorService` delegates to task-management helpers to avoid dual implementations drifting apart.
4. Prompt assembly externalized into template files:
   - new `src/awe_agentcheck/prompt_templates/*.txt` for discussion/implementation/review/proposal stages.
   - both `workflow.py` and proposal-stage prompts now render via shared template loader.
5. LangGraph execution upgraded to real per-round graph progression:
   - each `round` node now executes exactly one workflow round and routes by state/result.
   - no longer wraps `_run_classic` as a full-loop single-node execution.
6. Dashboard modularization deepened:
   - extracted frontend modules into:
     - `web/assets/modules/api.js`
     - `web/assets/modules/store.js`
     - `web/assets/modules/utils.js`
     - `web/assets/modules/ui.js`
     - `web/assets/modules/create_task_help.js`
   - switched dashboard loader to ES module mode (`<script type="module" ...>`).
   - `initElements()` now exposes grouped panel scopes (`project/summary/history/controls/create/providers`) with backward-compatible flat aliases.
7. Provider model catalog is now backend-driven first:
   - Web state starts from empty provider lists and hydrates from `GET /api/provider-models`.
   - dashboard normalization now merges server payload with cached runtime state, not hardcoded model constants.
8. Reliability fixes from integration verification:
   - fixed dry-run evidence output so `selftest_local_smoke.py` can pass precompletion checks consistently.
   - removed lint-breaking lambda assignments in LangGraph nodes.
9. Regression verification completed:
   - `pytest -q tests/unit`
   - `py -m ruff check .`
   - `py scripts/selftest_local_smoke.py --port 8011 --health-timeout-seconds 40 --task-timeout-seconds 120`

## Previous Update (2026-02-19)

1. Default model profiles are now stronger and explicit:
   - Claude default command pins `claude-opus-4-6`
   - Codex default command uses `model_reasoning_effort=xhigh`
   - Gemini default command is normalized to `gemini --yolo`
2. Added provider model catalog endpoint (`GET /api/provider-models`) and wired Web dropdowns to server-driven model lists.
3. Added per-task conversation language control (`conversation_language`: `en`/`zh`) in API, CLI, workflow prompts, and dashboard form.
4. Added provider model parameter passthrough end-to-end (`provider_model_params` / `--provider-model-param provider=args`).
5. Hardened Windows runtime execution:
   - participant adapter resolves executables via `shutil.which(...)`
   - fixed intermittent `command_not_found` for CLI shims.
6. Added stable API lifecycle scripts:
   - `scripts/start_api.ps1` (health-gated startup, PID file, failure log tail)
   - `scripts/stop_api.ps1` (PID + port cleanup)
7. Startup reliability improved:
   - default PostgreSQL URL now includes `connect_timeout=2` for faster fallback when DB is unavailable.
8. Added project-level history endpoint and dashboard history card:
   - `GET /api/project-history`
   - includes core findings, revisions, disputes, and recommended next steps.
9. Improved Web traceability:
   - project selector now includes historical projects even when no active tasks are loaded.
10. Startup defaults now preserve local history:
   - `scripts/start_api.ps1` and overnight launcher default to persistent local SQLite when `AWE_DATABASE_URL` is unset.
11. Added explicit repair policy mode end-to-end:
   - `repair_mode`: `minimal` / `balanced` / `structural`
   - wired through Web form, API, CLI, task metadata, and workflow prompts.
12. Reviewer fault tolerance improved:
   - single reviewer runtime failures (e.g., Gemini `provider_limit`) no longer crash the whole task as `failed_system`.
   - now emits `review_error` / `proposal_review_error`, downgrades that reviewer to `unknown`, and continues to gate/manual decision.
13. Added `plain_mode` (default enabled) for beginner-readable outputs:
   - enabled: concise, less jargon-heavy responses in conversation flow
   - disabled: raw technical output style.
14. Reviewer-first alignment is now explicit:
   - when `debate_mode=1`, reviewers precheck first and author responds with a revised plan.
   - author remains the implementation owner; reviewers do not write final code changes.
15. Manual mode consensus now has explicit stall safeguards:
   - in `self_loop_mode=0`, `max_rounds` means required proposal consensus rounds (not just one discussion pass).
   - if one round keeps bouncing for 10+ retries without alignment, task is paused at `waiting_manual` with reason `proposal_consensus_stalled_in_round`, and a `consensus_stall` artifact is written.
   - if the same issue signature repeats across 4+ consensus rounds, task is paused at `waiting_manual` with reason `proposal_consensus_stalled_across_rounds`, with highlighted stall logs/artifacts.
16. Added richer proposal-stage events for observability:
   - `proposal_precheck_review*`
   - `proposal_consensus_reached` / `proposal_consensus_retry` / `proposal_review_partial`
17. Merged two real codex self-check rounds into production code with regression coverage:
   - round-1 hardening: artifact events traversal guard, Windows command path parsing, SQL conditional status-update atomicity.
   - round-2 hardening: concurrent event sequence reservation, sandbox bootstrap rollback/cleanup, private-by-default sandbox base, secret-file skip rules.
   - fixed reviewer-blocked FK risk by cleaning `task_event_counters` before task deletion; added DB regression test.
18. Shipped roadmap capabilities previously marked as Q2/Q3:
   - Richer GitHub/PR integration: `GET /api/tasks/{task_id}/github-summary` with PR-ready markdown and task-artifact links.
   - Policy templates by repo size/risk profile: `GET /api/policy-templates`, plus Web "Apply Policy" flow.
   - Pluggable participant adapters: `AWE_PROVIDER_ADAPTERS_JSON` supports extra providers beyond built-in Claude/Codex/Gemini.
   - Branch-aware promotion guard: auto-merge and round-promote now run branch/worktree guard checks before fusion.
   - Advanced visual analytics: `GET /api/analytics` and dashboard panel for failure taxonomy trends + reviewer drift signals.
19. Added participant-level capability overrides end-to-end:
   - new API/UI fields: `participant_models` and `participant_model_params` (`participant_id -> value`).
   - workflow now resolves runtime model/params by participant first, then provider fallback.
   - Create Task now includes a **Bot Capability Matrix** so author and each reviewer can use different settings even under the same provider (for example Codex author `high` vs Codex reviewer `xhigh`).
20. Added hard `PreCompletionChecklist` middleware before pass:
   - task cannot enter `passed` unless verification ran and evidence paths are present.
   - new events include `precompletion_checklist` and checklist-specific gate reasons (`precompletion_evidence_missing`, `precompletion_commands_missing`).
21. Added environment context auto-injection at task start:
   - prompts now include workspace excerpt, validation commands, and execution constraints to reduce blind repo scans.
22. Added fine-grained dead-loop detection with strategy switching:
   - detects repeated gate reason / repeated implementation summary / repeated review signature.
   - emits `strategy_shifted` and injects next-round strategy hints; hard-stops with `loop_no_progress` after repeated shifts.
23. Added analytics-driven policy adaptation in overnight loop:
   - `overnight_autoevolve.py` now reads `/api/analytics` + `/api/policy-templates` and automatically adjusts next task template/knobs.
   - new flags: `--adaptive-policy`, `--adaptive-interval`, `--analytics-limit`, `--policy-template`.
24. Added fixed benchmark harness for A/B orchestration regression:
   - new script: `scripts/benchmark_harness.py`
   - fixed benchmark suite: `ops/benchmark_tasks.json`
   - outputs JSON + Markdown comparison reports under `.agents/benchmarks/`.
25. Added hard evidence/resume reliability guards:
   - each `precompletion_checklist` now emits persisted evidence artifacts: `artifacts/evidence_bundle_round_<n>.json`.
   - pass + auto-merge path now validates the latest evidence bundle before fusion (`No evidence, no merge`).
   - tasks now store a workspace fingerprint; start/resume verifies fingerprint consistency and blocks with `workspace_resume_guard_mismatch` on drift.
   - new CLI wrapper command: `py -m awe_agentcheck.cli benchmark ...` (runs `scripts/benchmark_harness.py`).
26. Added hard 1-5 reliability loop upgrades:
   - start-path singleflight guard (`start_deduped`) to avoid duplicate concurrent start/rerun execution.
   - preflight risk-policy gate now runs before consensus/execution and can fail fast (`preflight_risk_gate_failed`) before expensive loops.
   - strict auto-merge head-SHA discipline: merge target drift during run blocks fusion (`head_sha_mismatch`).
   - structured `evidence_manifest.json` is written for passed `WorkflowEngine` runs.
   - failed tasks now auto-emit regression tasks to `.agents/regressions/failure_tasks.json`, and benchmark harness can include them by default (`--include-regression`).
27. Added structured reviewer control parsing (P0):
   - reviewer output now supports JSON control schema first, with legacy `VERDICT:` / `NEXT_ACTION:` regex as fallback.
   - reduces format drift failures when model output style changes.
28. Added `architecture_audit` stage (P0):
   - emits `architecture_audit` event with LOC thresholds, mixed-responsibility heuristics, and cross-platform script coverage checks.
   - supports enforcement mode `off|warn|hard` via `AWE_ARCH_AUDIT_MODE` (default: `warn` at evolution level 1, `hard` at evolution level 2).
29. Refactored participant adapter into provider registry (P1):
   - registry now carries provider command template, model-flag strategy, and capability toggles.
   - extra providers from `AWE_PROVIDER_ADAPTERS_JSON` are auto-registered with sane defaults.
30. Started gradual monolith split (P1):
   - moved policy template catalog into `src/awe_agentcheck/policy_templates.py` (behavior unchanged).
   - split `web/index.html` inline payload into `web/assets/dashboard.css` + `web/assets/dashboard.js`.
31. Added explicit static asset serving route:
   - `GET /web/assets/{asset_name}` now serves split dashboard resources safely.
   - path traversal is blocked by root-relative guard checks.
32. Adapter runtime handling is now structured (hardening):
   - known provider/runtime failures (`command_not_found`, `command_timeout`, `provider_limit`, non-zero command failures) return structured adapter results instead of raising `RuntimeError` directly.
   - workflow/service now gate on those reasons deterministically (less silent empty-run behavior).
33. Author runtime errors are now hard-gated at phase boundary:
   - discussion/implementation phase runtime failures now fail fast with explicit gate reasons (`command_timeout`, `command_not_found`, etc.) instead of drifting into ambiguous downstream states.
34. Architecture audit now has broader hard-rule coverage and configurable thresholds:
   - new checks: `service_monolith_too_large`, `workflow_monolith_too_large`, `dashboard_monolith_too_large`, `prompt_assembly_hotspot`, `adapter_runtime_raise_detected`.
   - new env thresholds: `AWE_ARCH_*` (file-line limits, prompt-builder limit, adapter-runtime-raise limit).
35. Added cross-platform shell script set (Linux/macOS):
   - `scripts/start_api.sh`, `scripts/stop_api.sh`
   - `scripts/start_overnight_until_7.sh`, `scripts/stop_overnight.sh`
   - `scripts/supervise_until.sh`
36. Added `.env.example`:
   - centralized baseline for core runtime, provider commands, API/promotion policy, and architecture-audit hard-gate thresholds.

<br/>

## Why AWE-AgentForge?

<table>
<tr>
<td width="33%" align="center">

**Multi-Agent Collaboration**

Run cross-agent workflows where one model authors, others review, and sessions challenge each other until the result is defensible.

</td>
<td width="33%" align="center">

**Bug Resolution Engine**

Turn vague failures into structured rounds: reproduce, patch, review, verify, and gate. Built for real bug-fixing throughput, not demo chats.

</td>
<td width="33%" align="center">

**Continuous Self-Evolution**

Run guided or proactive evolution loops so agents can propose, test, and refine improvements beyond the immediate bug ticket.

</td>
</tr>
<tr>
<td width="33%" align="center">

**Human + Policy Control**

Manual author approval, medium-gate decisions, and force-fail controls keep operators in charge when risk is high.

</td>
<td width="33%" align="center">

**Live Operations Console**

Monitor project tree, role sessions, and conversation flow in real time, then execute task controls from a single surface.

</td>
<td width="33%" align="center">

**Reliability + Observability**

Use watchdog timeouts, provider fallback, cooldowns, metrics, logs, and traces to keep long-running automation measurable and stable.

</td>
</tr>
</table>

<br/>

## Architecture

<p align="center">
  <img src="docs/assets/architecture-overview.svg" alt="system architecture" width="100%" />
</p>

<br/>

## Visual Overview

### Monitor Dashboard (Terminal Pixel Theme)

<p align="center">
  <img src="docs/assets/dashboard-preview.svg" alt="terminal pixel dashboard preview with multi-role sessions" width="100%" />
</p>

Preview focus:

1. Terminal pixel visual style.
2. High-density multi-role session panel (not only 2-3 roles).
3. Conversation-centric layout with operational controls visible.

### Runtime Flow (Clean Lanes, No Arrow Crossing Through Bubbles)

<p align="center">
  <img src="docs/assets/workflow-flow.svg" alt="workflow flow" width="100%" />
</p>

<br/>

## Project Pulse (Stars)

<p align="center">
  <a href="https://github.com/cloveric/awe-agentforge/stargazers"><img alt="GitHub Repo stars" src="https://img.shields.io/github/stars/cloveric/awe-agentforge?style=for-the-badge&logo=github&label=GitHub%20Stars&color=fbbf24"></a>&nbsp;
  <a href="https://github.com/cloveric/awe-agentforge/network/members"><img alt="GitHub forks" src="https://img.shields.io/github/forks/cloveric/awe-agentforge?style=for-the-badge&logo=github&label=Forks&color=60a5fa"></a>
</p>

<p align="center">
  <a href="https://github.com/cloveric/awe-agentforge/stargazers">
    <img src="https://api.star-history.com/svg?repos=cloveric/awe-agentforge&type=Date" alt="Star History Chart" width="92%" />
  </a>
</p>

<br/>

## Core Concepts

Before diving into usage, here are the key concepts:

### Participants

Every task has one **author** (who writes the code) and one or more **reviewers** (who evaluate it). Participants are identified using the `provider#alias` format:

| Format | Meaning |
|:---|:---|
| `claude#author-A` | Claude CLI acting as author, alias "author-A" |
| `codex#review-B` | Codex CLI acting as reviewer, alias "review-B" |
| `gemini#review-C` | Gemini CLI acting as second reviewer, alias "review-C" |

The `provider` determines which CLI tool is invoked (`claude`, `codex`, or `gemini`). The `alias` is a human-readable label for identification in the web console and logs.

### Task Lifecycle

Every task follows this lifecycle:

```
queued → running → passed / failed_gate / failed_system / canceled
```

In manual mode (`self_loop_mode=0`), an extra state is inserted:

```
queued → running → waiting_manual → (approve) → queued → running → passed/failed
                                  → (reject)  → canceled
```

`running` in manual mode is now a proposal-consensus stage (reviewer-first when `debate_mode=1`) before pausing at `waiting_manual`.

### Three Controls

| Control | Values | Default | What It Does |
|:---|:---:|:---:|:---|
| `sandbox_mode` | `0` / `1` | **`1`** | `1` = run in an isolated `*-lab` copy of the workspace; `0` = run directly in main workspace |
| `self_loop_mode` | `0` / `1` | **`0`** | `0` = run proposal consensus rounds, then pause for approval; `1` = run autonomous implementation/review loops |
| `auto_merge` | `0` / `1` | **`1`** | `1` = on pass, auto-merge changes back + generate changelog; `0` = keep results in sandbox only |

> [!TIP]
> **Recommended defaults for safety**: `sandbox_mode=1` + `self_loop_mode=0` + `auto_merge=1` — sandbox execution with human sign-off and automatic artifact fusion on pass.

<br/>

## Quick Start

### Prerequisites

- **Python 3.10+**
- **Claude CLI** installed and authenticated (for Claude participants)
- **Codex CLI** installed and authenticated (for Codex participants)
- **Gemini CLI** installed and authenticated (for Gemini participants)
- **PostgreSQL** (optional — falls back to in-memory database if unavailable)

### Step 1: Install

```bash
git clone https://github.com/cloveric/awe-agentforge.git
cd awe-agentforge
pip install -e .[dev]
# Optional: copy baseline env and adjust
cp .env.example .env
```

### Step 2: Configure Environment

The system needs to know where your tools are and how to connect. Set the following environment variables:

```powershell
# Required: tell Python where the source is
$env:PYTHONPATH="src"

# Optional: database connection (omit for in-memory mode)
$env:AWE_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/awe_agentcheck?connect_timeout=2"

# Optional: where task artifacts (logs, reports, events) are stored
$env:AWE_ARTIFACT_ROOT=".agents"

# Optional: workflow orchestrator backend (langgraph/classic)
$env:AWE_WORKFLOW_BACKEND="langgraph"
```

Or use `.env.example` as a starter and export the values in your shell.

<details>
<summary><b>All environment variables reference</b></summary>

| Variable | Default | Description |
|:---|:---|:---|
| `PYTHONPATH` | _(none)_ | Must include `src/` directory |
| `AWE_DATABASE_URL` | `postgresql+psycopg://...?...connect_timeout=2` | PostgreSQL connection string. If DB is unavailable, fallback is faster and then switches to in-memory |
| `AWE_ARTIFACT_ROOT` | `.agents` | Directory for task artifacts (threads, events, reports) |
| `AWE_CLAUDE_COMMAND` | `claude -p --dangerously-skip-permissions --effort low --model claude-opus-4-6` | Command template for invoking Claude CLI |
| `AWE_CODEX_COMMAND` | `codex exec --skip-git-repo-check ... -c model_reasoning_effort=xhigh` | Command template for invoking Codex CLI |
| `AWE_GEMINI_COMMAND` | `gemini --yolo` | Command template for invoking Gemini CLI |
| `AWE_PARTICIPANT_TIMEOUT_SECONDS` | `3600` | Max seconds a single participant (Claude/Codex/Gemini) can run per step |
| `AWE_COMMAND_TIMEOUT_SECONDS` | `300` | Max seconds for test/lint commands |
| `AWE_PARTICIPANT_TIMEOUT_RETRIES` | `1` | Retry count when a participant times out |
| `AWE_MAX_CONCURRENT_RUNNING_TASKS` | `1` | How many tasks can run simultaneously |
| `AWE_WORKFLOW_BACKEND` | `langgraph` | Workflow backend (`langgraph` preferred, `classic` fallback) |
| `AWE_ARCH_AUDIT_MODE` | _(auto by evolution level)_ | Architecture audit enforcement mode: `off`, `warn`, `hard` |
| `AWE_ARCH_PYTHON_FILE_LINES_MAX` | `1200` | Override max lines for a Python file in architecture audit |
| `AWE_ARCH_FRONTEND_FILE_LINES_MAX` | `2500` | Override max lines for frontend files in architecture audit |
| `AWE_ARCH_RESPONSIBILITY_KEYWORDS_MAX` | `10` | Override mixed-responsibility keyword threshold for large Python files |
| `AWE_ARCH_SERVICE_FILE_LINES_MAX` | `4500` | Override max lines for `src/awe_agentcheck/service.py` |
| `AWE_ARCH_WORKFLOW_FILE_LINES_MAX` | `2600` | Override max lines for `src/awe_agentcheck/workflow.py` |
| `AWE_ARCH_DASHBOARD_JS_LINES_MAX` | `3800` | Override max lines for `web/assets/dashboard.js` |
| `AWE_ARCH_PROMPT_BUILDER_COUNT_MAX` | `14` | Override prompt-builder hotspot threshold |
| `AWE_ARCH_ADAPTER_RUNTIME_RAISE_MAX` | `0` | Max allowed raw `RuntimeError` raises in adapter runtime path |
| `AWE_PROVIDER_ADAPTERS_JSON` | _(none)_ | JSON map for extra providers, e.g. `{"qwen":"qwen-cli --yolo"}` |
| `AWE_PROMOTION_GUARD_ENABLED` | `true` | Enable promotion guard checks before auto-merge/promote-round |
| `AWE_PROMOTION_ALLOWED_BRANCHES` | _(empty)_ | Optional comma-separated allowed branches (empty = allow any branch) |
| `AWE_PROMOTION_REQUIRE_CLEAN` | `false` | Require clean git worktree for promotion when guard is enabled |
| `AWE_SANDBOX_USE_PUBLIC_BASE` | `false` | Use shared/public sandbox root only when explicitly set to `1/true` |
| `AWE_API_ALLOW_REMOTE` | `false` | Allow non-loopback API access (`false` keeps local-only default) |
| `AWE_API_TOKEN` | _(none)_ | Optional bearer token for API protection |
| `AWE_API_TOKEN_HEADER` | `Authorization` | Header name used for API token validation |
| `AWE_DRY_RUN` | `false` | When `true`, participants are not actually invoked |
| `AWE_SERVICE_NAME` | `awe-agentcheck` | Service name for observability |
| `AWE_OTEL_EXPORTER_OTLP_ENDPOINT` | _(none)_ | OpenTelemetry collector endpoint |

> [!NOTE]
> If `AWE_DATABASE_URL` is unset and you start via provided scripts, runtime defaults to local SQLite (`.agents/runtime/awe-agentcheck.sqlite3`) so history survives restarts. Direct custom startup paths may still choose in-memory fallback.
</details>

### Step 3: Start the API Server

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "scripts/start_api.ps1" -ForceRestart
```

```bash
bash scripts/start_api.sh --force-restart
```

Health check:

```powershell
(Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8000/healthz").Content
```

Expected:

```json
{"status":"ok"}
```

Stop API safely:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "scripts/stop_api.ps1"
```

```bash
bash scripts/stop_api.sh
```

### Step 4: Open the Web Monitor

Open your browser and navigate to:

```
http://localhost:8000/
```

You'll see the monitor dashboard with:
- **Left panel**: project file tree + roles/sessions
- **Right panel**: task controls, conversation stream, and task creation form

## Beginner Dashboard Guide (Button-by-Button)

If this is your first time, operate in this exact order:

1. Confirm API is online (`API: ONLINE` at the top right).
2. Click `Refresh`.
3. In `Dialogue Scope`, choose `Project` and `Task`.
4. Read `Conversation` first, then decide start/approve/reject.
5. Use `Force Fail` only when a task is stuck and cannot recover.

### Top Bar

| Control | What it means | When to use |
|:---|:---|:---|
| `Refresh` | Pull latest tasks/stats/tree/events immediately | Any time data looks stale |
| `Auto Poll: OFF/ON` | Toggle periodic refresh | Turn ON during active runs |
| `Theme` | Switch visual style (`Neon Grid`, `Terminal Pixel`, `Executive Glass`) | Personal preference |
| `API: ONLINE/RETRY(n)` | Backend health indicator | If `RETRY`, check server logs first |

### Left Panel: Project Structure

| Control | What it means | When to use |
|:---|:---|:---|
| `Expand` | Open all currently loaded folders in the tree | Get full repository context quickly |
| `Collapse` | Close all folders | Reduce noise when tree is too dense |
| Tree node (`[D]` / `[F]`) | Directory or file item for selected project | Verify target repo and key files |

### Left Panel: Roles / Sessions

| Control | What it means | When to use |
|:---|:---|:---|
| `all roles` | Show full mixed conversation stream | Default view for global context |
| `provider#alias` role row | Filter conversation to a single role/session | Debug one participant's behavior |

### Right Panel: Dialogue Scope + Task Controls

| Control | What it means | When to use |
|:---|:---|:---|
| `Project` | Active project scope | Switch when multiple repos are tracked |
| `Task` | Active task scope | Move between tasks in selected project |
| `Force-fail reason` | Reason text sent if force-failing a task | Fill before pressing `Force Fail` |
| `Start` | Start selected `queued` task | Normal start action |
| `Approve + Queue` | Approve proposal in `waiting_manual`, leave task queued | Approve now, start later |
| `Approve + Start` | Approve proposal and immediately run | Fast path after proposal review |
| `Reject` | Reject proposal in `waiting_manual` and cancel task | Proposal is risky or low quality |
| `Cancel` | Cancel current running/queued task | Stop work intentionally |
| `Force Fail` | Mark task `failed_system` with your reason | Last resort for stuck/hung tasks |
| `Reload Dialogue` | Force re-fetch event stream for selected task | Dialogue appears incomplete |

### Conversation Panel

| Area | What it means | How to read |
|:---|:---|:---|
| Actor label (e.g. `claude#author-A`) | Who sent the event | Track accountability by role |
| Event kind (e.g. `discussion`, `review`) | Workflow stage marker | Detect where failures happen |
| Message body | Raw or summarized event payload | Validate claims before approving |

### Create Task Form (Every Input)

| Field | Meaning | Recommended beginner value |
|:---|:---|:---|
| `Title` | Task name shown everywhere | Clear and short |
| `Workspace path` | Repository root path | Your actual project path |
| `Author` | Implementing participant | `claude#author-A` / `codex#author-A` / `gemini#author-A` |
| `Reviewers` | One or more reviewers, comma-separated | At least 1 reviewer |
| `Claude Model / Codex Model / Gemini Model` | Per-provider model pinning (dropdown + editable) | Start from defaults (`claude-opus-4-6`, `gpt-5.3-codex`, `gemini-3-pro-preview`) |
| `Claude/Codex/Gemini Model Params` | Optional extra args per provider | For Codex use `-c model_reasoning_effort=xhigh` |
| `Claude Team Agents` | Enable/disable Claude `--agents` mode | `0` (disabled) |
| `Evolution Level` | `0` fix-only, `1` guided evolve, `2` proactive evolve | Start with `0` |
| `Repair Mode` | `minimal` / `balanced` / `structural` | Start with `balanced` |
| `Max Rounds` | `self_loop_mode=0`: required consensus rounds; `self_loop_mode=1`: retry cap fallback when no deadline | `1` |
| `Evolve Until` | Optional deadline (`YYYY-MM-DD HH:MM`) | Empty unless running overnight |
| `Max Rounds` + `Evolve Until` | Priority rule | If `Evolve Until` is set, deadline wins; if empty, `Max Rounds` is used |
| `Conversation Language` | Prompt language for agent outputs (`en` / `zh`) | `English` for logs, `中文` for Chinese collaboration |
| `Plain Mode` | Beginner-friendly readable output (`1` on / `0` off) | Start with `1` |
| `Stream Mode` | Realtime stream chunks from participant stdout/stderr (`1` on / `0` off) | Start with `1` |
| `Debate Mode` | Enable reviewer-first debate/precheck stage (`1` on / `0` off) | Start with `1` |
| `Sandbox Mode` | `1` sandbox / `0` main workspace | Keep `1` for safety |
| `Sandbox Workspace Path` | Optional custom sandbox path | Leave blank (auto per-task path) |
| `Self Loop Mode` | `0` manual approval / `1` autonomous | Start with `0` |
| `Auto Merge` | `1` auto-fusion on pass / `0` disable | Keep `1` initially |
| `Merge Target Path` | Where pass results are merged | Project root |
| `Description` | Detailed requirement text | Include acceptance criteria |

UI policy note: when `Sandbox Mode = 0`, the dashboard forces `Auto Merge = 0` and locks that selector.

### Create Buttons

| Button | Behavior | Use case |
|:---|:---|:---|
| `Create` | Create task only (stays queued) | You want to review settings first |
| `Create + Start` | Create and start immediately | You already trust current settings |

### Safe Beginner Preset

Use this default stack for lowest risk:

- `Sandbox Mode = 1`
- `Self Loop Mode = 0`
- `Auto Merge = 1`
- Reviewer count `>= 1`

Then run this rhythm: `Create + Start` -> wait for `waiting_manual` -> inspect `Conversation` -> `Approve + Start` or `Reject`.

<br/>

### Step 5: Create Your First Task

You can create a task via the **Web UI** (use the "Create Task" form at the bottom of the dashboard) or via the **CLI**:

```powershell
py -m awe_agentcheck.cli run `
  --task "Fix the login validation bug" `
  --author "codex#author-A" `
  --reviewer "claude#review-B" `
  --conversation-language en `
  --workspace-path "." `
  --auto-start
```

This will:
1. Create a task with title "Fix the login validation bug"
2. Assign Codex as the author and Claude as the reviewer
3. Use default policies (`sandbox_mode=1`, `self_loop_mode=0`, `auto_merge=1`)
4. Automatically start the task (`--auto-start`)
5. Since `self_loop_mode=0`, the system will run reviewer-first proposal consensus rounds, then pause at `waiting_manual` for your approval

### Step 6: Approve and Execute (Manual Mode)

After the system pauses at `waiting_manual`, review the proposal in the web UI or via CLI, then approve:

```powershell
# Approve the proposal and immediately start execution
py -m awe_agentcheck.cli decide <task-id> --approve --auto-start
```

Or reject:

```powershell
# Reject the proposal (task will be canceled)
py -m awe_agentcheck.cli decide <task-id>
```

> [!IMPORTANT]
> In manual mode, the task **will not** proceed to implementation until you explicitly approve. This is by design — it ensures you have full control over what gets implemented.

<br/>

## CLI Reference

The CLI communicates with the API server over HTTP. Make sure the server is running before using any CLI command.

```
py -m awe_agentcheck.cli [--api-base URL] <command> [options]
```

Global option: `--api-base` (default: `http://127.0.0.1:8000`) — the API server URL.

### `run` — Create a New Task

Creates a task and optionally starts it immediately.

```powershell
py -m awe_agentcheck.cli run `
  --task "Task title" `
  --description "Detailed description of what to do" `
  --author "claude#author-A" `
  --reviewer "codex#review-B" `
  --reviewer "claude#review-C" `
  --conversation-language en `
  --sandbox-mode 1 `
  --self-loop-mode 0 `
  --auto-merge `
  --workspace-path "C:/path/to/your/project" `
  --max-rounds 3 `
  --test-command "py -m pytest -q" `
  --lint-command "py -m ruff check ." `
  --auto-start
```

| Flag | Required | Default | Description |
|:---|:---:|:---|:---|
| `--task` | Yes | — | Task title (shown in UI and logs) |
| `--description` | No | same as `--task` | Detailed description for the AI participants |
| `--author` | Yes | — | Author participant in `provider#alias` format |
| `--reviewer` | Yes | — | Reviewer participant (repeatable for multiple reviewers) |
| `--sandbox-mode` | No | `1` | `1` = sandbox, `0` = main workspace |
| `--sandbox-workspace-path` | No | auto-generated | Custom sandbox directory path |
| `--self-loop-mode` | No | `0` | `0` = manual approval, `1` = autonomous |
| `--auto-merge` / `--no-auto-merge` | No | enabled | Enable/disable auto-fusion on pass |
| `--merge-target-path` | No | project root | Where to merge changes back to |
| `--workspace-path` | No | `.` | Path to the target repository |
| `--max-rounds` | No | `3` | Manual mode: required consensus rounds. Autonomous mode: max gate retries when no deadline |
| `--test-command` | No | `py -m pytest -q` | Command to run tests |
| `--lint-command` | No | `py -m ruff check .` | Command to run linter |
| `--evolution-level` | No | `0` | `0` = fix-only, `1` = guided evolve, `2` = proactive evolve |
| `--repair-mode` | No | `balanced` | Repair policy (`minimal` / `balanced` / `structural`) |
| `--evolve-until` | No | — | Deadline for evolution (e.g. `2026-02-13 06:00`) |
| `--conversation-language` | No | `en` | Agent output language (`en` or `zh`) |
| `--plain-mode` / `--no-plain-mode` | No | enabled | Toggle beginner-readable output mode |
| `--stream-mode` / `--no-stream-mode` | No | enabled | Toggle realtime stream events |
| `--debate-mode` / `--no-debate-mode` | No | enabled | Toggle reviewer-first debate/precheck stage |
| `--provider-model` | No | — | Per-provider model override in `provider=model` format (repeatable) |
| `--provider-model-param` | No | — | Per-provider extra args in `provider=args` format (repeatable) |
| `--claude-team-agents` | No | `0` | `1` enables Claude `--agents` mode for Claude participants |
| `--auto-start` | No | `false` | Start immediately after creation |

### `decide` — Submit Author Decision

Used in manual mode to approve or reject a proposal at `waiting_manual` state.

```powershell
# Approve and immediately start
py -m awe_agentcheck.cli decide <task-id> --approve --auto-start

# Approve without auto-start (task goes to queued)
py -m awe_agentcheck.cli decide <task-id> --approve

# Reject (task is canceled)
py -m awe_agentcheck.cli decide <task-id>

# Approve with a note
py -m awe_agentcheck.cli decide <task-id> --approve --note "Looks good, proceed" --auto-start
```

### `status` — Get Task Details

```powershell
py -m awe_agentcheck.cli status <task-id>
```

Returns the full task object as JSON, including status, rounds completed, gate reason, etc.

### `tasks` — List All Tasks

```powershell
py -m awe_agentcheck.cli tasks --limit 20
```

### `stats` — Show Aggregated Statistics

```powershell
py -m awe_agentcheck.cli stats
```

Returns pass rates, failure buckets, provider error counts, and average task duration.

### `analytics` — Show Advanced Analytics

```powershell
py -m awe_agentcheck.cli analytics --limit 300
```

Returns failure taxonomy/trend and reviewer drift metrics for observability analysis.

### `policy-templates` — Get Recommended Policy Presets

```powershell
py -m awe_agentcheck.cli policy-templates --workspace-path "."
```

Returns repo profile and suggested task-control presets by size/risk.

### `benchmark` — Run Fixed A/B Benchmark Harness

```powershell
py -m awe_agentcheck.cli benchmark `
  --workspace-path "." `
  --variant-a-name "baseline" `
  --variant-b-name "candidate" `
  --reviewer "claude#review-B"
```

Runs the fixed benchmark pack and writes JSON/Markdown reports under `.agents/benchmarks/`.

### `github-summary` — Generate PR-Ready Summary

```powershell
py -m awe_agentcheck.cli github-summary <task-id>
```

Returns markdown summary and artifact links suitable for GitHub PR description.

### `start` — Start an Existing Task

```powershell
py -m awe_agentcheck.cli start <task-id>
py -m awe_agentcheck.cli start <task-id> --background
```

### `cancel` — Cancel a Task

```powershell
py -m awe_agentcheck.cli cancel <task-id>
```

### `force-fail` — Force-Fail a Task

```powershell
py -m awe_agentcheck.cli force-fail <task-id> --reason "Manual abort: wrong branch"
```

### `promote-round` — Promote One Round Snapshot (Manual Multi-Round Mode)

```powershell
py -m awe_agentcheck.cli promote-round <task-id> --round 2 --merge-target-path "."
```

Use when `max_rounds>1` and `auto_merge=0`. Promotes one selected round snapshot into target path.

### `events` — List Task Events

```powershell
py -m awe_agentcheck.cli events <task-id>
```

Returns the full event timeline for a task (discussions, reviews, verifications, gate results, etc.).

### `tree` — Show Workspace File Tree

```powershell
py -m awe_agentcheck.cli tree --workspace-path "." --max-depth 4
```

<br/>

## Usage Examples

### Example 1: Safe Manual Review (Recommended for First Use)

The most conservative approach — sandbox execution with manual approval:

```powershell
py -m awe_agentcheck.cli run `
  --task "Improve error handling in the API layer" `
  --author "claude#author-A" `
  --reviewer "codex#review-B" `
  --reviewer "claude#review-C" `
  --workspace-path "." `
  --auto-start
```

What happens:
1. System creates an isolated sandbox workspace (`awe-agentcheck-lab/20260213-...`)
2. Reviewers precheck and challenge the proposal first (reviewer-first stage)
3. Author revises proposal, reviewers re-check for consensus
4. Task pauses at `waiting_manual` — you review in the web UI
5. You approve → system runs implementation → reviewers review code → tests + lint → gate decision
6. If passed: changes auto-merge back to your main workspace with a changelog

### Example 2: Fully Autonomous Overnight Run

For unattended operation (make sure you trust the safety controls):

```powershell
py -m awe_agentcheck.cli run `
  --task "Overnight continuous improvement" `
  --author "codex#author-A" `
  --reviewer "claude#review-B" `
  --sandbox-mode 1 `
  --self-loop-mode 1 `
  --max-rounds 5 `
  --workspace-path "." `
  --auto-start
```

What happens:
1. Codex (author) goes directly into the workflow loop — no manual checkpoint
2. Each round: discussion → implementation → review → verify → gate
3. If gate passes: done. If fails: retries up to 5 rounds
4. Results auto-merge back on pass

### Example 3: No Auto-Merge (Keep Results in Sandbox)

When you want to review changes manually before merging:

```powershell
py -m awe_agentcheck.cli run `
  --task "Experimental refactoring" `
  --author "claude#author-A" `
  --reviewer "codex#review-B" `
  --workspace-path "." `
  --no-auto-merge `
  --auto-start
```

What happens:
1. Everything runs as normal, but on pass, changes stay in the sandbox
2. You can manually review the sandbox directory and merge changes yourself

### Example 4: Direct Main Workspace (No Sandbox)

When you want changes applied directly to your main workspace:

```powershell
py -m awe_agentcheck.cli run `
  --task "Quick fix: typo in README" `
  --author "claude#author-A" `
  --reviewer "codex#review-B" `
  --sandbox-mode 0 `
  --self-loop-mode 1 `
  --workspace-path "." `
  --auto-start
```

> [!WARNING]
> With `sandbox_mode=0`, changes are made directly in your workspace. Use this only for low-risk tasks or when you have git to revert.

<br/>

## API Reference

All endpoints are served at `http://localhost:8000`. Request/response bodies are JSON.

### Create Task

```
POST /api/tasks
```

<details>
<summary>Request body</summary>

```json
{
  "title": "Fix login validation bug",
  "description": "The email validator accepts invalid formats",
  "author_participant": "claude#author-A",
  "reviewer_participants": ["codex#review-B"],
  "conversation_language": "en",
  "provider_models": {
    "claude": "claude-opus-4-6",
    "codex": "gpt-5.3-codex"
  },
  "provider_model_params": {
    "codex": "-c model_reasoning_effort=xhigh"
  },
  "claude_team_agents": false,
  "sandbox_mode": true,
  "self_loop_mode": 0,
  "auto_merge": true,
  "workspace_path": ".",
  "max_rounds": 3,
  "test_command": "py -m pytest -q",
  "lint_command": "py -m ruff check .",
  "auto_start": true
}
```
</details>

<details>
<summary>Response (201)</summary>

```json
{
  "task_id": "task-abc123",
  "title": "Fix login validation bug",
  "status": "queued",
  "sandbox_mode": true,
  "self_loop_mode": 0,
  "auto_merge": true,
  "rounds_completed": 0,
  ...
}
```
</details>

### All Endpoints

| Method | Endpoint | Description |
|:---:|:---|:---|
| `POST` | `/api/tasks` | Create a new task |
| `GET` | `/api/tasks` | List all tasks (`?limit=100`) |
| `GET` | `/api/tasks/{id}` | Get task details |
| `POST` | `/api/tasks/{id}/start` | Start a task (`{"background": true}` for async) |
| `POST` | `/api/tasks/{id}/cancel` | Request task cancellation |
| `POST` | `/api/tasks/{id}/force-fail` | Force-fail with `{"reason": "..."}` |
| `POST` | `/api/tasks/{id}/promote-round` | Promote one selected round into merge target (requires `max_rounds>1` and `auto_merge=0`) |
| `POST` | `/api/tasks/{id}/author-decision` | Approve/reject in manual mode: `{"approve": true, "auto_start": true}` |
| `GET` | `/api/tasks/{id}/events` | Get full event timeline |
| `POST` | `/api/tasks/{id}/gate` | Submit manual gate result |
| `GET` | `/api/provider-models` | Get provider model catalog for UI dropdowns |
| `GET` | `/api/policy-templates` | Get workspace profile and recommended control presets |
| `GET` | `/api/analytics` | Get failure taxonomy/trends and reviewer drift analytics |
| `GET` | `/api/tasks/{id}/github-summary` | Build GitHub/PR-ready markdown summary |
| `GET` | `/api/project-history` | Project-level history records (`core_findings`, `revisions`, `disputes`, `next_steps`) |
| `POST` | `/api/project-history/clear` | Clear scoped history records (optionally includes matching live tasks) |
| `GET` | `/api/workspace-tree` | File tree (`?workspace_path=.&max_depth=4`) |
| `GET` | `/api/stats` | Aggregated statistics (pass rates, durations, failure buckets) |
| `GET` | `/healthz` | Health check |

<br/>

## Feature Matrix

| Capability | Description | Status |
|:---|:---|:---:|
| **Sandbox-first execution** | Default `sandbox_mode=1`, runs in `*-lab` workspace with auto-generated per-task isolation | `GA` |
| **Author-approval gate** | Default `self_loop_mode=0`, enters `waiting_manual` after reviewer-first proposal consensus rounds | `GA` |
| **Autonomous self-loop** | `self_loop_mode=1` for unattended operation | `GA` |
| **Auto fusion** | On pass: merge + `CHANGELOG.auto.md` + snapshot | `GA` |
| **Provider model pinning** | Set model per provider (`claude` / `codex` / `gemini`) per task | `GA` |
| **Claude team-agents mode** | Per-task toggle to enable Claude `--agents` behavior | `GA` |
| **Multi-provider role model** | `provider#alias` participants (cross-provider or same-provider multi-session) | `GA` |
| **Web monitor console** | Project tree, roles/sessions, avatar-based chat, task controls, drag-and-drop | `GA` |
| **Project history ledger** | Cross-task timeline with findings/revisions/disputes/next-steps by project | `GA` |
| **Multi-theme UI** | Neon Grid, Terminal Pixel, Executive Glass | `GA` |
| **Observability stack** | OpenTelemetry, Prometheus, Loki, Tempo, Grafana | `GA` |
| **Overnight supervisor** | Timeout watchdog, provider fallback, cooldown, single-instance lock | `GA` |

<br/>

## How the Workflow Works

### Manual Mode (`self_loop_mode=0` — Default)

This is the recommended mode for most use cases:

1. **Create task** → status becomes `queued`
2. **Start task** → system runs proposal-consensus rounds:
   - if `debate_mode=1`, reviewers precheck first (`proposal_precheck_review`)
   - author replies with a revised proposal based on reviewer feedback
   - reviewers evaluate proposal quality/alignment (`proposal_review`)
3. **Consensus rule**:
   - one round is counted only when all required reviewers return pass-level consensus
   - same-round retries continue until alignment, but now have a 10-retry stall guard (`proposal_consensus_stalled_in_round`)
   - repeated same-issue consensus across rounds has a 4-round stall guard (`proposal_consensus_stalled_across_rounds`)
4. **Wait for human** → after required consensus rounds are complete, status becomes `waiting_manual`
5. **Author decides**:
   - **Approve** → status becomes `queued` (with `author_approved` reason), then immediately re-starts into the full workflow
   - **Reject** → status becomes `canceled`
6. **Full workflow** runs: reviewer-first debate (optional) → author discussion → author implementation → reviewer review → verify (test + lint) → gate
7. **Gate result**:
   - **Pass** → `passed` → Auto Fusion (merge + changelog + snapshot + sandbox cleanup)
   - **Fail** → retry next round; limit by `Evolve Until` when set, otherwise by `max_rounds`, then `failed_gate`

### Autonomous Mode (`self_loop_mode=1`)

For unattended operation:

1. **Create task** → `queued`
2. **Start task** → immediately enters the full workflow (no manual checkpoint)
3. **Round 1..N**: reviewer-first debate (optional) → author discussion → author implementation → reviewer review → verify → gate
4. **Gate result**:
   - **Pass** → `passed` → Auto Fusion
   - **Fail** → retry until deadline (`Evolve Until`) or `max_rounds` (when no deadline), then `failed_gate`

### Auto-Fusion Details

When a task passes and `auto_merge=1`:

1. Changed files are copied from sandbox to your main workspace
2. `CHANGELOG.auto.md` is appended with a summary
3. A snapshot is saved to `.agents/snapshots/`
4. The auto-generated sandbox is cleaned up (if system-generated)
5. An `auto_merge_summary.json` artifact is written

<details>
<summary><b>Sandbox lifecycle details</b></summary>

1. Without explicit `sandbox_workspace_path`, the system creates a unique per-task sandbox: `<project>-lab/<timestamp>-<id>/`
2. The sandbox is a filtered copy of your project (excludes `.git`, `.venv`, `node_modules`, `__pycache__`, etc.)
3. When task passes and auto-fusion completes, system-generated sandboxes are auto-cleaned
4. If you specified a custom `sandbox_workspace_path`, it is retained by default
</details>

<br/>

## Roadmap

### 2026 Q1 &nbsp; <img src="https://img.shields.io/badge/status-complete-22c55e?style=flat-square" alt="complete"/>

- [x] Sandbox-first default policy
- [x] Author-approval gate
- [x] Auto-fusion + changelog + snapshot
- [x] Role/session monitor with multi-theme UI

### 2026 Q2 &nbsp; <img src="https://img.shields.io/badge/status-complete-22c55e?style=flat-square" alt="complete"/>

- [x] Richer GitHub/PR integration (change summary linking to task artifacts)
- [x] Policy templates by repo size/risk profile
- [x] Pluggable participant adapters beyond built-in Claude/Codex/Gemini

### 2026 Q3 &nbsp; <img src="https://img.shields.io/badge/status-complete-22c55e?style=flat-square" alt="complete"/>

- [x] Branch-aware auto promotion pipeline (sandbox -> main with policy guard)
- [x] Advanced visual analytics (failure taxonomy trends, reviewer drift signals)

<br/>

## Documentation

| Document | Description |
|:---|:---|
| [`README.zh-CN.md`](README.zh-CN.md) | Chinese documentation |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | Operations guide & commands |
| [`docs/ARCHITECTURE_FLOW.md`](docs/ARCHITECTURE_FLOW.md) | System architecture deep dive |
| [`docs/API_EXPOSURE_AUDIT.md`](docs/API_EXPOSURE_AUDIT.md) | Localhost/public API exposure audit and guardrails |
| [`docs/TESTING_TARGET_POLICY.md`](docs/TESTING_TARGET_POLICY.md) | Testing approach & policy |
| [`docs/GITHUB_ABOUT.md`](docs/GITHUB_ABOUT.md) | Suggested GitHub About/description copy (EN/CN) |
| [`docs/SESSION_HANDOFF.md`](docs/SESSION_HANDOFF.md) | Session handoff notes |

<br/>

## Development

```bash
# Lint
py -m ruff check .

# Test
py -m pytest -q
```

<br/>

## Contributing

Contributions are welcome! Please ensure:

1. Code passes `ruff check .` with no warnings
2. All tests pass with `pytest -q`
3. New features include appropriate test coverage

<br/>

## License

MIT

<br/>

---

<p align="center">
  <sub>Built for teams that demand structured, observable, and safe multi-model code review workflows.</sub>
</p>

