from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
from uuid import uuid4

from awe_agentcheck.adapters import ParticipantRunner
from awe_agentcheck.domain.gate import evaluate_medium_gate
from awe_agentcheck.domain.models import ReviewVerdict, TaskStatus
from awe_agentcheck.fusion import AutoFusionManager
from awe_agentcheck.observability import get_logger, set_task_context
from awe_agentcheck.participants import SUPPORTED_PROVIDERS, parse_participant_id
from awe_agentcheck.repository import TaskRepository
from awe_agentcheck.storage.artifacts import ArtifactStore
from awe_agentcheck.workflow import RunConfig, ShellCommandExecutor, WorkflowEngine

_log = get_logger('awe_agentcheck.service')


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
    claude_team_agents: bool = False
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
    claude_team_agents: bool
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
        'claude-opus-4-1',
        'claude-sonnet-4-5',
        'claude-3-7-sonnet',
        'claude-3-5-sonnet-latest',
    ],
    'codex': [
        'gpt-5.3-codex',
        'gpt-5-codex',
        'gpt-5',
        'gpt-5-mini',
        'gpt-4.1',
    ],
    'gemini': [
        'gemini-3-pro-preview',
        'gemini-2.5-pro',
        'gemini-2.5-flash',
        'gemini-2.5-flash-lite',
    ],
}

