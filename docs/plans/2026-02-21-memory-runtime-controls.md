# Memory System + Runtime Controls Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

## Completion Status (2026-02-21)

Implemented in one integrated batch (no phased rollout):

1. Memory service landed (`service_layers/memory.py`) with durable JSON store, query/rank, stage context recall, pin/clear, and outcome persistence.
2. Orchestrator/workflow hooks landed:
   - pre-start memory preload and `memory_hit` events
   - post-outcome memory persistence and `memory_persisted` events
   - per-phase runtime budgets (`proposal/discussion/implementation/review/command`)
3. Metadata + API + CLI landed:
   - task fields: `memory_mode`, `phase_timeout_seconds`
   - API endpoints: `/api/memory`, `/api/memory/query`, `/api/memory/pin`, `/api/memory/clear`
   - CLI flags: `--memory-mode`, `--phase-timeout phase=seconds`
4. Web dashboard landed:
   - Create Task controls for memory mode + phase timeouts
   - policy-template apply wiring for those fields
   - task snapshot rendering for memory/runtime controls
   - help page EN/ZH updated
5. Verification complete:
   - `py -m ruff check .`
   - `py -m mypy src`
   - `py -m bandit -q -r src -lll`
   - `py -m pytest -q`
   - `py -m pytest --cov=src --cov-fail-under=85 -q` (89.78%)

**Goal:** Implement production-grade agent memory (pre-hook retrieval + post-hook persistence), per-phase runtime budgets, and full API/Web controls in one integrated delivery.

**Architecture:** Add a durable MemoryService backed by artifact-root JSON storage with governance rules (verified-write, evidence paths, TTL/pinning). Wire memory retrieval into proposal/workflow prompts and persist outcome memories on completion. Extend task metadata/options to include `memory_mode` and `phase_timeout_seconds`, then expose through API/CLI/Web.

**Tech Stack:** Python (FastAPI, service layers), existing repository/artifact abstractions, JS dashboard modules, pytest.

---

### Task 1: Add Memory Service and Durable Store

**Files:**
- Create: `src/awe_agentcheck/service_layers/memory.py`
- Modify: `src/awe_agentcheck/service_layers/__init__.py`
- Test: `tests/unit/test_service_layers.py`

**Step 1: Write failing tests**
- Add tests for memory query ranking, pin/clear behavior, TTL cleanup, and verified-write filtering.

**Step 2: Implement store/service**
- Implement JSON-backed memory store under `.agents/memory/entries.json`.
- Support memory types: `session`, `preference`, `semantic`, `failure`.
- Add APIs: list, query, pin, clear, persist_task_preferences, persist_task_outcome, build_stage_context.

**Step 3: Run focused tests**
- Run: `py -m pytest -q tests/unit/test_service_layers.py`

### Task 2: Wire Memory into Orchestrator + Workflow Hooks

**Files:**
- Modify: `src/awe_agentcheck/service.py`
- Modify: `src/awe_agentcheck/workflow.py`
- Modify: `src/awe_agentcheck/workflow_prompting.py`
- Modify: `src/awe_agentcheck/proposal_helpers.py`
- Modify: `src/awe_agentcheck/domain/events.py`
- Test: `tests/unit/test_service.py`, `tests/unit/test_workflow.py`

**Step 1: Add task/runtime fields**
- Extend `CreateTaskInput`, `TaskView`, and `RunConfig` with `memory_mode` and `phase_timeout_seconds`.

**Step 2: Pre-hook retrieval**
- In `start_task` path build stage memory context for `proposal/discussion/implementation/review`.
- Emit memory-hit events.

**Step 3: Post-hook persistence**
- Persist session/failure/semantic memories from final outcome + evidence artifacts.
- Emit memory persisted events and avoid task failure when memory persistence fails.

**Step 4: Per-phase timeouts**
- Resolve and apply phase timeouts for proposal/discussion/implementation/review/verification commands.

**Step 5: Run focused tests**
- Run: `py -m pytest -q tests/unit/test_service.py tests/unit/test_workflow.py`

### Task 3: Metadata + API + CLI Surface

**Files:**
- Modify: `src/awe_agentcheck/task_options.py`
- Modify: `src/awe_agentcheck/repository.py`
- Modify: `src/awe_agentcheck/db.py`
- Modify: `src/awe_agentcheck/api.py`
- Modify: `src/awe_agentcheck/cli.py`
- Test: `tests/unit/test_api.py`, `tests/unit/test_task_options.py`, `tests/unit/test_repository_meta.py`

**Step 1: Add option normalization**
- Add `normalize_memory_mode` and `normalize_phase_timeout_seconds`.

**Step 2: Persist new task metadata**
- Encode/decode/read/write fields in repository/db paths.

**Step 3: Add API endpoints**
- Add `/api/memory`, `/api/memory/query`, `/api/memory/pin`, `/api/memory/clear`.

**Step 4: Add CLI options**
- `--memory-mode off|basic|strict`
- `--phase-timeout phase=seconds` (repeatable)

**Step 5: Run focused tests**
- Run: `py -m pytest -q tests/unit/test_api.py tests/unit/test_task_options.py tests/unit/test_repository_meta.py`

### Task 4: Web UI + Help + Policy Template Wiring

**Files:**
- Modify: `web/index.html`
- Modify: `web/assets/dashboard.js`
- Modify: `web/assets/modules/ui.js`
- Modify: `web/assets/modules/create_task_help.js`
- Modify: `web/assets/modules/store.js`
- Modify: `src/awe_agentcheck/policy_templates.py`
- Test: `tests/unit/test_dashboard_static.py`

**Step 1: Create Task controls**
- Add `Memory Mode` select and `Phase Timeouts` input.
- Include values in create payload.

**Step 2: Task snapshot and policy apply**
- Render memory fields in task snapshot.
- Apply defaults from policy template.

**Step 3: Help updates EN/ZH**
- Document memory and phase timeout semantics.

**Step 4: Static UI validation tests**
- Run: `py -m pytest -q tests/unit/test_dashboard_static.py`

### Task 5: Docs + Full Verification

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/RUNBOOK.md`
- Modify: `docs/SESSION_HANDOFF.md`

**Step 1: Update docs**
- Add memory architecture, controls, APIs, and governance behavior.

**Step 2: Full test pass**
- Run: `py -m pytest -q`

**Step 3: Coverage sanity**
- Run: `py -m pytest --cov=src --cov-fail-under=85`

**Step 4: Commit**
- `git add ...`
- `git commit -m "feat: land memory system with runtime hooks and controls"`
