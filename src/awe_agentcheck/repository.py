from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
from typing import Protocol
from uuid import uuid4

from awe_agentcheck.domain.events import EventType, normalize_event_type


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class TaskCreateRecord:
    title: str
    description: str
    author_participant: str
    reviewer_participants: list[str]
    evolution_level: int
    evolve_until: str | None
    conversation_language: str
    provider_models: dict[str, str]
    provider_model_params: dict[str, str]
    participant_models: dict[str, str] | None
    participant_model_params: dict[str, str] | None
    claude_team_agents: bool
    codex_multi_agents: bool
    claude_team_agents_overrides: dict[str, bool] | None
    codex_multi_agents_overrides: dict[str, bool] | None
    repair_mode: str
    plain_mode: bool
    stream_mode: bool
    debate_mode: bool
    auto_merge: bool
    merge_target_path: str | None
    sandbox_mode: bool
    sandbox_workspace_path: str | None
    sandbox_generated: bool
    sandbox_cleanup_on_pass: bool
    project_path: str
    self_loop_mode: int
    workspace_path: str
    workspace_fingerprint: dict[str, object] | None
    max_rounds: int
    test_command: str
    lint_command: str
    memory_mode: str = 'basic'
    phase_timeout_seconds: dict[str, int] | None = None


class TaskRepository(Protocol):
    def create_task_record(self, record: TaskCreateRecord) -> dict:
        ...

    def list_tasks(self, *, limit: int = 100) -> list[dict]:
        ...

    def get_task(self, task_id: str) -> dict | None:
        ...

    def update_task_status(
        self,
        task_id: str,
        *,
        status: str,
        reason: str | None,
        rounds_completed: int | None = None,
    ) -> dict:
        ...

    def set_cancel_requested(self, task_id: str, *, requested: bool) -> dict:
        ...

    def is_cancel_requested(self, task_id: str) -> bool:
        ...

    def update_task_status_if(
        self,
        task_id: str,
        *,
        expected_status: str,
        status: str,
        reason: str | None,
        rounds_completed: int | None = None,
        set_cancel_requested: bool | None = None,
    ) -> dict | None:
        """Atomically update status only if current status matches *expected_status*.

        Returns the updated row on success, or ``None`` if the current status
        did not match (i.e. a concurrent transition already happened).
        """
        ...

    def append_event(
        self,
        task_id: str,
        *,
        event_type: str | EventType,
        payload: dict,
        round_number: int | None = None,
    ) -> dict:
        ...

    def list_events(self, task_id: str) -> list[dict]:
        ...

    def delete_tasks(self, task_ids: list[str]) -> int:
        ...


