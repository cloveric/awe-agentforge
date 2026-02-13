<p align="center">
  <img src="docs/assets/awe-agentcheck-hero.svg" alt="awe-agentcheck hero" width="100%" />
</p>

<p align="center">
  <a href="https://github.com/cloveric/awe-agentcheck"><img alt="GitHub" src="https://img.shields.io/badge/GitHub-awe--agentcheck-0f172a?style=for-the-badge&logo=github"></a>
  <a href="#"><img alt="Python" src="https://img.shields.io/badge/Python-3.11+-2563eb?style=for-the-badge&logo=python&logoColor=white"></a>
  <a href="#"><img alt="FastAPI" src="https://img.shields.io/badge/FastAPI-Control%20Plane-0891b2?style=for-the-badge&logo=fastapi&logoColor=white"></a>
  <a href="#"><img alt="Sandbox First" src="https://img.shields.io/badge/Default-Sandbox%20First-1d4ed8?style=for-the-badge"></a>
  <a href="#"><img alt="Author Gate" src="https://img.shields.io/badge/Default-Author%20Approval-f97316?style=for-the-badge"></a>
  <a href="#"><img alt="Observability" src="https://img.shields.io/badge/Observability-OTel%20%7C%20Prom%20%7C%20Loki%20%7C%20Tempo-16a34a?style=for-the-badge"></a>
</p>

<p align="center">
  <b>Professional multi-CLI orchestration for author/reviewer agent engineering.</b><br/>
  Multi-provider, sandbox-first, observable, and production-oriented.
</p>

<p align="center">
  <a href="README.zh-CN.md"><b>中文文档 / Chinese README</b></a>
</p>

---

## Table of Contents

