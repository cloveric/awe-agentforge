# Architecture Flow

Date: 2026-02-12

## 1) Control Plane

```text
Operator / Script
    |
    |  REST (create/start/cancel/force-fail/query/tree)
    v
FastAPI (awe_agentcheck.api)
    |
    v
OrchestratorService
    |
    v
WorkflowEngine
```

## 2) Execution Flow (per task)

```text
create task (queued)
  -> if self_loop_mode=0:
       start task -> waiting_manual (discussion + proposal review only)
       author decision:
         approve -> queued -> start task (running)
         reject -> canceled
  -> if self_loop_mode=1:
       start task (running)
         -> round 1..N
            1) discussion (author CLI)
            2) implementation (author CLI)
            3) review (reviewer CLI(s))
            4) verify (test command + lint command)
            5) gate (medium policy)
         -> terminal: passed | failed_gate | failed_system | canceled

Task-level strategy controls:

- `evolution_level`:
  - `0` fix-only
  - `1` guided evolution
  - `2` proactive evolution
- `evolve_until`: optional discussion/evolution wall-clock deadline (reaches deadline -> graceful cancel with `deadline_reached`)
- `sandbox_mode`:
  - `1` execute in sandbox workspace (default `<project>-lab`)
  - `0` execute directly in project workspace
- `auto_merge`:
  - `1` default, auto-fusion on `passed` (merge/changelog/snapshot)
  - `0` disable fusion and keep task outputs in artifacts/sandbox only
- default sandbox allocation:
  - if sandbox path not provided, allocate unique per-task workspace:
    - `<project>-lab/<timestamp>-<id>`
  - after `passed + auto_merge_completed`, generated sandbox is auto-cleaned
- `self_loop_mode`:
  - `0` discuss/review first, wait author confirmation before implementation (default)
  - `1` fully autonomous loop
```

## 3) Participant Model

- ID format: `provider#alias`
- Examples:
  - `claude#author-A`
  - `codex#review-B`
  - `gemini#review-C` (cross-provider review role)
- Supports cross-provider and same-provider multi-session review topologies.

## 4) Overnight Loop (auto-evolve)

```text
start_overnight_until_7.ps1
  -> start/reuse API
  -> launch overnight_autoevolve.py
      -> create auto-start task
      -> wait terminal
      -> append overnight markdown log
      -> repeat until deadline
```

## 5) Resilience Rules

- Single-instance lock: prevents duplicate overnight runners.
- Concurrency cap: limits simultaneously running tasks.
- Fallback switching:
  - Claude-side system failure -> switch to Codex fallback.
  - Codex-side system failure (`command_timeout`/`command_not_found`/`provider_limit`) -> switch back to primary.
- Provider-limit cooldown:
  - Claude `provider_limit` triggers temporary primary disable window.
- Watchdog timeout:
  - If a task exceeds `task-timeout-seconds`, runner issues cancel + `force-fail` (`watchdog_timeout`) to unblock progression.

## 6) Observability Surfaces

- API: `/api/stats`
  - `status_counts`
  - `reason_bucket_counts`
  - `provider_error_counts`
  - recent terminal rates and mean duration
- API: `/api/workspace-tree` for project file structure
- API: `/api/tasks/{task_id}/author-decision` for manual approve/reject in waiting state
- Web console: `http://127.0.0.1:8000/`
- Artifacts per task: `.agents/threads/<task_id>/`
- Overnight logs: `.agents/overnight/`

## 7) Monitor UI Layout

```text
Left column
  top    -> Project structure tree (directories + files)
  bottom -> Roles / sessions (participant grouped)

Right column
  top    -> scope + task controls
  middle -> dialogue stream
  bottom -> task creation
```
