from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
from typing import Callable

from awe_agentcheck.domain.models import TaskStatus


_TERMINAL_STATUSES = {
    TaskStatus.PASSED.value,
    TaskStatus.FAILED_GATE.value,
    TaskStatus.FAILED_SYSTEM.value,
    TaskStatus.CANCELED.value,
}


class AnalyticsService:
    def __init__(
        self,
        *,
        repository,
        stats_factory: Callable[..., object],
        reason_bucket_fn: Callable[[str | None], str | None],
        provider_pattern: re.Pattern,
        parse_iso_datetime_fn: Callable[[object], datetime | None],
        format_task_day_fn: Callable[[object], str],
        merged_event_payload_fn: Callable[[dict], dict],
    ):
        self.repository = repository
        self._stats_factory = stats_factory
        self._reason_bucket = reason_bucket_fn
        self._provider_pattern = provider_pattern
        self._parse_iso_datetime = parse_iso_datetime_fn
        self._format_task_day = format_task_day_fn
        self._merged_event_payload = merged_event_payload_fn

    def get_stats(self):
        rows = self.repository.list_tasks(limit=10_000)
        counts: dict[str, int] = {}
        reason_bucket_counts: dict[str, int] = {}
        provider_error_counts: dict[str, int] = {}
        for row in rows:
            status = str(row.get('status', 'unknown'))
            counts[status] = counts.get(status, 0) + 1

            reason = row.get('last_gate_reason')
            bucket = self._reason_bucket(reason)
            if bucket:
                reason_bucket_counts[bucket] = reason_bucket_counts.get(bucket, 0) + 1

            provider_match = self._provider_pattern.search(str(reason or ''))
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

        return self._stats_factory(
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

    def get_analytics(self, *, limit: int = 300) -> dict:
        rows = self.repository.list_tasks(limit=max(1, min(2000, int(limit))))
        failures = [row for row in rows if str(row.get('status') or '') == TaskStatus.FAILED_GATE.value]
        total_failures = len(failures)

        failure_taxonomy: dict[str, int] = {}
        trend_by_day: dict[str, dict[str, int]] = {}
        for row in failures:
            reason = str(row.get('last_gate_reason') or '')
            bucket = self._reason_bucket(reason) or 'other'
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


class HistoryService:
    def __init__(
        self,
        *,
        repository,
        artifact_store,
        normalize_project_path_key_fn: Callable[[object], str],
        build_project_history_item_fn: Callable[..., dict | None],
        read_git_state_fn: Callable[[Path | None], dict],
        collect_task_artifacts_fn: Callable[..., list[dict]],
        clip_snippet_fn: Callable[..., str],
    ):
        self.repository = repository
        self.artifact_store = artifact_store
        self._normalize_project_path_key = normalize_project_path_key_fn
        self._build_project_history_item = build_project_history_item_fn
        self._read_git_state = read_git_state_fn
        self._collect_task_artifacts = collect_task_artifacts_fn
        self._clip_snippet = clip_snippet_fn

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


@dataclass(frozen=True)
class TaskManagementService:
    create_task_fn: Callable[[object], object]
    list_tasks_fn: Callable[..., object]
    get_task_fn: Callable[[str], object]

    def create_task(self, payload):
        return self.create_task_fn(payload)

    def list_tasks(self, *, limit: int = 100):
        return self.list_tasks_fn(limit=limit)

    def get_task(self, task_id: str):
        return self.get_task_fn(task_id)