_SUPPORTED_CONVERSATION_LANGUAGES = {'en', 'zh'}


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
            parse_participant_id(payload.author_participant)
        except ValueError as exc:
            raise ValueError(f'invalid author_participant: {exc}') from exc
        for i, rp in enumerate(payload.reviewer_participants):
            try:
                parse_participant_id(rp)
            except ValueError as exc:
                raise ValueError(f'invalid reviewer_participants[{i}]: {exc}') from exc

        project_root = Path(payload.workspace_path).resolve()
        if not project_root.exists() or not project_root.is_dir():
            raise ValueError(f'workspace_path must be an existing directory: {payload.workspace_path}')
        evolution_level = max(0, min(2, int(payload.evolution_level)))
        evolve_until = self._normalize_evolve_until(payload.evolve_until)
        conversation_language = self._normalize_conversation_language(
            payload.conversation_language,
            strict=True,
        )
        provider_models = self._normalize_provider_models(payload.provider_models)
        provider_model_params = self._normalize_provider_model_params(payload.provider_model_params)
        claude_team_agents = bool(payload.claude_team_agents)
        sandbox_mode = bool(payload.sandbox_mode)
        self_loop_mode = max(0, min(1, int(payload.self_loop_mode)))
        sandbox_cleanup_on_pass = bool(payload.sandbox_cleanup_on_pass)
        sandbox_workspace_path = self._normalize_merge_target_path(payload.sandbox_workspace_path)
        sandbox_generated = False
        if sandbox_mode:
            if not sandbox_workspace_path:
                sandbox_workspace_path = self._default_sandbox_path(project_root)
                sandbox_generated = True
            sandbox_root = Path(sandbox_workspace_path)
            if sandbox_root.exists() and not sandbox_root.is_dir():
                raise ValueError(f'sandbox_workspace_path must be a directory: {sandbox_workspace_path}')
            sandbox_root.mkdir(parents=True, exist_ok=True)
            self._bootstrap_sandbox_workspace(project_root, sandbox_root)
            workspace_root = sandbox_root
        else:
            sandbox_workspace_path = None
            sandbox_generated = False
            workspace_root = project_root

        auto_merge = bool(payload.auto_merge)
        merge_target_path = self._normalize_merge_target_path(payload.merge_target_path)
        if auto_merge and sandbox_mode and not merge_target_path:
            merge_target_path = str(project_root)
        if auto_merge and merge_target_path:
            merge_target = Path(merge_target_path)
            if not merge_target.exists() or not merge_target.is_dir():
                raise ValueError(f'merge_target_path must be an existing directory: {merge_target_path}')

        row = self.repository.create_task(
            title=payload.title,
            description=payload.description,
            author_participant=payload.author_participant,
            reviewer_participants=payload.reviewer_participants,
            evolution_level=evolution_level,
            evolve_until=evolve_until,
            conversation_language=conversation_language,
            provider_models=provider_models,
            provider_model_params=provider_model_params,
            claude_team_agents=claude_team_agents,
            sandbox_mode=sandbox_mode,
            sandbox_workspace_path=sandbox_workspace_path,
            sandbox_generated=sandbox_generated,
            sandbox_cleanup_on_pass=sandbox_cleanup_on_pass,
            project_path=str(project_root),
            self_loop_mode=self_loop_mode,
            auto_merge=auto_merge,
            merge_target_path=merge_target_path,
            workspace_path=str(workspace_root),
            max_rounds=payload.max_rounds,
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
                'claude_team_agents': bool(row.get('claude_team_agents', False)),
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
        catalog: dict[str, list[str]] = {
            provider: list(_DEFAULT_PROVIDER_MODELS.get(provider, []))
            for provider in sorted(SUPPORTED_PROVIDERS)
        }

        def add_model(provider: str, model: str) -> None:
            provider_key = str(provider or '').strip().lower()
            model_name = str(model or '').strip()
            if provider_key not in SUPPORTED_PROVIDERS or not model_name:
                return
            bucket = catalog.setdefault(provider_key, [])
            if model_name not in bucket:
                bucket.append(model_name)

        runner = getattr(self.workflow_engine, 'runner', None)
        commands = getattr(runner, 'commands', {}) if runner is not None else {}
        if isinstance(commands, dict):
            for raw_provider, raw_command in commands.items():
                provider = str(raw_provider or '').strip().lower()
                if provider not in SUPPORTED_PROVIDERS:
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
        for provider in sorted(SUPPORTED_PROVIDERS):
            out[provider] = [str(v) for v in catalog.get(provider, []) if str(v).strip()]
        return out

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

    def list_events(self, task_id: str) -> list[dict]:
        return self.repository.list_events(task_id)

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

        interesting = {'gate_failed', 'gate_passed', 'manual_gate', 'review', 'proposal_review', 'discussion'}
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
            'changed_files': int(summary.get('changed_files') or 0),
            'copied_files': int(summary.get('copied_files') or 0),
            'deleted_files': int(summary.get('deleted_files') or 0),
            'snapshot_path': str(summary.get('snapshot_path') or '').strip() or None,
            'changelog_path': str(summary.get('changelog_path') or '').strip() or None,
            'merged_at': str(summary.get('merged_at') or '').strip() or None,
        }

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

        if int(row.get('self_loop_mode', 0)) == 0 and str(row.get('last_gate_reason') or '') != 'author_approved':
            row = self.repository.update_task_status(
                task_id,
                status=TaskStatus.RUNNING.value,
                reason=None,
                rounds_completed=row.get('rounds_completed', 0),
            )
            self.repository.append_event(task_id, event_type='task_running', payload={'status': 'running'}, round_number=None)
            self.artifact_store.update_state(task_id, {'status': 'running'})
            return self._prepare_author_confirmation(task_id, row)

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

        row = self.repository.update_task_status(task_id, status=TaskStatus.RUNNING.value, reason=None, rounds_completed=row.get('rounds_completed', 0))
        self.repository.append_event(task_id, event_type='task_running', payload={'status': 'running'}, round_number=None)
        self.artifact_store.update_state(task_id, {'status': 'running'})
        set_task_context(task_id=task_id)
        _log.info('task_started task_id=%s', task_id)

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
            if event_type in {'discussion', 'implementation', 'review'} and content:
                role = event_type
                participant = event.get('participant') or event.get('provider') or role
                self.artifact_store.append_discussion(
                    task_id,
                    role=f'{role}:{participant}',
                    round_number=max(round_no, 1),
                    content=content,
                )

        def should_cancel() -> bool:
            return self.repository.is_cancel_requested(task_id)

        try:
            author = parse_participant_id(row['author_participant'])
            reviewers = [parse_participant_id(v) for v in row['reviewer_participants']]
            workspace_root = Path(str(row.get('workspace_path') or Path.cwd()))
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
                    claude_team_agents=bool(row.get('claude_team_agents', False)),
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

    def submit_author_decision(self, task_id: str, *, approve: bool, note: str | None = None) -> TaskView:
        row = self.repository.get_task(task_id)
        if row is None:
            raise KeyError(task_id)
        if row['status'] in {TaskStatus.PASSED.value, TaskStatus.CANCELED.value}:
            return self._to_view(row)
        if row['status'] != TaskStatus.WAITING_MANUAL.value:
            return self._to_view(row)

        note_text = str(note or '').strip() or None
        payload = {
            'decision': 'approved' if approve else 'rejected',
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
        if approve:
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

    def _prepare_author_confirmation(self, task_id: str, row: dict) -> TaskView:
        summary = (
            f"Task: {str(row.get('title') or '')}\n"
            "Generated proposal requires author approval before implementation."
        )
        review_payload: list[dict] = []

        try:
            runner = getattr(self.workflow_engine, 'runner', None)
            timeout = int(getattr(self.workflow_engine, 'participant_timeout_seconds', 240))
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
                claude_team_agents=bool(row.get('claude_team_agents', False)),
                cwd=Path(str(row.get('workspace_path') or Path.cwd())),
                max_rounds=int(row.get('max_rounds', 3)),
                test_command=str(row.get('test_command', 'py -m pytest -q')),
                lint_command=str(row.get('lint_command', 'py -m ruff check .')),
            )

            discussion_text = str(row.get('description') or '').strip()
            if runner is not None:
                discussion = runner.run(
                    participant=author,
                    prompt=WorkflowEngine._discussion_prompt(config, 1, None),
                    cwd=config.cwd,
                    timeout_seconds=timeout,
                    model=(config.provider_models or {}).get(author.provider),
                    model_params=(config.provider_model_params or {}).get(author.provider),
                    claude_team_agents=bool(config.claude_team_agents),
                )
                discussion_text = str(discussion.output or '').strip() or discussion_text
                if discussion_text:
                    discussion_event = {
                        'type': 'discussion',
                        'round': 1,
                        'provider': author.provider,
                        'output': discussion_text,
                    }
                    self.repository.append_event(
                        task_id,
                        event_type='discussion',
                        payload=discussion_event,
                        round_number=1,
                    )
                    self.artifact_store.append_event(task_id, discussion_event)
                    self.artifact_store.append_discussion(
                        task_id,
                        role=f'discussion:{author.participant_id}',
                        round_number=1,
                        content=discussion_text,
                    )

                for reviewer in reviewers:
                    review = runner.run(
                        participant=reviewer,
                        prompt=self._proposal_review_prompt(config, discussion_text),
                        cwd=config.cwd,
                        timeout_seconds=timeout,
                        model=(config.provider_models or {}).get(reviewer.provider),
                        model_params=(config.provider_model_params or {}).get(reviewer.provider),
                        claude_team_agents=bool(config.claude_team_agents),
                    )
                    verdict = WorkflowEngine._normalize_verdict(str(getattr(review, 'verdict', '') or ''))
                    review_text = str(getattr(review, 'output', '') or '').strip()
                    payload = {
                        'type': 'proposal_review',
                        'round': 1,
                        'participant': reviewer.participant_id,
                        'verdict': verdict.value,
                        'output': review_text,
                    }
                    review_payload.append(payload)
                    self.repository.append_event(
                        task_id,
                        event_type='proposal_review',
                        payload=payload,
                        round_number=1,
                    )
                    self.artifact_store.append_event(task_id, payload)
                    if review_text:
                        self.artifact_store.append_discussion(
                            task_id,
                            role=f'proposal_review:{reviewer.participant_id}',
                            round_number=1,
                            content=review_text,
                        )

            proposal_preview = WorkflowEngine._clip_text(discussion_text, max_chars=1200).strip()
            no_blocker = sum(1 for item in review_payload if str(item.get('verdict')) == ReviewVerdict.NO_BLOCKER.value)
            blocker = sum(1 for item in review_payload if str(item.get('verdict')) == ReviewVerdict.BLOCKER.value)
            unknown = sum(1 for item in review_payload if str(item.get('verdict')) == ReviewVerdict.UNKNOWN.value)
            summary = (
                f"Task: {str(row.get('title') or '')}\n"
                f"Proposal verdicts: no_blocker={no_blocker}, blocker={blocker}, unknown={unknown}\n"
                f"Proposal:\n{proposal_preview}"
            )
        except Exception as exc:
            return self.mark_failed_system(task_id, reason=f'proposal_error: {exc}')

        waiting = self.repository.update_task_status(
            task_id,
            status=TaskStatus.WAITING_MANUAL.value,
            reason='author_confirmation_required',
            rounds_completed=row.get('rounds_completed', 0),
        )
        pending_payload = {
            'summary': summary,
            'self_loop_mode': int(row.get('self_loop_mode', 0)),
            'review_payload': review_payload,
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
            raise ValueError(f'evolve_until must be ISO/local datetime, got: {text}') from exc
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
                raise ValueError(f'invalid conversation_language: {text}')
            return 'en'
        return normalized

    @staticmethod
    def _normalize_provider_models(value: dict[str, str] | None) -> dict[str, str]:
        if not value:
            return {}
        if not isinstance(value, dict):
            raise ValueError('provider_models must be an object')

        out: dict[str, str] = {}
        for raw_provider, raw_model in value.items():
            provider = str(raw_provider or '').strip().lower()
            model = str(raw_model or '').strip()
            if provider not in SUPPORTED_PROVIDERS:
                raise ValueError(f'invalid provider_models key: {provider}')
            if not model:
                raise ValueError(f'provider_models[{provider}] cannot be empty')
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
            raise ValueError('provider_model_params must be an object')

        out: dict[str, str] = {}
        for raw_provider, raw_params in value.items():
            provider = str(raw_provider or '').strip().lower()
            params = str(raw_params or '').strip()
            if provider not in SUPPORTED_PROVIDERS:
                raise ValueError(f'invalid provider_model_params key: {provider}')
            if not params:
                raise ValueError(f'provider_model_params[{provider}] cannot be empty')
            out[provider] = params
        return out

    @staticmethod
    def _default_sandbox_path(project_root: Path) -> str:
        parent = project_root.parent
        root = parent / f'{project_root.name}-lab'
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        suffix = uuid4().hex[:6]
        return str(root / f'{stamp}-{suffix}')

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
        return False

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
    def _proposal_review_prompt(config: RunConfig, discussion_output: str) -> str:
        clipped = WorkflowEngine._clip_text(discussion_output, max_chars=2500)
        language_instruction = WorkflowEngine._conversation_language_instruction(config.conversation_language)
        return (
            f"Task: {config.title}\n"
            "You are reviewing a proposed implementation plan before code changes.\n"
            "Mark BLOCKER only for correctness, regression, security, or data-loss risks.\n"
            f"{language_instruction}\n"
            "Output one line: VERDICT: NO_BLOCKER or VERDICT: BLOCKER or VERDICT: UNKNOWN.\n"
            "Then provide concise rationale and critical risks.\n"
            f"Plan:\n{clipped}\n"
        )

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
            claude_team_agents=bool(row.get('claude_team_agents', False)),
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
