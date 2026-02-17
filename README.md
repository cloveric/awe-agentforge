<p align="center">
  <img src="docs/assets/awe-agentcheck-hero.svg" alt="AWE-AgentForge" width="100%" />
</p>

<p align="center">
  <a href="https://github.com/cloveric/awe-agentforge"><img alt="GitHub" src="https://img.shields.io/badge/GitHub-awe--agentforge-0f172a?style=for-the-badge&logo=github"></a>&nbsp;
  <a href="https://github.com/cloveric/awe-agentforge/stargazers"><img alt="GitHub Repo stars" src="https://img.shields.io/github/stars/cloveric/awe-agentforge?style=for-the-badge&logo=github&label=Stars&color=fbbf24"></a>&nbsp;
  <a href="#"><img alt="Version" src="https://img.shields.io/badge/version-0.1.0-f59e0b?style=for-the-badge"></a>&nbsp;
  <a href="#"><img alt="Python" src="https://img.shields.io/badge/Python-3.10+-3b82f6?style=for-the-badge&logo=python&logoColor=white"></a>&nbsp;
  <a href="#"><img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-009688?style=for-the-badge&logo=fastapi&logoColor=white"></a>&nbsp;
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
  <b>Production-grade multi-agent collaboration engine for real software work.</b><br/>
  <sub>Coordinate Claude, Codex, and other CLI agents to diagnose bugs, implement fixes, review each other, and continuously evolve your codebase.</sub>
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

## Latest Update (2026-02-18)

1. Default model profiles are now stronger and explicit:
   - Claude default command pins `claude-opus-4-6`
   - Codex default command uses `model_reasoning_effort=xhigh`
   - Gemini default command is normalized to `gemini --yolo`
2. Added provider model catalog endpoint (`GET /api/provider-models`) and expanded built-in model candidates so UI dropdowns are not single-option.
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

### Three Controls

| Control | Values | Default | What It Does |
|:---|:---:|:---:|:---|
| `sandbox_mode` | `0` / `1` | **`1`** | `1` = run in an isolated `*-lab` copy of the workspace; `0` = run directly in main workspace |
| `self_loop_mode` | `0` / `1` | **`0`** | `0` = pause for author approval after discussion; `1` = run autonomously end-to-end |
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
```

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
| `AWE_PARTICIPANT_TIMEOUT_SECONDS` | `240` | Max seconds a single participant (Claude/Codex/Gemini) can run per step |
| `AWE_COMMAND_TIMEOUT_SECONDS` | `300` | Max seconds for test/lint commands |
| `AWE_PARTICIPANT_TIMEOUT_RETRIES` | `1` | Retry count when a participant times out |
| `AWE_MAX_CONCURRENT_RUNNING_TASKS` | `1` | How many tasks can run simultaneously |
| `AWE_DRY_RUN` | `false` | When `true`, participants are not actually invoked |
| `AWE_SERVICE_NAME` | `awe-agentcheck` | Service name for observability |
| `AWE_OTEL_EXPORTER_OTLP_ENDPOINT` | _(none)_ | OpenTelemetry collector endpoint |

> [!NOTE]
> Without `AWE_DATABASE_URL` (or when PostgreSQL is down), the system automatically uses an in-memory database. This is fine for development and testing, but data is lost on restart.
</details>

### Step 3: Start the API Server

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "scripts/start_api.ps1" -ForceRestart
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
| `Max Rounds` | Round cap fallback when no deadline is provided | `3` |
| `Evolve Until` | Optional deadline (`YYYY-MM-DD HH:MM`) | Empty unless running overnight |
| `Max Rounds` + `Evolve Until` | Priority rule | If `Evolve Until` is set, deadline wins; if empty, `Max Rounds` is used |
| `Conversation Language` | Prompt language for agent outputs (`en` / `zh`) | `English` for logs, `中文` for Chinese collaboration |
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
  --author "claude#author-A" `
  --reviewer "codex#review-B" `
  --conversation-language en `
  --workspace-path "." `
  --auto-start
```

