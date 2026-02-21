from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import difflib
import os
from pathlib import Path
import re
import shutil
import stat
import threading

from awe_agentcheck.adapters import ParticipantRunner
from awe_agentcheck.domain.events import EventType
from awe_agentcheck.domain.gate import evaluate_medium_gate
from awe_agentcheck.domain.models import ReviewVerdict, TaskStatus
from awe_agentcheck.event_analysis import (
    clip_snippet as event_clip_snippet,
    coerce_revision_count as event_coerce_revision_count,
    derive_next_steps as event_derive_next_steps,
    extract_core_findings as event_extract_core_findings,
    extract_disputes as event_extract_disputes,
    extract_revisions as event_extract_revisions,
    guess_task_created_at as event_guess_task_created_at,
    guess_task_updated_at as event_guess_task_updated_at,
    is_path_within as event_is_path_within,
    load_history_events as event_load_history_events,
    merged_event_payload as event_merged_event_payload,
    normalize_history_events as event_normalize_history_events,
    read_json_file as event_read_json_file,
    read_markdown_highlights as event_read_markdown_highlights,
    validate_artifact_task_id as event_validate_artifact_task_id,
)
from awe_agentcheck.fusion import AutoFusionManager
from awe_agentcheck.git_operations import (
    evaluate_promotion_guard,
    promotion_guard_config,
    read_git_head_sha,
    read_git_state,
    run_git_command,
)
from awe_agentcheck.observability import get_logger, set_task_context
from awe_agentcheck.participants import parse_participant_id
from awe_agentcheck.policy_templates import (
    POLICY_TEMPLATE_CATALOG,
)
from awe_agentcheck.proposal_helpers import (
    PROPOSAL_REPEAT_ROUNDS_LIMIT,
    PROPOSAL_STALL_RETRY_LIMIT,
    append_proposal_feedback_context,
    is_audit_intent,
    is_actionable_proposal_review_text,
    looks_like_hard_risk,
    looks_like_scope_ambiguity,
    normalize_proposal_reviewer_result,
    proposal_author_prompt,
    proposal_consensus_reached,
    proposal_review_prompt,
    proposal_review_usable_count,
    proposal_round_signature,
    proposal_verdict_counts,
    review_timeout_seconds,
)
from awe_agentcheck.proposal_contract import (
    extract_required_issue_ids,
    parse_author_issue_responses,
    parse_reviewer_issues,
    validate_author_issue_responses,
    validate_reviewer_issue_contract,
)
from awe_agentcheck.risk_assessment import (
    analyze_workspace_profile,
    load_risk_policy_contract,
    normalize_required_checks,
    recommend_policy_template,
    risk_contract_file_candidates,
    requires_browser_evidence,
    resolve_risk_tier_from_profile,
    run_preflight_risk_gate,
)
from awe_agentcheck.repository import TaskRepository
from awe_agentcheck.service_layers import (
    AnalyticsService,
    EvidenceDeps,
    EvidenceService,
    HistoryDeps,
    HistoryService,
    MemoryDeps,
    MemoryService,
    TaskManagementService,
    normalize_memory_mode,
    normalize_phase_timeout_seconds,
)
from awe_agentcheck.storage.artifacts import ArtifactStore
from awe_agentcheck.task_options import (
    extract_model_from_command,
    normalize_bool_flag,
    normalize_conversation_language,
    normalize_merge_target_path,
    normalize_participant_agent_overrides_runtime,
    normalize_plain_mode,
    normalize_repair_mode,
    resolve_agent_toggle_for_participant,
    resolve_model_for_participant,
    resolve_model_params_for_participant,
    supported_providers,
)
from awe_agentcheck.workflow import RunConfig, ShellCommandExecutor, WorkflowEngine
from awe_agentcheck.workflow_architecture import build_environment_context
from awe_agentcheck.workflow_text import clip_text

_log = get_logger('awe_agentcheck.service')


class InputValidationError(ValueError):
    def __init__(self, message: str, *, field: str | None = None, code: str = 'validation_error'):
        super().__init__(message)
        self.message = message
        self.field = field
        self.code = code


@dataclass(frozen=True)
class CreateTaskInput:
    title: str
    description: str
    author_participant: str
    reviewer_participants: list[str]
    evolution_level: int = 0
    evolve_until: str | None = None
    conversation_language: str = 'en'
    provider_models: dict[str, str] | None = None
    provider_model_params: dict[str, str] | None = None
    participant_models: dict[str, str] | None = None
    participant_model_params: dict[str, str] | None = None
    claude_team_agents: bool = False
    codex_multi_agents: bool = False
    claude_team_agents_overrides: dict[str, bool] | None = None
    codex_multi_agents_overrides: dict[str, bool] | None = None
    repair_mode: str = 'balanced'
    memory_mode: str = 'basic'
    phase_timeout_seconds: dict[str, int] | None = None
    plain_mode: bool = True
    stream_mode: bool = True
    debate_mode: bool = True
    sandbox_mode: bool = True
    sandbox_workspace_path: str | None = None
    sandbox_cleanup_on_pass: bool = True
    self_loop_mode: int = 0
    auto_merge: bool = True
    merge_target_path: str | None = None
    workspace_path: str = str(Path.cwd())
    max_rounds: int = 3
    test_command: str = 'python -m pytest -q'
    lint_command: str = 'python -m ruff check .'


@dataclass(frozen=True)
class GateInput:
    tests_ok: bool
    lint_ok: bool
    reviewer_verdicts: list[ReviewVerdict]


@dataclass(frozen=True)
class TaskView:
    task_id: str
    title: str
    description: str
    author_participant: str
    reviewer_participants: list[str]
    evolution_level: int
    evolve_until: str | None
    conversation_language: str
    provider_models: dict[str, str]
    provider_model_params: dict[str, str]
    participant_models: dict[str, str]
    participant_model_params: dict[str, str]
    claude_team_agents: bool
    codex_multi_agents: bool
    claude_team_agents_overrides: dict[str, bool]
    codex_multi_agents_overrides: dict[str, bool]
    repair_mode: str
    memory_mode: str
    phase_timeout_seconds: dict[str, int]
    plain_mode: bool
    stream_mode: bool
    debate_mode: bool
    sandbox_mode: bool
    sandbox_workspace_path: str | None
    sandbox_generated: bool
    sandbox_cleanup_on_pass: bool
    self_loop_mode: int
    project_path: str
    auto_merge: bool
    merge_target_path: str | None
    workspace_path: str
    status: TaskStatus
    last_gate_reason: str | None
    max_rounds: int
    test_command: str
    lint_command: str
    rounds_completed: int
    cancel_requested: bool


@dataclass(frozen=True)
class StatsView:
    total_tasks: int
    status_counts: dict[str, int]
    active_tasks: int
    reason_bucket_counts: dict[str, int]
    provider_error_counts: dict[str, int]
    recent_terminal_total: int
    pass_rate_50: float
    failed_gate_rate_50: float
    failed_system_rate_50: float
    mean_task_duration_seconds_50: float
    prompt_prefix_reuse_rate_50: float
    prompt_cache_break_count_50: int
    prompt_cache_break_model_50: int
    prompt_cache_break_toolset_50: int
    prompt_cache_break_prefix_50: int


_PROVIDER_RE = re.compile(r'provider=([a-zA-Z0-9_-]+)')
_TERMINAL_STATUSES = {
    TaskStatus.PASSED.value,
    TaskStatus.FAILED_GATE.value,
    TaskStatus.FAILED_SYSTEM.value,
    TaskStatus.CANCELED.value,
}
_DEFAULT_PROVIDER_MODELS = {
    'claude': [
        'claude-opus-4-6',
        'claude-sonnet-4-6',
        'claude-opus-4-1',
        'claude-sonnet-4-5',
        'claude-3-7-sonnet',
        'claude-3-5-sonnet-latest',
    ],
    'codex': [
        'gpt-5.3-codex',
        'gpt-5.3-codex-spark',
        'gpt-5-codex',
        'gpt-5',
        'gpt-5-mini',
        'gpt-4.1',
    ],
    'gemini': [
        'gemini-3-flash-preview',
        'gemini-3-pro-preview',
        'gemini-3-flash',
        'gemini-3-pro',
        'gemini-flash-latest',
        'gemini-pro-latest',
    ],
}

_ARTIFACT_TASK_ID_RE = re.compile(r'^[A-Za-z0-9._-]+$')


def _supported_providers() -> set[str]:
    return supported_providers()


def _reason_bucket(reason: str | None) -> str | None:
    text = (reason or '').strip().lower()
    if not text:
        return None
    if text in {'passed', 'canceled'}:
        return None
    if 'watchdog_timeout' in text:
        return 'watchdog_timeout'
    if 'provider_limit' in text:
        return 'provider_limit'
    if 'command_timeout' in text:
        return 'command_timeout'
    if 'command_not_found' in text:
        return 'command_not_found'
    if 'review_blocker' in text:
        return 'review_blocker'
    if 'review_unknown' in text:
        return 'review_unknown'
    if 'review_missing' in text:
        return 'review_missing'
    if 'tests_failed' in text:
        return 'tests_failed'
    if 'lint_failed' in text:
        return 'lint_failed'
    if 'precompletion_evidence_missing' in text:
        return 'precompletion_evidence_missing'
    if 'precompletion_commands_missing' in text:
        return 'precompletion_commands_missing'
    if 'preflight_risk_gate_failed' in text:
        return 'preflight_risk_gate_failed'
    if 'head_sha_mismatch' in text:
        return 'head_sha_mismatch'
    if 'loop_no_progress' in text:
        return 'loop_no_progress'
    if 'concurrency_limit' in text:
        return 'concurrency_limit'
    if 'author_confirmation_required' in text:
        return 'author_confirmation_required'
    if 'workspace_resume_guard_mismatch' in text:
        return 'workspace_resume_guard_mismatch'
    if 'author_rejected' in text:
        return 'author_rejected'
    if 'workflow_error' in text:
        return 'workflow_error_other'
    return 'other'


