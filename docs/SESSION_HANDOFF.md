# Session Handoff (2026-02-12)

## Update (2026-02-21, memory/runtime controls integrated closeout)

1. Landed durable memory layer:
   - `src/awe_agentcheck/service_layers/memory.py`
   - storage path: `.agents/memory/entries.json`
   - supports query/rank, stage-context build, preference/outcome persistence, pin/clear.
2. Landed orchestration/runtime integration:
   - `RunConfig` + task metadata now carry `memory_mode` and `phase_timeout_seconds`.
   - workflow applies per-phase timeout budgets (`proposal/discussion/implementation/review/command`).
   - prompt extras now support stage memory context injection.
   - new events: `memory_hit`, `memory_persisted`.
3. Landed API + CLI + Web surfaces:
   - API: `/api/memory`, `/api/memory/query`, `/api/memory/pin`, `/api/memory/clear`.
   - CLI: `run --memory-mode ... --phase-timeout phase=seconds`.
   - Web Create Task: Memory Mode + Phase Timeouts controls; task snapshot now displays both.
4. Policy templates expanded with runtime defaults:
   - each template now includes `memory_mode` and `phase_timeout_seconds` defaults.
5. Verification completed:
   - `py -m ruff check .`
   - `py -m mypy src`
   - `py -m bandit -q -r src -lll`
   - `py -m pytest -q`
   - `py -m pytest --cov=src --cov-fail-under=85 -q` (89.78%)

## Update (2026-02-21, full completion closeout for remaining 1-15 items)

1. Remaining staged items are now fully closed:
   - task create persistence is now record-only (`TaskCreateRecord` -> `create_task_record`), legacy long-arg repo/db create path removed.
   - task option/model/override normalization is centralized in `src/awe_agentcheck/task_options.py` and reused by service/task-management.
2. Workflow split completed further:
   - added `src/awe_agentcheck/workflow_architecture.py` (architecture audit + env context helpers).
   - added `src/awe_agentcheck/workflow_prompting.py` (prompt template loading/rendering + extras).
   - added `src/awe_agentcheck/workflow_runtime.py` (runtime model/param/override normalization + resolver helpers).
   - added `src/awe_agentcheck/workflow_text.py` (clip/signature helpers).
   - `workflow.py` reduced to threshold-safe size for architecture hard-gate.
3. Event and exception hardening:
   - remaining core event payload literals migrated to `EventType.*.value`.
   - broad catches in parse/convert paths narrowed to typed exceptions; orchestration boundary catches now log explicitly.
4. Stability guard:
   - subprocess env now strips inherited pytest/coverage variables (`COVERAGE_PROCESS_START`, `COV_CORE_*`, `PYTEST_*`) to prevent coverage pollution during internal task command execution.
5. Frontend modularization extended:
   - new `web/assets/modules/formatters.js`.
   - `web/assets/dashboard.js` reduced below architecture threshold and keeps existing behavior.
6. Verification:
   - `py -m ruff check .`
   - `py -m mypy src`
   - `py -m bandit -q -r src -lll`
   - `py -m pytest --cov=src --cov-report=term-missing --cov-fail-under=80 -q` (passes with coverage gate).

## Update (2026-02-20, one-shot 1-15 hardening batch)

1. Security/runtime hardening completed:
   - strict artifact filename sanitization in `src/awe_agentcheck/storage/artifacts.py`.
   - API rate limiter for `/api/*` (`AWE_API_RATE_LIMIT_PER_MINUTE`, `429 too_many_requests`, `x-rate-limit-*` headers).
   - observability bootstrap made thread-safe and idempotent.
2. Event taxonomy formalized:
   - added `src/awe_agentcheck/domain/events.py` (`EventType` + `normalize_event_type`).
   - repository and SQL event writes now normalize event type values.
3. Service/frontend monolith reduction:
   - extracted evidence logic into `src/awe_agentcheck/service_layers/evidence.py` and delegated from `service.py`.
   - extracted dialogue rendering/text normalization to `web/assets/modules/dialogue.js`; `dashboard.js` reduced.
4. Data + CI baseline upgrades:
   - DB composite indexes added for tasks and task_events hot query paths.
   - CI now runs `mypy src`, `bandit -lll`, and `pytest --cov=src --cov-fail-under=80`.
   - dependency ranges now include upper bounds.
5. Integrations/deployment:
   - added `tests/integration/test_orchestrator_flow.py` (pass path + architecture hard-fail path).
   - added `Dockerfile` + `.dockerignore`.
6. Verification completed:
   - `py -m ruff check .`
   - `py -m mypy src`
   - `py -m bandit -q -r src -lll`
   - `py -m pytest --cov=src --cov-report=term-missing --cov-fail-under=80 -q`

## Update (2026-02-20, adapters package split + dashboard module split + post-refactor runtime fix)

1. Adapter runtime layer is now physically package-split:
   - moved from single-file `src/awe_agentcheck/adapters.py` to:
     - `src/awe_agentcheck/adapters/base.py`
     - `src/awe_agentcheck/adapters/factory.py`
     - `src/awe_agentcheck/adapters/runner.py`
     - `src/awe_agentcheck/adapters/claude.py`
     - `src/awe_agentcheck/adapters/codex.py`
     - `src/awe_agentcheck/adapters/gemini.py`
     - `src/awe_agentcheck/adapters/__init__.py` (compatibility exports for existing imports/tests).
2. Dashboard module split expanded beyond the initial core modules:
   - added:
     - `web/assets/modules/avatar.js`
     - `web/assets/modules/tree.js`
     - `web/assets/modules/history.js`
   - `web/assets/dashboard.js` now delegates avatar rendering, tree rendering, and history rendering/clear flows to modules.