This will:
1. Create a task with title "Fix the login validation bug"
2. Assign Claude as the author and Codex as the reviewer
3. Use default policies (`sandbox_mode=1`, `self_loop_mode=0`, `auto_merge=1`)
4. Automatically start the task (`--auto-start`)
5. Since `self_loop_mode=0`, the system will run a discussion first, then pause at `waiting_manual` for your approval

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
| `--max-rounds` | No | `3` | Maximum discussion/review/gate rounds |
| `--test-command` | No | `py -m pytest -q` | Command to run tests |
| `--lint-command` | No | `py -m ruff check .` | Command to run linter |
| `--evolution-level` | No | `0` | `0` = fix-only, `1` = guided evolve, `2` = proactive evolve |
| `--evolve-until` | No | — | Deadline for evolution (e.g. `2026-02-13 06:00`) |
| `--conversation-language` | No | `en` | Agent output language (`en` or `zh`) |
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
2. Claude (author) generates a discussion proposal
3. Codex and Claude (reviewers) evaluate the proposal
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
| `POST` | `/api/tasks/{id}/author-decision` | Approve/reject in manual mode: `{"approve": true, "auto_start": true}` |
| `GET` | `/api/tasks/{id}/events` | Get full event timeline |
| `POST` | `/api/tasks/{id}/gate` | Submit manual gate result |
| `GET` | `/api/provider-models` | Get provider model catalog for UI dropdowns |
| `GET` | `/api/project-history` | Project-level history records (`core_findings`, `revisions`, `disputes`, `next_steps`) |
| `GET` | `/api/workspace-tree` | File tree (`?workspace_path=.&max_depth=4`) |
| `GET` | `/api/stats` | Aggregated statistics (pass rates, durations, failure buckets) |
| `GET` | `/healthz` | Health check |

<br/>

## Feature Matrix

| Capability | Description | Status |
|:---|:---|:---:|
| **Sandbox-first execution** | Default `sandbox_mode=1`, runs in `*-lab` workspace with auto-generated per-task isolation | `GA` |
| **Author-approval gate** | Default `self_loop_mode=0`, enters `waiting_manual` before implementation | `GA` |
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
2. **Start task** → system detects manual mode, runs the **discussion phase**:
   - Author (e.g. Claude) generates an implementation proposal
   - Reviewers evaluate the proposal and flag blockers
3. **Wait for human** → status becomes `waiting_manual`, task pauses
4. **Author decides**:
   - **Approve** → status becomes `queued` (with `author_approved` reason), then immediately re-starts into the full workflow
   - **Reject** → status becomes `canceled`
5. **Full workflow** runs: Discussion → Implementation → Review → Verify (test + lint) → Gate Decision
6. **Gate result**:
   - **Pass** → `passed` → Auto Fusion (merge + changelog + snapshot + sandbox cleanup)
   - **Fail** → retry next round; limit by `Evolve Until` when set, otherwise by `max_rounds`, then `failed_gate`

### Autonomous Mode (`self_loop_mode=1`)

For unattended operation:

1. **Create task** → `queued`
2. **Start task** → immediately enters the full workflow (no manual checkpoint)
3. **Round 1..N**: Discussion → Implementation → Review → Verify → Gate
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

### 2026 Q2 &nbsp; <img src="https://img.shields.io/badge/status-planned-3b82f6?style=flat-square" alt="planned"/>

- [ ] Richer GitHub/PR integration (change summary linking to task artifacts)
- [ ] Policy templates by repo size/risk profile
- [ ] Pluggable participant adapters beyond built-in Claude/Codex/Gemini

### 2026 Q3 &nbsp; <img src="https://img.shields.io/badge/status-planned-3b82f6?style=flat-square" alt="planned"/>

- [ ] Branch-aware auto promotion pipeline (sandbox -> main with policy guard)
- [ ] Advanced visual analytics (failure taxonomy trends, reviewer drift signals)

<br/>

## Documentation

| Document | Description |
|:---|:---|
| [`README.zh-CN.md`](README.zh-CN.md) | Chinese documentation |
| [`docs/RUNBOOK.md`](docs/RUNBOOK.md) | Operations guide & commands |
| [`docs/ARCHITECTURE_FLOW.md`](docs/ARCHITECTURE_FLOW.md) | System architecture deep dive |
| [`docs/API_EXPOSURE_AUDIT.md`](docs/API_EXPOSURE_AUDIT.md) | Localhost/public API exposure audit and guardrails |
| [`docs/TESTING_TARGET_POLICY.md`](docs/TESTING_TARGET_POLICY.md) | Testing approach & policy |
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