class InMemoryTaskRepository:
    def __init__(self):
        self.items: dict[str, dict] = {}
        self.events: dict[str, list[dict]] = {}

    def create_task_record(self, record: TaskCreateRecord) -> dict:
        task_id = f'task-{uuid4().hex[:12]}'
        row = {
            'task_id': task_id,
            'title': record.title,
            'description': record.description,
            'author_participant': record.author_participant,
            'reviewer_participants': record.reviewer_participants,
            'evolution_level': int(max(0, min(3, int(record.evolution_level)))),
            'evolve_until': (str(record.evolve_until).strip() if record.evolve_until else None),
            'conversation_language': str(record.conversation_language or 'en').strip().lower() or 'en',
            'provider_models': {str(k).strip().lower(): str(v).strip() for k, v in (record.provider_models or {}).items() if str(k).strip() and str(v).strip()},
            'provider_model_params': {str(k).strip().lower(): str(v).strip() for k, v in (record.provider_model_params or {}).items() if str(k).strip() and str(v).strip()},
            'participant_models': {str(k).strip(): str(v).strip() for k, v in (record.participant_models or {}).items() if str(k).strip() and str(v).strip()},
            'participant_model_params': {str(k).strip(): str(v).strip() for k, v in (record.participant_model_params or {}).items() if str(k).strip() and str(v).strip()},
            'claude_team_agents': bool(record.claude_team_agents),
            'codex_multi_agents': bool(record.codex_multi_agents),
            'claude_team_agents_overrides': {str(k).strip(): bool(v) for k, v in (record.claude_team_agents_overrides or {}).items() if str(k).strip()},
            'codex_multi_agents_overrides': {str(k).strip(): bool(v) for k, v in (record.codex_multi_agents_overrides or {}).items() if str(k).strip()},
            'repair_mode': str(record.repair_mode or 'balanced').strip().lower() or 'balanced',
            'memory_mode': str(record.memory_mode or 'basic').strip().lower() or 'basic',
            'phase_timeout_seconds': {
                str(k).strip().lower(): int(v)
                for k, v in dict(record.phase_timeout_seconds or {}).items()
                if str(k).strip()
            },
            'plain_mode': bool(record.plain_mode),
            'stream_mode': bool(record.stream_mode),
            'debate_mode': bool(record.debate_mode),
            'auto_merge': bool(record.auto_merge),
            'merge_target_path': (str(record.merge_target_path).strip() if record.merge_target_path else None),
            'sandbox_mode': bool(record.sandbox_mode),
            'sandbox_workspace_path': (str(record.sandbox_workspace_path).strip() if record.sandbox_workspace_path else None),
            'sandbox_generated': bool(record.sandbox_generated),
            'sandbox_cleanup_on_pass': bool(record.sandbox_cleanup_on_pass),
            'project_path': str(record.project_path).strip() or record.workspace_path,
            'self_loop_mode': int(max(0, min(1, int(record.self_loop_mode)))),
            'workspace_path': record.workspace_path,
            'workspace_fingerprint': (
                {str(k).strip(): v for k, v in dict(record.workspace_fingerprint or {}).items() if str(k).strip()}
                if isinstance(record.workspace_fingerprint, dict)
                else {}
            ),
            'status': 'queued',
            'last_gate_reason': None,
            'max_rounds': int(record.max_rounds),
            'test_command': record.test_command,
            'lint_command': record.lint_command,
            'rounds_completed': 0,
            'cancel_requested': False,
            'created_at': _utc_now_iso(),
            'updated_at': _utc_now_iso(),
        }
        self.items[task_id] = row
        self.events[task_id] = []
        return dict(row)

    def list_tasks(self, *, limit: int = 100) -> list[dict]:
        rows = list(self.items.values())
        rows.sort(key=lambda r: r.get('created_at', ''), reverse=True)
        return [dict(r) for r in rows[:limit]]

    def get_task(self, task_id: str) -> dict | None:
        row = self.items.get(task_id)
        return dict(row) if row else None

    def update_task_status(
        self,
        task_id: str,
        *,
        status: str,
        reason: str | None,
        rounds_completed: int | None = None,
    ) -> dict:
        if task_id not in self.items:
            raise KeyError(task_id)
        self.items[task_id]['status'] = status
        self.items[task_id]['last_gate_reason'] = reason
        if rounds_completed is not None:
            self.items[task_id]['rounds_completed'] = int(rounds_completed)
        self.items[task_id]['updated_at'] = _utc_now_iso()
        return dict(self.items[task_id])

    def set_cancel_requested(self, task_id: str, *, requested: bool) -> dict:
        if task_id not in self.items:
            raise KeyError(task_id)
        self.items[task_id]['cancel_requested'] = bool(requested)
        self.items[task_id]['updated_at'] = _utc_now_iso()
        return dict(self.items[task_id])

    def update_task_status_if(
        self,
        task_id: str,
        *,
        expected_status: str,
        status: str,
        reason: str | None,
        rounds_completed: int | None = None,
        set_cancel_requested: bool | None = None,
    ) -> dict | None:
        if task_id not in self.items:
            raise KeyError(task_id)
        if self.items[task_id]['status'] != expected_status:
            return None
        self.items[task_id]['status'] = status
        self.items[task_id]['last_gate_reason'] = reason
        if rounds_completed is not None:
            self.items[task_id]['rounds_completed'] = int(rounds_completed)
        if set_cancel_requested is not None:
            self.items[task_id]['cancel_requested'] = bool(set_cancel_requested)
        self.items[task_id]['updated_at'] = _utc_now_iso()
        return dict(self.items[task_id])

    def is_cancel_requested(self, task_id: str) -> bool:
        row = self.items.get(task_id)
        if row is None:
            raise KeyError(task_id)
        return bool(row.get('cancel_requested', False))

    def append_event(
        self,
        task_id: str,
        *,
        event_type: str | EventType,
        payload: dict,
        round_number: int | None = None,
    ) -> dict:
        if task_id not in self.items:
            raise KeyError(task_id)
        event = {
            'seq': len(self.events[task_id]) + 1,
            'task_id': task_id,
            'type': normalize_event_type(event_type),
            'round': round_number,
            'payload': payload,
            'created_at': _utc_now_iso(),
        }
        self.events[task_id].append(event)
        return dict(event)

    def list_events(self, task_id: str) -> list[dict]:
        if task_id not in self.items:
            raise KeyError(task_id)
        return [dict(e) for e in self.events.get(task_id, [])]

    def delete_tasks(self, task_ids: list[str]) -> int:
        unique_ids: list[str] = []
        seen: set[str] = set()
        for raw in task_ids:
            task_id = str(raw or '').strip()
            if not task_id or task_id in seen:
                continue
            seen.add(task_id)
            unique_ids.append(task_id)
        deleted = 0
        for task_id in unique_ids:
            if task_id in self.items:
                del self.items[task_id]
                self.events.pop(task_id, None)
                deleted += 1
        return deleted