3. Post-refactor runtime bug fixed:
   - restored missing Create Task help handlers in `web/assets/dashboard.js`:
     - `renderCreateHelp`
     - `setCreateHelpCollapsed`
     - `setCreateHelpLanguage`
   - this resolved browser runtime `ReferenceError` during dashboard initialization.
4. Verification completed:
   - `py -m pytest -q tests/unit`
   - `py scripts/selftest_local_smoke.py --port 8011 --health-timeout-seconds 60 --task-timeout-seconds 240`
   - browser console check on `http://127.0.0.1:8000/` (runtime error removed; only non-blocking accessibility issues remain).
5. Docs/assets sync:
   - `README.md`, `README.zh-CN.md`
   - `docs/ARCHITECTURE_FLOW.md`
   - `docs/GITHUB_ABOUT.md`
   - architecture/dashboard/workflow SVG copy refreshed to match current module layout and flow semantics.

## Update (2026-02-20, evolution level 3 + frontier template + author openness tuning)

1. End-to-end support for `evolution_level=3` is now enabled:
   - API schema: `CreateTaskRequest.evolution_level` accepts `0..3`.
   - CLI: `--evolution-level` choices expanded to `0|1|2|3`.
   - task normalization/storage/service/workflow clamps expanded to `min(3, ...)`.
   - overnight scripts updated (`ValidateRange(0,3)` / payload clamp to 3).
2. New policy template added:
   - `frontier-evolve` in `src/awe_agentcheck/policy_templates.py`
   - defaults: sandbox on, self-loop on, auto-merge on, rounds=4, repair=`structural`, `evolution_level=3`.
3. Prompt behavior for level 3:
   - discussion/implementation/review guidance now includes aggressive proactive evolution cues
     (feature ideas, framework/runtime upgrades, UI/UX improvements).
   - review checklist expanded for level 3 with opportunity mapping and impact/risk/effort/verification requirements.
4. Author openness tuning (level 3 only):
   - proposal-author prompt now allows `1..3` optional proactive evolution candidates.
   - bug/blocker baseline remains unchanged (`BLOCKER` still tied to correctness/regression/security/data-loss risks).
5. UI/docs/tests sync:
   - Web `Evolution Level` dropdown now includes `3 | frontier evolve (aggressive)`.
   - Create Task help includes level-3 and `frontier-evolve` descriptions.
   - README EN/CN + architecture docs updated.
   - tests added/updated across `test_cli.py`, `test_api.py`, `test_service.py`, `test_workflow.py`.

## Update (2026-02-20, service package split + prompt templates + langgraph per-round)

1. `service_layers` monolith was replaced by a package:
   - removed `src/awe_agentcheck/service_layers.py`
   - added:
     - `src/awe_agentcheck/service_layers/analytics.py`
     - `src/awe_agentcheck/service_layers/history.py`
     - `src/awe_agentcheck/service_layers/task_management.py`
     - `src/awe_agentcheck/service_layers/__init__.py`
2. History dependency wiring is now consolidated:
   - introduced `HistoryDeps` dataclass in `src/awe_agentcheck/service_layers/history.py`
   - `OrchestratorService` now passes one dependency object instead of 5+ callback parameters.
3. Workspace fingerprint/sandbox helper duplication was removed:
   - `OrchestratorService` helper methods now delegate to `TaskManagementService` implementations.
   - this keeps one source of truth for:
     - sandbox path generation
     - sandbox bootstrap ignore rules
     - workspace fingerprint signatures
4. Prompt assembly was externalized into template files:
   - added `src/awe_agentcheck/prompt_templates/*.txt`
   - workflow/service prompt builders now render from template files via shared loader (`WorkflowEngine._render_prompt_template`).
5. LangGraph flow is now true per-round progression:
   - round node runs exactly one round (`max_rounds=1` override + `force_single_round`)
   - graph loops between `round -> round` until finish criteria are met, instead of single-node full classic execution.
   - loop-tracker state is persisted across LangGraph rounds to preserve strategy-shift detection behavior.
6. Frontend maintainability updates:
   - added grouped panel structure in `initElements()` (`project`, `summary`, `history`, `controls`, `create`, `providers`) with backward-compatible flat aliases.
   - moved Create Task help content to `web/assets/modules/create_task_help.js`.
   - provider model dropdown state is now backend-driven first (`/api/provider-models`) with runtime cache merge; no hardcoded default model catalog in state bootstrap.
7. Verification:
   - `py -m ruff check src tests`
   - `py -m pytest -q`
   - `py -m pytest tests/unit/test_packaging_smoke.py -q`

## Update (2026-02-20, strategy adapters + service-layer split + dashboard modules)

1. Provider runtime adapter refactor completed:
   - `ParticipantRunner` now dispatches via `ProviderFactory` and provider-specific strategy adapters.
   - behavior parity retained for model flags, team/multi-agent toggles, prompt handling, and output normalization.
2. Service responsibilities are now deeply extracted from `OrchestratorService`:
   - new service layer package (`src/awe_agentcheck/service_layers/`) with:
     - `AnalyticsService`
     - `HistoryService`
     - `TaskManagementService`
   - `TaskManagementService` is now a real dependency-driven class (repository/artifact_store/validation class), not callback dataclass wiring.
   - `OrchestratorService` removed `_create_task_impl` / `_list_tasks_impl` / `_get_task_impl`; public methods now call the task-management layer.
3. Web dashboard modularization deepened:
   - `web/assets/dashboard.js` now imports:
      - `modules/api.js`
      - `modules/store.js`
      - `modules/utils.js`
      - `modules/ui.js`
   - moved state/theme/api-health + participant-draft pruning into `modules/store.js`.
   - moved DOM element initialization + participant capability matrix rendering into `modules/ui.js`.
   - `web/index.html` uses module loading (`type="module"`).
