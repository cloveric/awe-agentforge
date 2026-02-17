from __future__ import annotations

from datetime import datetime, timezone
import json
from typing import Protocol


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TaskRepository(Protocol):
    def create_task(
        self,
        *,
        title: str,
        description: str,
        author_participant: str,
        reviewer_participants: list[str],
        evolution_level: int,
        evolve_until: str | None,
        auto_merge: bool,
        merge_target_path: str | None,
        sandbox_mode: bool,
        sandbox_workspace_path: str | None,
        sandbox_generated: bool,
        sandbox_cleanup_on_pass: bool,
        project_path: str,
        self_loop_mode: int,
        workspace_path: str,
        max_rounds: int,
        test_command: str,
        lint_command: str,
    ) -> dict:
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
        event_type: str,
        payload: dict,
        round_number: int | None = None,
    ) -> dict:
        ...

    def list_events(self, task_id: str) -> list[dict]:
        ...


class InMemoryTaskRepository:
    def __init__(self):
        self.items: dict[str, dict] = {}
        self.events: dict[str, list[dict]] = {}

    def create_task(
        self,
        *,
        title: str,
        description: str,
        author_participant: str,
        reviewer_participants: list[str],
        evolution_level: int,
        evolve_until: str | None,
        auto_merge: bool,
        merge_target_path: str | None,
        sandbox_mode: bool,
        sandbox_workspace_path: str | None,
        sandbox_generated: bool,
        sandbox_cleanup_on_pass: bool,
        project_path: str,
        self_loop_mode: int,
        workspace_path: str,
        max_rounds: int,
        test_command: str,
        lint_command: str,
    ) -> dict:
        task_id = f'task-{len(self.items) + 1}'
        row = {
            'task_id': task_id,
            'title': title,
            'description': description,
            'author_participant': author_participant,
            'reviewer_participants': reviewer_participants,
            'evolution_level': int(max(0, min(2, int(evolution_level)))),
            'evolve_until': (str(evolve_until).strip() if evolve_until else None),
            'auto_merge': bool(auto_merge),
            'merge_target_path': (str(merge_target_path).strip() if merge_target_path else None),
            'sandbox_mode': bool(sandbox_mode),
            'sandbox_workspace_path': (str(sandbox_workspace_path).strip() if sandbox_workspace_path else None),
            'sandbox_generated': bool(sandbox_generated),
            'sandbox_cleanup_on_pass': bool(sandbox_cleanup_on_pass),
            'project_path': str(project_path).strip() or workspace_path,
            'self_loop_mode': int(max(0, min(1, int(self_loop_mode)))),
            'workspace_path': workspace_path,
            'status': 'queued',
            'last_gate_reason': None,
            'max_rounds': int(max_rounds),
            'test_command': test_command,
            'lint_command': lint_command,
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
        event_type: str,
        payload: dict,
        round_number: int | None = None,
    ) -> dict:
        if task_id not in self.items:
            raise KeyError(task_id)
        event = {
            'seq': len(self.events[task_id]) + 1,
            'task_id': task_id,
            'type': event_type,
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


def encode_reviewer_meta(
    reviewer_participants: list[str],
    evolution_level: int,
    evolve_until: str | None,
) -> str:
    return encode_task_meta(
        reviewer_participants=reviewer_participants,
        evolution_level=evolution_level,
        evolve_until=evolve_until,
        auto_merge=True,
        merge_target_path=None,
        sandbox_mode=False,
        sandbox_workspace_path=None,
        sandbox_generated=False,
        sandbox_cleanup_on_pass=False,
        project_path='.',
        self_loop_mode=1,
    )


def encode_task_meta(
    *,
    reviewer_participants: list[str],
    evolution_level: int,
    evolve_until: str | None,
    auto_merge: bool,
    merge_target_path: str | None,
    sandbox_mode: bool,
    sandbox_workspace_path: str | None,
    sandbox_generated: bool,
    sandbox_cleanup_on_pass: bool,
    project_path: str,
    self_loop_mode: int,
) -> str:
    payload = {
        'participants': [str(v) for v in reviewer_participants],
        'evolution_level': int(max(0, min(2, int(evolution_level)))),
        'evolve_until': (str(evolve_until).strip() if evolve_until else None),
        'auto_merge': bool(auto_merge),
        'merge_target_path': (str(merge_target_path).strip() if merge_target_path else None),
        'sandbox_mode': bool(sandbox_mode),
        'sandbox_workspace_path': (str(sandbox_workspace_path).strip() if sandbox_workspace_path else None),
        'sandbox_generated': bool(sandbox_generated),
        'sandbox_cleanup_on_pass': bool(sandbox_cleanup_on_pass),
        'project_path': (str(project_path).strip() if project_path else '.'),
        'self_loop_mode': int(max(0, min(1, int(self_loop_mode)))),
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
        'auto_merge': True,
        'merge_target_path': None,
        'sandbox_mode': False,
        'sandbox_workspace_path': None,
        'sandbox_generated': False,
        'sandbox_cleanup_on_pass': False,
        'project_path': '.',
        'self_loop_mode': 1,
    }
    try:
        parsed = json.loads(raw)
    except Exception:
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
        except Exception:
            level_int = 0
        level_int = max(0, min(2, level_int))
        evolve_until = parsed.get('evolve_until')
        evolve_until_text = (str(evolve_until).strip() if evolve_until else None)
        auto_merge = bool(parsed.get('auto_merge', True))
        merge_target_path = parsed.get('merge_target_path')
        merge_target_text = (str(merge_target_path).strip() if merge_target_path else None)
        sandbox_mode = bool(parsed.get('sandbox_mode', False))
        sandbox_workspace_path = parsed.get('sandbox_workspace_path')
        sandbox_workspace_text = (str(sandbox_workspace_path).strip() if sandbox_workspace_path else None)
        sandbox_generated = bool(parsed.get('sandbox_generated', False))
        sandbox_cleanup_on_pass = bool(parsed.get('sandbox_cleanup_on_pass', False))
        project_path = parsed.get('project_path')
        project_path_text = (str(project_path).strip() if project_path else '.')
        self_loop_mode = parsed.get('self_loop_mode', 1)
        try:
            self_loop_mode_int = int(self_loop_mode)
        except Exception:
            self_loop_mode_int = 1
        self_loop_mode_int = max(0, min(1, self_loop_mode_int))
        out = dict(default)
        out['participants'] = [str(v) for v in participants]
        out['evolution_level'] = level_int
        out['evolve_until'] = evolve_until_text
        out['auto_merge'] = auto_merge
        out['merge_target_path'] = merge_target_text
        out['sandbox_mode'] = sandbox_mode
        out['sandbox_workspace_path'] = sandbox_workspace_text
        out['sandbox_generated'] = sandbox_generated
        out['sandbox_cleanup_on_pass'] = sandbox_cleanup_on_pass
        out['project_path'] = project_path_text
        out['self_loop_mode'] = self_loop_mode_int
        return out
    return dict(default)