def encode_reviewer_meta(
    reviewer_participants: list[str],
    evolution_level: int,
    evolve_until: str | None,
) -> str:
    return encode_task_meta(
        reviewer_participants=reviewer_participants,
        evolution_level=evolution_level,
        evolve_until=evolve_until,
        provider_models={},
        provider_model_params={},
        participant_models={},
        participant_model_params={},
        conversation_language='en',
        claude_team_agents=False,
        codex_multi_agents=False,
        repair_mode='balanced',
        memory_mode='basic',
        phase_timeout_seconds={},
        plain_mode=True,
        stream_mode=True,
        debate_mode=True,
        auto_merge=True,
        merge_target_path=None,
        sandbox_mode=False,
        sandbox_workspace_path=None,
        sandbox_generated=False,
        sandbox_cleanup_on_pass=False,
        project_path='.',
        self_loop_mode=1,
        workspace_fingerprint=None,
    )


def encode_task_meta(
    *,
    reviewer_participants: list[str],
    evolution_level: int,
    evolve_until: str | None,
    provider_models: dict[str, str],
    provider_model_params: dict[str, str],
    participant_models: dict[str, str],
    participant_model_params: dict[str, str],
    conversation_language: str,
    claude_team_agents: bool,
    codex_multi_agents: bool,
    claude_team_agents_overrides: dict[str, bool] | None = None,
    codex_multi_agents_overrides: dict[str, bool] | None = None,
    repair_mode: str,
    plain_mode: bool,
    stream_mode: bool,
    debate_mode: bool,
    auto_merge: bool,
    merge_target_path: str | None,
    sandbox_mode: bool,
    sandbox_workspace_path: str | None,
    sandbox_generated: bool,
    sandbox_cleanup_on_pass: bool,
    project_path: str,
    self_loop_mode: int,
    workspace_fingerprint: dict[str, object] | None = None,
    memory_mode: str = 'basic',
    phase_timeout_seconds: dict[str, int] | None = None,
) -> str:
    payload = {
        'participants': [str(v) for v in reviewer_participants],
        'evolution_level': int(max(0, min(3, int(evolution_level)))),
        'evolve_until': (str(evolve_until).strip() if evolve_until else None),
        'conversation_language': str(conversation_language or 'en').strip().lower() or 'en',
        'provider_models': {str(k).strip().lower(): str(v).strip() for k, v in (provider_models or {}).items() if str(k).strip() and str(v).strip()},
        'provider_model_params': {str(k).strip().lower(): str(v).strip() for k, v in (provider_model_params or {}).items() if str(k).strip() and str(v).strip()},
        'participant_models': {str(k).strip(): str(v).strip() for k, v in (participant_models or {}).items() if str(k).strip() and str(v).strip()},
        'participant_model_params': {str(k).strip(): str(v).strip() for k, v in (participant_model_params or {}).items() if str(k).strip() and str(v).strip()},
        'claude_team_agents': bool(claude_team_agents),
        'codex_multi_agents': bool(codex_multi_agents),
        'claude_team_agents_overrides': {str(k).strip(): bool(v) for k, v in (claude_team_agents_overrides or {}).items() if str(k).strip()},
        'codex_multi_agents_overrides': {str(k).strip(): bool(v) for k, v in (codex_multi_agents_overrides or {}).items() if str(k).strip()},
        'repair_mode': str(repair_mode or 'balanced').strip().lower() or 'balanced',
        'memory_mode': str(memory_mode or 'basic').strip().lower() or 'basic',
        'phase_timeout_seconds': {
            str(k).strip().lower(): int(v)
            for k, v in dict(phase_timeout_seconds or {}).items()
            if str(k).strip() and isinstance(v, int)
        },
        'plain_mode': bool(plain_mode),
        'stream_mode': bool(stream_mode),
        'debate_mode': bool(debate_mode),
        'auto_merge': bool(auto_merge),
        'merge_target_path': (str(merge_target_path).strip() if merge_target_path else None),
        'sandbox_mode': bool(sandbox_mode),
        'sandbox_workspace_path': (str(sandbox_workspace_path).strip() if sandbox_workspace_path else None),
        'sandbox_generated': bool(sandbox_generated),
        'sandbox_cleanup_on_pass': bool(sandbox_cleanup_on_pass),
        'project_path': (str(project_path).strip() if project_path else '.'),
        'self_loop_mode': int(max(0, min(1, int(self_loop_mode)))),
        'workspace_fingerprint': (
            {str(k).strip(): v for k, v in dict(workspace_fingerprint or {}).items() if str(k).strip()}
            if isinstance(workspace_fingerprint, dict)
            else {}
        ),
    }
    return json.dumps(payload, ensure_ascii=True)