4. Stability fixes validated during integration:
   - dry-run output now includes evidence paths to satisfy precompletion gate in smoke runs.
   - LangGraph noop callbacks changed to `def` functions to satisfy `ruff` and avoid lint regressions.
5. Verification:
   - `pytest -q tests/unit`
   - `py -m ruff check .`
   - `py scripts/selftest_local_smoke.py --port 8011 --health-timeout-seconds 40 --task-timeout-seconds 120`
6. Current local verification status:
   - `py -m ruff check src tests/unit`
   - `pytest -q tests/unit/test_service.py tests/unit/test_api.py tests/unit/test_workflow.py tests/unit/test_adapters.py`
   - `pytest -q tests/unit`

## Update (2026-02-19, adapter structured errors + expanded architecture hard gate + cross-platform scripts)

1. Adapter runtime failure handling is now structured instead of hard-raising:
   - `ParticipantRunner.run` now returns `AdapterResult(verdict=unknown, next_action=stop, returncode=2)` for known runtime failures:
     - `command_not_configured`
     - `command_not_found`
     - `command_timeout`
     - `provider_limit`
     - `command_failed` (non-zero process exit)
   - keeps reason text in `output` for deterministic downstream gating/analytics buckets.
2. Workflow now hard-gates author runtime failures by phase:
   - if discussion/implementation returns runtime-failure result, workflow exits `failed_gate` with explicit reason (`command_timeout`, `command_not_found`, etc.).
   - reviewer runtime-failure results (non-exception path) now emit `review_error` and downgrade verdict to `unknown`.
3. Architecture audit hard-rule coverage expanded:
   - new violation kinds:
     - `service_monolith_too_large`
     - `workflow_monolith_too_large`
     - `dashboard_monolith_too_large`
     - `prompt_assembly_hotspot`
     - `adapter_runtime_raise_detected`
   - new env overrides:
     - `AWE_ARCH_PYTHON_FILE_LINES_MAX`
     - `AWE_ARCH_FRONTEND_FILE_LINES_MAX`
     - `AWE_ARCH_RESPONSIBILITY_KEYWORDS_MAX`
     - `AWE_ARCH_SERVICE_FILE_LINES_MAX`
     - `AWE_ARCH_WORKFLOW_FILE_LINES_MAX`
     - `AWE_ARCH_DASHBOARD_JS_LINES_MAX`
     - `AWE_ARCH_PROMPT_BUILDER_COUNT_MAX`
     - `AWE_ARCH_ADAPTER_RUNTIME_RAISE_MAX`
4. Cross-platform script coverage implemented:
   - added shell equivalents:
     - `scripts/start_api.sh`, `scripts/stop_api.sh`
     - `scripts/start_overnight_until_7.sh`, `scripts/stop_overnight.sh`
     - `scripts/supervise_until.sh`
   - added `.env.example` as centralized env baseline for runtime/policy/audit vars.
5. Tests added/updated:
   - `tests/unit/test_adapters.py`: runtime failures now assert structured results instead of exceptions.
   - `tests/unit/test_workflow.py`: added author runtime hard-gate tests + architecture env-override/script-gap/adapter-raise coverage.
6. Verification:
   - `py -m ruff check src tests/unit scripts`
   - `py -m pytest tests/unit/test_adapters.py tests/unit/test_workflow.py -q`
   - `py -m pytest tests/unit/test_service.py -q`
   - `py -m pytest tests/unit -q`

## Update (2026-02-19, reviewer-json + architecture-audit + registry + split-web + langgraph-nodes)

1. Reviewer control parsing hardened (P0):
   - `adapters.py` now supports JSON control schema first:
     - `{"verdict":"NO_BLOCKER|BLOCKER|UNKNOWN","next_action":"pass|retry|stop",...}`
   - legacy `VERDICT:` / `NEXT_ACTION:` regex parsing remains as fallback.
2. `architecture_audit` stage added (P0):
   - workflow now emits `architecture_audit` event each round with:
     - Python/frontend LOC thresholds
     - mixed-responsibility heuristic for large Python files
     - scripts cross-platform coverage gap (`.ps1` without matching `.sh`)
   - enforcement mode:
     - `AWE_ARCH_AUDIT_MODE=off|warn|hard`
     - default fallback: `warn` for `evolution_level=1`, `hard` for `evolution_level>=2`
3. Adapter provider registry refactor (P1):
   - command template/model-flag/capabilities now come from a provider registry.
   - `AWE_PROVIDER_ADAPTERS_JSON` providers are auto-registered with defaults.
4. Progressive split started (P1):
   - policy templates moved to `src/awe_agentcheck/policy_templates.py` (service behavior unchanged).
   - dashboard inline payload split:
     - `web/assets/dashboard.css`
     - `web/assets/dashboard.js`
   - API now serves static assets at `GET /web/assets/{asset_name}` with traversal guard.
5. LangGraph integration moved off single-node wrapper:
   - backend graph now uses multi-node flow: `preflight -> execute -> finalize` with conditional routing.
6. Verified:
   - `py -m pytest -q tests/unit/test_adapters.py tests/unit/test_workflow.py tests/unit/test_api.py`
   - `py -m ruff check src/awe_agentcheck/adapters.py src/awe_agentcheck/workflow.py src/awe_agentcheck/service.py src/awe_agentcheck/api.py src/awe_agentcheck/policy_templates.py tests/unit/test_adapters.py tests/unit/test_workflow.py tests/unit/test_api.py`

## Update (2026-02-19, hardening 1-5: head-sha/risk/preflight/evidence/regression loop)

1. Added start-path singleflight dedupe:
   - `OrchestratorService.start_task` now guards concurrent duplicate starts.
   - duplicate in-flight calls emit `start_deduped` and do not launch a second run.
