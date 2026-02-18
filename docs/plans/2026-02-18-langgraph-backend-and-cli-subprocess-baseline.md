# LangGraph Backend and CLI Subprocess Baseline Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Switch runtime orchestration to LangGraph by default while keeping current workflow semantics and stable CLI subprocess behavior.

**Architecture:** Keep existing workflow loop logic as the authoritative execution path, then wrap it in a LangGraph compiled graph backend (`langgraph`) with a safe fallback (`classic`) when dependency is unavailable. Drive backend selection from environment/config.

**Tech Stack:** Python 3.10+, FastAPI, LangGraph, pytest, ruff.

---

### Task 1: Add backend configuration wiring

**Files:**
- Modify: `src/awe_agentcheck/config.py`
- Modify: `src/awe_agentcheck/main.py`
- Test: `tests/unit/test_config.py`
- Test: `tests/unit/test_main.py`

**Steps:**
1. Add `workflow_backend` to settings and load from `AWE_WORKFLOW_BACKEND`.
2. Default to `langgraph`; support `classic` fallback value.
3. Pass `settings.workflow_backend` into `WorkflowEngine(...)`.
4. Add tests for default/override/invalid backend config behavior.

### Task 2: Add LangGraph execution backend in workflow engine

**Files:**
- Modify: `src/awe_agentcheck/workflow.py`
- Modify: `tests/unit/test_workflow.py`

**Steps:**
1. Add optional LangGraph import guard.
2. Add backend dispatch in `WorkflowEngine.run(...)`.
3. Implement LangGraph node wrapper invoking classic execution path.
4. Add fallback to `classic` backend when LangGraph is unavailable.
5. Add workflow test for `workflow_backend='langgraph'`.

### Task 3: Dependency and docs sync

**Files:**
- Modify: `pyproject.toml`
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `docs/RUNBOOK.md`
- Modify: `docs/SESSION_HANDOFF.md`

**Steps:**
1. Add `langgraph` dependency range compatible with current ecosystem.
2. Document `AWE_WORKFLOW_BACKEND` in EN/CN readme and runbook env examples.
3. Record handoff notes and verification evidence.

### Verification Commands

```bash
py -m ruff check .
py -m pytest -q
```
