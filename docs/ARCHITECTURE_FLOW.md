# Architecture Flow

Date: 2026-02-19

## 1) Control Plane

```text
Operator / Script
    |
    |  REST (create/start/cancel/force-fail/query/tree/history/history-clear/policy/analytics/github-summary/promote-round)
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
       start task -> proposal consensus rounds (running)
         when debate_mode=1:
           1) reviewer precheck pass
           2) author proposal/reply
           3) reviewer proposal review
        round counted only on reviewer consensus
        same-round retry until consensus, with stall guards:
          - 10+ retries in one round -> waiting_manual (proposal_consensus_stalled_in_round)
          - same issue signature repeated across 4+ consensus rounds -> waiting_manual (proposal_consensus_stalled_across_rounds)
          - reviewer outputs fully unavailable still fail fast (proposal_precheck_unavailable / proposal_review_unavailable)
       after target consensus rounds -> waiting_manual
       author decision:
         approve -> queued -> start task (running, full workflow)
         reject -> canceled
  -> if self_loop_mode=1:
       start task (running)
         -> round 1..N
            1) reviewer-first debate/precheck (optional, debate_mode=1)
            2) discussion (author CLI)
            3) implementation (author CLI)
            4) review (reviewer CLI(s))
            5) verify (test command + lint command)
            6) gate (medium policy)
         -> terminal: passed | failed_gate | failed_system | canceled

Task-level strategy controls:

- `evolution_level`:
  - `0` fix-only
  - `1` guided evolution
  - `2` proactive evolution
- `repair_mode`:
  - `minimal` smallest safe patch
  - `balanced` root-cause + focused scope (default)
  - `structural` allows deeper refactor
- `evolve_until`: optional discussion/evolution wall-clock deadline (reaches deadline -> graceful cancel with `deadline_reached`)
- precedence rule:
  - if `evolve_until` is set, deadline is primary stop condition
  - if `evolve_until` is empty, `max_rounds` is used
- `sandbox_mode`:
  - `1` execute in sandbox workspace (default `<project>-lab`)
  - `0` execute directly in project workspace
- `auto_merge`:
  - `1` default, auto-fusion on `passed` (merge/changelog/snapshot)
  - `0` disable fusion and keep task outputs in artifacts/sandbox only
- multi-round candidate mode:
  - when `max_rounds>1` and `auto_merge=0`, service enforces fresh sandbox isolation
  - per-round artifacts are captured at gate events (`round-N.patch`, `round-N.md`, round snapshots)
  - terminal task can use manual `promote-round` to fuse one selected round
- default sandbox allocation:
  - if sandbox path not provided, allocate unique per-task workspace:
    - `<project>-lab/<timestamp>-<id>`
  - after `passed + auto_merge_completed`, generated sandbox is auto-cleaned
- `self_loop_mode`:
  - `0` proposal consensus rounds first, then wait author confirmation before implementation (default)
  - `1` fully autonomous loop
- `plain_mode`:
  - `1` beginner-readable output style (default)
  - `0` raw technical style
- `stream_mode`:
  - `1` emit realtime participant stream chunks (default)
  - `0` emit stage-level outputs only
- `debate_mode`:
  - `1` enable reviewer-first debate stages (default)
  - `0` skip debate stages
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
- API: `/api/analytics`
  - failure taxonomy
  - failure taxonomy trend
  - reviewer global/drift indicators
- API: `/api/policy-templates`
  - workspace profile (`repo_size`, `risk_level`, markers)
  - recommended control presets for create-task form
- API: `/api/tasks/{task_id}/github-summary`
  - PR-ready markdown summary + artifact links
- API: `/api/workspace-tree` for project file structure
- API: `/api/project-history` for project-level historical ledger:
  - `core_findings`
  - `revisions`
  - `disputes`
  - `next_steps`
- API: `/api/project-history/clear` for scoped history cleanup
- API: `/api/tasks/{task_id}/author-decision` for manual approve/reject in waiting state
- API: `/api/tasks/{task_id}/promote-round` for selected-round fusion in multi-round candidate mode
- Web console: `http://127.0.0.1:8000/`
- Artifacts per task: `.agents/threads/<task_id>/`
- Round artifacts: `.agents/threads/<task_id>/artifacts/rounds/`
- Overnight logs: `.agents/overnight/`

## 7) Monitor UI Layout

```text
Left column
  top    -> Project structure tree (directories + files)
  bottom -> Roles / sessions (participant grouped)

Right column
  top    -> scope + task controls
  middle -> dialogue stream
  lower  -> project history ledger
  bottom -> task creation
```

## 8) Persistence Defaults

- If `AWE_DATABASE_URL` is unset, startup scripts default to local SQLite:
  - `.agents/runtime/awe-agentcheck.sqlite3`
- This keeps project/task history across API restarts.

## 9) Promotion Guard

- Auto-fusion and manual `promote-round` both execute guard evaluation before writing to target path.
- Guard defaults are configurable by env:
  - `AWE_PROMOTION_GUARD_ENABLED`
  - `AWE_PROMOTION_ALLOWED_BRANCHES`
  - `AWE_PROMOTION_REQUIRE_CLEAN`
- Guard check emits `promotion_guard_checked`; blocked promotions return explicit guard reason.