2. Added preflight risk gate before consensus/execution:
   - `start_task` now runs `_run_preflight_risk_gate` before expensive workflow stages.
   - failures hard-stop as `failed_gate` with reason `preflight_risk_gate_failed`.
   - artifact: `artifacts/preflight_risk_gate.json`.
3. Added strict merge-target head SHA discipline:
   - capture target head SHA at run start (auto-merge mode).
   - block fusion if target head drifts before merge (`head_sha_mismatch`).
4. Added structured evidence manifest:
   - for passed `WorkflowEngine` runs, service writes `artifacts/evidence_manifest.json`.
   - manifest includes checks, evidence paths, preflight snapshot, head snapshot, and artifact refs.
5. Added failure-to-regression loop:
   - failed tasks now emit/update `.agents/regressions/failure_tasks.json`.
   - benchmark harness and CLI wrapper now support/enable regression task inclusion:
     - `--regression-file`
     - `--include-regression` / `--no-include-regression`.
6. Tests added/updated:
   - `tests/unit/test_service.py`:
     - preflight fail-fast
     - concurrent start dedupe
     - evidence manifest emission
     - merge-target head-sha drift block
   - `tests/unit/test_benchmark.py`:
     - regression task loading
     - benchmark-task merge dedupe
   - `tests/unit/test_cli.py`:
     - benchmark parser coverage for regression flags.
7. Verification:
   - `py -m pytest -q tests/unit/test_service.py`
   - `py -m pytest -q tests/unit/test_benchmark.py tests/unit/test_cli.py`

## Update (2026-02-19, evidence-bundle hard gate + workspace resume guard + benchmark CLI wrapper)

1. Evidence bundle hardening completed:
   - `precompletion_checklist` now persists per-round evidence artifacts:
     - `artifacts/evidence_bundle_round_<n>.json`
   - service writes `evidence_bundle_ready` events and stores latest bundle in task state.
2. No-evidence-no-merge enforcement added:
   - pass + auto-merge path now validates the latest evidence bundle before fusion.
   - failed validation blocks completion and records `precompletion_guard_failed.json`.
3. Workspace resume guard added:
   - task creation stores `workspace_fingerprint` (project/workspace paths + head signatures).
   - `start_task` checks fingerprint consistency and blocks drift with:
     - status `waiting_manual`
     - reason `workspace_resume_guard_mismatch`
     - artifact `workspace_resume_guard.json`
4. CLI benchmark wrapper added:
   - new command: `py -m awe_agentcheck.cli benchmark ...`
   - wraps `scripts/benchmark_harness.py` and keeps reports under `.agents/benchmarks/`.
5. Docs synced:
   - `README.md`
   - `README.zh-CN.md`
   - `docs/RUNBOOK.md`
   - `docs/GITHUB_ABOUT.md`
   - `CHANGELOG.auto.md`
6. Verification:
   - `PYTHONPATH=src py -m pytest -q tests/unit/test_repository_meta.py tests/unit/test_cli.py tests/unit/test_service.py`
   - `PYTHONPATH=src py -m pytest -q tests/unit/test_db_timezone.py`
   - `py -m ruff check src/awe_agentcheck/service.py src/awe_agentcheck/repository.py src/awe_agentcheck/db.py src/awe_agentcheck/cli.py tests/unit/test_service.py tests/unit/test_cli.py tests/unit/test_repository_meta.py`

## Update (2026-02-19, full v1 hardening for checklist/adaptive-policy/harness)

1. Added hard pre-completion middleware in workflow:
   - new event: `precompletion_checklist`
   - new gate reasons: `precompletion_evidence_missing`, `precompletion_commands_missing`
   - task cannot pass unless verification ran and evidence paths are present.
2. Added automatic environment-context injection into prompts:
   - workspace tree excerpt + test/lint commands + execution constraints now injected into discussion/implementation/review prompts.
   - proposal-stage prompts (`self_loop_mode=0`) now receive the same environment context.
3. Added fine-grained dead-loop detection and strategy shift:
   - monitors repeated gate reasons, repeated implementation summaries, repeated review signatures.
   - emits `strategy_shifted` with remediation hint.
   - repeated shifts without progress end with `loop_no_progress`.
4. Added trace-driven adaptive policy loop in overnight runner:
   - `overnight_autoevolve.py` now consumes `/api/analytics` + `/api/policy-templates` and auto-adjusts next task controls.
   - new flags: `--adaptive-policy`, `--adaptive-interval`, `--analytics-limit`, `--policy-template`.
5. Added fixed benchmark A/B harness:
   - new module: `src/awe_agentcheck/benchmark.py`
   - new script: `scripts/benchmark_harness.py`
   - fixed task suite: `ops/benchmark_tasks.json`
   - report outputs: `.agents/benchmarks/benchmark-*.json|md`.
6. Docs synced:
   - `README.md`
   - `README.zh-CN.md`
   - `docs/RUNBOOK.md`
   - `docs/ARCHITECTURE_FLOW.md`
   - `docs/TESTING_TARGET_POLICY.md`
7. Verification:
   - `py -m ruff check .`
   - `PYTHONPATH=src py -m pytest -q`

## Update (2026-02-19, participant-level bot capability overrides)

1. Added participant-level model controls end-to-end:
   - new task payload/meta fields:
     - `participant_models` (`participant_id -> model`)
     - `participant_model_params` (`participant_id -> params`)
   - API models and task responses now include both fields.
2. Runtime resolution order is now explicit:
   - participant override first
   - provider default fallback second.
   - applies to proposal precheck, proposal discussion, implementation, and review phases.
3. Dashboard `Create Task` now includes **Bot Capability Matrix**:
   - one row per author/reviewer
   - each row supports model + custom model override + params
   - supports same-provider different-role configs (for example `codex#author-A=high`, `codex#review-B=xhigh`).