- [What is awe-agentcheck](#what-is-awe-agentcheck)
- [Visual Overview](#visual-overview)
- [Feature Matrix](#feature-matrix)
- [Task Strategy Modes](#task-strategy-modes)
- [Quick Start](#quick-start)
- [CLI](#cli)
- [API](#api)
- [Roadmap](#roadmap)
- [Documentation](#documentation)

## What is awe-agentcheck

`awe-agentcheck` is an orchestration control plane for structured AI engineering loops:

`discussion -> implementation -> review -> verification -> gate`

Designed for:

- Claude reviewing Codex outputs
- Codex reviewing Claude outputs
- same CLI, different sessions cross-checking
- overnight autonomous improvement with strict safety controls

## Visual Overview

### 1) Dashboard Preview

<p align="center">
  <img src="docs/assets/dashboard-preview.svg" alt="dashboard preview" width="100%" />
</p>

### 2) Runtime Flow

<p align="center">
  <img src="docs/assets/workflow-flow.svg" alt="workflow flow" width="100%" />
</p>

## Feature Matrix

| Capability | Description | Status |
|---|---|---|
| Sandbox-first execution | Default `sandbox_mode=1` runs in `*-lab` workspace | `GA` |
| Author-approval gate | Default `self_loop_mode=0`, waits `waiting_manual` before implementation | `GA` |
| Autonomous self-loop | `self_loop_mode=1` for unattended operation | `GA` |
| Auto fusion | On pass, optional merge + `CHANGELOG.auto.md` + snapshot | `GA` |
| Multi-provider role model | `provider#alias` participants (cross-provider or same-provider multi-session) | `GA` |
| Operator web console | Project tree (expand/collapse), roles/sessions, avatar-based chat dialogue, control panel | `GA` |
| Theme system | Neon, Terminal Pixel, Executive Glass | `GA` |
| Observability stack | OTel, Prometheus, Loki, Tempo, Grafana | `GA` |
| Overnight supervisor | Timeout watchdog, provider fallback, cooldown control | `GA` |

## Task Strategy Modes

| Control | Values | Default | Effect |
|---|---|---|---|
| `sandbox_mode` | `0` / `1` | `1` | run in main workspace or lab workspace |
| `self_loop_mode` | `0` / `1` | `0` | manual author approval or autonomous loop |
| `auto_merge` | `0` / `1` | `1` | auto-fusion artifacts on passed tasks |

Manual policy flow (`self_loop_mode=0`):

1. Start task
2. Generate discussion/proposal review
3. Move to `waiting_manual`
4. Author approves or rejects
5. Approve => queue/start implementation; Reject => canceled

Sandbox default behavior:

1. If `sandbox_workspace_path` is not provided, system creates a unique per-task sandbox under `<project>-lab/<timestamp>-<id>`.
2. If task passes and auto-fusion completes, generated sandbox is auto-cleaned to prevent run-to-run contamination.
3. If custom `sandbox_workspace_path` is provided, sandbox is retained by default.

## Quick Start

```powershell
cd C:/Users/hangw/awe-agentcheck
py -m pip install -e .[dev]
```

Start API:

```powershell
$env:AWE_DATABASE_URL="postgresql+psycopg://postgres:postgres@localhost:5432/awe_agentcheck"
$env:AWE_ARTIFACT_ROOT="C:/Users/hangw/awe-agentcheck/.agents"
$env:AWE_CLAUDE_COMMAND="claude -p --dangerously-skip-permissions --effort low"
$env:AWE_CODEX_COMMAND="codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=low"
$env:AWE_PARTICIPANT_TIMEOUT_SECONDS="240"
$env:AWE_COMMAND_TIMEOUT_SECONDS="300"
$env:AWE_PARTICIPANT_TIMEOUT_RETRIES="1"
$env:AWE_MAX_CONCURRENT_RUNNING_TASKS="1"
$env:PYTHONPATH="C:/Users/hangw/awe-agentcheck/src"
py -m uvicorn awe_agentcheck.main:app --reload --port 8000
```

Open web monitor:

- `http://localhost:8000/`

## CLI

Default policy (sandbox + author approval):

```powershell
py -m awe_agentcheck.cli run `
  --task "Improve monitor signal quality" `
  --author "claude#author-A" `
  --reviewer "codex#review-B" `
  --reviewer "claude#review-C" `
  --sandbox-mode 1 `
  --self-loop-mode 0 `
  --workspace-path "C:/Users/hangw/awe-agentcheck" `
  --auto-start
```

Approve and start:

```powershell
py -m awe_agentcheck.cli decide task-1 --approve --auto-start
```

Autonomous direct-main run:

```powershell
py -m awe_agentcheck.cli run `
  --task "Autonomous run" `
  --author "codex#author-A" `
  --reviewer "claude#review-B" `
  --sandbox-mode 0 `
  --self-loop-mode 1 `
  --workspace-path "C:/Users/hangw/awe-agentcheck" `
  --auto-start
```

## API

- `POST /api/tasks`
- `GET /api/tasks`
- `GET /api/tasks/{task_id}`
- `POST /api/tasks/{task_id}/start`
- `POST /api/tasks/{task_id}/cancel`
- `POST /api/tasks/{task_id}/force-fail`
- `POST /api/tasks/{task_id}/author-decision`
- `GET /api/tasks/{task_id}/events`
- `GET /api/workspace-tree`
- `GET /api/stats`

## Roadmap

### 2026 Q1

- [x] sandbox-first default policy
- [x] author-approval gate
- [x] auto-fusion + changelog + snapshot
- [x] role/session monitor with multi-theme UI

### 2026 Q2

- [ ] richer GitHub/PR integration (change summary linking to task artifacts)
- [ ] policy templates by repo size/risk profile
- [ ] pluggable participant adapters beyond Codex/Claude

### 2026 Q3

- [ ] branch-aware auto promotion pipeline (sandbox -> main with policy guard)
- [ ] advanced visual analytics (failure taxonomy trends, reviewer drift signals)

## Documentation

- `README.zh-CN.md`
- `docs/RUNBOOK.md`
- `docs/ARCHITECTURE_FLOW.md`
- `docs/TESTING_TARGET_POLICY.md`
- `docs/SESSION_HANDOFF.md`
- `docs/plans/2026-02-13-sandbox-and-author-gate.md`

## Verification

```powershell
py -m ruff check .
py -m pytest -q
```
