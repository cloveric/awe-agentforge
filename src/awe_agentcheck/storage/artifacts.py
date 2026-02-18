from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class TaskWorkspace:
    root: Path
    discussion_md: Path
    summary_md: Path
    final_report_md: Path
    state_json: Path
    decisions_json: Path
    events_jsonl: Path
    artifacts_dir: Path


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def create_task_workspace(self, task_id: str) -> TaskWorkspace:
        task_id_text, task_root = self._resolve_task_root(task_id)
        artifacts_dir = task_root / 'artifacts'
        artifacts_dir.mkdir(parents=True, exist_ok=True)

        discussion_md = task_root / 'discussion.md'
        summary_md = task_root / 'summary.md'
        final_report_md = task_root / 'final_report.md'
        state_json = task_root / 'state.json'
        decisions_json = task_root / 'decisions.json'
        events_jsonl = task_root / 'events.jsonl'

        self._ensure_text(discussion_md, '# Discussion\n\n')
        self._ensure_text(summary_md, '# Summary\n\n')
        self._ensure_text(final_report_md, '# Final Report\n\n')
        self._ensure_json(
            state_json,
            {
                'task_id': task_id_text,
                'status': 'queued',
                'rounds_completed': 0,
                'updated_at': self._utc_now_iso(),
            },
        )
        self._ensure_json(decisions_json, {'decisions': []})
        self._ensure_text(events_jsonl, '')

        return TaskWorkspace(
            root=task_root,
            discussion_md=discussion_md,
            summary_md=summary_md,
            final_report_md=final_report_md,
            state_json=state_json,
            decisions_json=decisions_json,
            events_jsonl=events_jsonl,
            artifacts_dir=artifacts_dir,
        )

    def append_event(self, task_id: str, event: dict) -> None:
        ws = self.create_task_workspace(task_id)
        line = json.dumps(event, ensure_ascii=True)
        with ws.events_jsonl.open('a', encoding='utf-8') as f:
            f.write(line + '\n')

    def append_discussion(self, task_id: str, *, role: str, round_number: int, content: str) -> None:
        ws = self.create_task_workspace(task_id)
        stamp = datetime.now(timezone.utc).isoformat()
        block = (
            f"## Round {round_number} - {role} ({stamp})\n\n"
            f"{(content or '').strip()}\n\n"
        )
        with ws.discussion_md.open('a', encoding='utf-8') as f:
            f.write(block)

    def write_summary(self, task_id: str, content: str) -> None:
        ws = self.create_task_workspace(task_id)
        ws.summary_md.write_text('# Summary\n\n' + (content or '').strip() + '\n', encoding='utf-8')

    def write_final_report(self, task_id: str, content: str) -> None:
        ws = self.create_task_workspace(task_id)
        ws.final_report_md.write_text('# Final Report\n\n' + (content or '').strip() + '\n', encoding='utf-8')

    def update_state(self, task_id: str, state_update: dict) -> None:
        ws = self.create_task_workspace(task_id)
        current = {}
        if ws.state_json.exists():
            try:
                current = json.loads(ws.state_json.read_text(encoding='utf-8'))
            except Exception:
                current = {}
        merged = dict(current)
        merged.update(state_update)
        merged['updated_at'] = self._utc_now_iso()
        ws.state_json.write_text(json.dumps(merged, ensure_ascii=True, indent=2), encoding='utf-8')

    def write_artifact_json(self, task_id: str, *, name: str, payload: dict) -> Path:
        ws = self.create_task_workspace(task_id)
        safe_name = str(name or "").strip()
        if not safe_name:
            raise ValueError("artifact name is required")
        safe_name = safe_name.replace("\\", "_").replace("/", "_")
        if not safe_name.endswith(".json"):
            safe_name += ".json"
        path = ws.artifacts_dir / safe_name
        path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        return path

    def remove_task_workspace(self, task_id: str) -> bool:
        _, task_root = self._resolve_task_root(task_id)
        if not task_root.exists() or not task_root.is_dir():
            return False
        shutil.rmtree(task_root, ignore_errors=False)
        return True

    def _resolve_task_root(self, task_id: str) -> tuple[str, Path]:
        task_id_text = str(task_id or '').strip()
        if not task_id_text:
            raise ValueError('task_id is required')

        threads_root = (self.root / 'threads').resolve()
        task_root = (threads_root / task_id_text).resolve(strict=False)
        try:
            task_root.relative_to(threads_root)
        except ValueError as exc:
            raise ValueError('invalid task_id') from exc
        return task_id_text, task_root

    @staticmethod
    def _ensure_text(path: Path, content: str) -> None:
        if not path.exists():
            path.write_text(content, encoding='utf-8')

    @staticmethod
    def _ensure_json(path: Path, payload: dict) -> None:
        if not path.exists():
            path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding='utf-8')

    @staticmethod
    def _utc_now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()