4. Role/session model display now respects participant-level overrides before provider-level defaults.
5. Docs synced:
   - `README.md`
   - `README.zh-CN.md`
   - `docs/RUNBOOK.md`
6. Verification:
   - `PYTHONPATH=src py -m pytest -q tests/unit/test_workflow.py`
   - `PYTHONPATH=src py -m pytest -q tests/unit/test_api.py`
   - `PYTHONPATH=src py -m pytest -q tests/unit/test_service.py`
   - `PYTHONPATH=src py -m pytest -q tests/unit/test_db_timezone.py`
   - `PYTHONPATH=src py -m pytest -q tests/unit`
   - `PYTHONPATH=src py -m ruff check src/awe_agentcheck/api.py src/awe_agentcheck/db.py src/awe_agentcheck/repository.py src/awe_agentcheck/service.py src/awe_agentcheck/workflow.py tests/unit/test_api.py tests/unit/test_service.py tests/unit/test_workflow.py`

## Update (2026-02-19, consensus stall safeguards + docs/homepage refresh)

1. Proposal consensus loop now has explicit pending/stall exits instead of open-ended retries:
   - same-round unresolved retries >= 10 -> `waiting_manual` with `last_gate_reason=proposal_consensus_stalled_in_round`
   - repeated same-issue signature across consensus rounds >= 4 -> `waiting_manual` with `last_gate_reason=proposal_consensus_stalled_across_rounds`
2. Added explicit observability + artifacts for stalled consensus:
   - event: `proposal_consensus_stalled`
   - artifact: `artifacts/consensus_stall.json`
   - pending payload includes `stall` details and is still written to `pending_proposal.json`.
3. Manual-mode operator guidance improved:
   - `waiting_manual + proposal_consensus_stalled*` now maps to actionable next step text (`Custom Reply + Re-run`).
4. Follow-up recommendation logic updated:
   - automation now recognizes `proposal_consensus_stalled*` reasons for process hardening topics.
5. Test coverage added:
   - same-round stall guard test
   - cross-round repeated-issue stall guard test.
6. Documentation and homepage sync:
   - README EN/CN latest update moved to 2026-02-19.
   - RUNBOOK/ARCHITECTURE updated with new stall semantics and operator recovery path.
7. Verification:
   - `py -m pytest -q tests/unit/test_service.py tests/unit/test_automation.py`
   - `py -m ruff check src/awe_agentcheck/service.py src/awe_agentcheck/automation.py tests/unit/test_service.py`

## Update (2026-02-19, manual custom reply path + create-help sync)

1. Added third manual decision path for `waiting_manual` tasks:
   - `decision=revise` (in addition to approve/reject)
   - API: `POST /api/tasks/{task_id}/author-decision` now accepts `decision: approve|reject|revise`
   - revise action requeues task with `last_gate_reason=author_feedback_requested` and can auto-start.
2. Added reviewer/author custom manual note flow in dashboard:
   - new `Custom Reply + Re-run` button in `Dialogue Scope`
   - new free-text `manualReplyNote` input (enabled only when `status=waiting_manual`).
3. Consensus re-run now consumes latest operator feedback:
   - service injects latest revise-note context into next proposal seed and pending summary.
4. CLI updated:
   - `awe-agentcheck decide` supports `--decision approve|reject|revise`.
5. Create Task help updated:
   - `Policy Template` help now explains `custom` mode keeps manual values unchanged.
6. Verification:
   - `py -m pytest tests/unit/test_cli.py tests/unit/test_api.py tests/unit/test_service.py -q`
   - `py -m ruff check src/awe_agentcheck/api.py src/awe_agentcheck/cli.py src/awe_agentcheck/service.py tests/unit/test_api.py tests/unit/test_cli.py tests/unit/test_service.py`

## Update (2026-02-18, docs + GitHub about sync)

1. Documentation sync completed for newly shipped control-plane capabilities:
   - README EN/CN now includes missing CLI commands:
     - `analytics`
     - `policy-templates`
     - `github-summary`
     - `promote-round`
   - README EN/CN API tables now include:
     - `POST /api/tasks/{task_id}/promote-round`
     - `GET /api/policy-templates`
     - `GET /api/analytics`
     - `GET /api/tasks/{task_id}/github-summary`
     - `POST /api/project-history/clear`
2. `docs/RUNBOOK.md` synced with production behavior:
   - added promotion-guard env vars and extra-provider adapter env example.
   - documented multi-round (`max_rounds>1` + `auto_merge=0`) per-round artifacts and manual promote flow.
   - added web capability notes for `Project History Clear`, `GitHub / PR Summary`, `Advanced Analytics`, and `Promote Round`.
3. `docs/ARCHITECTURE_FLOW.md` synced:
   - expanded control-plane REST surface, observability endpoints, round-artifact storage, and promotion guard section.
4. GitHub About/description text refreshed to match current positioning:
   - reviewer-first multi-CLI orchestration + policy guardrails + analytics + PR-ready summaries.

## Update (2026-02-19, roadmap Q2/Q3 features shipped)

1. GitHub/PR integration landed:
   - new endpoint `GET /api/tasks/{task_id}/github-summary`
   - returns PR-ready markdown summary with task status, findings, revisions, disputes, next steps, and artifact paths.
2. Policy templates by repo size/risk profile landed:
   - new endpoint `GET /api/policy-templates?workspace_path=...`
   - returns workspace profile (`repo_size`, `risk_level`, file/risk markers) + template defaults.
   - dashboard create-form now supports selecting/applying policy templates.
3. Pluggable provider adapters landed:
   - participant provider registry now supports dynamic providers beyond built-in claude/codex/gemini.
   - new env `AWE_PROVIDER_ADAPTERS_JSON` to inject extra provider command adapters.
   - CLI model/model-param validation now honors configured extra providers.