def decode_reviewer_meta(raw: str) -> tuple[list[str], int, str | None]:
    parsed = decode_task_meta(raw)
    return (
        parsed['participants'],
        parsed['evolution_level'],
        parsed['evolve_until'],
    )


def decode_task_meta(raw: str) -> dict:
    default = {
        'participants': [],
        'evolution_level': 0,
        'evolve_until': None,
        'conversation_language': 'en',
        'provider_models': {},
        'provider_model_params': {},
        'participant_models': {},
        'participant_model_params': {},
        'claude_team_agents': False,
        'codex_multi_agents': False,
        'claude_team_agents_overrides': {},
        'codex_multi_agents_overrides': {},
        'repair_mode': 'balanced',
        'memory_mode': 'basic',
        'phase_timeout_seconds': {},
        'plain_mode': True,
        'stream_mode': True,
        'debate_mode': True,
        'auto_merge': True,
        'merge_target_path': None,
        'sandbox_mode': False,
        'sandbox_workspace_path': None,
        'sandbox_generated': False,
        'sandbox_cleanup_on_pass': False,
        'project_path': '.',
        'self_loop_mode': 1,
        'workspace_fingerprint': {},
    }
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return dict(default)

    if isinstance(parsed, list):
        out = dict(default)
        out['participants'] = [str(v) for v in parsed]
        return out
    if isinstance(parsed, dict):
        participants = parsed.get('participants', [])
        if not isinstance(participants, list):
            participants = []
        level = parsed.get('evolution_level', 0)
        try:
            level_int = int(level)
        except (TypeError, ValueError):
            level_int = 0
        level_int = max(0, min(3, level_int))
        evolve_until = parsed.get('evolve_until')
        evolve_until_text = (str(evolve_until).strip() if evolve_until else None)
        conversation_language = str(parsed.get('conversation_language') or 'en').strip().lower() or 'en'
        if conversation_language not in {'en', 'zh'}:
            conversation_language = 'en'
        auto_merge = bool(parsed.get('auto_merge', True))
        provider_models = parsed.get('provider_models', {})
        if not isinstance(provider_models, dict):
            provider_models = {}
        provider_models_out: dict[str, str] = {}
        for key, raw in provider_models.items():
            provider = str(key or '').strip().lower()
            model = str(raw or '').strip()
            if provider and model:
                provider_models_out[provider] = model
        provider_model_params = parsed.get('provider_model_params', {})
        if not isinstance(provider_model_params, dict):
            provider_model_params = {}
        provider_model_params_out: dict[str, str] = {}
        for key, raw in provider_model_params.items():
            provider = str(key or '').strip().lower()
            params = str(raw or '').strip()
            if provider and params:
                provider_model_params_out[provider] = params
        participant_models = parsed.get('participant_models', {})
        if not isinstance(participant_models, dict):
            participant_models = {}
        participant_models_out: dict[str, str] = {}
        for key, raw in participant_models.items():
            participant = str(key or '').strip()
            model = str(raw or '').strip()
            if participant and model:
                participant_models_out[participant] = model
        participant_model_params = parsed.get('participant_model_params', {})
        if not isinstance(participant_model_params, dict):
            participant_model_params = {}
        participant_model_params_out: dict[str, str] = {}
        for key, raw in participant_model_params.items():
            participant = str(key or '').strip()
            params = str(raw or '').strip()
            if participant and params:
                participant_model_params_out[participant] = params
        claude_team_agents = _coerce_meta_bool(parsed.get('claude_team_agents', False), default=False)
        codex_multi_agents = _coerce_meta_bool(parsed.get('codex_multi_agents', False), default=False)
        claude_team_agents_overrides_raw = parsed.get('claude_team_agents_overrides', {})
        if not isinstance(claude_team_agents_overrides_raw, dict):
            claude_team_agents_overrides_raw = {}
        claude_team_agents_overrides: dict[str, bool] = {}
        for key, raw in claude_team_agents_overrides_raw.items():
            participant = str(key or '').strip()
            if not participant:
                continue
            claude_team_agents_overrides[participant] = _coerce_meta_bool(raw, default=False)
        codex_multi_agents_overrides_raw = parsed.get('codex_multi_agents_overrides', {})
        if not isinstance(codex_multi_agents_overrides_raw, dict):
            codex_multi_agents_overrides_raw = {}
        codex_multi_agents_overrides: dict[str, bool] = {}
        for key, raw in codex_multi_agents_overrides_raw.items():
            participant = str(key or '').strip()
            if not participant:
                continue
            codex_multi_agents_overrides[participant] = _coerce_meta_bool(raw, default=False)
        repair_mode = str(parsed.get('repair_mode') or 'balanced').strip().lower() or 'balanced'
        if repair_mode not in {'minimal', 'balanced', 'structural'}:
            repair_mode = 'balanced'
        memory_mode = str(parsed.get('memory_mode') or 'basic').strip().lower() or 'basic'
        if memory_mode not in {'off', 'basic', 'strict'}:
            memory_mode = 'basic'
        phase_timeout_seconds_raw = parsed.get('phase_timeout_seconds', {})
        if not isinstance(phase_timeout_seconds_raw, dict):
            phase_timeout_seconds_raw = {}
        phase_timeout_seconds: dict[str, int] = {}
        for key, value in phase_timeout_seconds_raw.items():
            phase = str(key or '').strip().lower()
            if phase not in {'proposal', 'discussion', 'implementation', 'review', 'command'}:
                continue
            try:
                seconds = int(value)
            except (TypeError, ValueError):
                continue
            phase_timeout_seconds[phase] = max(10, min(60_000, seconds))
        plain_mode = _coerce_meta_bool(parsed.get('plain_mode', True), default=True)
        stream_mode = _coerce_meta_bool(parsed.get('stream_mode', True), default=True)
        debate_mode = _coerce_meta_bool(parsed.get('debate_mode', True), default=True)
        merge_target_path = parsed.get('merge_target_path')
        merge_target_text = (str(merge_target_path).strip() if merge_target_path else None)
        sandbox_mode = bool(parsed.get('sandbox_mode', False))
        sandbox_workspace_path = parsed.get('sandbox_workspace_path')
        sandbox_workspace_text = (str(sandbox_workspace_path).strip() if sandbox_workspace_path else None)
        sandbox_generated = bool(parsed.get('sandbox_generated', False))
        sandbox_cleanup_on_pass = bool(parsed.get('sandbox_cleanup_on_pass', False))
        project_path = parsed.get('project_path')
        project_path_text = (str(project_path).strip() if project_path else '.')
        workspace_fingerprint_raw = parsed.get('workspace_fingerprint', {})
        if not isinstance(workspace_fingerprint_raw, dict):
            workspace_fingerprint_raw = {}
        workspace_fingerprint: dict[str, object] = {}
        for key, value in workspace_fingerprint_raw.items():
            label = str(key or '').strip()
            if not label:
                continue
            workspace_fingerprint[label] = value
        self_loop_mode = parsed.get('self_loop_mode', 1)
        try:
            self_loop_mode_int = int(self_loop_mode)
        except (TypeError, ValueError):
            self_loop_mode_int = 1
        self_loop_mode_int = max(0, min(1, self_loop_mode_int))
        out = dict(default)
        out['participants'] = [str(v) for v in participants]
        out['evolution_level'] = level_int
        out['evolve_until'] = evolve_until_text
        out['conversation_language'] = conversation_language
        out['provider_models'] = provider_models_out
        out['provider_model_params'] = provider_model_params_out
        out['participant_models'] = participant_models_out
        out['participant_model_params'] = participant_model_params_out
        out['claude_team_agents'] = claude_team_agents
        out['codex_multi_agents'] = codex_multi_agents
        out['claude_team_agents_overrides'] = claude_team_agents_overrides
        out['codex_multi_agents_overrides'] = codex_multi_agents_overrides
        out['repair_mode'] = repair_mode
        out['memory_mode'] = memory_mode
        out['phase_timeout_seconds'] = phase_timeout_seconds
        out['plain_mode'] = plain_mode
        out['stream_mode'] = stream_mode
        out['debate_mode'] = debate_mode
        out['auto_merge'] = auto_merge
        out['merge_target_path'] = merge_target_text
        out['sandbox_mode'] = sandbox_mode
        out['sandbox_workspace_path'] = sandbox_workspace_text
        out['sandbox_generated'] = sandbox_generated
        out['sandbox_cleanup_on_pass'] = sandbox_cleanup_on_pass
        out['project_path'] = project_path_text
        out['self_loop_mode'] = self_loop_mode_int
        out['workspace_fingerprint'] = workspace_fingerprint
        return out
    return dict(default)


def _coerce_meta_bool(value, *, default: bool) -> bool:
    text = str(value).strip().lower()
    if text in {'1', 'true', 'yes', 'on'}:
        return True
    if text in {'0', 'false', 'no', 'off'}:
        return False
    if text in {'', 'none', 'null'}:
        return bool(default)
    return bool(value)
