from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
from uuid import uuid4

from awe_agentcheck.adapters import ParticipantRunner
from awe_agentcheck.domain.gate import evaluate_medium_gate
from awe_agentcheck.domain.models import ReviewVerdict, TaskStatus
from awe_agentcheck.fusion import AutoFusionManager
from awe_agentcheck.observability import get_logger, set_task_context
from awe_agentcheck.participants import get_supported_providers, parse_participant_id
from awe_agentcheck.repository import TaskRepository
from awe_agentcheck.storage.artifacts import ArtifactStore
from awe_agentcheck.workflow import RunConfig, ShellCommandExecutor, WorkflowEngine

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
    repair_mode: str = 'balanced'
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
    test_command: str = 'py -m pytest -q'
    lint_command: str = 'py -m ruff check .'


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
    repair_mode: str
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

_SUPPORTED_CONVERSATION_LANGUAGES = {'en', 'zh'}
_SUPPORTED_REPAIR_MODES = {'minimal', 'balanced', 'structural'}
_ARTIFACT_TASK_ID_RE = re.compile(r'^[A-Za-z0-9._-]+$')
_DEFAULT_POLICY_TEMPLATE = 'balanced-default'
_PROPOSAL_STALL_RETRY_LIMIT = 10
_PROPOSAL_REPEAT_ROUNDS_LIMIT = 4
_POLICY_TEMPLATE_CATALOG: dict[str, dict] = {
    'balanced-default': {
        'id': 'balanced-default',
        'label': 'Balanced Default',
        'description': 'General-purpose profile for most repositories.',
        'defaults': {
            'sandbox_mode': 1,
            'self_loop_mode': 0,
            'auto_merge': 1,
            'max_rounds': 1,
            'debate_mode': 1,
            'plain_mode': 1,
            'stream_mode': 1,
            'repair_mode': 'balanced',
        },
    },
    'safe-review': {
        'id': 'safe-review',
        'label': 'Safe Review',
        'description': 'Conservative profile for high-risk or large repositories.',
        'defaults': {
            'sandbox_mode': 1,
            'self_loop_mode': 0,
            'auto_merge': 0,
            'max_rounds': 2,
            'debate_mode': 1,
            'plain_mode': 1,
            'stream_mode': 1,
            'repair_mode': 'minimal',
        },
    },
    'rapid-fix': {
        'id': 'rapid-fix',
        'label': 'Rapid Fix',
        'description': 'Fast patch profile for small/low-risk repositories.',
        'defaults': {
            'sandbox_mode': 1,
            'self_loop_mode': 1,
            'auto_merge': 1,
            'max_rounds': 1,
            'debate_mode': 1,
            'plain_mode': 1,
            'stream_mode': 1,
            'repair_mode': 'minimal',
        },
    },
    'deep-evolve': {
        'id': 'deep-evolve',
        'label': 'Deep Evolve',
        'description': 'Multi-round structural evolution with stronger scrutiny.',
        'defaults': {
            'sandbox_mode': 1,
            'self_loop_mode': 1,
            'auto_merge': 0,
            'max_rounds': 3,
            'debate_mode': 1,
            'plain_mode': 1,
            'stream_mode': 1,
            'repair_mode': 'structural',
        },
    },
}