4. Branch-aware promotion guard landed:
   - auto-merge and manual round promotion now execute a promotion guard before fusion.
   - guard emits `promotion_guard_checked` and can block promotion with explicit reason.
   - defaults are non-blocking for local dev; strict behavior enabled via env:
     `AWE_PROMOTION_GUARD_ENABLED`, `AWE_PROMOTION_ALLOWED_BRANCHES`, `AWE_PROMOTION_REQUIRE_CLEAN`.
5. Advanced visual analytics landed:
   - new endpoint `GET /api/analytics`
   - exposes failure taxonomy distribution/trends and reviewer drift signals.
   - dashboard now includes an Advanced Analytics panel and GitHub/PR summary panel.
6. Verification:
   - `py -m ruff check src tests`
   - `py -m pytest tests/unit/test_config.py tests/unit/test_main.py tests/unit/test_participants.py tests/unit/test_cli.py -q`
   - `py -m pytest tests/unit/test_service.py tests/unit/test_api.py -q`
   - `py -m pytest -q`

## Update (2026-02-18, merged from two codex self-check rounds)

1. Merged round-1 hardening into mainline:
   - artifact fallback event loading now validates task id and blocks traversal (`..`, `/`, `\\`) with path containment checks.
   - workflow command splitting now preserves Windows paths (`posix=(os.name != 'nt')`).
   - SQL conditional status transition (`update_task_status_if`) now uses single-statement compare-and-set semantics.
2. Merged round-2 hardening into mainline:
   - SQL event sequence allocation now uses per-task counters + uniqueness constraints for concurrent writers.
   - sandbox bootstrap now supports rollback cleanup on create-task failure.
   - default sandbox base is private-by-default; shared/public base requires explicit opt-in (`AWE_SANDBOX_USE_PUBLIC_BASE=1`).
   - sandbox bootstrap now skips common secret-like files (`.env*`, `*.pem`, `*.key`, token/secret patterns).
3. Fixed reviewer-blocked FK risk from round-2:
   - `delete_tasks` now removes `task_event_counters` rows before deleting tasks.
   - added regression coverage to ensure delete path does not fail after event writes.
4. Tests added/updated:
   - traversal rejection in API/service event access
   - Windows command path parsing behavior
   - SQL CAS conflict/missing-task behavior
   - concurrent event seq uniqueness under 50 threads
   - delete-with-counter regression
5. Verification:
   - `py -m ruff check src/awe_agentcheck/db.py src/awe_agentcheck/service.py src/awe_agentcheck/workflow.py tests/unit/test_api.py tests/unit/test_db_timezone.py tests/unit/test_service.py tests/unit/test_workflow.py`
   - `py -m pytest tests/unit/test_db_timezone.py tests/unit/test_service.py tests/unit/test_api.py tests/unit/test_workflow.py -q`
   - `py -m pytest -q`

## Update (2026-02-18, LangGraph backend + subprocess reliability baseline)

1. Added workflow backend switch in runtime settings:
   - new env var: `AWE_WORKFLOW_BACKEND`
   - supported values: `langgraph` (default), `classic` (fallback)
2. `main.build_app()` now wires selected backend into `WorkflowEngine`.
3. `WorkflowEngine` now supports backend dispatch:
   - `langgraph`: executes workflow through a LangGraph compiled graph wrapper
   - `classic`: uses existing imperative workflow loop
4. Safety fallback behavior:
   - when `AWE_WORKFLOW_BACKEND=langgraph` but LangGraph package is unavailable, engine logs warning and falls back to `classic`.
5. CLI subprocess behavior remains intact:
   - existing participant timeout/retry/backoff/model-param handling in `ParticipantRunner` is preserved.
6. Docs synced:
   - `README.md`, `README.zh-CN.md`, `docs/RUNBOOK.md` now include `AWE_WORKFLOW_BACKEND`.
7. Verification:
   - `py -m ruff check src/awe_agentcheck/workflow.py src/awe_agentcheck/config.py src/awe_agentcheck/main.py tests/unit/test_config.py tests/unit/test_main.py tests/unit/test_workflow.py`
   - `py -m pytest tests/unit/test_config.py tests/unit/test_main.py tests/unit/test_workflow.py -q`
   - `py -m pytest -q`

## Update (2026-02-18, overnight stability + plain-language monitor)

1. Monitor verdict wording is now plain-language:
   - `no_blocker` -> `通过（可继续）` / `Pass (can continue)`
   - `blocker` -> `不通过（需先修复）` / `Needs fixes (blocking)`
   - `unknown` -> `不确定（信息不足）` / `Unclear (insufficient info)`
2. Conversation stream is now operator-friendly by default:
   - added `Stream Details` toggle in dashboard (`OFF` by default).
   - default view suppresses low-signal internal provider stream noise to avoid unreadable "log flood".
3. Dialogue stability/readability improvements landed:
   - selection persistence across refresh (`project/task/role`).
   - reduced unnecessary conversation re-render/flicker via signature check.
   - history-only tasks can still be selected/read when live task rows are missing.
4. Event traceability hardened:
   - `/api/tasks/{task_id}/events` now falls back to artifact history if repository row is absent.
   - added API test for history fallback path in `tests/unit/test_api.py`.
5. Reviewer-first proposal behavior for audit intent improved:
   - audit/discovery tasks no longer fail solely due to broad initial scope wording.
   - proposal reviewer normalization converts scope-ambiguity-only blocker/unknown to actionable non-blocking guidance under audit intent.
6. Verification status:
   - `py -m pytest -q` passed.
   - `py -m ruff check .` passed.
7. Overnight launcher stability hardened:
   - `scripts/start_overnight_until_7.ps1` now waits for `/healthz` before launching worker.
   - default restart behavior now resets active `AutoEvolve:*` tasks (best-effort) to avoid startup `concurrency_limit` queue buildup.