class OrchestratorService:
    def __init__(
        self,
        *,
        repository: TaskRepository,
        artifact_store: ArtifactStore,
        workflow_engine: WorkflowEngine | None = None,
        max_concurrent_running_tasks: int = 1,
    ):
        self.repository = repository
        self.artifact_store = artifact_store
        self.max_concurrent_running_tasks = max(0, int(max_concurrent_running_tasks))
        self.fusion_manager = AutoFusionManager(snapshot_root=self.artifact_store.root / 'snapshots')
        self.workflow_engine = workflow_engine or WorkflowEngine(
            runner=ParticipantRunner(),
            command_executor=ShellCommandExecutor(),
        )
        self._start_slot_guard = threading.Lock()
        self._running_state_guard = threading.Lock()
        self._start_slots: set[str] = set()
        self._active_run_slots: set[str] = set()
        self.analytics_service = AnalyticsService(
            repository=self.repository,
            stats_factory=StatsView,
            reason_bucket_fn=_reason_bucket,
            provider_pattern=_PROVIDER_RE,
            parse_iso_datetime_fn=self._parse_iso_datetime,
            format_task_day_fn=self._format_task_day,
            merged_event_payload_fn=self._merged_event_payload,
        )
        self.evidence_service = EvidenceService(
            repository=self.repository,
            artifact_store=self.artifact_store,
            deps=EvidenceDeps(
                validate_artifact_task_id=self._validate_artifact_task_id,
                validate_evidence_bundle=self._validate_evidence_bundle,
                coerce_evidence_checks=self._coerce_evidence_checks,
                coerce_evidence_paths=self._coerce_evidence_paths,
            ),
        )
        self.history_service = HistoryService(
            repository=self.repository,
            artifact_store=self.artifact_store,
            deps=HistoryDeps(
                normalize_project_path_key=self._normalize_project_path_key,
                build_project_history_item=self._build_project_history_item,
                read_git_state=self._read_git_state,
                collect_task_artifacts=self.evidence_service.collect_task_artifacts,
                clip_snippet=self._clip_snippet,
            ),
        )
        self.memory_service = MemoryService(
            artifact_root=self.artifact_store.root,
            deps=MemoryDeps(
                list_events=self.repository.list_events,
                read_artifact_json=self._read_task_artifact_json,
            ),
        )
        self.task_management_service = TaskManagementService(
            repository=self.repository,
            artifact_store=self.artifact_store,
            validation_error_cls=InputValidationError,
        )

    def _try_claim_start_slot(self, task_id: str) -> bool:
        key = str(task_id or '').strip()
        if not key:
            return False
        with self._start_slot_guard:
            if key in self._start_slots:
                return False
            self._start_slots.add(key)
            return True

    def _release_start_slot(self, task_id: str) -> None:
        key = str(task_id or '').strip()
        if not key:
            return
        with self._start_slot_guard:
            self._start_slots.discard(key)

    def _try_claim_running_capacity(self, task_id: str) -> tuple[bool, int]:
        key = str(task_id or '').strip()
        if not key:
            return False, 0
        with self._running_state_guard:
            if key in self._active_run_slots:
                return True, 0
            running_ids = self._running_task_ids(exclude_task_id=key)
            inflight_ids = {item for item in self._active_run_slots if item != key}
            occupied = running_ids | inflight_ids
            if self.max_concurrent_running_tasks > 0 and len(occupied) >= self.max_concurrent_running_tasks:
                return False, len(occupied)
            self._active_run_slots.add(key)
            return True, len(occupied)

    def _release_running_capacity(self, task_id: str) -> None:
        key = str(task_id or '').strip()
        if not key:
            return
        with self._running_state_guard:
            self._active_run_slots.discard(key)

    def _enter_running_state_or_defer(self, *, task_id: str, row: dict) -> dict:
        claimed, running_now = self._try_claim_running_capacity(task_id)
        if not claimed:
            deferred = self.repository.update_task_status(
                task_id,
                status=TaskStatus.QUEUED.value,
                reason='concurrency_limit',
                rounds_completed=row.get('rounds_completed', 0),
            )
            self.repository.append_event(
                task_id,
                event_type='start_deferred',
                payload={
                    'reason': 'concurrency_limit',
                    'running_now': running_now,
                    'limit': self.max_concurrent_running_tasks,
                },
                round_number=None,
            )
            self.artifact_store.update_state(
                task_id,
                {
                    'status': TaskStatus.QUEUED.value,
                    'last_gate_reason': 'concurrency_limit',
                },
            )
            return deferred

        expected_status = str(row.get('status') or '').strip()
        running_row = self.repository.update_task_status_if(
            task_id,
            expected_status=expected_status,
            status=TaskStatus.RUNNING.value,
            reason=None,
            rounds_completed=row.get('rounds_completed', 0),
        )
        if running_row is None:
            latest = self.repository.get_task(task_id)
            if latest is None:
                raise KeyError(task_id)
            return latest

        self.repository.append_event(
            task_id,
            event_type='task_running',
            payload={'status': TaskStatus.RUNNING.value},
            round_number=None,
        )
        self.artifact_store.update_state(task_id, {'status': TaskStatus.RUNNING.value})
        return running_row

    def create_task(self, payload: CreateTaskInput) -> TaskView:
        row = self.task_management_service.create_task(payload)
        try:
            self.memory_service.persist_task_preferences(row=row)
        except Exception:
            _log.exception('memory_preference_persist_failed task_id=%s', str(row.get('task_id') or ''))
        return self._to_view(row)

    def list_tasks(self, *, limit: int = 100) -> list[TaskView]:
        rows = self.task_management_service.list_tasks(limit=limit)
        return [self._to_view(row) for row in rows]

    def get_task(self, task_id: str) -> TaskView | None:
        row = self.task_management_service.get_task(task_id)
        if row is None:
            return None
        return self._to_view(row)

    def get_stats(self) -> StatsView:
        return self.analytics_service.get_stats()

    def get_provider_models_catalog(self) -> dict[str, list[str]]:
        supported = _supported_providers()
        catalog: dict[str, list[str]] = {
            provider: list(_DEFAULT_PROVIDER_MODELS.get(provider, []))
            for provider in sorted(supported)
        }

        def add_model(provider: str, model: str) -> None:
            provider_key = str(provider or '').strip().lower()
            model_name = str(model or '').strip()
            if provider_key not in supported or not model_name:
                return
            bucket = catalog.setdefault(provider_key, [])
            if model_name not in bucket:
                bucket.append(model_name)

        runner = getattr(self.workflow_engine, 'runner', None)
        commands = getattr(runner, 'commands', {}) if runner is not None else {}
        if isinstance(commands, dict):
            for raw_provider, raw_command in commands.items():
                provider = str(raw_provider or '').strip().lower()
                if provider not in supported:
                    continue
                detected = extract_model_from_command(str(raw_command or ''))
                if detected:
                    add_model(provider, detected)

        for row in self.repository.list_tasks(limit=10_000):
            models = row.get('provider_models', {})
            if not isinstance(models, dict):
                continue
            for raw_provider, raw_model in models.items():
                provider = str(raw_provider or '').strip().lower()
                model = str(raw_model or '').strip()
                add_model(provider, model)

        out: dict[str, list[str]] = {}
        for provider in sorted(supported):
            out[provider] = [str(v) for v in catalog.get(provider, []) if str(v).strip()]
        return out

    def get_policy_templates(self, *, workspace_path: str | None = None) -> dict:
        profile = self._analyze_workspace_profile(workspace_path)
        recommended = self._recommend_policy_template(profile=profile)
        templates = []
        for key in sorted(POLICY_TEMPLATE_CATALOG):
            item = POLICY_TEMPLATE_CATALOG[key]
            templates.append(
                {
                    'id': item['id'],
                    'label': item['label'],
                    'description': item['description'],
                    'defaults': dict(item['defaults']),
                }
            )
        return {
            'recommended_template': recommended,
            'profile': profile,
            'templates': templates,
        }

    def get_analytics(self, *, limit: int = 300) -> dict:
        return self.analytics_service.get_analytics(limit=limit)

    def list_memory(
        self,
        *,
        project_path: str | None = None,
        memory_type: str | None = None,
        include_expired: bool = False,
        limit: int = 200,
    ) -> list[dict]:
        return self.memory_service.list_entries(
            project_path=project_path,
            memory_type=memory_type,
            include_expired=include_expired,
            limit=limit,
        )

    def query_memory(
        self,
        *,
        query: str,
        memory_mode: str = 'basic',
        project_path: str | None = None,
        stage: str | None = None,
        limit: int = 8,
    ) -> list[dict]:
        return self.memory_service.query_entries(
            query=query,
            memory_mode=normalize_memory_mode(memory_mode),
            project_path=project_path,
            stage=stage,
            limit=limit,
        )

    def set_memory_pin(self, *, memory_id: str, pinned: bool) -> dict | None:
        return self.memory_service.set_pinned(memory_id=memory_id, pinned=bool(pinned))

    def clear_memory(
        self,
        *,
        project_path: str | None = None,
        memory_type: str | None = None,
        include_pinned: bool = False,
    ) -> dict[str, int]:
        return self.memory_service.clear_entries(
            project_path=project_path,
            memory_type=memory_type,
            include_pinned=include_pinned,
        )

    def build_github_pr_summary(self, task_id: str) -> dict:
        return self.history_service.build_github_pr_summary(task_id)

    def list_project_history(self, *, project_path: str | None = None, limit: int = 200) -> list[dict]:
        return self.history_service.list_project_history(project_path=project_path, limit=limit)

    def clear_project_history(
        self,
        *,
        project_path: str | None = None,
        include_non_terminal: bool = False,
    ) -> dict:
        requested_text = str(project_path or '').strip() or None
        requested_project = (
            self._normalize_project_path_key(requested_text)
            if requested_text
            else None
        )
        rows = self.repository.list_tasks(limit=10_000)
        row_by_id: dict[str, dict] = {}
        for row in rows:
            task_id = str(row.get('task_id') or '').strip()
            if task_id:
                row_by_id[task_id] = row

        candidate_ids: set[str] = set()
        skipped_non_terminal = 0

        for row in rows:
            task_id = str(row.get('task_id') or '').strip()
            if not task_id:
                continue
            row_project = (
                str(row.get('project_path') or '').strip()
                or str(row.get('workspace_path') or '').strip()
            )
            row_project_key = self._normalize_project_path_key(row_project)
            if requested_project and row_project_key != requested_project:
                continue

            status = str(row.get('status') or '').strip().lower()
            if (not include_non_terminal) and status not in _TERMINAL_STATUSES:
                skipped_non_terminal += 1
                continue
            candidate_ids.add(task_id)

        # Also clear history-only task artifacts that may no longer have a row
        # in repository storage (for example, old imported thread folders).
        threads_root = self.artifact_store.root / 'threads'
        if threads_root.exists() and threads_root.is_dir():
            for child in threads_root.iterdir():
                if not child.is_dir():
                    continue
                task_id = str(child.name or '').strip()
                if not task_id or task_id in row_by_id:
                    continue
                item = self._build_project_history_item(task_id=task_id, row=None, task_dir=child)
                if item is None:
                    continue
                item_project = self._normalize_project_path_key(item.get('project_path'))
                if requested_project and item_project != requested_project:
                    continue
                status = str(item.get('status') or '').strip().lower()
                if (not include_non_terminal) and status not in _TERMINAL_STATUSES:
                    skipped_non_terminal += 1
                    continue
                candidate_ids.add(task_id)

        delete_order = sorted(candidate_ids)
        deleted_tasks = self.repository.delete_tasks(delete_order)
        deleted_artifacts = 0
        for task_id in delete_order:
            try:
                if self.artifact_store.remove_task_workspace(task_id):
                    deleted_artifacts += 1
            except OSError:
                continue

        return {
            'project_path': requested_text,
            'deleted_tasks': int(deleted_tasks),
            'deleted_artifacts': int(deleted_artifacts),
            'skipped_non_terminal': int(skipped_non_terminal),
        }

    def list_events(self, task_id: str) -> list[dict]:
        try:
            events = self.repository.list_events(task_id)
        except KeyError as exc:
            fallback = self._load_events_from_artifacts(task_id)
            if fallback is not None:
                return fallback
            raise exc

        if events:
            return events

        fallback = self._load_events_from_artifacts(task_id)
        if fallback:
            return fallback
        return events

    def _load_events_from_artifacts(self, task_id: str) -> list[dict] | None:
        key = self._validate_artifact_task_id(task_id)
        threads_root = (self.artifact_store.root / 'threads').resolve(strict=False)
        task_dir = (threads_root / key).resolve(strict=False)
        if not self._is_path_within(threads_root, task_dir):
            raise InputValidationError('invalid task_id', field='task_id')
        if not task_dir.exists() or not task_dir.is_dir():
            return None
        raw_events = self._load_history_events(task_id=key, row={}, task_dir=task_dir)
        return self._normalize_history_events(task_id=key, events=raw_events)

    @staticmethod
    def _is_path_within(base: Path, target: Path) -> bool:
        return event_is_path_within(base, target)

    @staticmethod
    def _validate_artifact_task_id(task_id: str) -> str:
        try:
            return event_validate_artifact_task_id(task_id, pattern=_ARTIFACT_TASK_ID_RE)
        except ValueError as exc:
            raise InputValidationError(str(exc), field='task_id') from exc

    @staticmethod
    def _normalize_history_events(*, task_id: str, events: list[dict]) -> list[dict]:
        return event_normalize_history_events(task_id=task_id, events=events)

    def _build_project_history_item(self, *, task_id: str, row: dict | None, task_dir: Path | None) -> dict | None:
        state = self._read_json_file(task_dir / 'state.json') if task_dir is not None else {}
        row_data = row or {}
        project_path = str(
            row_data.get('project_path')
            or state.get('project_path')
            or row_data.get('workspace_path')
            or state.get('workspace_path')
            or ''
        ).strip()
        if not project_path:
            return None

        status = str(row_data.get('status') or state.get('status') or 'unknown').strip().lower() or 'unknown'
        last_reason = str(row_data.get('last_gate_reason') or state.get('last_gate_reason') or '').strip() or None
        created_at = str(row_data.get('created_at') or '').strip() or self._guess_task_created_at(task_dir, state)
        updated_at = (
            str(row_data.get('updated_at') or '').strip()
            or str(state.get('updated_at') or '').strip()
            or self._guess_task_updated_at(task_dir)
        )

        events = self._load_history_events(task_id=task_id, row=row_data, task_dir=task_dir)
        findings = self._extract_core_findings(task_dir=task_dir, events=events, fallback_reason=last_reason)
        disputes = self._extract_disputes(events)
        revisions = self._extract_revisions(task_dir=task_dir, events=events)
        next_steps = self._derive_next_steps(status=status, reason=last_reason, disputes=disputes)

        title = str(row_data.get('title') or '').strip() or f'Task {task_id}'
        return {
            'task_id': task_id,
            'title': title,
            'project_path': project_path,
            'status': status,
            'last_gate_reason': last_reason,
            'created_at': created_at or None,
            'updated_at': updated_at or None,
            'core_findings': findings,
            'revisions': revisions,
            'disputes': disputes,
            'next_steps': next_steps,
        }

    def _load_history_events(self, *, task_id: str, row: dict, task_dir: Path | None) -> list[dict]:
        return event_load_history_events(
            repository=self.repository,
            task_id=task_id,
            row=row,
            task_dir=task_dir,
            logger=_log,
        )

    def _extract_core_findings(self, *, task_dir: Path | None, events: list[dict], fallback_reason: str | None) -> list[str]:
        return event_extract_core_findings(
            task_dir=task_dir,
            events=events,
            fallback_reason=fallback_reason,
        )

    @staticmethod
    def _read_markdown_highlights(path: Path | None) -> list[str]:
        return event_read_markdown_highlights(path)

    def _extract_revisions(self, *, task_dir: Path | None, events: list[dict]) -> dict:
        return event_extract_revisions(task_dir=task_dir, events=events)

    @staticmethod
    def _coerce_revision_count(value) -> int:
        return event_coerce_revision_count(value)

    def _extract_disputes(self, events: list[dict]) -> list[dict]:
        return event_extract_disputes(events)

    @staticmethod
    def _merged_event_payload(event: dict) -> dict:
        return event_merged_event_payload(event)

    @staticmethod
    def _derive_next_steps(*, status: str, reason: str | None, disputes: list[dict]) -> list[str]:
        return event_derive_next_steps(status=status, reason=reason, disputes=disputes)

    @staticmethod
    def _clip_snippet(value, *, max_chars: int = 220) -> str:
        return event_clip_snippet(value, max_chars=max_chars)

    @staticmethod
    def _normalize_project_path_key(value) -> str:
        text = str(value or '').strip()
        if not text:
            return ''
        return text.replace('\\', '/').rstrip('/').lower()

    @staticmethod
    def _format_task_day(value) -> str:
        parsed = OrchestratorService._parse_iso_datetime(value)
        if parsed is None:
            return 'unknown'
        return parsed.date().isoformat()

    def _analyze_workspace_profile(self, workspace_path: str | None) -> dict:
        return analyze_workspace_profile(workspace_path)

    @staticmethod
    def _recommend_policy_template(*, profile: dict) -> str:
        return recommend_policy_template(profile=profile)

    @staticmethod
    def _risk_contract_file_candidates(project_root: Path) -> list[Path]:
        return risk_contract_file_candidates(project_root)

    @staticmethod
    def _normalize_required_checks(value: object) -> list[str]:
        return normalize_required_checks(value)

    def _load_risk_policy_contract(self, *, project_root: Path) -> dict[str, object]:
        return load_risk_policy_contract(project_root=project_root)

    @staticmethod
    def _resolve_risk_tier_from_profile(profile: dict) -> str:
        return resolve_risk_tier_from_profile(profile)

    @staticmethod
    def _requires_browser_evidence(*, title: str, description: str) -> bool:
        return requires_browser_evidence(title=title, description=description)

    def _run_preflight_risk_gate(self, *, row: dict, workspace_root: Path) -> dict[str, object]:
        payload = run_preflight_risk_gate(
            row=row,
            workspace_root=workspace_root,
            read_git_head_sha_fn=self._read_git_head_sha,
        )
        return {
            'passed': bool(payload.get('passed', False)),
            'reason': str(payload.get('reason') or 'preflight_risk_gate_failed'),
            'tier': str(payload.get('risk_tier') or 'low'),
            'required_checks': list(payload.get('required_checks') or []),
            'failed_checks': list(payload.get('failed_checks') or []),
            'profile': dict(payload.get('profile') or {}),
            'contract_version': str(payload.get('contract_version') or '1'),
            'contract_source': str(payload.get('contract_source') or 'builtin'),
            'workspace_path': str(workspace_root),
            'project_has_git': bool((Path(str(row.get('project_path') or row.get('workspace_path') or workspace_root)) / '.git').exists()),
            'head_sha': payload.get('head_sha'),
        }

    def _collect_task_artifacts(self, *, task_id: str) -> list[dict]:
        return self.evidence_service.collect_task_artifacts(task_id=task_id)

    def _read_task_artifact_json(self, task_id: str, artifact_name: str) -> dict | None:
        key = self._validate_artifact_task_id(task_id)
        name = str(artifact_name or '').strip()
        if not name:
            return None
        path = (self.artifact_store.root / 'threads' / key / 'artifacts' / f'{name}.json').resolve(strict=False)
        threads_root = (self.artifact_store.root / 'threads').resolve(strict=False)
        if not self._is_path_within(threads_root, path):
            return None
        return self._read_json_file(path)

    def _load_pending_proposal_contract(self, task_id: str) -> dict | None:
        payload = self._read_task_artifact_json(task_id, 'pending_proposal')
        if not isinstance(payload, dict):
            return None
        contract = payload.get('proposal_contract')
        if not isinstance(contract, dict):
            return None
        required = [str(v).strip().upper() for v in list(contract.get('required_issue_ids') or []) if str(v).strip()]
        accepted = [str(v).strip().upper() for v in list(contract.get('accepted_issue_ids') or []) if str(v).strip()]
        issue_ids = sorted(set(accepted or required))
        if not issue_ids:
            return None
        return {
            'issue_ids': issue_ids,
            'required_issue_ids': sorted(set(required)),
            'accepted_issue_ids': sorted(set(accepted)),
        }

    def _write_evidence_manifest(
        self,
        *,
        task_id: str,
        row: dict,
        workspace_root: Path,
        rounds_completed: int,
        status: str,
        reason: str,
        preflight_guard: dict | None,
        evidence_bundle: dict | None,
        head_snapshot: dict | None,
    ) -> dict[str, object]:
        return self.evidence_service.write_evidence_manifest(
            task_id=task_id,
            row=row,
            workspace_root=workspace_root,
            rounds_completed=rounds_completed,
            status=status,
            reason=reason,
            preflight_guard=preflight_guard,
            evidence_bundle=evidence_bundle,
            head_snapshot=head_snapshot,
        )

    def _emit_regression_case(
        self,
        *,
        task_id: str,
        row: dict,
        status: TaskStatus,
        reason: str,
    ) -> dict[str, object] | None:
        return self.evidence_service.emit_regression_case(
            task_id=task_id,
            row=row,
            status=status,
            reason=reason,
        )

    def _persist_memory_outcome(
        self,
        *,
        task_id: str,
        row: dict,
        status: TaskStatus,
        reason: str,
    ) -> None:
        try:
            saved = self.memory_service.persist_task_outcome(
                task_id=task_id,
                row=row,
                status=status.value,
                reason=reason,
            )
        except Exception:
            _log.exception('memory_outcome_persist_failed task_id=%s', task_id)
            return
        if not saved:
            return
        payload = {
            'saved_count': len(saved),
            'memory_ids': [str(item.get('memory_id') or '') for item in saved],
            'memory_types': [str(item.get('memory_type') or '') for item in saved],
        }
        self.repository.append_event(
            task_id,
            event_type='memory_persisted',
            payload=payload,
            round_number=None,
        )
        self.artifact_store.append_event(task_id, {'type': EventType.MEMORY_PERSISTED.value, **payload})
        self.artifact_store.update_state(task_id, {'memory_persisted_last': payload})

    @staticmethod
    def _run_git_command(*, root: Path, args: list[str]) -> tuple[bool, str]:
        return run_git_command(root=root, args=args)

    def _read_git_head_sha(self, root: Path | None) -> str | None:
        return read_git_head_sha(root)

    def _read_git_state(self, root: Path | None) -> dict:
        return read_git_state(root)

    @staticmethod
    def _promotion_guard_config() -> dict:
        return promotion_guard_config()

    def _evaluate_promotion_guard(self, *, target_root: Path) -> dict:
        return evaluate_promotion_guard(target_root=target_root)

    @staticmethod
    def _read_json_file(path: Path | None) -> dict:
        return event_read_json_file(path)

    @staticmethod
    def _guess_task_created_at(task_dir: Path | None, state: dict) -> str:
        return event_guess_task_created_at(task_dir, state)

    @staticmethod
    def _guess_task_updated_at(task_dir: Path | None) -> str:
        return event_guess_task_updated_at(task_dir)

    def request_cancel(self, task_id: str) -> TaskView:
        row = self.repository.set_cancel_requested(task_id, requested=True)
        self.repository.append_event(
            task_id,
            event_type='cancel_requested',
            payload={'requested': True},
            round_number=None,
        )
        self.artifact_store.update_state(task_id, {'cancel_requested': True})
        return self._to_view(row)

    def mark_failed_system(self, task_id: str, *, reason: str) -> TaskView:
        _log.warning('mark_failed_system task_id=%s reason=%s', task_id, reason)
        # Use atomic CAS: only transition if the task is still RUNNING.
        # If an external force_fail already moved the task to a terminal
        # state, honour that state instead of blindly overwriting it.
        row = self.repository.get_task(task_id)
        if row is None:
            raise KeyError(task_id)
        current_status = row['status']

        if current_status == TaskStatus.RUNNING.value:
            updated = self.repository.update_task_status_if(
                task_id,
                expected_status=TaskStatus.RUNNING.value,
                status=TaskStatus.FAILED_SYSTEM.value,
                reason=reason,
                rounds_completed=row.get('rounds_completed'),
            )
            if updated is None:
                # Lost the CAS race – another transition happened first.
                return self._to_view(self.repository.get_task(task_id))
            row = updated
        elif current_status in (TaskStatus.PASSED.value, TaskStatus.CANCELED.value,
                                TaskStatus.FAILED_SYSTEM.value):
            # Already terminal – nothing to do.
            return self._to_view(row)
        else:
            # Non-RUNNING, non-terminal (e.g. QUEUED, WAITING_MANUAL,
            # FAILED_GATE) – unconditional update is safe here because
            # these states are not contested by the workflow loop.
            row = self.repository.update_task_status(
                task_id,
                status=TaskStatus.FAILED_SYSTEM.value,
                reason=reason,
                rounds_completed=None,
            )

        self.repository.append_event(
            task_id,
            event_type='system_failure',
            payload={'reason': reason},
            round_number=None,
        )
        self.artifact_store.update_state(
            task_id,
            {'status': TaskStatus.FAILED_SYSTEM.value, 'last_gate_reason': reason},
        )
        self.artifact_store.write_final_report(task_id, f'status=failed_system\\nreason={reason}')
        return self._to_view(row)

    def force_fail_task(self, task_id: str, *, reason: str) -> TaskView:
        row = self.repository.get_task(task_id)
        if row is None:
            raise KeyError(task_id)
        current_status = row['status']
        if current_status in {TaskStatus.PASSED.value, TaskStatus.CANCELED.value}:
            return self._to_view(row)

        # Attempt atomic conditional update: only transition if the status
        # hasn't changed since we read it (prevents TOCTOU race with a
        # concurrent workflow completion).
        updated = self.repository.update_task_status_if(
            task_id,
            expected_status=current_status,
            status=TaskStatus.FAILED_SYSTEM.value,
            reason=reason,
            rounds_completed=row.get('rounds_completed', 0),
            set_cancel_requested=True,
        )
        if updated is None:
            # Status changed concurrently — re-read and return current state.
            row = self.repository.get_task(task_id)
            if row is None:
                raise KeyError(task_id)
            return self._to_view(row)
        row = updated
        self.repository.append_event(
            task_id,
            event_type='force_failed',
            payload={'reason': reason, 'cancel_requested': True},
            round_number=None,
        )
        self.artifact_store.update_state(
            task_id,
            {
                'status': TaskStatus.FAILED_SYSTEM.value,
                'last_gate_reason': reason,
                'cancel_requested': True,
            },
        )
        self.artifact_store.write_final_report(task_id, f'status=failed_system\\nreason={reason}')
        self._persist_memory_outcome(
            task_id=task_id,
            row=row,
            status=TaskStatus.FAILED_SYSTEM,
            reason=reason,
        )
        return self._to_view(row)

    def start_task(self, task_id: str) -> TaskView:
        if not self._try_claim_start_slot(task_id):
            row = self.repository.get_task(task_id)
            if row is None:
                raise KeyError(task_id)
            payload = {
                'reason': 'start_inflight_dedup',
                'status': str(row.get('status') or ''),
            }
            self.repository.append_event(
                task_id,
                event_type='start_deduped',
                payload=payload,
                round_number=None,
            )
            self.artifact_store.append_event(task_id, {'type': EventType.START_DEDUPED.value, **payload})
            return self._to_view(row)
        try:
            return self._start_task_impl(task_id)
        finally:
            self._release_running_capacity(task_id)
            self._release_start_slot(task_id)

    def _start_task_impl(self, task_id: str) -> TaskView:
        row = self.repository.get_task(task_id)
        if row is None:
            raise KeyError(task_id)

        # Terminal states are treated as idempotent no-op starts.
        if row['status'] in {TaskStatus.PASSED.value, TaskStatus.CANCELED.value}:
            return self._to_view(row)

        if row['status'] == TaskStatus.RUNNING.value:
            return self._to_view(row)

        if row['status'] == TaskStatus.WAITING_MANUAL.value:
            return self._to_view(row)

        resume_guard = self._evaluate_workspace_resume_guard(row)
        if not bool(resume_guard.get('ok', False)):
            blocked_reason = 'workspace_resume_guard_mismatch'
            payload = {
                'reason': blocked_reason,
                **resume_guard,
            }
            updated = self.repository.update_task_status(
                task_id,
                status=TaskStatus.WAITING_MANUAL.value,
                reason=blocked_reason,
                rounds_completed=row.get('rounds_completed', 0),
            )
            self.repository.append_event(
                task_id,
                event_type='workspace_resume_guard_blocked',
                payload=payload,
                round_number=None,
            )
            self.artifact_store.append_event(task_id, {'type': EventType.WORKSPACE_RESUME_GUARD_BLOCKED.value, **payload})
            self.artifact_store.write_artifact_json(
                task_id,
                name='workspace_resume_guard',
                payload=payload,
            )
            self.artifact_store.update_state(
                task_id,
                {
                    'status': TaskStatus.WAITING_MANUAL.value,
                    'last_gate_reason': blocked_reason,
                    'workspace_resume_guard_last': payload,
                },
            )
            return self._to_view(updated)

        workspace_root = Path(str(row.get('workspace_path') or Path.cwd()))
        preflight_guard = self._run_preflight_risk_gate(row=row, workspace_root=workspace_root)
        self.repository.append_event(
            task_id,
            event_type='preflight_risk_gate',
            payload=preflight_guard,
            round_number=None,
        )
        self.artifact_store.append_event(task_id, {'type': EventType.PREFLIGHT_RISK_GATE.value, **preflight_guard})
        self.artifact_store.write_artifact_json(task_id, name='preflight_risk_gate', payload=preflight_guard)
        self.artifact_store.update_state(task_id, {'preflight_risk_gate_last': preflight_guard})
        if not bool(preflight_guard.get('passed', False)):
            blocked_reason = str(preflight_guard.get('reason') or 'preflight_risk_gate_failed').strip() or 'preflight_risk_gate_failed'
            updated = self.repository.update_task_status(
                task_id,
                status=TaskStatus.FAILED_GATE.value,
                reason=blocked_reason,
                rounds_completed=row.get('rounds_completed', 0),
            )
            self.repository.append_event(
                task_id,
                event_type='preflight_risk_gate_failed',
                payload=preflight_guard,
                round_number=None,
            )
            self.artifact_store.append_event(task_id, {'type': EventType.PREFLIGHT_RISK_GATE_FAILED.value, **preflight_guard})
            self.artifact_store.update_state(
                task_id,
                {
                    'status': TaskStatus.FAILED_GATE.value,
                    'last_gate_reason': blocked_reason,
                },
            )
            self.artifact_store.write_final_report(task_id, f'status=failed_gate\nreason={blocked_reason}')
            self._emit_regression_case(
                task_id=task_id,
                row=updated,
                status=TaskStatus.FAILED_GATE,
                reason=blocked_reason,
            )
            return self._to_view(updated)

        merge_target_head_before_run: str | None = None
        merge_target_is_git = False
        merge_target_path: str | None = None
        if bool(row.get('auto_merge', True)):
            target_root = self._resolve_merge_target(row)
            merge_target_path = str(target_root)
            merge_target_is_git = bool((target_root / '.git').exists())
            merge_target_head_before_run = self._read_git_head_sha(target_root)
        head_guard_payload = {
            'workspace_head_sha': self._read_git_head_sha(workspace_root),
            'merge_target_head_sha': merge_target_head_before_run,
            'merge_target_is_git': merge_target_is_git,
            'workspace_path': str(workspace_root),
            'merge_target_path': merge_target_path,
        }
        self.repository.append_event(
            task_id,
            event_type='head_sha_captured',
            payload=head_guard_payload,
            round_number=None,
        )
        self.artifact_store.append_event(task_id, {'type': EventType.HEAD_SHA_CAPTURED.value, **head_guard_payload})
        self.artifact_store.update_state(task_id, {'head_sha_captured': head_guard_payload})
        if bool(row.get('auto_merge', True)) and merge_target_is_git and not merge_target_head_before_run:
            blocked_reason = 'head_sha_missing: merge_target_start_sha_missing'
            missing_payload = {
                'reason': blocked_reason,
                'phase': 'start',
                'before': merge_target_head_before_run,
                'target_path': merge_target_path,
            }
            self.repository.append_event(
                task_id,
                event_type='head_sha_missing',
                payload=missing_payload,
                round_number=None,
            )
            self.artifact_store.append_event(task_id, {'type': EventType.HEAD_SHA_MISSING.value, **missing_payload})
            updated = self.repository.update_task_status(
                task_id,
                status=TaskStatus.FAILED_GATE.value,
                reason=blocked_reason,
                rounds_completed=row.get('rounds_completed', 0),
            )
            self.artifact_store.update_state(
                task_id,
                {
                    'status': TaskStatus.FAILED_GATE.value,
                    'last_gate_reason': blocked_reason,
                    'head_sha_missing_last': missing_payload,
                },
            )
            self.artifact_store.write_final_report(task_id, f'status=failed_gate\nreason={blocked_reason}')
            self._emit_regression_case(
                task_id=task_id,
                row=updated,
                status=TaskStatus.FAILED_GATE,
                reason=blocked_reason,
            )
            return self._to_view(updated)

        memory_mode = normalize_memory_mode(row.get('memory_mode', 'basic'))
        stage_memory_context: dict[str, str] = {}
        memory_hits_by_stage: dict[str, list[dict]] = {}
        try:
            memory_pack = self.memory_service.build_stage_context(
                row=row,
                query_text='\n'.join(
                    [
                        str(row.get('title') or '').strip(),
                        str(row.get('description') or '').strip(),
                        str(row.get('last_gate_reason') or '').strip(),
                    ]
                ),
                memory_mode=memory_mode,
                stage_sequence=('proposal', 'discussion', 'implementation', 'review'),
                limit_per_stage=3,
            )
            stage_memory_context = {
                str(k): str(v)
                for k, v in dict(memory_pack.get('contexts') or {}).items()
                if str(k).strip() and str(v).strip()
            }
            memory_hits_by_stage = {
                str(k): list(v)
                for k, v in dict(memory_pack.get('hits') or {}).items()
                if str(k).strip() and isinstance(v, list) and v
            }
            for stage_name, stage_hits in memory_hits_by_stage.items():
                hit_payload = {
                    'stage': stage_name,
                    'memory_mode': memory_mode,
                    'hits': [
                        {
                            'memory_id': str(item.get('memory_id') or ''),
                            'memory_type': str(item.get('memory_type') or ''),
                            'title': str(item.get('title') or ''),
                            'score': float(item.get('score') or 0.0),
                            'source_task_id': str(item.get('source_task_id') or ''),
                        }
                        for item in stage_hits[:3]
                    ],
                    'hit_count': len(stage_hits),
                }
                self.repository.append_event(
                    task_id,
                    event_type='memory_hit',
                    payload=hit_payload,
                    round_number=None,
                )
                self.artifact_store.append_event(task_id, {'type': EventType.MEMORY_HIT.value, **hit_payload})
            self.artifact_store.update_state(
                task_id,
                {
                    'memory_mode': memory_mode,
                    'memory_context_stages': sorted(stage_memory_context.keys()),
                },
            )
        except Exception:
            _log.exception('memory_preload_failed task_id=%s', task_id)

        # All modes require proposal consensus before implementation.
        needs_consensus = str(row.get('last_gate_reason') or '') != 'author_approved'
        if needs_consensus:
            row = self._enter_running_state_or_defer(task_id=task_id, row=row)
            if str(row.get('status') or '') != TaskStatus.RUNNING.value:
                return self._to_view(row)
            auto_approve = int(row.get('self_loop_mode', 0)) == 1
            prepared = self._prepare_author_confirmation(
                task_id,
                row,
                auto_approve=auto_approve,
                memory_mode=memory_mode,
                memory_context=stage_memory_context,
            )
            if not auto_approve:
                return prepared
            if prepared.status != TaskStatus.RUNNING:
                return prepared
            row = self.repository.get_task(task_id)
            if row is None:
                raise KeyError(task_id)
        else:
            row = self._enter_running_state_or_defer(task_id=task_id, row=row)
            if str(row.get('status') or '') != TaskStatus.RUNNING.value:
                return self._to_view(row)

        set_task_context(task_id=task_id)
        _log.info('task_started task_id=%s', task_id)
        round_artifacts_enabled = int(row.get('max_rounds', 1)) > 1 and not bool(row.get('auto_merge', True))
        round_snapshot_holder: list[Path | None] = [None]
        latest_evidence_bundle: list[dict | None] = [None]
        if round_artifacts_enabled:
            round_snapshot_holder[0] = self._initialize_round_artifact_baseline(
                task_id=task_id,
                workspace_root=workspace_root,
            )

        def on_event(event: dict) -> None:
            self.repository.append_event(
                task_id,
                event_type=str(event.get('type', 'event')),
                payload=event,
                round_number=event.get('round'),
            )
            self.artifact_store.append_event(task_id, event)

            event_type = str(event.get('type', ''))
            content = str(event.get('output', '')).strip()
            round_no = int(event.get('round', 0) or 0)
            if event_type in {'discussion', 'implementation', 'review', 'debate_review', 'debate_reply'} and content:
                role = event_type
                participant = event.get('participant') or event.get('provider') or role
                self.artifact_store.append_discussion(
                    task_id,
                    role=f'{role}:{participant}',
                    round_number=max(round_no, 1),
                    content=content,
                )
            if event_type == 'precompletion_checklist' and round_no > 0:
                evidence_payload = {
                    'task_id': task_id,
                    'round': round_no,
                    'passed': bool(event.get('passed', False)),
                    'reason': str(event.get('reason') or '').strip() or 'unknown',
                    'checks': self._coerce_evidence_checks(event.get('checks')),
                    'evidence_paths': self._coerce_evidence_paths(event.get('evidence_paths')),
                    'workspace_path': str(workspace_root),
                    'generated_at': datetime.now(timezone.utc).isoformat(),
                }
                try:
                    artifact_path = self.artifact_store.write_artifact_json(
                        task_id,
                        name=f'evidence_bundle_round_{int(round_no)}',
                        payload=evidence_payload,
                    )
                    evidence_payload['artifact_path'] = str(artifact_path)
                except Exception as exc:
                    _log.exception('evidence_bundle_artifact_write_failed task_id=%s round=%s', task_id, round_no)
                    evidence_payload['artifact_error'] = str(exc)
                latest_evidence_bundle[0] = dict(evidence_payload)
                self.repository.append_event(
                    task_id,
                    event_type='evidence_bundle_ready',
                    payload=evidence_payload,
                    round_number=round_no,
                )
                self.artifact_store.append_event(task_id, {'type': EventType.EVIDENCE_BUNDLE_READY.value, **evidence_payload})
                self.artifact_store.update_state(task_id, {'evidence_bundle_last': evidence_payload})
            if round_artifacts_enabled and event_type in {'gate_passed', 'gate_failed'} and round_no > 0:
                previous_snapshot = round_snapshot_holder[0]
                if previous_snapshot is not None:
                    try:
                        round_payload, new_snapshot = self._capture_round_artifacts(
                            task_id=task_id,
                            round_no=round_no,
                            previous_snapshot=previous_snapshot,
                            workspace_root=workspace_root,
                            gate_reason=str(event.get('reason') or ''),
                            gate_status=event_type,
                        )
                        round_snapshot_holder[0] = new_snapshot
                        self.repository.append_event(
                            task_id,
                            event_type='round_artifact_ready',
                            payload=round_payload,
                            round_number=round_no,
                        )
                        self.artifact_store.append_event(task_id, {'type': EventType.ROUND_ARTIFACT_READY.value, **round_payload})
                    except Exception as exc:
                        _log.exception('round_artifact_capture_failed task_id=%s round=%s', task_id, round_no)
                        error_payload = {
                            'round': round_no,
                            'reason': str(exc or 'round_artifact_error').strip() or 'round_artifact_error',
                        }
                        self.repository.append_event(
                            task_id,
                            event_type='round_artifact_error',
                            payload=error_payload,
                            round_number=round_no,
                        )
                        self.artifact_store.append_event(task_id, {'type': EventType.ROUND_ARTIFACT_ERROR.value, **error_payload})

        def should_cancel() -> bool:
            return self.repository.is_cancel_requested(task_id)

        try:
            author = parse_participant_id(row['author_participant'])
            reviewers = [parse_participant_id(v) for v in row['reviewer_participants']]
            baseline_manifest = self.fusion_manager.build_manifest(workspace_root)
            proposal_issue_contract = self._load_pending_proposal_contract(task_id)

            result = self.workflow_engine.run(
                RunConfig(
                    task_id=task_id,
                    title=row['title'],
                    description=row['description'],
                    author=author,
                    reviewers=reviewers,
                    evolution_level=max(0, min(3, int(row.get('evolution_level', 0)))),
                    evolve_until=(str(row.get('evolve_until')).strip() if row.get('evolve_until') else None),
                    conversation_language=normalize_conversation_language(row.get('conversation_language')),
                    provider_models=dict(row.get('provider_models', {})),
                    provider_model_params=dict(row.get('provider_model_params', {})),
                    participant_models=dict(row.get('participant_models', {})),
                    participant_model_params=dict(row.get('participant_model_params', {})),
                    claude_team_agents=bool(row.get('claude_team_agents', False)),
                    codex_multi_agents=bool(row.get('codex_multi_agents', False)),
                    claude_team_agents_overrides=dict(row.get('claude_team_agents_overrides', {})),
                    codex_multi_agents_overrides=dict(row.get('codex_multi_agents_overrides', {})),
                    repair_mode=normalize_repair_mode(row.get('repair_mode')),
                    memory_mode=memory_mode,
                    memory_context=stage_memory_context,
                    phase_timeout_seconds=normalize_phase_timeout_seconds(
                        row.get('phase_timeout_seconds'),
                        strict=False,
                    ),
                    plain_mode=normalize_plain_mode(row.get('plain_mode')),
                    stream_mode=normalize_bool_flag(row.get('stream_mode', True), default=True),
                    debate_mode=normalize_bool_flag(row.get('debate_mode', True), default=True),
                    cwd=Path(str(row.get('workspace_path') or Path.cwd())),
                    max_rounds=int(row['max_rounds']),
                    test_command=row['test_command'],
                    lint_command=row['lint_command'],
                    proposal_issue_contract=proposal_issue_contract,
                ),
                on_event=on_event,
                should_cancel=should_cancel,
            )
        except Exception as exc:
            _log.error('workflow_error task_id=%s', task_id, exc_info=True)
            return self.mark_failed_system(task_id, reason=f'workflow_error: {exc}')

        final_status = self._map_run_status(result.status)
        final_reason = result.gate_reason
        evidence_manifest_payload: dict | None = None
        if final_status == TaskStatus.PASSED and isinstance(self.workflow_engine, WorkflowEngine):
            evidence_guard = self._validate_evidence_bundle(
                evidence_bundle=latest_evidence_bundle[0],
                expected_round=max(1, int(result.rounds)),
            )
            if not bool(evidence_guard.get('ok', False)):
                final_status = TaskStatus.FAILED_GATE
                final_reason = str(evidence_guard.get('reason') or 'precompletion_evidence_missing')
                evidence_payload = {
                    'type': EventType.PRECOMPLETION_GUARD_FAILED.value,
                    'reason': final_reason,
                    'expected_round': max(1, int(result.rounds)),
                    'evidence_bundle': dict(latest_evidence_bundle[0] or {}),
                }
                self.repository.append_event(
                    task_id,
                    event_type='precompletion_guard_failed',
                    payload=evidence_payload,
                    round_number=max(1, int(result.rounds)),
                )
                self.artifact_store.append_event(task_id, evidence_payload)
                self.artifact_store.write_artifact_json(
                    task_id,
                    name='precompletion_guard_failed',
                    payload=evidence_payload,
                )
        if final_status == TaskStatus.PASSED and isinstance(self.workflow_engine, WorkflowEngine):
            manifest_result = self._write_evidence_manifest(
                task_id=task_id,
                row=row,
                workspace_root=workspace_root,
                rounds_completed=int(result.rounds),
                status=final_status.value,
                reason=str(final_reason or ''),
                preflight_guard=preflight_guard,
                evidence_bundle=latest_evidence_bundle[0],
                head_snapshot={
                    'workspace_head_sha': head_guard_payload.get('workspace_head_sha'),
                    'merge_target_head_sha': head_guard_payload.get('merge_target_head_sha'),
                },
            )
            evidence_manifest_payload = dict(manifest_result)
            if not bool(manifest_result.get('ok', False)):
                final_status = TaskStatus.FAILED_GATE
                final_reason = str(manifest_result.get('reason') or 'precompletion_evidence_missing')
                failure_payload = {
                    'type': EventType.EVIDENCE_MANIFEST_FAILED.value,
                    'reason': final_reason,
                    'manifest': manifest_result,
                }
                self.repository.append_event(
                    task_id,
                    event_type='evidence_manifest_failed',
                    payload=failure_payload,
                    round_number=max(1, int(result.rounds)),
                )
                self.artifact_store.append_event(task_id, failure_payload)
                self.artifact_store.write_artifact_json(
                    task_id,
                    name='evidence_manifest_failed',
                    payload=failure_payload,
                )
            else:
                self.repository.append_event(
                    task_id,
                    event_type='evidence_manifest_ready',
                    payload=manifest_result,
                    round_number=max(1, int(result.rounds)),
                )
                self.artifact_store.append_event(task_id, {'type': EventType.EVIDENCE_MANIFEST_READY.value, **manifest_result})
                self.artifact_store.update_state(task_id, {'evidence_manifest_last': manifest_result})
        _log.info('task_finished task_id=%s status=%s rounds=%d reason=%s',
                  task_id, final_status.value, result.rounds, final_reason)

        # Atomic conditional update: only write the final status if the task
        # is still RUNNING.  If an external force_fail already transitioned it,
        # the update returns None and we honour the external state.
        updated = self.repository.update_task_status_if(
            task_id,
            expected_status=TaskStatus.RUNNING.value,
            status=final_status.value,
            reason=final_reason,
            rounds_completed=result.rounds,
            set_cancel_requested=False,
        )
        if updated is None:
            latest = self.repository.get_task(task_id)
            if latest is None:
                raise KeyError(task_id)
            return self._to_view(latest)

        state_payload = {
            'status': final_status.value,
            'last_gate_reason': final_reason,
            'rounds_completed': result.rounds,
            'cancel_requested': False,
        }
        if evidence_manifest_payload is not None:
            state_payload['evidence_manifest_last'] = evidence_manifest_payload
        self.artifact_store.update_state(task_id, state_payload)
        self.artifact_store.write_final_report(
            task_id,
            f"status={final_status.value}\nrounds={result.rounds}\nreason={final_reason}",
        )

        if final_status == TaskStatus.PASSED and bool(row.get('auto_merge', True)):
            try:
                target_root = self._resolve_merge_target(row)
                current_target_head = self._read_git_head_sha(target_root)
                if merge_target_is_git and not current_target_head:
                    blocked_reason = 'head_sha_missing: merge_target_end_sha_missing'
                    missing_payload = {
                        'reason': blocked_reason,
                        'phase': 'end',
                        'before': merge_target_head_before_run,
                        'current': current_target_head,
                        'target_path': str(target_root),
                    }
                    self.repository.append_event(
                        task_id,
                        event_type='head_sha_missing',
                        payload=missing_payload,
                        round_number=None,
                    )
                    self.artifact_store.append_event(task_id, {'type': EventType.HEAD_SHA_MISSING.value, **missing_payload})
                    updated = self.repository.update_task_status(
                        task_id,
                        status=TaskStatus.FAILED_GATE.value,
                        reason=blocked_reason,
                        rounds_completed=result.rounds,
                    )
                    self.artifact_store.update_state(
                        task_id,
                        {
                            'status': TaskStatus.FAILED_GATE.value,
                            'last_gate_reason': blocked_reason,
                            'rounds_completed': result.rounds,
                            'head_sha_missing_last': missing_payload,
                        },
                    )
                    self.artifact_store.write_final_report(task_id, f'status=failed_gate\nreason={blocked_reason}')
                    self._emit_regression_case(
                        task_id=task_id,
                        row=updated,
                        status=TaskStatus.FAILED_GATE,
                        reason=blocked_reason,
                    )
                    return self._to_view(updated)
                if merge_target_head_before_run and current_target_head and merge_target_head_before_run != current_target_head:
                    blocked_reason = (
                        'head_sha_mismatch: merge_target_head_changed '
                        f'{merge_target_head_before_run[:12]}->{current_target_head[:12]}'
                    )
                    mismatch_payload = {
                        'reason': blocked_reason,
                        'before': merge_target_head_before_run,
                        'current': current_target_head,
                        'target_path': str(target_root),
                    }
                    self.repository.append_event(
                        task_id,
                        event_type='head_sha_mismatch',
                        payload=mismatch_payload,
                        round_number=None,
                    )
                    self.artifact_store.append_event(task_id, {'type': EventType.HEAD_SHA_MISMATCH.value, **mismatch_payload})
                    updated = self.repository.update_task_status(
                        task_id,
                        status=TaskStatus.FAILED_GATE.value,
                        reason=blocked_reason,
                        rounds_completed=result.rounds,
                    )
                    self.artifact_store.update_state(
                        task_id,
                        {
                            'status': TaskStatus.FAILED_GATE.value,
                            'last_gate_reason': blocked_reason,
                            'rounds_completed': result.rounds,
                            'head_sha_mismatch_last': mismatch_payload,
                        },
                    )
                    self.artifact_store.write_final_report(task_id, f'status=failed_gate\nreason={blocked_reason}')
                    self._emit_regression_case(
                        task_id=task_id,
                        row=updated,
                        status=TaskStatus.FAILED_GATE,
                        reason=blocked_reason,
                    )
                    return self._to_view(updated)
                guard = self._evaluate_promotion_guard(target_root=target_root)
                self.repository.append_event(
                    task_id,
                    event_type='promotion_guard_checked',
                    payload=guard,
                    round_number=None,
                )
                self.artifact_store.append_event(task_id, {'type': EventType.PROMOTION_GUARD_CHECKED.value, **guard})
                self.artifact_store.update_state(task_id, {'promotion_guard_last': guard})
                if not bool(guard.get('guard_allowed', True)):
                    blocked_reason = f'promotion_guard_blocked: {guard.get("guard_reason") or "blocked"}'
                    self.repository.append_event(
                        task_id,
                        event_type='promotion_guard_blocked',
                        payload={'reason': blocked_reason, **guard},
                        round_number=None,
                    )
                    self.artifact_store.append_event(
                        task_id,
                        {'type': EventType.PROMOTION_GUARD_BLOCKED.value, 'reason': blocked_reason, **guard},
                    )
                    updated = self.repository.update_task_status(
                        task_id,
                        status=TaskStatus.FAILED_GATE.value,
                        reason=blocked_reason,
                        rounds_completed=result.rounds,
                    )
                    self.artifact_store.update_state(
                        task_id,
                        {
                            'status': TaskStatus.FAILED_GATE.value,
                            'last_gate_reason': blocked_reason,
                            'rounds_completed': result.rounds,
                        },
                    )
                    self.artifact_store.write_final_report(task_id, f'status=failed_gate\nreason={blocked_reason}')
                    self._emit_regression_case(
                        task_id=task_id,
                        row=updated,
                        status=TaskStatus.FAILED_GATE,
                        reason=blocked_reason,
                    )
                    return self._to_view(updated)
                fusion = self.fusion_manager.run(
                    task_id=task_id,
                    source_root=workspace_root,
                    target_root=target_root,
                    before_manifest=baseline_manifest,
                )
                fusion_payload = {
                    'source_path': fusion.source_path,
                    'target_path': fusion.target_path,
                    'changed_files': fusion.changed_files,
                    'copied_files': fusion.copied_files,
                    'deleted_files': fusion.deleted_files,
                    'snapshot_path': fusion.snapshot_path,
                    'changelog_path': fusion.changelog_path,
                    'merged_at': fusion.merged_at,
                    'mode': fusion.mode,
                }
                self.repository.append_event(
                    task_id,
                    event_type='auto_merge_completed',
                    payload=fusion_payload,
                    round_number=None,
                )
                self.artifact_store.write_artifact_json(task_id, name='auto_merge_summary', payload=fusion_payload)
                self.artifact_store.update_state(task_id, {'auto_merge_last': fusion_payload})

                cleanup_payload = self._cleanup_sandbox_after_merge(row=row, workspace_root=workspace_root)
                if cleanup_payload is not None:
                    event_type = 'sandbox_cleanup_completed' if cleanup_payload.get('ok') else 'sandbox_cleanup_failed'
                    self.repository.append_event(
                        task_id,
                        event_type=event_type,
                        payload=cleanup_payload,
                        round_number=None,
                    )
                    self.artifact_store.append_event(task_id, {'type': event_type, **cleanup_payload})
                    self.artifact_store.update_state(task_id, {'sandbox_cleanup_last': cleanup_payload})
            except Exception as exc:
                _log.exception('auto_merge_or_cleanup_failed task_id=%s', task_id)
                return self.mark_failed_system(task_id, reason=f'auto_merge_error: {exc}')

        if final_status in {TaskStatus.FAILED_GATE, TaskStatus.FAILED_SYSTEM}:
            self._emit_regression_case(
                task_id=task_id,
                row=updated,
                status=final_status,
                reason=str(final_reason or ''),
            )
        self._persist_memory_outcome(
            task_id=task_id,
            row=updated,
            status=final_status,
            reason=str(final_reason or ''),
        )
        return self._to_view(updated)

    def submit_author_decision(
        self,
        task_id: str,
        *,
        approve: bool | None = None,
        decision: str | None = None,
        note: str | None = None,
    ) -> TaskView:
        row = self.repository.get_task(task_id)
        if row is None:
            raise KeyError(task_id)
        if row['status'] in {TaskStatus.PASSED.value, TaskStatus.CANCELED.value}:
            return self._to_view(row)
        if row['status'] != TaskStatus.WAITING_MANUAL.value:
            return self._to_view(row)

        decision_text = str(decision or '').strip().lower()
        if decision_text:
            if decision_text not in {'approve', 'reject', 'revise'}:
                raise InputValidationError(
                    f'invalid author decision: {decision_text}',
                    field='decision',
                )
        else:
            decision_text = 'approve' if bool(approve) else 'reject'

        note_text = str(note or '').strip() or None
        payload = {
            'decision': decision_text,
            'note': note_text,
        }
        self.repository.append_event(
            task_id,
            event_type='author_decision',
            payload=payload,
            round_number=None,
        )
        self.artifact_store.append_event(
            task_id,
            {'type': EventType.AUTHOR_DECISION.value, **payload},
        )

        rounds = int(row.get('rounds_completed', 0))
        if decision_text == 'approve':
            self.repository.set_cancel_requested(task_id, requested=False)
            updated = self.repository.update_task_status(
                task_id,
                status=TaskStatus.QUEUED.value,
                reason='author_approved',
                rounds_completed=rounds,
            )
            self.artifact_store.update_state(
                task_id,
                {
                    'status': TaskStatus.QUEUED.value,
                    'last_gate_reason': 'author_approved',
                    'cancel_requested': False,
                    'author_decision': payload,
                },
            )
            return self._to_view(updated)

        if decision_text == 'revise':
            feedback_payload = {
                'decision': 'revise',
                'note': note_text,
            }
            self.repository.append_event(
                task_id,
                event_type='author_feedback_requested',
                payload=feedback_payload,
                round_number=None,
            )
            self.artifact_store.append_event(
                task_id,
                {'type': EventType.AUTHOR_FEEDBACK_REQUESTED.value, **feedback_payload},
            )
            self.repository.set_cancel_requested(task_id, requested=False)
            updated = self.repository.update_task_status(
                task_id,
                status=TaskStatus.QUEUED.value,
                reason='author_feedback_requested',
                rounds_completed=rounds,
            )
            self.artifact_store.update_state(
                task_id,
                {
                    'status': TaskStatus.QUEUED.value,
                    'last_gate_reason': 'author_feedback_requested',
                    'cancel_requested': False,
                    'author_decision': payload,
                    'author_feedback_requested': feedback_payload,
                },
            )
            return self._to_view(updated)

        self.repository.set_cancel_requested(task_id, requested=True)
        updated = self.repository.update_task_status(
            task_id,
            status=TaskStatus.CANCELED.value,
            reason='author_rejected',
            rounds_completed=rounds,
        )
        self.artifact_store.update_state(
            task_id,
            {
                'status': TaskStatus.CANCELED.value,
                'last_gate_reason': 'author_rejected',
                'cancel_requested': True,
                'author_decision': payload,
            },
        )
        self.artifact_store.write_final_report(task_id, 'status=canceled\nreason=author_rejected')
        return self._to_view(updated)

    def _latest_author_feedback_note(self, task_id: str) -> str | None:
        try:
            events = self.repository.list_events(task_id)
        except Exception:
            _log.exception('latest_author_feedback_list_events_failed task_id=%s', task_id)
            return None
        for event in reversed(events):
            event_type = str(event.get('type') or '').strip().lower()
            payload = event.get('payload')
            payload_obj = payload if isinstance(payload, dict) else {}
            if event_type == 'author_feedback_requested':
                note = str(payload_obj.get('note') or '').strip()
                if note:
                    return note
            if event_type == 'author_decision':
                decision = str(payload_obj.get('decision') or '').strip().lower()
                if decision == 'revise':
                    note = str(payload_obj.get('note') or '').strip()
                    if note:
                        return note
        return None

    def evaluate_gate(self, task_id: str, payload: GateInput) -> TaskView:
        outcome = evaluate_medium_gate(
            tests_ok=payload.tests_ok,
            lint_ok=payload.lint_ok,
            reviewer_verdicts=payload.reviewer_verdicts,
        )
        next_status = TaskStatus.PASSED if outcome.passed else TaskStatus.FAILED_GATE
        row = self.repository.update_task_status(
            task_id,
            status=next_status.value,
            reason=outcome.reason,
            rounds_completed=None,
        )
        self.repository.append_event(
            task_id,
            event_type='manual_gate',
            payload={
                'tests_ok': payload.tests_ok,
                'lint_ok': payload.lint_ok,
                'reviewer_verdicts': [v.value for v in payload.reviewer_verdicts],
                'result': outcome.reason,
            },
            round_number=None,
        )
        return self._to_view(row)

    def promote_selected_round(
        self,
        task_id: str,
        *,
        round_number: int,
        merge_target_path: str | None = None,
    ) -> dict:
        row = self.repository.get_task(task_id)
        if row is None:
            raise KeyError(task_id)

        if bool(row.get('auto_merge', True)):
            raise InputValidationError(
                'promote_selected_round is available only when auto_merge=0',
                field='auto_merge',
            )
        if int(row.get('max_rounds', 1)) <= 1:
            raise InputValidationError(
                'promote_selected_round is available only when max_rounds>1',
                field='max_rounds',
            )
        if str(row.get('status') or '') not in _TERMINAL_STATUSES:
            raise InputValidationError(
                'promote_selected_round requires terminal task status',
                field='status',
            )

        round_no = max(1, int(round_number))
        rounds_root = self._round_artifacts_root(task_id)
        source_snapshot = self._round_snapshot_dir(rounds_root, round_no)
        if not source_snapshot.exists() or not source_snapshot.is_dir():
            raise InputValidationError(
                f'round snapshot not found for round {round_no}',
                field='round',
            )
        baseline_snapshot = self._round_snapshot_dir(rounds_root, 0)
        if not baseline_snapshot.exists() or not baseline_snapshot.is_dir():
            raise InputValidationError(
                'round baseline snapshot missing',
                field='round',
            )

        target_text = (
            normalize_merge_target_path(merge_target_path)
            or normalize_merge_target_path(row.get('merge_target_path'))
            or str(row.get('project_path') or row.get('workspace_path') or '').strip()
        )
        target_root = Path(str(target_text)).resolve()
        if not target_root.exists() or not target_root.is_dir():
            raise InputValidationError(
                'merge_target_path must be an existing directory',
                field='merge_target_path',
            )

        guard = self._evaluate_promotion_guard(target_root=target_root)
        self.repository.append_event(
            task_id,
            event_type='promotion_guard_checked',
            payload=guard,
            round_number=round_no,
        )
        self.artifact_store.append_event(task_id, {'type': EventType.PROMOTION_GUARD_CHECKED.value, **guard})
        self.artifact_store.update_state(task_id, {'promotion_guard_last': guard})
        if not bool(guard.get('guard_allowed', True)):
            raise InputValidationError(
                f'promotion guard blocked: {guard.get("guard_reason") or "blocked"}',
                field='merge_target_path',
                code='promotion_guard_blocked',
            )

        before_manifest = self.fusion_manager.build_manifest(baseline_snapshot)
        fusion = self.fusion_manager.run(
            task_id=f'{task_id}-round-{round_no}',
            source_root=source_snapshot,
            target_root=target_root,
            before_manifest=before_manifest,
        )
        payload = {
            'task_id': task_id,
            'round': round_no,
            'source_snapshot_path': str(source_snapshot),
            'target_path': str(target_root),
            'changed_files': fusion.changed_files,
            'copied_files': fusion.copied_files,
            'deleted_files': fusion.deleted_files,
            'snapshot_path': fusion.snapshot_path,
            'changelog_path': fusion.changelog_path,
            'merged_at': fusion.merged_at,
            'mode': fusion.mode,
        }
        self.repository.append_event(
            task_id,
            event_type='manual_round_promoted',
            payload=payload,
            round_number=round_no,
        )
        self.artifact_store.append_event(task_id, {'type': EventType.MANUAL_ROUND_PROMOTED.value, **payload})
        self.artifact_store.write_artifact_json(
            task_id,
            name=f'round-{round_no}-promote-summary',
            payload=payload,
        )
        self.artifact_store.update_state(
            task_id,
            {
                'last_promoted_round': round_no,
                'last_promote_summary': payload,
            },
        )
        return payload

    def _prepare_author_confirmation(
        self,
        task_id: str,
        row: dict,
        *,
        auto_approve: bool = False,
        memory_mode: str = 'basic',
        memory_context: dict[str, str] | None = None,
    ) -> TaskView:
        summary = (
            f"Task: {str(row.get('title') or '')}\n"
            "Generated proposal requires author approval before implementation."
        )
        review_payload: list[dict] = []
        proposal_contract: dict[str, object] = {}
        last_author_issue_validation: dict[str, object] = {}
        consensus_rounds = 0
        # Proposal consensus is a pre-execution checkpoint and should complete once
        # per task start. max_rounds is reserved for execution/review loop rounds.
        target_rounds = 1
        retry_limit = self._proposal_stall_retry_limit()
        repeat_round_limit = self._proposal_repeat_rounds_limit()

        try:
            runner = getattr(self.workflow_engine, 'runner', None)
            timeout = int(getattr(self.workflow_engine, 'participant_timeout_seconds', 3600))
            author = parse_participant_id(str(row['author_participant']))
            reviewers = [parse_participant_id(v) for v in row.get('reviewer_participants', [])]
            config = RunConfig(
                task_id=task_id,
                title=str(row.get('title', '')),
                description=str(row.get('description', '')),
                author=author,
                reviewers=reviewers,
                evolution_level=max(0, min(3, int(row.get('evolution_level', 0)))),
                evolve_until=(str(row.get('evolve_until')).strip() if row.get('evolve_until') else None),
                conversation_language=normalize_conversation_language(row.get('conversation_language')),
                provider_models=dict(row.get('provider_models', {})),
                provider_model_params=dict(row.get('provider_model_params', {})),
                participant_models=dict(row.get('participant_models', {})),
                participant_model_params=dict(row.get('participant_model_params', {})),
                claude_team_agents=bool(row.get('claude_team_agents', False)),
                codex_multi_agents=bool(row.get('codex_multi_agents', False)),
                claude_team_agents_overrides=dict(row.get('claude_team_agents_overrides', {})),
                codex_multi_agents_overrides=dict(row.get('codex_multi_agents_overrides', {})),
                repair_mode=normalize_repair_mode(row.get('repair_mode')),
                memory_mode=normalize_memory_mode(memory_mode),
                memory_context={
                    str(k): str(v)
                    for k, v in dict(memory_context or {}).items()
                    if str(k).strip() and str(v).strip()
                },
                phase_timeout_seconds=normalize_phase_timeout_seconds(
                    row.get('phase_timeout_seconds'),
                    strict=False,
                ),
                plain_mode=normalize_plain_mode(row.get('plain_mode')),
                stream_mode=normalize_bool_flag(row.get('stream_mode', True), default=True),
                debate_mode=normalize_bool_flag(row.get('debate_mode', True), default=True),
                cwd=Path(str(row.get('workspace_path') or Path.cwd())),
                max_rounds=int(row.get('max_rounds', 3)),
                test_command=str(row.get('test_command', 'python -m pytest -q')),
                lint_command=str(row.get('lint_command', 'python -m ruff check .')),
            )
            review_timeout = self._resolve_phase_timeout_seconds(
                phase_timeout_seconds=config.phase_timeout_seconds,
                phase='review',
                fallback=self._review_timeout_seconds(timeout),
            )
            proposal_timeout = self._resolve_phase_timeout_seconds(
                phase_timeout_seconds=config.phase_timeout_seconds,
                phase='proposal',
                fallback=timeout,
            )
            proposal_environment_context = build_environment_context(
                cwd=Path(config.cwd),
                test_command=config.test_command,
                lint_command=config.lint_command,
            )
            claude_team_agents_overrides = normalize_participant_agent_overrides_runtime(
                config.claude_team_agents_overrides
            )
            codex_multi_agents_overrides = normalize_participant_agent_overrides_runtime(
                config.codex_multi_agents_overrides
            )

            discussion_text = str(row.get('description') or '').strip()
            author_feedback_note = self._latest_author_feedback_note(task_id)
            if author_feedback_note:
                feedback_prefix = 'Operator custom feedback (must be addressed in next proposal):'
                feedback_block = f'{feedback_prefix}\n- {author_feedback_note}'
                discussion_text = f'{discussion_text}\n\n{feedback_block}'.strip() if discussion_text else feedback_block
            if runner is not None:
                def emit_runtime_event(event: dict) -> None:
                    self.repository.append_event(
                        task_id,
                        event_type=str(event.get('type', 'participant_stream')),
                        payload=event,
                        round_number=event.get('round'),
                    )
                    self.artifact_store.append_event(task_id, event)

                def run_proposal_reviewer_pass(
                    source_text: str,
                    *,
                    round_no: int,
                    stage: str,
                ) -> tuple[list[dict], str]:
                    payloads: list[dict] = []
                    merged_context = str(source_text or '').strip()
                    for reviewer in reviewers:
                        started_type = f'{stage}_started'
                        error_type = f'{stage}_error'
                        review_started = {
                            'type': started_type,
                            'round': round_no,
                            'participant': reviewer.participant_id,
                            'provider': reviewer.provider,
                            'timeout_seconds': review_timeout,
                        }
                        self.repository.append_event(
                            task_id,
                            event_type=started_type,
                            payload=review_started,
                            round_number=round_no,
                        )
                        self.artifact_store.append_event(task_id, review_started)
                        try:
                            review = runner.run(
                                participant=reviewer,
                                prompt=self._proposal_review_prompt(
                                    config,
                                    merged_context,
                                    stage=stage,
                                    environment_context=proposal_environment_context,
                                    memory_context=(config.memory_context or {}).get('proposal'),
                                ),
                                cwd=config.cwd,
                                timeout_seconds=review_timeout,
                                model=resolve_model_for_participant(
                                    participant_id=reviewer.participant_id,
                                    provider=reviewer.provider,
                                    provider_models=config.provider_models,
                                    participant_models=config.participant_models,
                                ),
                                model_params=resolve_model_params_for_participant(
                                    participant_id=reviewer.participant_id,
                                    provider=reviewer.provider,
                                    provider_model_params=config.provider_model_params,
                                    participant_model_params=config.participant_model_params,
                                ),
                                claude_team_agents=resolve_agent_toggle_for_participant(
                                    participant_id=reviewer.participant_id,
                                    global_enabled=bool(config.claude_team_agents),
                                    overrides=claude_team_agents_overrides,
                                ),
                                codex_multi_agents=resolve_agent_toggle_for_participant(
                                    participant_id=reviewer.participant_id,
                                    global_enabled=bool(config.codex_multi_agents),
                                    overrides=codex_multi_agents_overrides,
                                ),
                                on_stream=(
                                    WorkflowEngine._stream_emitter(
                                        emit=emit_runtime_event,
                                        round_no=round_no,
                                        stage=stage,
                                        participant=reviewer.participant_id,
                                        provider=reviewer.provider,
                                    )
                                    if bool(config.stream_mode)
                                    else None
                                ),
                            )
                            verdict = WorkflowEngine._normalize_verdict(str(getattr(review, 'verdict', '') or ''))
                            review_text = str(getattr(review, 'output', '') or '').strip()
                            verdict, review_text = self._normalize_proposal_reviewer_result(
                                config=config,
                                stage=stage,
                                verdict=verdict,
                                review_text=review_text,
                            )
                        except Exception as exc:
                            _log.exception(
                                'proposal_reviewer_stage_failed task_id=%s round=%s participant=%s',
                                task_id,
                                round_no,
                                reviewer.participant_id,
                            )
                            reason = str(exc or 'review_failed').strip() or 'review_failed'
                            error_payload = {
                                'type': error_type,
                                'round': round_no,
                                'participant': reviewer.participant_id,
                                'provider': reviewer.provider,
                                'reason': reason,
                            }
                            self.repository.append_event(
                                task_id,
                                event_type=error_type,
                                payload=error_payload,
                                round_number=round_no,
                            )
                            self.artifact_store.append_event(task_id, error_payload)
                            verdict = ReviewVerdict.UNKNOWN
                            review_text = f'[{error_type}] {reason}'
                        parsed_issues = parse_reviewer_issues(
                            output=review_text,
                            verdict=verdict.value,
                        )
                        contract_ok = not (
                            verdict in {ReviewVerdict.BLOCKER, ReviewVerdict.UNKNOWN}
                            and len(parsed_issues) == 0
                        )
                        payload = {
                            'type': stage,
                            'round': round_no,
                            'participant': reviewer.participant_id,
                            'provider': reviewer.provider,
                            'verdict': verdict.value,
                            'output': review_text,
                            'issues': parsed_issues,
                            'issue_contract_ok': contract_ok,
                        }
                        payloads.append(payload)
                        self.repository.append_event(
                            task_id,
                            event_type='proposal_review',
                            payload=payload,
                            round_number=round_no,
                        )
                        self.artifact_store.append_event(task_id, payload)
                        if review_text:
                            self.artifact_store.append_discussion(
                                task_id,
                                role=f'{stage}:{reviewer.participant_id}',
                                round_number=round_no,
                                content=review_text,
                            )
                        merged_context = self._append_proposal_feedback_context(
                            merged_context,
                            reviewer_id=reviewer.participant_id,
                            review_text=review_text,
                        )
                    return payloads, merged_context

                proposal_seed = discussion_text or str(row.get('title') or '').strip()
                current_seed = proposal_seed
                # Consensus stage is reviewer-first for all modes.
                reviewer_first_mode = bool(reviewers)
                proposal_deadline = WorkflowEngine._parse_deadline(config.evolve_until)

                def finish_proposal_terminal(
                    *,
                    status: TaskStatus,
                    reason: str,
                    rounds_completed: int,
                    event_type: str,
                    payload: dict,
                    round_number: int | None,
                ) -> TaskView:
                    updated = self.repository.update_task_status(
                        task_id,
                        status=status.value,
                        reason=reason,
                        rounds_completed=rounds_completed,
                    )
                    self.repository.append_event(
                        task_id,
                        event_type=event_type,
                        payload=payload,
                        round_number=round_number,
                    )
                    self.artifact_store.append_event(task_id, {'type': event_type, **payload})
                    self.artifact_store.update_state(
                        task_id,
                        {
                            'status': status.value,
                            'last_gate_reason': reason,
                            'rounds_completed': rounds_completed,
                        },
                    )
                    self.artifact_store.write_final_report(
                        task_id,
                        f"status={status.value}\nreason={reason}",
                    )
                    return self._to_view(updated)

                def finish_proposal_stalled(
                    *,
                    reason: str,
                    summary_text: str,
                    rounds_completed: int,
                    round_number: int | None,
                    stall_payload: dict,
                    latest_reviews: list[dict],
                ) -> TaskView:
                    waiting = self.repository.update_task_status(
                        task_id,
                        status=TaskStatus.WAITING_MANUAL.value,
                        reason=reason,
                        rounds_completed=rounds_completed,
                    )
                    self.repository.append_event(
                        task_id,
                        event_type='proposal_consensus_stalled',
                        payload=stall_payload,
                        round_number=round_number,
                    )
                    self.artifact_store.append_event(
                        task_id,
                        {'type': EventType.PROPOSAL_CONSENSUS_STALLED.value, **stall_payload},
                    )
                    pending_payload = {
                        'summary': summary_text,
                        'self_loop_mode': int(row.get('self_loop_mode', 0)),
                        'consensus_rounds': rounds_completed,
                        'target_rounds': target_rounds,
                        'review_payload': list(latest_reviews),
                        'proposal_contract': dict(proposal_contract or {}),
                        'author_issue_validation': dict(last_author_issue_validation or {}),
                        'author_feedback_note': author_feedback_note,
                        'stall': stall_payload,
                    }
                    self.repository.append_event(
                        task_id,
                        event_type='author_confirmation_required',
                        payload=pending_payload,
                        round_number=None,
                    )
                    self.artifact_store.append_event(
                        task_id,
                        {'type': EventType.AUTHOR_CONFIRMATION_REQUIRED.value, **pending_payload},
                    )
                    self.artifact_store.write_artifact_json(
                        task_id,
                        name='pending_proposal',
                        payload=pending_payload,
                    )
                    self.artifact_store.write_artifact_json(
                        task_id,
                        name='consensus_stall',
                        payload={'reason': reason, **stall_payload},
                    )
                    self.artifact_store.update_state(
                        task_id,
                        {
                            'status': TaskStatus.WAITING_MANUAL.value,
                            'last_gate_reason': reason,
                            'rounds_completed': rounds_completed,
                            'pending_proposal': pending_payload,
                        },
                    )
                    self.artifact_store.write_final_report(
                        task_id,
                        f"status={TaskStatus.WAITING_MANUAL.value}\nreason={reason}",
                    )
                    return self._to_view(waiting)

                last_round_signature = ''
                repeated_signature_rounds = 0

                while consensus_rounds < target_rounds:
                    round_no = consensus_rounds + 1
                    attempt = 0
                    consensus_reached = False
                    round_latest_reviews: list[dict] = []
                    round_latest_proposal = current_seed

                    while not consensus_reached:
                        if self.repository.is_cancel_requested(task_id):
                            cancel_payload = {
                                'round': round_no,
                                'attempt': attempt,
                                'consensus_rounds': consensus_rounds,
                                'target_rounds': target_rounds,
                            }
                            return finish_proposal_terminal(
                                status=TaskStatus.CANCELED,
                                reason='canceled',
                                rounds_completed=consensus_rounds,
                                event_type='proposal_canceled',
                                payload=cancel_payload,
                                round_number=round_no,
                            )
                        if proposal_deadline is not None and datetime.now(timezone.utc) >= proposal_deadline:
                            deadline_payload = {
                                'round': round_no,
                                'attempt': attempt,
                                'deadline': proposal_deadline.isoformat(),
                                'consensus_rounds': consensus_rounds,
                                'target_rounds': target_rounds,
                            }
                            return finish_proposal_terminal(
                                status=TaskStatus.CANCELED,
                                reason='deadline_reached',
                                rounds_completed=consensus_rounds,
                                event_type='proposal_deadline_reached',
                                payload=deadline_payload,
                                round_number=round_no,
                            )

                        attempt += 1
                        pre_reviews: list[dict] = []
                        merged_context = current_seed
                        if reviewer_first_mode:
                            pre_reviews, merged_context = run_proposal_reviewer_pass(
                                merged_context,
                                round_no=round_no,
                                stage='proposal_precheck_review',
                            )
                            precheck_actionable_reviews = [
                                item
                                for item in pre_reviews
                                if self._is_actionable_proposal_review_text(str(item.get('output') or ''))
                            ]
                            precheck_contract = validate_reviewer_issue_contract(precheck_actionable_reviews)
                            if not bool(precheck_contract.get('ok', False)):
                                contract_payload = {
                                    'round': round_no,
                                    'attempt': attempt,
                                    'stage': 'proposal_precheck_review',
                                    'missing_issue_participants': list(precheck_contract.get('missing_issue_participants') or []),
                                }
                                self.repository.append_event(
                                    task_id,
                                    event_type='proposal_review_contract_violation',
                                    payload=contract_payload,
                                    round_number=round_no,
                                )
                                self.artifact_store.append_event(
                                    task_id,
                                    {'type': EventType.PROPOSAL_REVIEW_CONTRACT_VIOLATION.value, **contract_payload},
                                )
                                current_seed = self._append_proposal_feedback_context(
                                    merged_context,
                                    reviewer_id='contract',
                                    review_text=(
                                        'proposal_precheck_review contract violation: '
                                        f"missing structured issues from {', '.join(list(precheck_contract.get('missing_issue_participants') or []))}"
                                    ),
                                )
                                continue
                            if pre_reviews and self._proposal_review_usable_count(pre_reviews) == 0:
                                fail_reason = 'proposal_precheck_unavailable'
                                fail_payload = {
                                    'round': round_no,
                                    'attempt': attempt,
                                    'reviewers_total': len(pre_reviews),
                                    'reviewers_usable': 0,
                                    'latest_reviews': pre_reviews,
                                }
                                failed = self.repository.update_task_status(
                                    task_id,
                                    status=TaskStatus.FAILED_GATE.value,
                                    reason=fail_reason,
                                    rounds_completed=consensus_rounds,
                                )
                                self.repository.append_event(
                                    task_id,
                                    event_type='proposal_precheck_unavailable',
                                    payload=fail_payload,
                                    round_number=round_no,
                                )
                                self.artifact_store.append_event(
                                    task_id,
                                    {'type': EventType.PROPOSAL_PRECHECK_UNAVAILABLE.value, **fail_payload},
                                )
                                self.artifact_store.update_state(
                                    task_id,
                                    {
                                        'status': TaskStatus.FAILED_GATE.value,
                                        'last_gate_reason': fail_reason,
                                        'rounds_completed': consensus_rounds,
                                    },
                                )
                                self.artifact_store.write_final_report(task_id, f'status=failed_gate\nreason={fail_reason}')
                                return self._to_view(failed)

                        discussion_started = {
                            'type': EventType.PROPOSAL_DISCUSSION_STARTED.value,
                            'round': round_no,
                            'provider': author.provider,
                            'participant': author.participant_id,
                            'timeout_seconds': proposal_timeout,
                            'attempt': attempt,
                        }
                        self.repository.append_event(
                            task_id,
                            event_type='proposal_discussion_started',
                            payload=discussion_started,
                            round_number=round_no,
                        )
                        self.artifact_store.append_event(task_id, discussion_started)

                        discussion_prompt = (
                            self._proposal_author_prompt(
                                config,
                                merged_context,
                                pre_reviews,
                                environment_context=proposal_environment_context,
                                memory_context=(config.memory_context or {}).get('discussion'),
                            )
                            if reviewer_first_mode
                            else WorkflowEngine._discussion_prompt(
                                config,
                                round_no,
                                None,
                                environment_context=proposal_environment_context,
                                memory_context=(config.memory_context or {}).get('discussion'),
                            )
                        )
                        try:
                            discussion = runner.run(
                                participant=author,
                                prompt=discussion_prompt,
                                cwd=config.cwd,
                                timeout_seconds=proposal_timeout,
                                model=resolve_model_for_participant(
                                    participant_id=author.participant_id,
                                    provider=author.provider,
                                    provider_models=config.provider_models,
                                    participant_models=config.participant_models,
                                ),
                                model_params=resolve_model_params_for_participant(
                                    participant_id=author.participant_id,
                                    provider=author.provider,
                                    provider_model_params=config.provider_model_params,
                                    participant_model_params=config.participant_model_params,
                                ),
                                claude_team_agents=resolve_agent_toggle_for_participant(
                                    participant_id=author.participant_id,
                                    global_enabled=bool(config.claude_team_agents),
                                    overrides=claude_team_agents_overrides,
                                ),
                                codex_multi_agents=resolve_agent_toggle_for_participant(
                                    participant_id=author.participant_id,
                                    global_enabled=bool(config.codex_multi_agents),
                                    overrides=codex_multi_agents_overrides,
                                ),
                                on_stream=(
                                    WorkflowEngine._stream_emitter(
                                        emit=emit_runtime_event,
                                        round_no=round_no,
                                        stage='proposal_discussion',
                                        participant=author.participant_id,
                                        provider=author.provider,
                                    )
                                    if bool(config.stream_mode)
                                    else None
                                ),
                            )
                        except Exception as exc:
                            _log.exception(
                                'proposal_discussion_failed task_id=%s round=%s participant=%s',
                                task_id,
                                round_no,
                                author.participant_id,
                            )
                            reason = str(exc or 'discussion_failed').strip() or 'discussion_failed'
                            error_payload = {
                                'type': EventType.PROPOSAL_DISCUSSION_ERROR.value,
                                'round': round_no,
                                'attempt': attempt,
                                'participant': author.participant_id,
                                'provider': author.provider,
                                'reason': reason,
                            }
                            self.repository.append_event(
                                task_id,
                                event_type='proposal_discussion_error',
                                payload=error_payload,
                                round_number=round_no,
                            )
                            self.artifact_store.append_event(task_id, error_payload)
                            current_seed = self._append_proposal_feedback_context(
                                current_seed,
                                reviewer_id='author',
                                review_text=f'proposal_discussion_error attempt={attempt}: {reason}',
                            )
                            continue
                        discussion_text = str(discussion.output or '').strip() or current_seed
                        round_latest_proposal = discussion_text
                        required_issue_ids = extract_required_issue_ids(pre_reviews)
                        author_issue_responses = parse_author_issue_responses(discussion_text)
                        author_validation = validate_author_issue_responses(
                            required_issue_ids=required_issue_ids,
                            responses=author_issue_responses,
                        )
                        last_author_issue_validation = dict(author_validation)
                        if required_issue_ids and not bool(author_validation.get('ok', False)):
                            incomplete_payload = {
                                'round': round_no,
                                'attempt': attempt,
                                'required_issue_ids': list(author_validation.get('required_issue_ids') or []),
                                'missing_issue_ids': list(author_validation.get('missing_issue_ids') or []),
                                'invalid_reject_issue_ids': list(author_validation.get('invalid_reject_issue_ids') or []),
                            }
                            self.repository.append_event(
                                task_id,
                                event_type='proposal_discussion_incomplete',
                                payload=incomplete_payload,
                                round_number=round_no,
                            )
                            self.artifact_store.append_event(
                                task_id,
                                {'type': EventType.PROPOSAL_DISCUSSION_INCOMPLETE.value, **incomplete_payload},
                            )
                            current_seed = self._append_proposal_feedback_context(
                                merged_context,
                                reviewer_id='contract',
                                review_text=(
                                    'proposal_discussion_incomplete: missing/invalid issue responses. '
                                    f"missing={','.join(list(author_validation.get('missing_issue_ids') or [])) or 'n/a'}; "
                                    f"invalid_reject={','.join(list(author_validation.get('invalid_reject_issue_ids') or [])) or 'n/a'}"
                                ),
                            )
                            if attempt >= retry_limit:
                                proposal_preview = clip_text(round_latest_proposal, max_chars=800).strip()
                                stall_summary = (
                                    f"Task: {str(row.get('title') or '')}\n"
                                    f"Proposal discussion incomplete in round {round_no}: reached retry limit ({retry_limit}).\n"
                                    f"Consensus rounds completed: {consensus_rounds}/{target_rounds}\n"
                                    f"Latest proposal preview:\n{proposal_preview}"
                                )
                                stall_payload = {
                                    'stall_kind': 'in_round',
                                    'round': round_no,
                                    'attempt': attempt,
                                    'retry_limit': retry_limit,
                                    'missing_issue_ids': list(author_validation.get('missing_issue_ids') or []),
                                    'invalid_reject_issue_ids': list(author_validation.get('invalid_reject_issue_ids') or []),
                                }
                                review_payload = list(pre_reviews)
                                return finish_proposal_stalled(
                                    reason='proposal_consensus_stalled_in_round',
                                    summary_text=stall_summary,
                                    rounds_completed=consensus_rounds,
                                    round_number=round_no,
                                    stall_payload=stall_payload,
                                    latest_reviews=list(pre_reviews),
                                )
                            continue
                        if discussion_text:
                            discussion_event = {
                                'type': EventType.DISCUSSION.value,
                                'round': round_no,
                                'participant': author.participant_id,
                                'provider': author.provider,
                                'attempt': attempt,
                                'output': discussion_text,
                            }
                            self.repository.append_event(
                                task_id,
                                event_type='discussion',
                                payload=discussion_event,
                                round_number=round_no,
                            )
                            self.artifact_store.append_event(task_id, discussion_event)
                            self.artifact_store.append_discussion(
                                task_id,
                                role=f'discussion:{author.participant_id}',
                                round_number=round_no,
                                content=discussion_text,
                            )

                        round_latest_reviews, merged_after_review = run_proposal_reviewer_pass(
                            discussion_text,
                            round_no=round_no,
                            stage='proposal_review',
                        )
                        review_contract = validate_reviewer_issue_contract(
                            [
                                item
                                for item in round_latest_reviews
                                if self._is_actionable_proposal_review_text(str(item.get('output') or ''))
                            ]
                        )
                        if not bool(review_contract.get('ok', False)):
                            contract_payload = {
                                'round': round_no,
                                'attempt': attempt,
                                'stage': 'proposal_review',
                                'missing_issue_participants': list(review_contract.get('missing_issue_participants') or []),
                            }
                            self.repository.append_event(
                                task_id,
                                event_type='proposal_review_contract_violation',
                                payload=contract_payload,
                                round_number=round_no,
                            )
                            self.artifact_store.append_event(
                                task_id,
                                {'type': EventType.PROPOSAL_REVIEW_CONTRACT_VIOLATION.value, **contract_payload},
                            )
                            current_seed = self._append_proposal_feedback_context(
                                merged_after_review,
                                reviewer_id='contract',
                                review_text=(
                                    'proposal_review contract violation: '
                                    f"missing structured issues from {', '.join(list(review_contract.get('missing_issue_participants') or []))}"
                                ),
                            )
                            if attempt >= retry_limit:
                                proposal_preview = clip_text(round_latest_proposal, max_chars=800).strip()
                                stall_summary = (
                                    f"Task: {str(row.get('title') or '')}\n"
                                    f"Proposal review contract violation in round {round_no}: reached retry limit ({retry_limit}).\n"
                                    f"Consensus rounds completed: {consensus_rounds}/{target_rounds}\n"
                                    f"Latest proposal preview:\n{proposal_preview}"
                                )
                                stall_payload = {
                                    'stall_kind': 'in_round',
                                    'round': round_no,
                                    'attempt': attempt,
                                    'retry_limit': retry_limit,
                                    'missing_issue_participants': list(review_contract.get('missing_issue_participants') or []),
                                }
                                review_payload = list(round_latest_reviews)
                                return finish_proposal_stalled(
                                    reason='proposal_consensus_stalled_in_round',
                                    summary_text=stall_summary,
                                    rounds_completed=consensus_rounds,
                                    round_number=round_no,
                                    stall_payload=stall_payload,
                                    latest_reviews=list(round_latest_reviews),
                                )
                            continue
                        review_payload = list(round_latest_reviews)
                        usable_reviews = self._proposal_review_usable_count(round_latest_reviews)
                        if round_latest_reviews and usable_reviews <= 0:
                            fail_reason = 'proposal_review_unavailable'
                            unavailable_payload = {
                                'round': round_no,
                                'attempt': attempt,
                                'reviewers_total': len(round_latest_reviews),
                                'reviewers_usable': 0,
                                'latest_reviews': round_latest_reviews,
                            }
                            return finish_proposal_terminal(
                                status=TaskStatus.FAILED_GATE,
                                reason=fail_reason,
                                rounds_completed=consensus_rounds,
                                event_type='proposal_review_unavailable',
                                payload=unavailable_payload,
                                round_number=round_no,
                            )
                        actionable_reviews = list(round_latest_reviews)
                        if 0 < usable_reviews < len(reviewers):
                            partial_payload = {
                                'round': round_no,
                                'attempt': attempt,
                                'reviewers_total': len(round_latest_reviews),
                                'reviewers_usable': usable_reviews,
                                'latest_reviews': round_latest_reviews,
                            }
                            self.repository.append_event(
                                task_id,
                                event_type='proposal_review_partial',
                                payload=partial_payload,
                                round_number=round_no,
                            )
                            self.artifact_store.append_event(
                                task_id,
                                {'type': EventType.PROPOSAL_REVIEW_PARTIAL.value, **partial_payload},
                            )
                            actionable_reviews = [
                                item
                                for item in round_latest_reviews
                                if OrchestratorService._is_actionable_proposal_review_text(
                                    str(item.get('output') or ''),
                                )
                            ]
                        no_blocker, blocker, unknown = self._proposal_verdict_counts(actionable_reviews)
                        required_from_latest_review = extract_required_issue_ids(actionable_reviews)
                        accepted_issue_ids = sorted(
                            [
                                issue_id
                                for issue_id, response in dict(author_issue_responses or {}).items()
                                if str(dict(response).get('status') or '').strip().lower() == 'accept'
                            ]
                        )
                        proposal_contract = {
                            'required_issue_ids': list(required_from_latest_review),
                            'accepted_issue_ids': accepted_issue_ids,
                            'author_issue_validation': dict(author_validation),
                        }

                        if self._proposal_consensus_reached(
                            actionable_reviews,
                            expected_reviewers=len(actionable_reviews),
                        ):
                            consensus_reached = True
                            consensus_rounds += 1
                            current_seed = round_latest_proposal
                            ok_payload = {
                                'round': round_no,
                                'attempt': attempt,
                                'verdicts': {
                                    'no_blocker': no_blocker,
                                    'blocker': blocker,
                                    'unknown': unknown,
                                },
                                'consensus_rounds': consensus_rounds,
                                'target_rounds': target_rounds,
                            }
                            self.repository.append_event(
                                task_id,
                                event_type='proposal_consensus_reached',
                                payload=ok_payload,
                                round_number=round_no,
                            )
                            self.artifact_store.append_event(task_id, {'type': EventType.PROPOSAL_CONSENSUS_REACHED.value, **ok_payload})
                            round_signature = self._proposal_round_signature(
                                actionable_reviews,
                                proposal_text=round_latest_proposal,
                            )
                            if round_signature:
                                if round_signature == last_round_signature:
                                    repeated_signature_rounds += 1
                                else:
                                    last_round_signature = round_signature
                                    repeated_signature_rounds = 1
                            else:
                                last_round_signature = ''
                                repeated_signature_rounds = 0
                            if target_rounds > 1 and round_signature and repeated_signature_rounds >= repeat_round_limit:
                                proposal_preview = clip_text(round_latest_proposal, max_chars=800).strip()
                                stall_summary = (
                                    f"Task: {str(row.get('title') or '')}\n"
                                    f"Consensus stalled across rounds: repeated issue signature for {repeated_signature_rounds} rounds.\n"
                                    f"Consensus rounds completed: {consensus_rounds}/{target_rounds}\n"
                                    f"Latest proposal preview:\n{proposal_preview}"
                                )
                                stall_payload = {
                                    'stall_kind': 'across_rounds',
                                    'round': round_no,
                                    'attempt': attempt,
                                    'repeated_rounds': repeated_signature_rounds,
                                    'repeat_round_limit': repeat_round_limit,
                                    'round_signature': round_signature,
                                    'latest_reviews': list(actionable_reviews),
                                }
                                review_payload = list(actionable_reviews)
                                return finish_proposal_stalled(
                                    reason='proposal_consensus_stalled_across_rounds',
                                    summary_text=stall_summary,
                                    rounds_completed=consensus_rounds,
                                    round_number=round_no,
                                    stall_payload=stall_payload,
                                    latest_reviews=list(actionable_reviews),
                                )
                        else:
                            retry_payload = {
                                'round': round_no,
                                'attempt': attempt,
                                'verdicts': {
                                    'no_blocker': no_blocker,
                                    'blocker': blocker,
                                    'unknown': unknown,
                                },
                            }
                            self.repository.append_event(
                                task_id,
                                event_type='proposal_consensus_retry',
                                payload=retry_payload,
                                round_number=round_no,
                            )
                            self.artifact_store.append_event(task_id, {'type': EventType.PROPOSAL_CONSENSUS_RETRY.value, **retry_payload})
                            current_seed = self._append_proposal_feedback_context(
                                merged_after_review,
                                reviewer_id='consensus',
                                review_text=f'unresolved blockers={blocker}, unknown={unknown}',
                            )
                            if attempt >= retry_limit:
                                proposal_preview = clip_text(round_latest_proposal, max_chars=800).strip()
                                stall_summary = (
                                    f"Task: {str(row.get('title') or '')}\n"
                                    f"Consensus stalled in round {round_no}: reached retry limit ({retry_limit}).\n"
                                    f"Consensus rounds completed: {consensus_rounds}/{target_rounds}\n"
                                    f"Latest proposal preview:\n{proposal_preview}"
                                )
                                stall_payload = {
                                    'stall_kind': 'in_round',
                                    'round': round_no,
                                    'attempt': attempt,
                                    'retry_limit': retry_limit,
                                    'verdicts': {
                                        'no_blocker': no_blocker,
                                        'blocker': blocker,
                                        'unknown': unknown,
                                    },
                                    'latest_reviews': list(actionable_reviews),
                                }
                                review_payload = list(actionable_reviews)
                                return finish_proposal_stalled(
                                    reason='proposal_consensus_stalled_in_round',
                                    summary_text=stall_summary,
                                    rounds_completed=consensus_rounds,
                                    round_number=round_no,
                                    stall_payload=stall_payload,
                                    latest_reviews=list(actionable_reviews),
                                )

                discussion_text = current_seed

            proposal_preview = clip_text(discussion_text, max_chars=1200).strip()
            no_blocker = sum(1 for item in review_payload if str(item.get('verdict')) == ReviewVerdict.NO_BLOCKER.value)
            blocker = sum(1 for item in review_payload if str(item.get('verdict')) == ReviewVerdict.BLOCKER.value)
            unknown = sum(1 for item in review_payload if str(item.get('verdict')) == ReviewVerdict.UNKNOWN.value)
            summary = (
                f"Task: {str(row.get('title') or '')}\n"
                f"Consensus rounds: {consensus_rounds}/{target_rounds}\n"
                f"Proposal verdicts: no_blocker={no_blocker}, blocker={blocker}, unknown={unknown}\n"
                f"Proposal:\n{proposal_preview}"
            )
            if author_feedback_note:
                summary = f"{summary}\nAuthor feedback:\n- {author_feedback_note}"
        except Exception as exc:
            _log.exception('author_confirmation_prepare_failed task_id=%s', task_id)
            return self.mark_failed_system(task_id, reason=f'proposal_error: {exc}')

        waiting = self.repository.update_task_status(
            task_id,
            status=TaskStatus.WAITING_MANUAL.value,
            reason='author_confirmation_required',
            rounds_completed=consensus_rounds,
        )
        pending_payload = {
            'summary': summary,
            'self_loop_mode': int(row.get('self_loop_mode', 0)),
            'consensus_rounds': consensus_rounds,
            'target_rounds': target_rounds,
            'review_payload': review_payload,
            'proposal_contract': dict(proposal_contract or {}),
            'author_issue_validation': dict(last_author_issue_validation or {}),
            'author_feedback_note': author_feedback_note,
        }
        self.repository.append_event(
            task_id,
            event_type='author_confirmation_required',
            payload=pending_payload,
            round_number=None,
        )
        self.artifact_store.append_event(
            task_id,
            {'type': EventType.AUTHOR_CONFIRMATION_REQUIRED.value, **pending_payload},
        )
        self.artifact_store.write_artifact_json(task_id, name='pending_proposal', payload=pending_payload)

        if auto_approve:
            decision_payload = {
                'decision': 'approved',
                'note': 'auto_approved_by_self_loop_mode',
            }
            self.repository.append_event(
                task_id,
                event_type='author_decision',
                payload=decision_payload,
                round_number=None,
            )
            self.artifact_store.append_event(task_id, {'type': EventType.AUTHOR_DECISION.value, **decision_payload})
            self.repository.set_cancel_requested(task_id, requested=False)
            approved = self.repository.update_task_status(
                task_id,
                status=TaskStatus.RUNNING.value,
                reason='author_approved',
                rounds_completed=consensus_rounds,
            )
            self.artifact_store.update_state(
                task_id,
                {
                    'status': TaskStatus.RUNNING.value,
                    'last_gate_reason': 'author_approved',
                    'cancel_requested': False,
                    'pending_proposal': pending_payload,
                    'author_decision': decision_payload,
                },
            )
            return self._to_view(approved)

        self.artifact_store.update_state(
            task_id,
            {
                'status': TaskStatus.WAITING_MANUAL.value,
                'last_gate_reason': 'author_confirmation_required',
                'pending_proposal': pending_payload,
            },
        )
        return self._to_view(waiting)

    @staticmethod
    def _map_run_status(status: str) -> TaskStatus:
        normalized = (status or '').strip().lower()
        if normalized == 'passed':
            return TaskStatus.PASSED
        if normalized == 'canceled':
            return TaskStatus.CANCELED
        return TaskStatus.FAILED_GATE

    def _count_running_tasks(self, *, exclude_task_id: str | None = None) -> int:
        return len(self._running_task_ids(exclude_task_id=exclude_task_id))

    def _running_task_ids(self, *, exclude_task_id: str | None = None) -> set[str]:
        rows = self.repository.list_tasks(limit=10_000)
        running_ids: set[str] = set()
        for row in rows:
            task_id = str(row.get('task_id', ''))
            if exclude_task_id and task_id == exclude_task_id:
                continue
            if str(row.get('status', '')) == TaskStatus.RUNNING.value:
                running_ids.add(task_id)
        return running_ids

    @staticmethod
    def _parse_iso_datetime(value) -> datetime | None:
        text = str(value or '').strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    def _evaluate_workspace_resume_guard(self, row: dict) -> dict[str, object]:
        expected_raw = row.get('workspace_fingerprint')
        expected = dict(expected_raw) if isinstance(expected_raw, dict) else {}
        if not expected:
            return {
                'ok': True,
                'reason': 'workspace_resume_guard_unavailable',
            }

        project_root = Path(str(row.get('project_path') or row.get('workspace_path') or Path.cwd()))
        workspace_root = Path(str(row.get('workspace_path') or Path.cwd()))
        actual = TaskManagementService._build_workspace_fingerprint(
            project_root=project_root,
            workspace_root=workspace_root,
            sandbox_mode=bool(row.get('sandbox_mode', False)),
            sandbox_workspace_path=(str(row.get('sandbox_workspace_path')).strip() if row.get('sandbox_workspace_path') else None),
            merge_target_path=(str(row.get('merge_target_path')).strip() if row.get('merge_target_path') else None),
        )

        compare_fields = [
            'schema',
            'project_path',
            'workspace_path',
            'sandbox_mode',
            'sandbox_workspace_path',
            'merge_target_path',
            'workspace_head_signature',
            'project_head_signature',
            'project_has_git',
        ]
        mismatches: list[str] = []
        for field in compare_fields:
            if expected.get(field) != actual.get(field):
                mismatches.append(field)

        if not workspace_root.exists() or not workspace_root.is_dir():
            mismatches.append('workspace_exists')

        if mismatches:
            return {
                'ok': False,
                'reason': 'workspace_resume_guard_mismatch',
                'mismatch_fields': sorted(set(mismatches)),
                'expected': expected,
                'actual': actual,
            }
        return {
            'ok': True,
            'reason': 'workspace_resume_guard_passed',
            'mismatch_fields': [],
            'expected': expected,
            'actual': actual,
        }

    @staticmethod
    def _coerce_evidence_checks(value: object) -> dict[str, object]:
        if not isinstance(value, dict):
            return {}
        out: dict[str, object] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key or '').strip()
            if not key:
                continue
            out[key] = raw_value
        return out

    @staticmethod
    def _coerce_evidence_paths(value: object) -> list[str]:
        if isinstance(value, str):
            candidates = [value]
        elif isinstance(value, (list, tuple, set)):
            candidates = list(value)
        else:
            return []

        out: list[str] = []
        seen: set[str] = set()
        for raw in candidates:
            text = str(raw or '').strip()
            if not text:
                continue
            key = text.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(text)
        return out

    @staticmethod
    def _validate_evidence_bundle(*, evidence_bundle: dict | None, expected_round: int) -> dict[str, object]:
        bundle = dict(evidence_bundle or {})
        if not bundle:
            return {'ok': False, 'reason': 'precompletion_evidence_missing'}
        try:
            bundle_round = int(bundle.get('round') or 0)
        except (TypeError, ValueError):
            bundle_round = 0
        if bundle_round != int(expected_round):
            return {'ok': False, 'reason': 'precompletion_evidence_missing'}
        if not bool(bundle.get('passed', False)):
            reason = str(bundle.get('reason') or '').strip() or 'precompletion_evidence_missing'
            return {'ok': False, 'reason': reason}
        evidence_paths = OrchestratorService._coerce_evidence_paths(bundle.get('evidence_paths'))
        if not evidence_paths:
            return {'ok': False, 'reason': 'precompletion_evidence_missing'}
        checks = OrchestratorService._coerce_evidence_checks(bundle.get('checks'))
        if checks and (not bool(checks.get('tests_ok', True)) or not bool(checks.get('lint_ok', True))):
            return {'ok': False, 'reason': 'precompletion_verification_missing'}
        return {'ok': True, 'reason': 'passed'}

    @staticmethod
    def _default_sandbox_path(project_root: Path) -> str:
        return TaskManagementService._default_sandbox_path(project_root)

    @staticmethod
    def _cleanup_create_task_sandbox_failure(
        *,
        sandbox_mode: bool,
        sandbox_generated: bool,
        project_root: Path,
        sandbox_root: Path | None,
    ) -> None:
        TaskManagementService._cleanup_create_task_sandbox_failure(
            sandbox_mode=sandbox_mode,
            sandbox_generated=sandbox_generated,
            project_root=project_root,
            sandbox_root=sandbox_root,
        )

    @staticmethod
    def _cleanup_sandbox_after_merge(*, row: dict, workspace_root: Path) -> dict | None:
        if not bool(row.get('sandbox_mode', False)):
            return None
        if not bool(row.get('sandbox_generated', False)):
            return None
        if not bool(row.get('sandbox_cleanup_on_pass', False)):
            return None

        project_root = Path(str(row.get('project_path') or '')).resolve()
        sandbox_root = workspace_root.resolve()
        if sandbox_root == project_root:
            return None

        payload = {
            'path': str(sandbox_root),
            'project_path': str(project_root),
            'ok': False,
        }
        try:
            if sandbox_root.exists():
                def _onerror(func, p, exc_info):
                    try:
                        os.chmod(p, stat.S_IWRITE)
                        func(p)
                    except OSError:
                        _log.debug('sandbox_cleanup_onerror_failed path=%s', str(p))
                shutil.rmtree(sandbox_root, onerror=_onerror)
            payload['ok'] = True
            payload['removed'] = True
        except OSError as exc:
            payload['error'] = str(exc)
        return payload

    def _round_artifacts_root(self, task_id: str) -> Path:
        key = self._validate_artifact_task_id(task_id)
        root = (self.artifact_store.root / 'threads' / key / 'artifacts' / 'rounds').resolve(strict=False)
        root.mkdir(parents=True, exist_ok=True)
        return root

    @staticmethod
    def _round_snapshot_dir(rounds_root: Path, round_no: int) -> Path:
        return rounds_root / f'round-{int(round_no):03d}-snapshot'

    def _initialize_round_artifact_baseline(self, *, task_id: str, workspace_root: Path) -> Path:
        rounds_root = self._round_artifacts_root(task_id)
        baseline = self._round_snapshot_dir(rounds_root, 0)
        if baseline.exists():
            shutil.rmtree(baseline, ignore_errors=True)
        baseline.mkdir(parents=True, exist_ok=True)
        self._copy_workspace_snapshot(source_root=workspace_root, target_root=baseline)
        return baseline

    def _capture_round_artifacts(
        self,
        *,
        task_id: str,
        round_no: int,
        previous_snapshot: Path,
        workspace_root: Path,
        gate_reason: str,
        gate_status: str,
    ) -> tuple[dict, Path]:
        rounds_root = self._round_artifacts_root(task_id)
        next_snapshot = self._round_snapshot_dir(rounds_root, round_no)
        if next_snapshot.exists():
            shutil.rmtree(next_snapshot, ignore_errors=True)
        next_snapshot.mkdir(parents=True, exist_ok=True)
        self._copy_workspace_snapshot(source_root=workspace_root, target_root=next_snapshot)

        before_manifest = self.fusion_manager.build_manifest(previous_snapshot)
        after_manifest = self.fusion_manager.build_manifest(next_snapshot)
        changed_paths = sorted(
            [rel for rel in set(before_manifest) | set(after_manifest) if before_manifest.get(rel) != after_manifest.get(rel)]
        )
        added_files = sorted([rel for rel in after_manifest if rel not in before_manifest])
        deleted_files = sorted([rel for rel in before_manifest if rel not in after_manifest])
        modified_files = sorted(
            [
                rel
                for rel in changed_paths
                if rel in before_manifest and rel in after_manifest and before_manifest.get(rel) != after_manifest.get(rel)
            ]
        )

        patch_text = self._build_patch_text(
            from_root=previous_snapshot,
            to_root=next_snapshot,
            changed_paths=changed_paths,
        )
        patch_path = rounds_root / f'round-{int(round_no)}.patch'
        if patch_text.strip():
            patch_path.write_text(patch_text, encoding='utf-8')
        else:
            patch_path.write_text('# no file-level changes detected for this round\n', encoding='utf-8')

        summary_path = rounds_root / f'round-{int(round_no)}.md'
        lines = [
            f'# Round {int(round_no)} Summary',
            '',
            f'- status: `{gate_status}`',
            f'- reason: `{gate_reason or "n/a"}`',
            f'- changed_files: `{len(changed_paths)}`',
            f'- added_files: `{len(added_files)}`',
            f'- modified_files: `{len(modified_files)}`',
            f'- deleted_files: `{len(deleted_files)}`',
            f'- patch: `{patch_path}`',
            f'- snapshot: `{next_snapshot}`',
            '',
        ]
        if changed_paths:
            lines.append('## Changed Paths')
            lines.append('')
            for rel in changed_paths[:200]:
                lines.append(f'- `{rel}`')
            if len(changed_paths) > 200:
                lines.append(f'- ... ({len(changed_paths) - 200} more)')
            lines.append('')
        summary_path.write_text('\n'.join(lines), encoding='utf-8')

        meta_payload = {
            'round': int(round_no),
            'status': gate_status,
            'reason': gate_reason or None,
            'changed_paths': changed_paths,
            'added_files': added_files,
            'modified_files': modified_files,
            'deleted_files': deleted_files,
            'patch_path': str(patch_path),
            'summary_path': str(summary_path),
            'snapshot_path': str(next_snapshot),
            'created_at': datetime.now().isoformat(),
        }
        self.artifact_store.write_artifact_json(
            task_id,
            name=f'round-{int(round_no)}-artifact',
            payload=meta_payload,
        )
        return meta_payload, next_snapshot

    def _copy_workspace_snapshot(self, *, source_root: Path, target_root: Path) -> None:
        source = Path(source_root)
        target = Path(target_root)
        for src in self._iter_workspace_files(source):
            rel = src.relative_to(source)
            dst = target / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    def _iter_workspace_files(self, root: Path):
        base = Path(root)
        for path in base.rglob('*'):
            if not path.is_file():
                continue
            rel = path.relative_to(base).as_posix()
            if self._is_sandbox_ignored(rel):
                continue
            yield path

    def _build_patch_text(self, *, from_root: Path, to_root: Path, changed_paths: list[str]) -> str:
        output: list[str] = []
        for rel in changed_paths:
            old_path = from_root / rel
            new_path = to_root / rel
            old_text, old_binary = self._read_text_for_patch(old_path)
            new_text, new_binary = self._read_text_for_patch(new_path)
            if old_binary or new_binary:
                output.append(f'diff --git a/{rel} b/{rel}')
                output.append('Binary files differ')
                output.append('')
                continue
            old_lines = old_text.splitlines()
            new_lines = new_text.splitlines()
            from_name = '/dev/null' if not old_path.exists() else f'a/{rel}'
            to_name = '/dev/null' if not new_path.exists() else f'b/{rel}'
            diff_lines = list(
                difflib.unified_diff(
                    old_lines,
                    new_lines,
                    fromfile=from_name,
                    tofile=to_name,
                    lineterm='',
                )
            )
            if not diff_lines:
                continue
            output.extend(diff_lines)
            output.append('')
        return '\n'.join(output).rstrip() + '\n' if output else ''

    @staticmethod
    def _read_text_for_patch(path: Path) -> tuple[str, bool]:
        if not path.exists() or not path.is_file():
            return '', False
        try:
            data = path.read_bytes()
        except OSError:
            return '', True
        if len(data) > 2 * 1024 * 1024:
            return '', True
        if b'\x00' in data:
            return '', True
        try:
            return data.decode('utf-8'), False
        except UnicodeDecodeError:
            return '', True

    @staticmethod
    def _is_sandbox_ignored(rel_path: str) -> bool:
        return TaskManagementService._is_sandbox_ignored(rel_path)

    @staticmethod
    def _is_windows_reserved_device_name(filename: str) -> bool:
        return TaskManagementService._is_windows_reserved_device_name(filename)

    @staticmethod
    def _bootstrap_sandbox_workspace(project_root: Path, sandbox_root: Path) -> None:
        TaskManagementService._bootstrap_sandbox_workspace(project_root, sandbox_root)

    @staticmethod
    def _proposal_review_prompt(
        config: RunConfig,
        discussion_output: str,
        *,
        stage: str = 'proposal_review',
        environment_context: str | None = None,
        memory_context: str | None = None,
    ) -> str:
        return proposal_review_prompt(
            config,
            discussion_output,
            stage=stage,
            environment_context=environment_context,
            memory_context=memory_context,
        )

    @staticmethod
    def _review_timeout_seconds(participant_timeout_seconds: int) -> int:
        return review_timeout_seconds(participant_timeout_seconds)

    @staticmethod
    def _resolve_phase_timeout_seconds(
        *,
        phase_timeout_seconds: dict[str, int] | None,
        phase: str,
        fallback: int,
    ) -> int:
        phase_key = str(phase or '').strip().lower()
        mapping = normalize_phase_timeout_seconds(phase_timeout_seconds, strict=False)
        resolved = int(mapping.get(phase_key, int(fallback)))
        return max(10, resolved)

    @staticmethod
    def _proposal_author_prompt(
        config: RunConfig,
        merged_context: str,
        review_payload: list[dict],
        *,
        environment_context: str | None = None,
        memory_context: str | None = None,
    ) -> str:
        return proposal_author_prompt(
            config,
            merged_context,
            review_payload,
            environment_context=environment_context,
            memory_context=memory_context,
        )

    @staticmethod
    def _is_audit_intent(config: RunConfig) -> bool:
        return is_audit_intent(config)

    @staticmethod
    def _looks_like_scope_ambiguity(review_text: str) -> bool:
        return looks_like_scope_ambiguity(review_text)

    @staticmethod
    def _looks_like_hard_risk(review_text: str) -> bool:
        return looks_like_hard_risk(review_text)

    @staticmethod
    def _normalize_proposal_reviewer_result(
        *,
        config: RunConfig,
        stage: str,
        verdict: ReviewVerdict,
        review_text: str,
    ) -> tuple[ReviewVerdict, str]:
        return normalize_proposal_reviewer_result(
            config=config,
            stage=stage,
            verdict=verdict,
            review_text=review_text,
        )

    @staticmethod
    def _append_proposal_feedback_context(base_text: str, *, reviewer_id: str, review_text: str) -> str:
        return append_proposal_feedback_context(base_text, reviewer_id=reviewer_id, review_text=review_text)

    @staticmethod
    def _proposal_verdict_counts(review_payload: list[dict]) -> tuple[int, int, int]:
        return proposal_verdict_counts(review_payload)

    @staticmethod
    def _proposal_consensus_reached(review_payload: list[dict], *, expected_reviewers: int) -> bool:
        return proposal_consensus_reached(review_payload, expected_reviewers=expected_reviewers)

    @staticmethod
    def _proposal_review_usable_count(review_payload: list[dict]) -> int:
        return proposal_review_usable_count(review_payload)

    @staticmethod
    def _proposal_stall_retry_limit() -> int:
        return PROPOSAL_STALL_RETRY_LIMIT

    @staticmethod
    def _proposal_repeat_rounds_limit() -> int:
        return PROPOSAL_REPEAT_ROUNDS_LIMIT

    @staticmethod
    def _proposal_round_signature(review_payload: list[dict], *, proposal_text: str) -> str:
        return proposal_round_signature(review_payload, proposal_text=proposal_text)

    @staticmethod
    def _is_actionable_proposal_review_text(text: str) -> bool:
        return is_actionable_proposal_review_text(text)

    @staticmethod
    def _resolve_merge_target(row: dict) -> Path:
        merge_target = str(row.get('merge_target_path') or '').strip()
        if merge_target:
            return Path(merge_target)
        return Path(str(row.get('workspace_path') or Path.cwd()))

    @staticmethod
    def _to_view(row: dict) -> TaskView:
        return TaskView(
            task_id=str(row['task_id']),
            title=str(row['title']),
            description=str(row['description']),
            author_participant=str(row['author_participant']),
            reviewer_participants=[str(v) for v in row.get('reviewer_participants', [])],
            evolution_level=max(0, min(3, int(row.get('evolution_level', 0)))),
            evolve_until=(str(row.get('evolve_until')).strip() if row.get('evolve_until') else None),
            conversation_language=normalize_conversation_language(row.get('conversation_language')),
            provider_models={str(k): str(v) for k, v in dict(row.get('provider_models', {})).items()},
            provider_model_params={str(k): str(v) for k, v in dict(row.get('provider_model_params', {})).items()},
            participant_models={str(k): str(v) for k, v in dict(row.get('participant_models', {})).items()},
            participant_model_params={str(k): str(v) for k, v in dict(row.get('participant_model_params', {})).items()},
            claude_team_agents=bool(row.get('claude_team_agents', False)),
            codex_multi_agents=bool(row.get('codex_multi_agents', False)),
            claude_team_agents_overrides={str(k): bool(v) for k, v in dict(row.get('claude_team_agents_overrides', {})).items()},
            codex_multi_agents_overrides={str(k): bool(v) for k, v in dict(row.get('codex_multi_agents_overrides', {})).items()},
            repair_mode=normalize_repair_mode(row.get('repair_mode')),
            memory_mode=normalize_memory_mode(row.get('memory_mode', 'basic')),
            phase_timeout_seconds=normalize_phase_timeout_seconds(
                row.get('phase_timeout_seconds'),
                strict=False,
            ),
            plain_mode=normalize_plain_mode(row.get('plain_mode', True)),
            stream_mode=normalize_bool_flag(row.get('stream_mode', True), default=True),
            debate_mode=normalize_bool_flag(row.get('debate_mode', True), default=True),
            sandbox_mode=bool(row.get('sandbox_mode', False)),
            sandbox_workspace_path=(str(row.get('sandbox_workspace_path')).strip() if row.get('sandbox_workspace_path') else None),
            sandbox_generated=bool(row.get('sandbox_generated', False)),
            sandbox_cleanup_on_pass=bool(row.get('sandbox_cleanup_on_pass', False)),
            self_loop_mode=max(0, min(1, int(row.get('self_loop_mode', 0)))),
            project_path=str(row.get('project_path') or row.get('workspace_path') or Path.cwd()),
            auto_merge=bool(row.get('auto_merge', True)),
            merge_target_path=(str(row.get('merge_target_path')).strip() if row.get('merge_target_path') else None),
            workspace_path=str(row.get('workspace_path', str(Path.cwd()))),
            status=TaskStatus(str(row['status'])),
            last_gate_reason=row.get('last_gate_reason'),
            max_rounds=int(row.get('max_rounds', 3)),
            test_command=str(row.get('test_command', 'python -m pytest -q')),
            lint_command=str(row.get('lint_command', 'python -m ruff check .')),
            rounds_completed=int(row.get('rounds_completed', 0)),
            cancel_requested=bool(row.get('cancel_requested', False)),
        )


