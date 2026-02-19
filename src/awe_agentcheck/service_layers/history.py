from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class HistoryDeps:
    normalize_project_path_key: Callable[[object], str]
    build_project_history_item: Callable[..., dict | None]
    read_git_state: Callable[[Path | None], dict]
    collect_task_artifacts: Callable[..., list[dict]]
    clip_snippet: Callable[..., str]


class HistoryService:
    def __init__(
        self,
        *,
        repository,
        artifact_store,
        deps: HistoryDeps,
    ):
        self.repository = repository
        self.artifact_store = artifact_store
        self._normalize_project_path_key = deps.normalize_project_path_key
        self._build_project_history_item = deps.build_project_history_item
        self._read_git_state = deps.read_git_state
        self._collect_task_artifacts = deps.collect_task_artifacts
        self._clip_snippet = deps.clip_snippet

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