8. Overnight self-loop strategy expanded to dual-channel follow-up:
   - process channel: task terminal status/reason is mapped to next-round process-hardening topic.
   - finding channel: latest review/gate/runtime error events are summarized into next-round fix topics.
9. Added stall watchdog for active running tasks:
   - `scripts/overnight_autoevolve.py` periodically probes `/api/tasks/{id}/events`.
   - if no new events for configured window, task is force-failed with `watchdog_stall` and loop proceeds.
10. Added helper coverage:
   - `src/awe_agentcheck/automation.py`: process follow-up recommendation + event-to-topic extraction.
   - `tests/unit/test_automation.py`: tests for new recommendation/summarization/extraction behavior.
11. Deep anomaly root-cause (2026-02-17~2026-02-18) confirmed:
   - issue was not "no response"; it was "continuous stream without lifecycle progression".
   - representative diagnostic: `task-dda3ac6360ef` had 6330 events, 6319 were `participant_stream`, only 11 lifecycle events, still stuck at round 1.
12. Why this happened:
   - generated sandbox paths under `C:\Users\hangw\...` inherited parent `AGENTS.md` constraints in nested CLI subprocesses.
   - subprocess agents spent substantial budget on meta-skill orchestration output before converging on task deliverables.
13. Mitigations applied:
   - default generated sandbox path now escapes AGENTS-heavy ancestor chain (`C:\Users\Public\<project>-lab` on Windows) unless explicitly overridden.
   - `AWE_SANDBOX_BASE` supported for deterministic operator control.
   - overnight defaults tuned for throughput stability (`stream_mode=0`, `debate_mode=0`) and stage-stall watchdog escalation.

## Update (2026-02-18, reviewer-first + consensus semantics sync)

1. Reviewer-first workflow alignment completed:
   - autonomous loop (`self_loop_mode=1`) now runs reviewer-first debate/precheck when `debate_mode=1`.
   - author remains implementation owner; reviewers provide critique and gate decisions.
2. Manual mode (`self_loop_mode=0`) semantics tightened:
   - `max_rounds` is interpreted as required proposal consensus rounds.
   - each round now keeps retrying in-place until consensus; it exits early only on cancel/deadline or reviewer outputs fully unavailable.
   - `waiting_manual` is entered only after required consensus rounds complete.
3. Proposal-stage observability expanded:
   - new event families: `proposal_precheck_review*`, `proposal_consensus_reached`, `proposal_consensus_retry`, `proposal_review_partial`.
4. Documentation sync completed:
   - `README.md`, `README.zh-CN.md`, `docs/RUNBOOK.md`, `docs/ARCHITECTURE_FLOW.md` updated to match runtime behavior.

## Update (2026-02-18, docs + repo metadata sync)

1. Synced architecture doc to current runtime:
   - added `/api/project-history` ledger surface and monitor history panel layout.
   - clarified `evolve_until` vs `max_rounds` precedence.
   - documented persistent SQLite default path for restart-safe history.
2. Synced GitHub repository description to highlight:
   - multi-agent CLI orchestration
   - cross-review + bug fixing
   - project history ledger + continuous evolution.

## Update (2026-02-18, history traceability + persistence)

1. Root cause of empty `Project` dropdown clarified:
   - dashboard project list was derived from live `/api/tasks` only.
   - when API booted with in-memory fallback, restart cleared task rows, so project list could become empty.
2. Added persistent local fallback for startup scripts:
   - `scripts/start_api.ps1` now defaults `AWE_DATABASE_URL` to local SQLite when env is unset.
   - `scripts/start_overnight_until_7.ps1` uses same persistent default (and records URL in session metadata).
3. Added project history API:
   - `GET /api/project-history?project_path=...&limit=...`
   - returns per-task `core_findings`, `revisions`, `disputes`, `next_steps` for traceability.
4. Added dashboard history panel:
   - new `Project History` card renders historical records for selected project.
   - project selector now merges live tasks + history index, so old projects remain visible even without active tasks.
5. Included workflow progress quality-of-life:
   - stage start events (`discussion_started`, `implementation_started`, `review_started`, `verification_started`) improve perceived responsiveness during long runs.

## Update (2026-02-18, deadline priority + UI policy coupling)

1. Added round/deadline precedence rule:
   - if `evolve_until` is set, workflow uses deadline as the primary stop condition.
   - if `evolve_until` is empty, workflow uses `max_rounds`.
2. Added dashboard `Max Rounds` input (1..20) to task create form.
3. Added dashboard policy coupling:
   - when `Sandbox Mode = 0`, UI forces `Auto Merge = 0` and locks the selector.
   - merge target input is disabled when `Auto Merge = 0`.
4. Added UI hinting:
   - when `Evolve Until` has a value, `Max Rounds` input is disabled (deadline precedence).
5. Documentation synced in EN/CN README and RUNBOOK with precedence/policy notes.

## Update (2026-02-18, doc sync + runtime stability)

1. Synced defaults to current runtime behavior:
   - Claude default model: `claude-opus-4-6`
   - Codex default reasoning: `model_reasoning_effort=xhigh`
   - Gemini default command: `gemini --yolo`
2. Added and documented task-level model controls:
   - `provider_models` for provider -> model pinning
   - `provider_model_params` for provider-specific extra args passthrough
3. Added and documented task-level conversation language:
   - `conversation_language` supports `en|zh` across API/CLI/workflow/UI.
4. Added API lifecycle scripts for stable local operation on Windows:
   - `scripts/start_api.ps1` (health-gated startup, PID tracking, startup log tail)
   - `scripts/stop_api.ps1` (PID + port listener cleanup)
5. Clarified `127.0.0.1:8000` refusal root cause and operator path:
   - refusal indicates no active listener (or startup failure), not hidden auto-run.
   - operator should use `start_api.ps1` then verify `/healthz`, and stop via `stop_api.ps1`.