def _supported_providers() -> set[str]:
    return get_supported_providers()


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
    if 'concurrency_limit' in text:
        return 'concurrency_limit'
    if 'author_confirmation_required' in text:
        return 'author_confirmation_required'
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

    def create_task(self, payload: CreateTaskInput) -> TaskView:
        # Validate participant IDs early so callers get a clear 400 error
        # instead of a delayed workflow_error at start time.
        try:
            author_participant = parse_participant_id(payload.author_participant)
        except ValueError as exc:
            raise InputValidationError(
                f'invalid author_participant: {exc}',
                field='author_participant',
            ) from exc
        reviewer_participants: list[str] = []
        for i, rp in enumerate(payload.reviewer_participants):
            try:
                reviewer_participants.append(parse_participant_id(rp).participant_id)
            except ValueError as exc:
                raise InputValidationError(
                    f'invalid reviewer_participants[{i}]: {exc}',
                    field=f'reviewer_participants[{i}]',
                ) from exc

        project_root = Path(payload.workspace_path).resolve()
        if not project_root.exists() or not project_root.is_dir():
            raise InputValidationError(
                'workspace_path must be an existing directory',
                field='workspace_path',
            )
        evolution_level = max(0, min(2, int(payload.evolution_level)))
        evolve_until = self._normalize_evolve_until(payload.evolve_until)
        conversation_language = self._normalize_conversation_language(
            payload.conversation_language,
            strict=True,
        )
        provider_models = self._normalize_provider_models(payload.provider_models)
        provider_model_params = self._normalize_provider_model_params(payload.provider_model_params)
        known_participants = {author_participant.participant_id, *reviewer_participants}
        participant_models = self._normalize_participant_models(
            payload.participant_models,
            known_participants=known_participants,
        )
        participant_model_params = self._normalize_participant_model_params(
            payload.participant_model_params,
            known_participants=known_participants,
        )
        claude_team_agents = bool(payload.claude_team_agents)
        codex_multi_agents = bool(payload.codex_multi_agents)
        repair_mode = self._normalize_repair_mode(payload.repair_mode, strict=True)
        plain_mode = self._normalize_plain_mode(payload.plain_mode)
        stream_mode = self._normalize_bool_flag(payload.stream_mode, default=True)
        debate_mode = self._normalize_bool_flag(payload.debate_mode, default=True)
        sandbox_mode = bool(payload.sandbox_mode)
        self_loop_mode = max(0, min(1, int(payload.self_loop_mode)))
        sandbox_cleanup_on_pass = bool(payload.sandbox_cleanup_on_pass)
        sandbox_workspace_path = self._normalize_merge_target_path(payload.sandbox_workspace_path)
        sandbox_generated = False
        auto_merge = bool(payload.auto_merge)
        max_rounds = max(1, min(20, int(payload.max_rounds)))
        multi_round_manual_promote = max_rounds > 1 and not auto_merge
        if multi_round_manual_promote:
            # Hard rule: multi-round no-auto-merge must run in a fresh sandbox.
            sandbox_mode = True
            sandbox_workspace_path = None
        merge_target_path = self._normalize_merge_target_path(payload.merge_target_path)
        if auto_merge and sandbox_mode and not merge_target_path:
            merge_target_path = str(project_root)
        if auto_merge and merge_target_path:
            merge_target = Path(merge_target_path)
            if not merge_target.exists() or not merge_target.is_dir():
                raise InputValidationError(
                    'merge_target_path must be an existing directory',
                    field='merge_target_path',
                )

        workspace_root = project_root
        sandbox_root: Path | None = None
        try:
            if sandbox_mode:
                if not sandbox_workspace_path:
                    sandbox_workspace_path = self._default_sandbox_path(project_root)
                    sandbox_generated = True
                sandbox_root = Path(sandbox_workspace_path)
                if sandbox_root.exists() and not sandbox_root.is_dir():
                    raise InputValidationError(
                        'sandbox_workspace_path must be a directory',
                        field='sandbox_workspace_path',
                    )
                sandbox_root.mkdir(parents=True, exist_ok=True)
                self._bootstrap_sandbox_workspace(project_root, sandbox_root)
                workspace_root = sandbox_root
            else:
                sandbox_workspace_path = None
                sandbox_generated = False

            row = self.repository.create_task(
                title=payload.title,
                description=payload.description,
                author_participant=author_participant.participant_id,
                reviewer_participants=reviewer_participants,
                evolution_level=evolution_level,
                evolve_until=evolve_until,
                conversation_language=conversation_language,
                provider_models=provider_models,
                provider_model_params=provider_model_params,
                participant_models=participant_models,
                participant_model_params=participant_model_params,
                claude_team_agents=claude_team_agents,
                codex_multi_agents=codex_multi_agents,
                repair_mode=repair_mode,
                plain_mode=plain_mode,
                stream_mode=stream_mode,
                debate_mode=debate_mode,
                sandbox_mode=sandbox_mode,
                sandbox_workspace_path=sandbox_workspace_path,
                sandbox_generated=sandbox_generated,
                sandbox_cleanup_on_pass=sandbox_cleanup_on_pass,
                project_path=str(project_root),
                self_loop_mode=self_loop_mode,
                auto_merge=auto_merge,
                merge_target_path=merge_target_path,
                workspace_path=str(workspace_root),
                max_rounds=max_rounds,
                test_command=payload.test_command,
                lint_command=payload.lint_command,
            )
            self.artifact_store.create_task_workspace(row['task_id'])
            self.artifact_store.update_state(
                row['task_id'],
                {
                    'status': row['status'],
                    'rounds_completed': row.get('rounds_completed', 0),
                    'cancel_requested': row.get('cancel_requested', False),
                    'conversation_language': str(row.get('conversation_language') or 'en'),
                    'provider_models': dict(row.get('provider_models', {})),
                    'provider_model_params': dict(row.get('provider_model_params', {})),
                    'participant_models': dict(row.get('participant_models', {})),
                    'participant_model_params': dict(row.get('participant_model_params', {})),
                    'claude_team_agents': bool(row.get('claude_team_agents', False)),
                    'codex_multi_agents': bool(row.get('codex_multi_agents', False)),
                    'repair_mode': str(row.get('repair_mode') or 'balanced'),
                    'plain_mode': bool(row.get('plain_mode', True)),
                    'stream_mode': bool(row.get('stream_mode', True)),
                    'debate_mode': bool(row.get('debate_mode', True)),
                    'sandbox_mode': bool(row.get('sandbox_mode', False)),
                    'sandbox_workspace_path': row.get('sandbox_workspace_path'),
                    'sandbox_generated': bool(row.get('sandbox_generated', False)),
                    'sandbox_cleanup_on_pass': bool(row.get('sandbox_cleanup_on_pass', False)),
                    'self_loop_mode': int(row.get('self_loop_mode', 0)),
                    'project_path': row.get('project_path'),
                    'auto_merge': bool(row.get('auto_merge', True)),
                    'merge_target_path': row.get('merge_target_path'),
                },
            )
        except Exception:
            self._cleanup_create_task_sandbox_failure(
                sandbox_mode=sandbox_mode,
                sandbox_generated=sandbox_generated,
                project_root=project_root,
                sandbox_root=sandbox_root,
            )
            raise
        _log.info('task_created task_id=%s title=%s', row['task_id'], payload.title)
        return self._to_view(row)

    def list_tasks(self, *, limit: int = 100) -> list[TaskView]:
        rows = self.repository.list_tasks(limit=limit)
        return [self._to_view(row) for row in rows]

    def get_task(self, task_id: str) -> TaskView | None:
        row = self.repository.get_task(task_id)
        if row is None:
            return None
        return self._to_view(row)

    def get_stats(self) -> StatsView:
        rows = self.repository.list_tasks(limit=10_000)
        counts: dict[str, int] = {}
        reason_bucket_counts: dict[str, int] = {}
        provider_error_counts: dict[str, int] = {}
        for row in rows:
            status = str(row.get('status', 'unknown'))
            counts[status] = counts.get(status, 0) + 1

            reason = row.get('last_gate_reason')
            bucket = _reason_bucket(reason)
            if bucket:
                reason_bucket_counts[bucket] = reason_bucket_counts.get(bucket, 0) + 1

            provider_match = _PROVIDER_RE.search(str(reason or ''))
            if provider_match:
                provider = provider_match.group(1).strip().lower()
                provider_error_counts[provider] = provider_error_counts.get(provider, 0) + 1

        active = counts.get(TaskStatus.RUNNING.value, 0) + counts.get(TaskStatus.QUEUED.value, 0)
        recent_rows = rows[:50]
        recent_terminal = [r for r in recent_rows if str(r.get('status', '')) in _TERMINAL_STATUSES]
        recent_terminal_total = len(recent_terminal)
        if recent_terminal_total > 0:
            pass_rate_50 = sum(1 for r in recent_terminal if str(r.get('status')) == TaskStatus.PASSED.value) / recent_terminal_total
            failed_gate_rate_50 = sum(1 for r in recent_terminal if str(r.get('status')) == TaskStatus.FAILED_GATE.value) / recent_terminal_total
            failed_system_rate_50 = sum(1 for r in recent_terminal if str(r.get('status')) == TaskStatus.FAILED_SYSTEM.value) / recent_terminal_total
        else:
            pass_rate_50 = 0.0
            failed_gate_rate_50 = 0.0
            failed_system_rate_50 = 0.0

        durations: list[float] = []
        for row in recent_terminal:
            created = self._parse_iso_datetime(row.get('created_at'))
            updated = self._parse_iso_datetime(row.get('updated_at'))
            if created is None or updated is None:
                continue
            delta = (updated - created).total_seconds()
            if delta >= 0:
                durations.append(delta)
        mean_task_duration_seconds_50 = (sum(durations) / len(durations)) if durations else 0.0

        return StatsView(
            total_tasks=len(rows),
            status_counts=counts,
            active_tasks=active,
            reason_bucket_counts=reason_bucket_counts,
            provider_error_counts=provider_error_counts,
            recent_terminal_total=recent_terminal_total,
            pass_rate_50=pass_rate_50,
            failed_gate_rate_50=failed_gate_rate_50,
            failed_system_rate_50=failed_system_rate_50,
            mean_task_duration_seconds_50=mean_task_duration_seconds_50,
        )

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
                detected = self._extract_model_from_command(str(raw_command or ''))
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
        for key in sorted(_POLICY_TEMPLATE_CATALOG):
            item = _POLICY_TEMPLATE_CATALOG[key]
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
        rows = self.repository.list_tasks(limit=max(1, min(2000, int(limit))))
        failures = [row for row in rows if str(row.get('status') or '') == TaskStatus.FAILED_GATE.value]
        total_failures = len(failures)

        failure_taxonomy: dict[str, int] = {}
        trend_by_day: dict[str, dict[str, int]] = {}
        for row in failures:
            reason = str(row.get('last_gate_reason') or '')
            bucket = _reason_bucket(reason) or 'other'
            failure_taxonomy[bucket] = failure_taxonomy.get(bucket, 0) + 1

            day = self._format_task_day(row.get('updated_at') or row.get('created_at'))
            day_bucket = trend_by_day.setdefault(day, {})
            day_bucket[bucket] = day_bucket.get(bucket, 0) + 1

        taxonomy_rows: list[dict] = []
        for bucket, count in sorted(failure_taxonomy.items(), key=lambda item: (-item[1], item[0])):
            share = (count / total_failures) if total_failures else 0.0
            taxonomy_rows.append({'bucket': bucket, 'count': count, 'share': round(share, 4)})

        trend_rows: list[dict] = []
        for day in sorted(trend_by_day.keys()):
            day_counts = trend_by_day[day]
            trend_rows.append(
                {
                    'day': day,
                    'total': int(sum(day_counts.values())),
                    'buckets': dict(sorted(day_counts.items(), key=lambda item: (-item[1], item[0]))),
                }
            )

        reviewer_counts: dict[str, dict[str, int]] = {}
        global_counts = {'no_blocker': 0, 'blocker': 0, 'unknown': 0}
        for row in rows:
            task_id = str(row.get('task_id') or '').strip()
            if not task_id:
                continue
            try:
                events = self.repository.list_events(task_id)
            except Exception:
                events = []
            for event in events:
                etype = str(event.get('type') or '').strip().lower()
                if etype not in {'review', 'proposal_review', 'proposal_precheck_review', 'debate_review'}:
                    continue
                payload = self._merged_event_payload(event)
                participant = str(payload.get('participant') or '').strip()
                if not participant:
                    continue
                verdict = str(payload.get('verdict') or '').strip().lower()
                if verdict not in {'no_blocker', 'blocker', 'unknown'}:
                    verdict = 'unknown'

                bucket = reviewer_counts.setdefault(participant, {'no_blocker': 0, 'blocker': 0, 'unknown': 0})
                bucket[verdict] = int(bucket.get(verdict, 0)) + 1
                global_counts[verdict] = int(global_counts.get(verdict, 0)) + 1

        global_total = int(sum(global_counts.values()))
        global_adverse_rate = (
            (global_counts.get('blocker', 0) + global_counts.get('unknown', 0)) / global_total
            if global_total
            else 0.0
        )

        reviewer_rows: list[dict] = []
        for participant, counts in reviewer_counts.items():
            total = int(sum(int(v) for v in counts.values()))
            if total <= 0:
                continue
            blocker_rate = counts.get('blocker', 0) / total
            unknown_rate = counts.get('unknown', 0) / total
            no_blocker_rate = counts.get('no_blocker', 0) / total
            adverse_rate = blocker_rate + unknown_rate
            reviewer_rows.append(
                {
                    'participant': participant,
                    'reviews': total,
                    'no_blocker_rate': round(no_blocker_rate, 4),
                    'blocker_rate': round(blocker_rate, 4),
                    'unknown_rate': round(unknown_rate, 4),
                    'adverse_rate': round(adverse_rate, 4),
                    'drift_score': round(abs(adverse_rate - global_adverse_rate), 4),
                }
            )

        reviewer_rows.sort(key=lambda item: (-float(item.get('drift_score', 0.0)), -int(item.get('reviews', 0)), item.get('participant', '')))

        return {
            'generated_at': datetime.now().isoformat(),
            'window_tasks': len(rows),
            'window_failed_gate': total_failures,
            'failure_taxonomy': taxonomy_rows,
            'failure_taxonomy_trend': trend_rows,
            'reviewer_global': {
                'reviews': global_total,
                'no_blocker_rate': round((global_counts.get('no_blocker', 0) / global_total) if global_total else 0.0, 4),
                'blocker_rate': round((global_counts.get('blocker', 0) / global_total) if global_total else 0.0, 4),
                'unknown_rate': round((global_counts.get('unknown', 0) / global_total) if global_total else 0.0, 4),
                'adverse_rate': round(global_adverse_rate, 4),
            },
            'reviewer_drift': reviewer_rows,
        }

    def build_github_pr_summary(self, task_id: str) -> dict:
        row = self.repository.get_task(task_id)
        if row is None:
            raise KeyError(task_id)

        project_path = str(row.get('project_path') or row.get('workspace_path') or '').strip()
        git = self._read_git_state(Path(project_path) if project_path else None)

        history_items = self.list_project_history(project_path=project_path, limit=500)
        history = next((item for item in history_items if str(item.get('task_id') or '') == task_id), None)

        findings = list(history.get('core_findings', [])) if isinstance(history, dict) else []
        revisions = dict(history.get('revisions', {})) if isinstance(history, dict) else {}
        disputes = list(history.get('disputes', [])) if isinstance(history, dict) else []
        next_steps = list(history.get('next_steps', [])) if isinstance(history, dict) else []

        artifacts = self._collect_task_artifacts(task_id=task_id)

        lines: list[str] = []
        lines.append(f'### AWE-AgentForge Task Summary | {task_id}')
        lines.append('')
        lines.append(f'- Title: {row.get("title")}')
        lines.append(f'- Status: {row.get("status")}')
        lines.append(f'- Last reason: {row.get("last_gate_reason") or "n/a"}')
        lines.append(f'- Rounds: {row.get("rounds_completed", 0)}/{row.get("max_rounds", 1)}')
        lines.append(f'- Project path: `{project_path}`')
        if git.get('is_git_repo'):
            lines.append(f'- Git branch: `{git.get("branch") or "detached"}`')
            lines.append(f'- Git worktree clean: `{git.get("worktree_clean")}`')
            if git.get('remote_origin'):
                lines.append(f'- Git remote: `{git.get("remote_origin")}`')
        lines.append('')
        lines.append('#### Core Findings')
        if findings:
            for item in findings[:5]:
                lines.append(f'- {item}')
        else:
            lines.append('- n/a')
        lines.append('')
        lines.append('#### Revisions')
        if revisions:
            lines.append(f'- auto_merge: `{bool(revisions.get("auto_merge", False))}`')
            lines.append(f'- mode: `{revisions.get("mode") or "n/a"}`')
            lines.append(f'- changed_files: `{int(revisions.get("changed_files") or 0)}`')
            lines.append(f'- copied_files: `{int(revisions.get("copied_files") or 0)}`')
            lines.append(f'- deleted_files: `{int(revisions.get("deleted_files") or 0)}`')
            if revisions.get('snapshot_path'):
                lines.append(f'- snapshot_path: `{revisions.get("snapshot_path")}`')
            if revisions.get('changelog_path'):
                lines.append(f'- changelog_path: `{revisions.get("changelog_path")}`')
        else:
            lines.append('- n/a')
        lines.append('')
        lines.append('#### Review Disputes')
        if disputes:
            for item in disputes[:5]:
                lines.append(
                    f'- {item.get("participant", "reviewer")} | {item.get("verdict", "unknown")}: '
                    f'{self._clip_snippet(item.get("note")) or "n/a"}'
                )
        else:
            lines.append('- none')
        lines.append('')
        lines.append('#### Next Steps')
        if next_steps:
            for item in next_steps[:5]:
                lines.append(f'- {item}')
        else:
            lines.append('- n/a')
        lines.append('')
        lines.append('#### Task Artifacts')
        if artifacts:
            for item in artifacts:
                lines.append(f'- {item["name"]}: `{item["path"]}`')
        else:
            lines.append('- n/a')

        return {
            'task_id': task_id,
            'project_path': project_path,
            'status': str(row.get('status') or ''),
            'git': git,
            'summary_markdown': '\n'.join(lines).strip() + '\n',
            'artifacts': artifacts,
        }

    def list_project_history(self, *, project_path: str | None = None, limit: int = 200) -> list[dict]:
        limit_int = max(1, min(1000, int(limit)))
        requested_project = self._normalize_project_path_key(project_path) if str(project_path or '').strip() else None

        rows = self.repository.list_tasks(limit=10_000)
        row_by_id: dict[str, dict] = {}
        for row in rows:
            task_id = str(row.get('task_id', '')).strip()
            if task_id:
                row_by_id[task_id] = row

        items: list[dict] = []
        seen: set[str] = set()
        threads_root = self.artifact_store.root / 'threads'
        thread_dirs: list[tuple[float, Path]] = []
        if threads_root.exists() and threads_root.is_dir():
            for child in threads_root.iterdir():
                if not child.is_dir():
                    continue
                try:
                    stamp = float(child.stat().st_mtime)
                except OSError:
                    stamp = 0.0
                thread_dirs.append((stamp, child))
            thread_dirs.sort(key=lambda pair: pair[0], reverse=True)

        for _, task_dir in thread_dirs:
            task_id = str(task_dir.name or '').strip()
            if not task_id:
                continue
            item = self._build_project_history_item(task_id=task_id, row=row_by_id.get(task_id), task_dir=task_dir)
            if item is None:
                continue
            project_key = self._normalize_project_path_key(item.get('project_path'))
            if requested_project and project_key != requested_project:
                continue
            items.append(item)
            seen.add(task_id)
            if len(items) >= limit_int:
                return items

        for row in rows:
            task_id = str(row.get('task_id', '')).strip()
            if not task_id or task_id in seen:
                continue
            item = self._build_project_history_item(task_id=task_id, row=row, task_dir=None)
            if item is None:
                continue
            project_key = self._normalize_project_path_key(item.get('project_path'))
            if requested_project and project_key != requested_project:
                continue
            items.append(item)
            if len(items) >= limit_int:
                break

        return items

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
        try:
            target.relative_to(base)
            return True
        except ValueError:
            return False

    @staticmethod
    def _validate_artifact_task_id(task_id: str) -> str:
        key = str(task_id or '').strip()
        if not key:
            raise InputValidationError('task_id is required', field='task_id')
        if '..' in key or '/' in key or '\\' in key:
            raise InputValidationError('invalid task_id', field='task_id')
        if not _ARTIFACT_TASK_ID_RE.fullmatch(key):
            raise InputValidationError('invalid task_id', field='task_id')
        return key

    @staticmethod
    def _normalize_history_events(*, task_id: str, events: list[dict]) -> list[dict]:
        normalized: list[dict] = []
        next_seq = 1
        for raw in events:
            if not isinstance(raw, dict):
                continue

            seq_raw = raw.get('seq')
            try:
                seq_value = int(seq_raw)
            except Exception:
                seq_value = next_seq
            if seq_value < 1:
                seq_value = next_seq

            payload_raw = raw.get('payload') if isinstance(raw.get('payload'), dict) else {}
            payload = dict(payload_raw) if isinstance(payload_raw, dict) else {}
            for key in (
                'output',
                'reason',
                'verdict',
                'participant',
                'provider',
                'stage',
                'mode',
                'changed_files',
                'copied_files',
                'deleted_files',
                'snapshot_path',
                'changelog_path',
                'merged_at',
            ):
                if key not in payload and key in raw:
                    payload[key] = raw.get(key)

            round_raw = raw.get('round')
            try:
                round_number = int(round_raw) if round_raw is not None else None
            except Exception:
                round_number = None

            created_at = str(raw.get('created_at') or '').strip() or datetime.now().isoformat()
            event_type = str(raw.get('type') or '').strip() or 'history_event'

            normalized.append(
                {
                    'seq': seq_value,
                    'task_id': str(raw.get('task_id') or task_id),
                    'type': event_type,
                    'round': round_number,
                    'payload': payload,
                    'created_at': created_at,
                }
            )
            next_seq = max(next_seq + 1, seq_value + 1)

        normalized.sort(key=lambda item: int(item.get('seq', 0)))
        return normalized

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
        if row:
            try:
                events = self.repository.list_events(task_id)
                if events:
                    return events
            except Exception:
                pass

        if task_dir is None:
            return []
        path = task_dir / 'events.jsonl'
        if not path.exists():
            return []
        out: list[dict] = []
        try:
            for raw in path.read_text(encoding='utf-8', errors='replace').splitlines():
                text = str(raw or '').strip()
                if not text:
                    continue
                try:
                    obj = json.loads(text)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    out.append(obj)
        except OSError:
            return []
        return out

    def _extract_core_findings(self, *, task_dir: Path | None, events: list[dict], fallback_reason: str | None) -> list[str]:
        findings: list[str] = []
        for line in self._read_markdown_highlights(task_dir / 'summary.md' if task_dir else None):
            if line not in findings:
                findings.append(line)
            if len(findings) >= 3:
                return findings

        for line in self._read_markdown_highlights(task_dir / 'final_report.md' if task_dir else None):
            if line not in findings:
                findings.append(line)
            if len(findings) >= 3:
                return findings

        interesting = {
            'gate_failed',
            'gate_passed',
            'manual_gate',
            'review',
            'proposal_review',
            'discussion',
            'debate_review',
            'debate_reply',
        }
        for event in events:
            etype = str(event.get('type') or '').strip().lower()
            if etype not in interesting:
                continue
            payload = self._merged_event_payload(event)
            snippet = (
                self._clip_snippet(payload.get('output'))
                or self._clip_snippet(payload.get('reason'))
                or self._clip_snippet(event.get('type'))
            )
            if not snippet:
                continue
            if snippet not in findings:
                findings.append(snippet)
            if len(findings) >= 3:
                return findings

        if fallback_reason and not findings:
            findings.append(f'Final reason: {fallback_reason}')

        return findings

    @staticmethod
    def _read_markdown_highlights(path: Path | None) -> list[str]:
        if path is None or not path.exists():
            return []
        try:
            raw = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return []
        lines: list[str] = []
        for item in raw.splitlines():
            text = str(item or '').strip()
            if not text:
                continue
            if text.startswith('#'):
                continue
            lines.append(text)
            if len(lines) >= 5:
                break
        return [OrchestratorService._clip_snippet(v) for v in lines if OrchestratorService._clip_snippet(v)]

    def _extract_revisions(self, *, task_dir: Path | None, events: list[dict]) -> dict:
        summary_path = (task_dir / 'artifacts' / 'auto_merge_summary.json') if task_dir is not None else None
        summary = self._read_json_file(summary_path) if summary_path else {}
        if not summary:
            for event in reversed(events):
                if str(event.get('type') or '').strip().lower() != 'auto_merge_completed':
                    continue
                payload = self._merged_event_payload(event)
                if isinstance(payload, dict):
                    summary = payload
                    break

        if not summary:
            return {'auto_merge': False}

        return {
            'auto_merge': True,
            'mode': str(summary.get('mode') or '').strip() or None,
            'changed_files': self._coerce_revision_count(summary.get('changed_files')),
            'copied_files': self._coerce_revision_count(summary.get('copied_files')),
            'deleted_files': self._coerce_revision_count(summary.get('deleted_files')),
            'snapshot_path': str(summary.get('snapshot_path') or '').strip() or None,
            'changelog_path': str(summary.get('changelog_path') or '').strip() or None,
            'merged_at': str(summary.get('merged_at') or '').strip() or None,
        }

    @staticmethod
    def _coerce_revision_count(value) -> int:
        if value is None:
            return 0
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        if isinstance(value, bool):
            return int(value)
        try:
            return max(0, int(value))
        except (TypeError, ValueError):
            pass
        text = str(value or '').strip()
        if not text:
            return 0
        try:
            return max(0, int(float(text)))
        except (TypeError, ValueError):
            return 0

    def _extract_disputes(self, events: list[dict]) -> list[dict]:
        disputes: list[dict] = []
        for event in events:
            etype = str(event.get('type') or '').strip().lower()
            payload = self._merged_event_payload(event)

            if etype in {'review', 'proposal_review'}:
                verdict = str(payload.get('verdict') or '').strip().lower()
                if verdict not in {ReviewVerdict.BLOCKER.value, ReviewVerdict.UNKNOWN.value}:
                    continue
                disputes.append(
                    {
                        'participant': str(payload.get('participant') or 'reviewer'),
                        'verdict': verdict,
                        'note': self._clip_snippet(payload.get('output')) or 'review raised concerns',
                    }
                )
            elif etype == 'gate_failed':
                reason = str(payload.get('reason') or '').strip()
                if not reason:
                    continue
                disputes.append(
                    {
                        'participant': 'system',
                        'verdict': 'gate_failed',
                        'note': self._clip_snippet(reason) or reason,
                    }
                )

            if len(disputes) >= 5:
                break

        return disputes

    @staticmethod
    def _merged_event_payload(event: dict) -> dict:
        payload = event.get('payload') if isinstance(event.get('payload'), dict) else {}
        out = dict(payload) if isinstance(payload, dict) else {}
        for key in (
            'output',
            'reason',
            'verdict',
            'participant',
            'provider',
            'mode',
            'changed_files',
            'copied_files',
            'deleted_files',
            'snapshot_path',
            'changelog_path',
            'merged_at',
        ):
            if key not in out and key in event:
                out[key] = event.get(key)
        return out

    @staticmethod
    def _derive_next_steps(*, status: str, reason: str | None, disputes: list[dict]) -> list[str]:
        s = str(status or '').strip().lower()
        r = str(reason or '').strip()
        if s == TaskStatus.WAITING_MANUAL.value:
            if r.startswith('proposal_consensus_stalled'):
                return ['Proposal discussion stalled. Use Custom Reply + Re-run to provide specific direction, then continue.']
            return ['Approve + Start to continue, or Reject to cancel this proposal.']
        if s == TaskStatus.RUNNING.value:
            return ['Task is still executing. Watch latest stage events or worker logs for progress.']
        if s == TaskStatus.QUEUED.value:
            return ['Start the task when ready, or keep it queued for scheduling.']
        if s == TaskStatus.PASSED.value:
            return ['Task passed. Optionally launch a follow-up evolution task for additional improvements.']
        if s == TaskStatus.FAILED_GATE.value:
            if disputes:
                return ['Address blocker/unknown review points, then rerun the task.']
            return [f'Address gate failure reason: {r}' if r else 'Address gate failures, then rerun.']
        if s == TaskStatus.FAILED_SYSTEM.value:
            return [f'Fix system issue: {r}' if r else 'Fix system/runtime issue, then rerun.']
        if s == TaskStatus.CANCELED.value:
            return ['Task was canceled. Recreate or restart only if requirements still apply.']
        return ['Inspect events and logs, then decide whether to rerun or revise scope.']

    @staticmethod
    def _clip_snippet(value, *, max_chars: int = 220) -> str:
        text = str(value or '').strip()
        if not text:
            return ''
        one_line = text.replace('\r', ' ').replace('\n', ' ')
        if len(one_line) <= max_chars:
            return one_line
        return one_line[:max_chars].rstrip() + '...'

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
        resolved = str(workspace_path or '').strip()
        if not resolved:
            return {
                'workspace_path': '',
                'exists': False,
                'repo_size': 'unknown',
                'risk_level': 'unknown',
                'file_count': 0,
                'risk_markers': 0,
            }

        root = Path(resolved)
        if not root.exists() or not root.is_dir():
            return {
                'workspace_path': str(root),
                'exists': False,
                'repo_size': 'unknown',
                'risk_level': 'unknown',
                'file_count': 0,
                'risk_markers': 0,
            }

        ignore_dirs = {
            '.git',
            '.agents',
            '.venv',
            '__pycache__',
            '.pytest_cache',
            '.ruff_cache',
            'node_modules',
        }
        risk_tokens = {
            'prod',
            'deploy',
            'k8s',
            'terraform',
            'helm',
            'security',
            'auth',
            'payment',
            'migrations',
            'migration',
            'database',
            'db',
        }
        risk_extensions = {'.sql', '.tf', '.yaml', '.yml'}

        file_count = 0
        risk_markers = 0
        max_scan = 5000
        for path in root.rglob('*'):
            if file_count >= max_scan:
                break
            if not path.is_file():
                continue
            rel_parts = path.relative_to(root).parts
            if any(part in ignore_dirs for part in rel_parts):
                continue
            file_count += 1
            rel_text = '/'.join(str(v).lower() for v in rel_parts)
            stem = path.stem.lower()
            ext = path.suffix.lower()
            if any(token in rel_text for token in risk_tokens):
                risk_markers += 1
                continue
            if ext in risk_extensions and stem in {'prod', 'deploy', 'migration', 'schema', 'security'}:
                risk_markers += 1

        if file_count <= 120:
            repo_size = 'small'
        elif file_count <= 1200:
            repo_size = 'medium'
        else:
            repo_size = 'large'

        if risk_markers >= 20 or (repo_size == 'large' and risk_markers >= 8):
            risk_level = 'high'
        elif risk_markers >= 6 or repo_size == 'large':
            risk_level = 'medium'
        else:
            risk_level = 'low'

        return {
            'workspace_path': str(root.resolve()),
            'exists': True,
            'repo_size': repo_size,
            'risk_level': risk_level,
            'file_count': file_count,
            'risk_markers': risk_markers,
            'scan_truncated': file_count >= max_scan,
        }

    @staticmethod
    def _recommend_policy_template(*, profile: dict) -> str:
        repo_size = str(profile.get('repo_size') or '').strip().lower()
        risk = str(profile.get('risk_level') or '').strip().lower()
        if repo_size == 'large' or risk == 'high':
            return 'safe-review'
        if repo_size == 'small' and risk == 'low':
            return 'rapid-fix'
        return _DEFAULT_POLICY_TEMPLATE

    def _collect_task_artifacts(self, *, task_id: str) -> list[dict]:
        key = self._validate_artifact_task_id(task_id)
        root = self.artifact_store.root / 'threads' / key
        if not root.exists() or not root.is_dir():
            return []
        out: list[dict] = []
        wanted = [
            ('state', root / 'state.json'),
            ('events', root / 'events.jsonl'),
            ('summary', root / 'summary.md'),
            ('final_report', root / 'final_report.md'),
            ('auto_merge_summary', root / 'artifacts' / 'auto_merge_summary.json'),
            ('pending_proposal', root / 'artifacts' / 'pending_proposal.json'),
        ]
        for name, path in wanted:
            if path.exists() and path.is_file():
                out.append({'name': name, 'path': str(path)})
        return out

    @staticmethod
    def _run_git_command(*, root: Path, args: list[str]) -> tuple[bool, str]:
        try:
            completed = subprocess.run(
                ['git', *args],
                cwd=str(root),
                capture_output=True,
                text=True,
                encoding='utf-8',
                errors='replace',
                timeout=5,
            )
        except Exception:
            return False, ''
        if completed.returncode != 0:
            return False, (completed.stderr or completed.stdout or '').strip()
        return True, (completed.stdout or '').strip()

    def _read_git_state(self, root: Path | None) -> dict:
        if root is None:
            return {
                'is_git_repo': False,
                'branch': None,
                'worktree_clean': None,
                'remote_origin': None,
                'guard_allowed': True,
                'guard_reason': 'no_target',
            }
        if not root.exists() or not root.is_dir():
            return {
                'is_git_repo': False,
                'branch': None,
                'worktree_clean': None,
                'remote_origin': None,
                'guard_allowed': True,
                'guard_reason': 'missing_path',
            }

        ok_git, git_flag = self._run_git_command(root=root, args=['rev-parse', '--is-inside-work-tree'])
        if not ok_git or git_flag.strip().lower() != 'true':
            return {
                'is_git_repo': False,
                'branch': None,
                'worktree_clean': None,
                'remote_origin': None,
                'guard_allowed': True,
                'guard_reason': 'non_git_repo',
            }

        ok_branch, branch = self._run_git_command(root=root, args=['branch', '--show-current'])
        ok_status, status_out = self._run_git_command(root=root, args=['status', '--porcelain'])
        ok_remote, remote = self._run_git_command(root=root, args=['remote', 'get-url', 'origin'])
        return {
            'is_git_repo': True,
            'branch': branch if ok_branch else None,
            'worktree_clean': (len(str(status_out or '').strip()) == 0) if ok_status else None,
            'remote_origin': remote if ok_remote else None,
            'guard_allowed': True,
            'guard_reason': 'unvalidated',
        }

    @staticmethod
    def _promotion_guard_config() -> dict:
        enabled = str(os.getenv('AWE_PROMOTION_GUARD_ENABLED', '1') or '1').strip().lower() in {'1', 'true', 'yes', 'on'}
        # Default is non-blocking for local development; enforce via env when needed.
        require_clean = str(os.getenv('AWE_PROMOTION_REQUIRE_CLEAN', '0') or '0').strip().lower() in {'1', 'true', 'yes', 'on'}
        raw_branches = str(os.getenv('AWE_PROMOTION_ALLOWED_BRANCHES', '') or '').strip()
        allowed_branches = [
            item.strip()
            for item in raw_branches.split(',')
            if item.strip()
        ]
        return {
            'enabled': enabled,
            'require_clean': require_clean,
            'allowed_branches': allowed_branches,
        }

    def _evaluate_promotion_guard(self, *, target_root: Path) -> dict:
        cfg = self._promotion_guard_config()
        git = self._read_git_state(target_root)
        payload = {
            'enabled': bool(cfg.get('enabled', True)),
            'target_path': str(target_root),
            'allowed_branches': list(cfg.get('allowed_branches', [])),
            'require_clean': bool(cfg.get('require_clean', True)),
            **git,
        }
        if not payload['enabled']:
            payload['guard_allowed'] = True
            payload['guard_reason'] = 'guard_disabled'
            return payload
        if not bool(payload.get('is_git_repo')):
            payload['guard_allowed'] = True
            payload['guard_reason'] = 'non_git_repo'
            return payload
        branch = str(payload.get('branch') or '').strip()
        allowed_branches = payload.get('allowed_branches') or []
        if allowed_branches and branch and branch not in allowed_branches:
            payload['guard_allowed'] = False
            payload['guard_reason'] = f'branch_not_allowed:{branch}'
            return payload
        if bool(payload.get('require_clean')) and payload.get('worktree_clean') is False:
            payload['guard_allowed'] = False
            payload['guard_reason'] = 'dirty_worktree'
            return payload
        payload['guard_allowed'] = True
        payload['guard_reason'] = 'ok'
        return payload

    @staticmethod
    def _read_json_file(path: Path | None) -> dict:
        if path is None or not path.exists():
            return {}
        try:
            raw = path.read_text(encoding='utf-8', errors='replace')
        except OSError:
            return {}
        try:
            data = json.loads(raw)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _guess_task_created_at(task_dir: Path | None, state: dict) -> str:
        if task_dir is None:
            return ''
        events_path = task_dir / 'events.jsonl'
        if events_path.exists():
            try:
                for raw in events_path.read_text(encoding='utf-8', errors='replace').splitlines():
                    text = str(raw or '').strip()
                    if not text:
                        continue
                    try:
                        obj = json.loads(text)
                    except Exception:
                        continue
                    if isinstance(obj, dict):
                        created_at = str(obj.get('created_at') or '').strip()
                        if created_at:
                            return created_at
            except OSError:
                pass
        updated = str(state.get('updated_at') or '').strip()
        if updated:
            return updated
        return ''

    @staticmethod
    def _guess_task_updated_at(task_dir: Path | None) -> str:
        if task_dir is None:
            return ''
        events_path = task_dir / 'events.jsonl'
        if events_path.exists():
            try:
                lines = [line.strip() for line in events_path.read_text(encoding='utf-8', errors='replace').splitlines() if line.strip()]
            except OSError:
                lines = []
            for raw in reversed(lines):
                try:
                    obj = json.loads(raw)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    created_at = str(obj.get('created_at') or '').strip()
                    if created_at:
                        return created_at
        try:
            return datetime.fromtimestamp(task_dir.stat().st_mtime).isoformat()
        except OSError:
            return ''

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
        return self._to_view(row)

    def start_task(self, task_id: str) -> TaskView:
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

        if self.max_concurrent_running_tasks > 0:
            running_now = self._count_running_tasks(exclude_task_id=task_id)
            if running_now >= self.max_concurrent_running_tasks:
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
                return self._to_view(deferred)

        # All modes require proposal consensus before implementation.
        needs_consensus = str(row.get('last_gate_reason') or '') != 'author_approved'
        if needs_consensus:
            row = self.repository.update_task_status(
                task_id,
                status=TaskStatus.RUNNING.value,
                reason=None,
                rounds_completed=row.get('rounds_completed', 0),
            )
            self.repository.append_event(task_id, event_type='task_running', payload={'status': 'running'}, round_number=None)
            self.artifact_store.update_state(task_id, {'status': 'running'})
            auto_approve = int(row.get('self_loop_mode', 0)) == 1
            prepared = self._prepare_author_confirmation(task_id, row, auto_approve=auto_approve)
            if not auto_approve:
                return prepared
            if prepared.status != TaskStatus.RUNNING:
                return prepared
            row = self.repository.get_task(task_id)
            if row is None:
                raise KeyError(task_id)
        else:
            row = self.repository.update_task_status(
                task_id,
                status=TaskStatus.RUNNING.value,
                reason=None,
                rounds_completed=row.get('rounds_completed', 0),
            )
            self.repository.append_event(task_id, event_type='task_running', payload={'status': 'running'}, round_number=None)
            self.artifact_store.update_state(task_id, {'status': 'running'})

        set_task_context(task_id=task_id)
        _log.info('task_started task_id=%s', task_id)
        workspace_root = Path(str(row.get('workspace_path') or Path.cwd()))
        round_artifacts_enabled = int(row.get('max_rounds', 1)) > 1 and not bool(row.get('auto_merge', True))
        round_snapshot_holder: list[Path | None] = [None]
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
                        self.artifact_store.append_event(task_id, {'type': 'round_artifact_ready', **round_payload})
                    except Exception as exc:
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
                        self.artifact_store.append_event(task_id, {'type': 'round_artifact_error', **error_payload})

        def should_cancel() -> bool:
            return self.repository.is_cancel_requested(task_id)

        try:
            author = parse_participant_id(row['author_participant'])
            reviewers = [parse_participant_id(v) for v in row['reviewer_participants']]
            baseline_manifest = self.fusion_manager.build_manifest(workspace_root)

            result = self.workflow_engine.run(
                RunConfig(
                    task_id=task_id,
                    title=row['title'],
                    description=row['description'],
                    author=author,
                    reviewers=reviewers,
                    evolution_level=max(0, min(2, int(row.get('evolution_level', 0)))),
                    evolve_until=(str(row.get('evolve_until')).strip() if row.get('evolve_until') else None),
                    conversation_language=self._normalize_conversation_language(row.get('conversation_language')),
                    provider_models=dict(row.get('provider_models', {})),
                    provider_model_params=dict(row.get('provider_model_params', {})),
                    participant_models=dict(row.get('participant_models', {})),
                    participant_model_params=dict(row.get('participant_model_params', {})),
                    claude_team_agents=bool(row.get('claude_team_agents', False)),
                    codex_multi_agents=bool(row.get('codex_multi_agents', False)),
                    repair_mode=self._normalize_repair_mode(row.get('repair_mode')),
                    plain_mode=self._normalize_plain_mode(row.get('plain_mode')),
                    stream_mode=self._normalize_bool_flag(row.get('stream_mode', True), default=True),
                    debate_mode=self._normalize_bool_flag(row.get('debate_mode', True), default=True),
                    cwd=Path(str(row.get('workspace_path') or Path.cwd())),
                    max_rounds=int(row['max_rounds']),
                    test_command=row['test_command'],
                    lint_command=row['lint_command'],
                ),
                on_event=on_event,
                should_cancel=should_cancel,
            )
        except Exception as exc:
            _log.error('workflow_error task_id=%s', task_id, exc_info=True)
            return self.mark_failed_system(task_id, reason=f'workflow_error: {exc}')

        final_status = self._map_run_status(result.status)
        _log.info('task_finished task_id=%s status=%s rounds=%d reason=%s',
                  task_id, final_status.value, result.rounds, result.gate_reason)

        # Atomic conditional update: only write the final status if the task
        # is still RUNNING.  If an external force_fail already transitioned it,
        # the update returns None and we honour the external state.
        updated = self.repository.update_task_status_if(
            task_id,
            expected_status=TaskStatus.RUNNING.value,
            status=final_status.value,
            reason=result.gate_reason,
            rounds_completed=result.rounds,
            set_cancel_requested=False,
        )
        if updated is None:
            latest = self.repository.get_task(task_id)
            if latest is None:
                raise KeyError(task_id)
            return self._to_view(latest)

        self.artifact_store.update_state(
            task_id,
            {
                'status': final_status.value,
                'last_gate_reason': result.gate_reason,
                'rounds_completed': result.rounds,
                'cancel_requested': False,
            },
        )
        self.artifact_store.write_final_report(
            task_id,
            f"status={final_status.value}\nrounds={result.rounds}\nreason={result.gate_reason}",
        )

        if final_status == TaskStatus.PASSED and bool(row.get('auto_merge', True)):
            try:
                target_root = self._resolve_merge_target(row)
                guard = self._evaluate_promotion_guard(target_root=target_root)
                self.repository.append_event(
                    task_id,
                    event_type='promotion_guard_checked',
                    payload=guard,
                    round_number=None,
                )
                self.artifact_store.append_event(task_id, {'type': 'promotion_guard_checked', **guard})
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
                        {'type': 'promotion_guard_blocked', 'reason': blocked_reason, **guard},
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
                return self.mark_failed_system(task_id, reason=f'auto_merge_error: {exc}')

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
            {'type': 'author_decision', **payload},
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
                {'type': 'author_feedback_requested', **feedback_payload},
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
            self._normalize_merge_target_path(merge_target_path)
            or self._normalize_merge_target_path(row.get('merge_target_path'))
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
        self.artifact_store.append_event(task_id, {'type': 'promotion_guard_checked', **guard})
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
        self.artifact_store.append_event(task_id, {'type': 'manual_round_promoted', **payload})
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

    def _prepare_author_confirmation(self, task_id: str, row: dict, *, auto_approve: bool = False) -> TaskView:
        summary = (
            f"Task: {str(row.get('title') or '')}\n"
            "Generated proposal requires author approval before implementation."
        )
        review_payload: list[dict] = []
        consensus_rounds = 0
        target_rounds = max(1, int(row.get('max_rounds', 1)))
        retry_limit = self._proposal_stall_retry_limit()
        repeat_round_limit = self._proposal_repeat_rounds_limit()

        try:
            runner = getattr(self.workflow_engine, 'runner', None)
            timeout = int(getattr(self.workflow_engine, 'participant_timeout_seconds', 3600))
            review_timeout = self._review_timeout_seconds(timeout)
            author = parse_participant_id(str(row['author_participant']))
            reviewers = [parse_participant_id(v) for v in row.get('reviewer_participants', [])]
            config = RunConfig(
                task_id=task_id,
                title=str(row.get('title', '')),
                description=str(row.get('description', '')),
                author=author,
                reviewers=reviewers,
                evolution_level=max(0, min(2, int(row.get('evolution_level', 0)))),
                evolve_until=(str(row.get('evolve_until')).strip() if row.get('evolve_until') else None),
                conversation_language=self._normalize_conversation_language(row.get('conversation_language')),
                provider_models=dict(row.get('provider_models', {})),
                provider_model_params=dict(row.get('provider_model_params', {})),
                participant_models=dict(row.get('participant_models', {})),
                participant_model_params=dict(row.get('participant_model_params', {})),
                claude_team_agents=bool(row.get('claude_team_agents', False)),
                codex_multi_agents=bool(row.get('codex_multi_agents', False)),
                repair_mode=self._normalize_repair_mode(row.get('repair_mode')),
                plain_mode=self._normalize_plain_mode(row.get('plain_mode')),
                stream_mode=self._normalize_bool_flag(row.get('stream_mode', True), default=True),
                debate_mode=self._normalize_bool_flag(row.get('debate_mode', True), default=True),
                cwd=Path(str(row.get('workspace_path') or Path.cwd())),
                max_rounds=int(row.get('max_rounds', 3)),
                test_command=str(row.get('test_command', 'py -m pytest -q')),
                lint_command=str(row.get('lint_command', 'py -m ruff check .')),
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
                                prompt=self._proposal_review_prompt(config, merged_context, stage=stage),
                                cwd=config.cwd,
                                timeout_seconds=review_timeout,
                                model=self._resolve_model_for_participant(
                                    participant_id=reviewer.participant_id,
                                    provider=reviewer.provider,
                                    provider_models=config.provider_models,
                                    participant_models=config.participant_models,
                                ),
                                model_params=self._resolve_model_params_for_participant(
                                    participant_id=reviewer.participant_id,
                                    provider=reviewer.provider,
                                    provider_model_params=config.provider_model_params,
                                    participant_model_params=config.participant_model_params,
                                ),
                                claude_team_agents=bool(config.claude_team_agents),
                                codex_multi_agents=bool(config.codex_multi_agents),
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
                        payload = {
                            'type': stage,
                            'round': round_no,
                            'participant': reviewer.participant_id,
                            'provider': reviewer.provider,
                            'verdict': verdict.value,
                            'output': review_text,
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
                        {'type': 'proposal_consensus_stalled', **stall_payload},
                    )
                    pending_payload = {
                        'summary': summary_text,
                        'self_loop_mode': int(row.get('self_loop_mode', 0)),
                        'consensus_rounds': rounds_completed,
                        'target_rounds': target_rounds,
                        'review_payload': list(latest_reviews),
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
                        {'type': 'author_confirmation_required', **pending_payload},
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
                                    {'type': 'proposal_precheck_unavailable', **fail_payload},
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
                            'type': 'proposal_discussion_started',
                            'round': round_no,
                            'provider': author.provider,
                            'participant': author.participant_id,
                            'timeout_seconds': timeout,
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
                            self._proposal_author_prompt(config, merged_context, pre_reviews)
                            if reviewer_first_mode
                            else WorkflowEngine._discussion_prompt(config, round_no, None)
                        )
                        try:
                            discussion = runner.run(
                                participant=author,
                                prompt=discussion_prompt,
                                cwd=config.cwd,
                                timeout_seconds=timeout,
                                model=self._resolve_model_for_participant(
                                    participant_id=author.participant_id,
                                    provider=author.provider,
                                    provider_models=config.provider_models,
                                    participant_models=config.participant_models,
                                ),
                                model_params=self._resolve_model_params_for_participant(
                                    participant_id=author.participant_id,
                                    provider=author.provider,
                                    provider_model_params=config.provider_model_params,
                                    participant_model_params=config.participant_model_params,
                                ),
                                claude_team_agents=bool(config.claude_team_agents),
                                codex_multi_agents=bool(config.codex_multi_agents),
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
                            reason = str(exc or 'discussion_failed').strip() or 'discussion_failed'
                            error_payload = {
                                'type': 'proposal_discussion_error',
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
                        if discussion_text:
                            discussion_event = {
                                'type': 'discussion',
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
                                {'type': 'proposal_review_partial', **partial_payload},
                            )
                            actionable_reviews = [
                                item
                                for item in round_latest_reviews
                                if OrchestratorService._is_actionable_proposal_review_text(
                                    str(item.get('output') or ''),
                                )
                            ]
                        no_blocker, blocker, unknown = self._proposal_verdict_counts(actionable_reviews)

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
                            self.artifact_store.append_event(task_id, {'type': 'proposal_consensus_reached', **ok_payload})
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
                                proposal_preview = WorkflowEngine._clip_text(round_latest_proposal, max_chars=800).strip()
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
                            self.artifact_store.append_event(task_id, {'type': 'proposal_consensus_retry', **retry_payload})
                            current_seed = self._append_proposal_feedback_context(
                                merged_after_review,
                                reviewer_id='consensus',
                                review_text=f'unresolved blockers={blocker}, unknown={unknown}',
                            )
                            if attempt >= retry_limit:
                                proposal_preview = WorkflowEngine._clip_text(round_latest_proposal, max_chars=800).strip()
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

            proposal_preview = WorkflowEngine._clip_text(discussion_text, max_chars=1200).strip()
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
            {'type': 'author_confirmation_required', **pending_payload},
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
            self.artifact_store.append_event(task_id, {'type': 'author_decision', **decision_payload})
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
        rows = self.repository.list_tasks(limit=10_000)
        count = 0
        for row in rows:
            task_id = str(row.get('task_id', ''))
            if exclude_task_id and task_id == exclude_task_id:
                continue
            if str(row.get('status', '')) == TaskStatus.RUNNING.value:
                count += 1
        return count

    @staticmethod
    def _parse_iso_datetime(value) -> datetime | None:
        text = str(value or '').strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            return None

    @staticmethod
    def _normalize_evolve_until(value: str | None) -> str | None:
        text = str(value or '').strip()
        if not text:
            return None
        candidate = text.replace(' ', 'T')
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError as exc:
            raise InputValidationError(
                'evolve_until must be ISO/local datetime',
                field='evolve_until',
            ) from exc
        return parsed.replace(microsecond=0).isoformat()

    @staticmethod
    def _normalize_merge_target_path(value: str | None) -> str | None:
        text = str(value or '').strip()
        if not text:
            return None
        return str(Path(text))

    @staticmethod
    def _normalize_conversation_language(value: str | None, *, strict: bool = False) -> str:
        text = str(value or '').strip().lower()
        if not text:
            return 'en'
        aliases = {
            'en': 'en',
            'english': 'en',
            'eng': 'en',
            'zh': 'zh',
            'zh-cn': 'zh',
            'cn': 'zh',
            'chinese': 'zh',
            '中文': 'zh',
        }
        normalized = aliases.get(text, text)
        if normalized not in _SUPPORTED_CONVERSATION_LANGUAGES:
            if strict:
                raise InputValidationError(
                    f'invalid conversation_language: {text}',
                    field='conversation_language',
                )
            return 'en'
        return normalized

    @staticmethod
    def _normalize_repair_mode(value, *, strict: bool = False) -> str:
        text = str(value or '').strip().lower()
        if not text:
            return 'balanced'
        if text not in _SUPPORTED_REPAIR_MODES:
            if strict:
                raise InputValidationError(
                    f'invalid repair_mode: {text}',
                    field='repair_mode',
                )
            return 'balanced'
        return text

    @staticmethod
    def _normalize_plain_mode(value) -> bool:
        text = str(value).strip().lower()
        if text in {'0', 'false', 'no', 'off'}:
            return False
        if text in {'1', 'true', 'yes', 'on'}:
            return True
        return bool(value)

    @staticmethod
    def _normalize_bool_flag(value, *, default: bool) -> bool:
        text = str(value).strip().lower()
        if text in {'0', 'false', 'no', 'off'}:
            return False
        if text in {'1', 'true', 'yes', 'on'}:
            return True
        if text in {'', 'none'}:
            return bool(default)
        return bool(value)

    @staticmethod
    def _normalize_provider_models(value: dict[str, str] | None) -> dict[str, str]:
        if not value:
            return {}
        if not isinstance(value, dict):
            raise InputValidationError('provider_models must be an object', field='provider_models')

        out: dict[str, str] = {}
        for raw_provider, raw_model in value.items():
            provider = str(raw_provider or '').strip().lower()
            model = str(raw_model or '').strip()
            if provider not in _supported_providers():
                raise InputValidationError(
                    f'invalid provider_models key: {provider}',
                    field='provider_models',
                )
            if not model:
                raise InputValidationError(
                    f'provider_models[{provider}] cannot be empty',
                    field=f'provider_models[{provider}]',
                )
            out[provider] = model
        return out

    @staticmethod
    def _extract_model_from_command(command: str) -> str | None:
        text = str(command or '').strip()
        if not text:
            return None
        try:
            argv = shlex.split(text, posix=False)
        except ValueError:
            argv = text.split()
        i = 0
        while i < len(argv):
            token = str(argv[i]).strip()
            if token in {'-m', '--model'}:
                if i + 1 < len(argv):
                    value = str(argv[i + 1]).strip()
                    return value or None
            if token.startswith('--model='):
                value = token.split('=', 1)[1].strip()
                return value or None
            i += 1
        return None

    @staticmethod
    def _normalize_provider_model_params(value: dict[str, str] | None) -> dict[str, str]:
        if not value:
            return {}
        if not isinstance(value, dict):
            raise InputValidationError('provider_model_params must be an object', field='provider_model_params')

        out: dict[str, str] = {}
        for raw_provider, raw_params in value.items():
            provider = str(raw_provider or '').strip().lower()
            params = str(raw_params or '').strip()
            if provider not in _supported_providers():
                raise InputValidationError(
                    f'invalid provider_model_params key: {provider}',
                    field='provider_model_params',
                )
            if not params:
                raise InputValidationError(
                    f'provider_model_params[{provider}] cannot be empty',
                    field=f'provider_model_params[{provider}]',
                )
            out[provider] = params
        return out

    @staticmethod
    def _normalize_participant_models(
        value: dict[str, str] | None,
        *,
        known_participants: set[str],
    ) -> dict[str, str]:
        if not value:
            return {}
        if not isinstance(value, dict):
            raise InputValidationError('participant_models must be an object', field='participant_models')

        known = {str(v or '').strip() for v in known_participants if str(v or '').strip()}
        known_lower = {v.lower() for v in known}
        out: dict[str, str] = {}
        for raw_participant, raw_model in value.items():
            participant = str(raw_participant or '').strip()
            model = str(raw_model or '').strip()
            if not participant:
                raise InputValidationError(
                    'participant_models key cannot be empty',
                    field='participant_models',
                )
            if participant.lower() not in known_lower:
                raise InputValidationError(
                    f'participant_models key is not in task participants: {participant}',
                    field='participant_models',
                )
            if not model:
                raise InputValidationError(
                    f'participant_models[{participant}] cannot be empty',
                    field=f'participant_models[{participant}]',
                )
            out[participant] = model
        return out

    @staticmethod
    def _normalize_participant_model_params(
        value: dict[str, str] | None,
        *,
        known_participants: set[str],
    ) -> dict[str, str]:
        if not value:
            return {}
        if not isinstance(value, dict):
            raise InputValidationError('participant_model_params must be an object', field='participant_model_params')

        known = {str(v or '').strip() for v in known_participants if str(v or '').strip()}
        known_lower = {v.lower() for v in known}
        out: dict[str, str] = {}
        for raw_participant, raw_params in value.items():
            participant = str(raw_participant or '').strip()
            params = str(raw_params or '').strip()
            if not participant:
                raise InputValidationError(
                    'participant_model_params key cannot be empty',
                    field='participant_model_params',
                )
            if participant.lower() not in known_lower:
                raise InputValidationError(
                    f'participant_model_params key is not in task participants: {participant}',
                    field='participant_model_params',
                )
            if not params:
                raise InputValidationError(
                    f'participant_model_params[{participant}] cannot be empty',
                    field=f'participant_model_params[{participant}]',
                )
            out[participant] = params
        return out

    @staticmethod
    def _resolve_model_for_participant(
        *,
        participant_id: str,
        provider: str,
        provider_models: dict[str, str] | None,
        participant_models: dict[str, str] | None,
    ) -> str | None:
        participant_text = str(participant_id or '').strip()
        participant_map = dict(participant_models or {})
        if participant_text:
            exact = str(participant_map.get(participant_text) or '').strip()
            if exact:
                return exact
            lowered = str(participant_map.get(participant_text.lower()) or '').strip()
            if lowered:
                return lowered
        provider_map = dict(provider_models or {})
        return str(provider_map.get(str(provider or '').strip().lower()) or '').strip() or None

    @staticmethod
    def _resolve_model_params_for_participant(
        *,
        participant_id: str,
        provider: str,
        provider_model_params: dict[str, str] | None,
        participant_model_params: dict[str, str] | None,
    ) -> str | None:
        participant_text = str(participant_id or '').strip()
        participant_map = dict(participant_model_params or {})
        if participant_text:
            exact = str(participant_map.get(participant_text) or '').strip()
            if exact:
                return exact
            lowered = str(participant_map.get(participant_text.lower()) or '').strip()
            if lowered:
                return lowered
        provider_map = dict(provider_model_params or {})
        return str(provider_map.get(str(provider or '').strip().lower()) or '').strip() or None

    @staticmethod
    def _default_sandbox_path(project_root: Path) -> str:
        configured_base = str(os.getenv('AWE_SANDBOX_BASE', '') or '').strip()
        if configured_base:
            base = Path(configured_base).resolve()
        else:
            shared_opt_in = str(os.getenv('AWE_SANDBOX_USE_PUBLIC_BASE', '') or '').strip().lower()
            if shared_opt_in in {'1', 'true', 'yes', 'on'}:
                if os.name == 'nt':
                    public_home = str(os.getenv('PUBLIC', 'C:/Users/Public') or 'C:/Users/Public').strip()
                    base = Path(public_home).resolve() / 'awe-agentcheck-sandboxes'
                else:
                    base = Path('/tmp/awe-agentcheck-sandboxes').resolve()
            else:
                base = Path.home().resolve() / '.awe-agentcheck' / 'sandboxes'
        root = base / f'{project_root.name}-lab'
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        suffix = uuid4().hex[:6]
        return str(root / f'{stamp}-{suffix}')

    @staticmethod
    def _cleanup_create_task_sandbox_failure(
        *,
        sandbox_mode: bool,
        sandbox_generated: bool,
        project_root: Path,
        sandbox_root: Path | None,
    ) -> None:
        if not sandbox_mode:
            return
        if not sandbox_generated:
            return
        if sandbox_root is None:
            return
        try:
            project_resolved = project_root.resolve()
            sandbox_resolved = sandbox_root.resolve()
        except Exception:
            return
        if sandbox_resolved == project_resolved:
            return
        try:
            if sandbox_resolved.exists():
                def _onerror(func, p, exc_info):
                    try:
                        os.chmod(p, stat.S_IWRITE)
                        func(p)
                    except Exception:
                        pass
                shutil.rmtree(sandbox_resolved, onerror=_onerror)
        except Exception:
            pass

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
                    except Exception:
                        pass
                shutil.rmtree(sandbox_root, onerror=_onerror)
            payload['ok'] = True
            payload['removed'] = True
        except Exception as exc:
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
        normalized = rel_path.replace('\\', '/').strip()
        while normalized.startswith('./'):
            normalized = normalized[2:]
        while normalized.startswith('/'):
            normalized = normalized[1:]
        if not normalized:
            return False
        head = normalized.split('/', 1)[0]
        ignored_heads = {
            '.git',
            '.agents',
            '.claude',
            '.venv',
            '__pycache__',
            '.pytest_cache',
            '.ruff_cache',
            'node_modules',
            '.mypy_cache',
            '.idea',
            '.vscode',
        }
        if head in ignored_heads:
            return True
        if normalized.endswith('.pyc') or normalized.endswith('.pyo'):
            return True
        leaf = Path(normalized).name
        if OrchestratorService._is_windows_reserved_device_name(leaf):
            return True
        leaf = leaf.lower()
        if leaf == '.env' or leaf.startswith('.env.'):
            return True
        if leaf.endswith('.pem') or leaf.endswith('.key'):
            return True
        if re.search(r'(^|[._-])(token|tokens|secret|secrets|apikey|api-key|access-key)([._-]|$)', leaf):
            return True
        return False

    @staticmethod
    def _is_windows_reserved_device_name(filename: str) -> bool:
        # Windows blocks these names even with extensions (for example, `nul.txt`).
        normalized = str(filename or '').strip().rstrip(' .').lower()
        if not normalized:
            return False
        normalized = normalized.split(':', 1)[0]
        stem = normalized.split('.', 1)[0]
        if stem in {'con', 'prn', 'aux', 'nul'}:
            return True
        return bool(re.fullmatch(r'(com|lpt)[1-9]', stem))

    @staticmethod
    def _bootstrap_sandbox_workspace(project_root: Path, sandbox_root: Path) -> None:
        try:
            entries = list(sandbox_root.iterdir())
        except OSError:
            entries = []
        if entries:
            return

        for root, dirs, files in os.walk(project_root):
            root_path = Path(root)
            rel_root = root_path.relative_to(project_root)
            rel_root_text = '' if str(rel_root) == '.' else rel_root.as_posix()
            if rel_root_text and OrchestratorService._is_sandbox_ignored(rel_root_text):
                dirs[:] = []
                continue

            keep_dirs: list[str] = []
            for name in dirs:
                rel = f'{rel_root_text}/{name}' if rel_root_text else name
                if not OrchestratorService._is_sandbox_ignored(rel):
                    keep_dirs.append(name)
            dirs[:] = keep_dirs

            for filename in files:
                rel = f'{rel_root_text}/{filename}' if rel_root_text else filename
                if OrchestratorService._is_sandbox_ignored(rel):
                    continue
                src = root_path / filename
                dst = sandbox_root / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)

    @staticmethod
    def _proposal_review_prompt(config: RunConfig, discussion_output: str, *, stage: str = 'proposal_review') -> str:
        clipped = WorkflowEngine._clip_text(discussion_output, max_chars=2500)
        language_instruction = WorkflowEngine._conversation_language_instruction(config.conversation_language)
        plain_instruction = WorkflowEngine._plain_mode_instruction(bool(config.plain_mode))
        plain_review_format = WorkflowEngine._plain_review_format_instruction(
            enabled=bool(config.plain_mode),
            language=config.conversation_language,
        )
        stage_text = str(stage or 'proposal_review').strip().lower()
        audit_intent = OrchestratorService._is_audit_intent(config)
        stage_guidance = (
            "Stage: precheck. Build a concrete review scope."
            " For audit/discovery tasks, run repository checks as needed and cite concrete evidence."
            " Then summarize findings for author discussion."
            if stage_text == 'proposal_precheck_review'
            else "Stage: proposal review. Evaluate the updated proposal and unresolved risks."
        )
        scope_guidance = (
            "If user request is broad, do not block only for broad wording."
            " Convert it into concrete review scope, checks, and priorities, then continue."
        )
        depth_guidance = (
            "Task mode is audit/discovery: run repository checks as needed and cite evidence."
            if audit_intent
            else "Keep checks focused on current feature scope and known risk paths."
        )
        return (
            f"Task: {config.title}\n"
            "You are reviewing a proposed implementation plan before code changes.\n"
            "Mark BLOCKER only for correctness, regression, security, or data-loss risks.\n"
            f"{language_instruction}\n"
            f"{plain_instruction}\n"
            f"{stage_guidance}\n"
            f"{scope_guidance}\n"
            f"{depth_guidance}\n"
            "Keep output concise but complete enough to justify verdict.\n"
            "Do not include command logs, internal process narration, or tool/skill references.\n"
            "If evidence is insufficient, return VERDICT: UNKNOWN quickly.\n"
            "Output one line: VERDICT: NO_BLOCKER or VERDICT: BLOCKER or VERDICT: UNKNOWN.\n"
            f"{plain_review_format}\n"
            f"Plan:\n{clipped}\n"
        )

    @staticmethod
    def _review_timeout_seconds(participant_timeout_seconds: int) -> int:
        return max(1, int(participant_timeout_seconds))

    @staticmethod
    def _proposal_author_prompt(config: RunConfig, merged_context: str, review_payload: list[dict]) -> str:
        clipped = WorkflowEngine._clip_text(merged_context, max_chars=3200)
        language_instruction = WorkflowEngine._conversation_language_instruction(config.conversation_language)
        plain_instruction = WorkflowEngine._plain_mode_instruction(bool(config.plain_mode))
        no_blocker = sum(1 for item in review_payload if str(item.get('verdict')) == ReviewVerdict.NO_BLOCKER.value)
        blocker = sum(1 for item in review_payload if str(item.get('verdict')) == ReviewVerdict.BLOCKER.value)
        unknown = sum(1 for item in review_payload if str(item.get('verdict')) == ReviewVerdict.UNKNOWN.value)
        audit_author_guidance = (
            "This is audit/discovery intent. Convert reviewer findings into a concrete execution plan: "
            "scope(files/modules), checks/tests, expected outputs, and stop conditions."
            if OrchestratorService._is_audit_intent(config)
            else "Keep proposal concrete and implementation-ready."
        )
        return (
            f"Task: {config.title}\n"
            "You are the author responding to reviewer-first proposal feedback.\n"
            "Reviewer leads this phase. Do not invent unrelated changes.\n"
            f"{language_instruction}\n"
            f"{plain_instruction}\n"
            f"Reviewer verdict snapshot: no_blocker={no_blocker}, blocker={blocker}, unknown={unknown}\n"
            "Produce an updated implementation proposal for manual approval.\n"
            "Address reviewer concerns explicitly and keep plan incremental.\n"
            "Only propose changes that map to reviewer findings and user intent.\n"
            "If a reviewer suggestion is unsafe, explain why and give a safer replacement.\n"
            "Do not add default secrets/tokens, hidden bypasses, or broad unrelated refactors.\n"
            f"{audit_author_guidance}\n"
            "Do not ask follow-up questions.\n"
            f"Context:\n{clipped}\n"
        )

    @staticmethod
    def _is_audit_intent(config: RunConfig) -> bool:
        text = f"{str(config.title or '')}\n{str(config.description or '')}".lower()
        if not text.strip():
            return False
        keywords = (
            'audit',
            'review',
            'inspect',
            'scan',
            'check',
            'bug',
            'bugs',
            'vulnerability',
            'vulnerabilities',
            'security',
            'hardening',
            'improve',
            'improvement',
            'quality',
            'refine',
            '漏洞',
            '缺陷',
            '错误',
            '检查',
            '审查',
            '评审',
            '改进',
            '优化',
            '完善',
            '风险',
        )
        return any(k in text for k in keywords)

    @staticmethod
    def _looks_like_scope_ambiguity(review_text: str) -> bool:
        text = str(review_text or '').lower()
        if not text:
            return False
        hints = (
            'too vague',
            'vague',
            'unclear scope',
            'not specific',
            'no specific bug',
            '缺少具体',
            '太模糊',
            '不明确',
            '没有具体',
            '先说明具体',
            '无法判断改动',
        )
        return any(h in text for h in hints)

    @staticmethod
    def _looks_like_hard_risk(review_text: str) -> bool:
        text = str(review_text or '').lower()
        if not text:
            return False
        hints = (
            'data loss',
            'destructive',
            'unsafe',
            'critical',
            'high risk',
            'regression',
            'security risk',
            'sql injection',
            'rce',
            '权限',
            '数据丢失',
            '高风险',
            '严重',
            '回归',
            '安全风险',
        )
        return any(h in text for h in hints)

    @staticmethod
    def _normalize_proposal_reviewer_result(
        *,
        config: RunConfig,
        stage: str,
        verdict: ReviewVerdict,
        review_text: str,
    ) -> tuple[ReviewVerdict, str]:
        stage_text = str(stage or '').strip().lower()
        if stage_text not in {'proposal_precheck_review', 'proposal_review'}:
            return verdict, review_text
        if verdict not in {ReviewVerdict.UNKNOWN, ReviewVerdict.BLOCKER}:
            return verdict, review_text
        if not OrchestratorService._looks_like_scope_ambiguity(review_text):
            return verdict, review_text
        if OrchestratorService._looks_like_hard_risk(review_text):
            return verdict, review_text

        guidance = (
            "[system_note] Scope ambiguity is non-blocking: reviewer must convert broad user intent into "
            "concrete scope (files/modules), checks, and priorities, then continue."
        )
        merged = str(review_text or '').strip()
        if guidance not in merged:
            merged = f"{merged}\n\n{guidance}".strip()
        return ReviewVerdict.NO_BLOCKER, merged

    @staticmethod
    def _append_proposal_feedback_context(base_text: str, *, reviewer_id: str, review_text: str) -> str:
        seed = str(base_text or '').strip()
        note = str(review_text or '').strip()
        if not note:
            return seed
        merged = f"{seed}\n\n[reviewer:{reviewer_id}]\n{note}".strip()
        return WorkflowEngine._clip_text(merged, max_chars=4500)

    @staticmethod
    def _proposal_verdict_counts(review_payload: list[dict]) -> tuple[int, int, int]:
        no_blocker = sum(1 for item in review_payload if str(item.get('verdict')) == ReviewVerdict.NO_BLOCKER.value)
        blocker = sum(1 for item in review_payload if str(item.get('verdict')) == ReviewVerdict.BLOCKER.value)
        unknown = sum(1 for item in review_payload if str(item.get('verdict')) == ReviewVerdict.UNKNOWN.value)
        return no_blocker, blocker, unknown

    @staticmethod
    def _proposal_consensus_reached(review_payload: list[dict], *, expected_reviewers: int) -> bool:
        if expected_reviewers <= 0:
            return True
        if len(review_payload) < expected_reviewers:
            return False
        return all(str(item.get('verdict')) == ReviewVerdict.NO_BLOCKER.value for item in review_payload[:expected_reviewers])

    @staticmethod
    def _proposal_review_usable_count(review_payload: list[dict]) -> int:
        usable = 0
        for item in review_payload:
            if OrchestratorService._is_actionable_proposal_review_text(str(item.get('output') or '')):
                usable += 1
        return usable

    @staticmethod
    def _proposal_stall_retry_limit() -> int:
        return _PROPOSAL_STALL_RETRY_LIMIT

    @staticmethod
    def _proposal_repeat_rounds_limit() -> int:
        return _PROPOSAL_REPEAT_ROUNDS_LIMIT

    @staticmethod
    def _proposal_round_signature(review_payload: list[dict], *, proposal_text: str) -> str:
        parts: list[str] = []
        for item in review_payload:
            participant = str(item.get('participant') or '').strip().lower()
            verdict = str(item.get('verdict') or '').strip().lower()
            text = str(item.get('output') or '').strip().lower()
            text = re.sub(r'\s+', ' ', text)
            if len(text) > 300:
                text = text[:300]
            parts.append(f'{participant}|{verdict}|{text}')

        proposal = re.sub(r'\s+', ' ', str(proposal_text or '').strip().lower())
        if len(proposal) > 200:
            proposal = proposal[:200]
        payload = '\n'.join(sorted(parts) + [f'proposal|{proposal}'])
        if not payload.strip():
            return ''
        return hashlib.sha1(payload.encode('utf-8')).hexdigest()[:16]

    @staticmethod
    def _is_actionable_proposal_review_text(text: str) -> bool:
        payload = str(text or '').strip()
        if not payload:
            return False
        lowered = payload.lower()
        if lowered.startswith('[proposal_precheck_review_error]'):
            return False
        if lowered.startswith('[proposal_review_error]'):
            return False
        if 'command_timeout provider=' in lowered:
            return False
        if 'provider_limit provider=' in lowered:
            return False
        if 'command_not_found provider=' in lowered:
            return False
        return True

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
            evolution_level=max(0, min(2, int(row.get('evolution_level', 0)))),
            evolve_until=(str(row.get('evolve_until')).strip() if row.get('evolve_until') else None),
            conversation_language=OrchestratorService._normalize_conversation_language(row.get('conversation_language')),
            provider_models={str(k): str(v) for k, v in dict(row.get('provider_models', {})).items()},
            provider_model_params={str(k): str(v) for k, v in dict(row.get('provider_model_params', {})).items()},
            participant_models={str(k): str(v) for k, v in dict(row.get('participant_models', {})).items()},
            participant_model_params={str(k): str(v) for k, v in dict(row.get('participant_model_params', {})).items()},
            claude_team_agents=bool(row.get('claude_team_agents', False)),
            codex_multi_agents=bool(row.get('codex_multi_agents', False)),
            repair_mode=OrchestratorService._normalize_repair_mode(row.get('repair_mode')),
            plain_mode=OrchestratorService._normalize_plain_mode(row.get('plain_mode', True)),
            stream_mode=OrchestratorService._normalize_bool_flag(row.get('stream_mode', True), default=True),
            debate_mode=OrchestratorService._normalize_bool_flag(row.get('debate_mode', True), default=True),
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
            test_command=str(row.get('test_command', 'py -m pytest -q')),
            lint_command=str(row.get('lint_command', 'py -m ruff check .')),
            rounds_completed=int(row.get('rounds_completed', 0)),
            cancel_requested=bool(row.get('cancel_requested', False)),
        )