6. Verification rerun completed:
   - `py -m ruff check .`
   - `py -m pytest -q`

## Update (2026-02-17, provider model control + Claude team agents)

1. Added per-provider model control end-to-end:
   - API/UI payload key: `provider_models` (map: provider -> model).
   - CLI support: repeatable `--provider-model provider=model`.
   - Service validates allowed providers (`claude`, `codex`, `gemini`) and non-empty model values.
2. Added Claude team-agents toggle end-to-end:
   - API/UI payload key: `claude_team_agents` (bool).
   - CLI support: `--claude-team-agents 0|1`.
3. Participant runner now applies provider-specific model flags when model override is present and command template has no model flag:
   - Claude: `--model`
   - Codex/Gemini: `-m`
   - Claude optional `--agents {}` is appended when `claude_team_agents=true`.
4. Monitor UI updates:
   - Create-task form now sends `provider_models` and `claude_team_agents`.
   - Task snapshot now displays `ProviderModels` and `ClaudeAgents`.
5. Test coverage added:
   - adapter/model-flag + claude-team-agents behavior
   - workflow forwarding of model/team options
   - service/API input acceptance and validation
   - CLI parser and payload wiring
6. Verification:
   - `py -m ruff check .` passed.
   - `py -m pytest -q` passed.

## Update (2026-02-18, Codex multi-agent toggle)

1. Added task-level Codex multi-agent toggle end-to-end:
   - API/UI payload key: `codex_multi_agents` (bool).
   - CLI support: `--codex-multi-agents 0|1`.
2. Participant runner behavior:
   - For `provider=codex`, appends `--enable multi_agent` when `codex_multi_agents=true`.
   - Existing explicit flags are respected; duplicate `--enable multi_agent` is not appended.
3. Monitor UI updates:
   - Create-task form includes `Codex Multi Agents`.
   - Task snapshot includes `CodexMultiAgents`.
4. Persistence and workflow propagation:
   - Stored in task meta, surfaced in `TaskView/TaskResponse`, propagated into `RunConfig`.
5. Coverage:
   - Adapter, API, service, workflow, CLI, and SQL repository tests updated.

## Update (2026-02-17, safety guard)

1. Added accidental-launch protection for overnight runner:
   - `scripts/start_overnight_until_7.ps1` now requires explicit `-Until`.
   - missing `-Until` now fails fast with clear error text.
2. Updated operator docs to match new launch contract:
   - `docs/RUNBOOK.md` examples now include explicit `-Until`.
3. Root-cause clarification for "sudden start" incident:
   - no Task Scheduler / startup item was found for the project.
   - launch behavior matched a direct invocation of `start_overnight_until_7.ps1` before this safety guard was enabled.

## Update (2026-02-17)

1. Added first-class Gemini CLI participant support across runtime:
   - `provider#alias` parser now accepts `gemini` in addition to `claude` and `codex`.
   - participant adapter default command includes `gemini --yolo`.
2. Added Gemini command wiring in app settings:
   - new env var `AWE_GEMINI_COMMAND` (default `gemini --yolo`).
   - `main.build_app()` now injects Gemini command overrides into `ParticipantRunner`.
3. Updated launcher automation for reliability with Gemini:
   - `scripts/start_overnight_until_7.ps1` resolves Gemini binary path.
   - launcher exports `AWE_GEMINI_COMMAND` and records it in session metadata.
4. Updated docs/UI examples:
   - README (EN/CN), RUNBOOK, and architecture flow include Gemini examples and env reference.
   - monitor create-task form now explicitly indicates reviewer providers support `claude/codex/gemini`.
   - pixel avatar palette now has a dedicated Gemini visual variant.
5. Verification:
   - targeted tests: `tests/unit/test_participants.py`, `tests/unit/test_adapters.py`, `tests/unit/test_config.py` passed.
   - integration coverage added in `tests/unit/test_main.py` for Gemini command wiring.
   - full checks passed: `py -m ruff check .` and `py -m pytest -q`.

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
12. GitHub repository rename executed:
   - old: `https://github.com/cloveric/awe-agentcheck`
   - new: `https://github.com/cloveric/awe-agentforge`
   - README (EN/CN) links/badges/clone examples synced to new repo URL

## Pause Window Notice (Operator Directive)

1. Operator instruction: do not run overnight/auto-evolution before `2026-02-17 09:03` (local time).
2. Pause command executed:
   - `pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/stop_overnight.ps1" -All`
3. Verification after stop:
   - stopped one active process: `PID 28840`
   - no remaining overnight python process (`overnight_autoevolve.py`)
   - lock cleared: `C:/Users/hangw/awe-agentcheck/.agents/overnight/overnight.lock` is missing
   - API unreachable on `http://127.0.0.1:8000/healthz` (service not running)
   - no relevant scheduled task found for auto-restart
4. Resume command (after pause window only):
   - `pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -Until "2026-02-18 07:00"`

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
   - Codex: `--dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=xhigh`
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
   - `evolution_level` (`0|1|2|3`) on task create
   - `evolve_until` datetime deadline for discussion/evolution phase.
28. Added workspace structure endpoint:
   - `GET /api/workspace-tree` for directory/file tree rendering.
29. Updated monitor UI:
   - left top now shows project structure tree (not just project list).
30. Added launcher deadline and intensity controls:
   - `start_overnight_until_7.ps1 -Until "..."`
   - `start_overnight_until_7.ps1 -EvolutionLevel 0|1|2|3`
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
$until = "2026-02-18 07:00"
pwsh -NoProfile -ExecutionPolicy Bypass -File "C:/Users/hangw/awe-agentcheck/scripts/start_overnight_until_7.ps1" -ForceRestart -RestartApi -Until "$until"
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
