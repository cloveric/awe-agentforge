from __future__ import annotations

from datetime import datetime, timedelta
import json
from pathlib import Path
import re

import pytest

from awe_agentcheck.domain.models import TaskStatus
from awe_agentcheck.service_layers import (
    AnalyticsService,
    EvidenceDeps,
    EvidenceService,
    HistoryDeps,
    HistoryService,
    MemoryDeps,
    MemoryService,
)
from awe_agentcheck.storage.artifacts import ArtifactStore


class _AnalyticsRepo:
    def __init__(self, rows: list[dict], events_by_task: dict[str, list[dict]], *, error_task_ids: set[str] | None = None):
        self._rows = list(rows)
        self._events_by_task = dict(events_by_task)
        self._error_task_ids = set(error_task_ids or set())

    def list_tasks(self, *, limit: int = 100):
        return list(self._rows[:limit])

    def list_events(self, task_id: str):
        if task_id in self._error_task_ids:
            raise RuntimeError('boom')
        if task_id not in self._events_by_task:
            raise KeyError(task_id)
        return list(self._events_by_task[task_id])


class _HistoryRepo:
    def __init__(self, rows: list[dict]):
        self._rows = list(rows)
        self._by_id = {str(r.get('task_id')): dict(r) for r in rows}

    def list_tasks(self, *, limit: int = 100):
        return list(self._rows[:limit])

    def get_task(self, task_id: str):
        row = self._by_id.get(task_id)
        return dict(row) if row else None


class _EvidenceRepo:
    def __init__(self):
        self.events: list[tuple[str, str, dict]] = []

    def append_event(self, task_id: str, *, event_type, payload: dict, round_number=None):  # noqa: ANN001
        self.events.append((task_id, str(getattr(event_type, 'value', event_type)), dict(payload)))
        return {'task_id': task_id, 'type': str(event_type), 'payload': payload}


def _stats_factory(**kwargs):
    return kwargs


def _reason_bucket(reason: str | None) -> str | None:
    text = str(reason or '').strip()
    if not text:
        return None
    return text.split(':', 1)[0].strip()


def _parse_iso(value) -> datetime | None:  # noqa: ANN001
    text = str(value or '').strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _format_task_day(value) -> str:  # noqa: ANN001
    parsed = _parse_iso(value)
    if parsed is None:
        return 'unknown'
    return parsed.date().isoformat()


def _merge_payload(event: dict) -> dict:
    payload = dict(event.get('payload') or {})
    merged = dict(event)
    merged.update(payload)
    return merged


def test_analytics_service_stats_and_analytics_cover_paths():
    now = datetime.now()
    rows = [
        {'task_id': 't1', 'status': 'running', 'last_gate_reason': None, 'created_at': now.isoformat(), 'updated_at': now.isoformat()},
        {'task_id': 't2', 'status': 'queued', 'last_gate_reason': 'provider_limit: provider=codex', 'created_at': now.isoformat(), 'updated_at': now.isoformat()},
        {'task_id': 't3', 'status': 'passed', 'last_gate_reason': '', 'created_at': now.isoformat(), 'updated_at': (now + timedelta(seconds=8)).isoformat()},
        {'task_id': 't4', 'status': 'failed_gate', 'last_gate_reason': 'review_blocker: provider=claude', 'created_at': now.isoformat(), 'updated_at': (now + timedelta(seconds=5)).isoformat()},
        {'task_id': 't5', 'status': 'failed_system', 'last_gate_reason': 'command_timeout: provider=gemini', 'created_at': now.isoformat(), 'updated_at': 'invalid-date'},
    ]
    events = {
        't3': [
            {
                'type': 'prompt_cache_probe',
                'payload': {'prefix_reuse_eligible': True, 'prefix_reused': True},
            }
        ],
        't4': [
            {'type': 'review', 'payload': {'participant': 'codex#review-B', 'verdict': 'blocker'}},
            {'type': 'prompt_cache_break', 'payload': {'reason': 'model_changed'}},
        ],
        't5': [
            {'type': 'debate_review', 'payload': {'participant': 'claude#review-C', 'verdict': 'maybe'}},
        ],
    }
    repo = _AnalyticsRepo(rows, events, error_task_ids={'t2'})
    service = AnalyticsService(
        repository=repo,
        stats_factory=_stats_factory,
        reason_bucket_fn=_reason_bucket,
        provider_pattern=re.compile(r'provider=([a-z0-9_-]+)', re.IGNORECASE),
        parse_iso_datetime_fn=_parse_iso,
        format_task_day_fn=_format_task_day,
        merged_event_payload_fn=_merge_payload,
    )

    stats = service.get_stats()
    assert stats['total_tasks'] == 5
    assert stats['active_tasks'] == 2
    assert stats['provider_error_counts']['codex'] == 1
    assert stats['provider_error_counts']['claude'] == 1
    assert stats['provider_error_counts']['gemini'] == 1
    assert stats['recent_terminal_total'] == 3
    assert stats['prompt_prefix_reuse_rate_50'] == 1.0
    assert stats['prompt_cache_break_count_50'] == 1
    assert stats['prompt_cache_break_model_50'] == 1
    assert stats['mean_task_duration_seconds_50'] > 0

    analytics = service.get_analytics(limit=10)
    assert analytics['window_tasks'] == 5
    assert analytics['window_failed_gate'] == 1
    assert analytics['reviewer_global']['reviews'] >= 2
    assert analytics['failure_taxonomy']
    assert any(str(item.get('participant')).startswith('codex#') for item in analytics['reviewer_drift'])


def _normalize_project_path_key(value) -> str:  # noqa: ANN001
    return str(Path(str(value or '')).resolve(strict=False)).replace('\\', '/').lower()


def _build_project_history_item(*, task_id: str, row: dict | None, task_dir: Path | None) -> dict | None:
    if row is None and task_dir is None:
        return None
    project_path = str((row or {}).get('project_path') or '')
    if not project_path and task_dir is not None:
        project_path = str((task_dir / '..').resolve(strict=False))
    return {
        'task_id': task_id,
        'project_path': project_path or '.',
        'core_findings': [f'finding-{task_id}'],
        'revisions': {'auto_merge': True, 'changed_files': 2, 'copied_files': 1, 'deleted_files': 0},
        'disputes': [{'participant': 'reviewer', 'verdict': 'unknown', 'note': 'needs more evidence'}],
        'next_steps': [f'next-{task_id}'],
    }


def test_history_service_lists_and_summarizes(tmp_path: Path):
    artifacts = ArtifactStore(tmp_path)
    artifacts.create_task_workspace('task-a')
    artifacts.create_task_workspace('task-b')

    rows = [
        {
            'task_id': 'task-a',
            'title': 'A',
            'status': 'passed',
            'last_gate_reason': 'passed',
            'rounds_completed': 1,
            'max_rounds': 1,
            'project_path': str(tmp_path / 'proj-a'),
        },
        {
            'task_id': 'task-c',
            'title': 'C',
            'status': 'failed_gate',
            'last_gate_reason': 'review_blocker',
            'rounds_completed': 1,
            'max_rounds': 2,
            'project_path': str(tmp_path / 'proj-c'),
        },
    ]
    repo = _HistoryRepo(rows)
    service = HistoryService(
        repository=repo,
        artifact_store=artifacts,
        deps=HistoryDeps(
            normalize_project_path_key=_normalize_project_path_key,
            build_project_history_item=_build_project_history_item,
            read_git_state=lambda _path: {
                'is_git_repo': True,
                'branch': 'main',
                'worktree_clean': True,
                'remote_origin': 'https://github.com/cloveric/awe-agentforge.git',
            },
            collect_task_artifacts=lambda **_kwargs: [{'name': 'summary', 'path': 'summary.md'}],
            clip_snippet=lambda text, *_a, **_kw: str(text or '')[:24],
        ),
    )

    history = service.list_project_history(project_path=str(tmp_path / 'proj-a'), limit=50)
    assert history
    assert all('task_id' in item for item in history)
    assert all(_normalize_project_path_key(item.get('project_path')) == _normalize_project_path_key(tmp_path / 'proj-a') for item in history)

    summary = service.build_github_pr_summary('task-a')
    text = str(summary['summary_markdown'])
    assert 'AWE-AgentForge Task Summary | task-a' in text
    assert 'Core Findings' in text
    assert 'Task Artifacts' in text

    with pytest.raises(KeyError):
        service.build_github_pr_summary('task-missing')


def _validate_artifact_task_id(value: str) -> str:
    text = str(value or '').strip()
    if not text:
        raise ValueError('task_id required')
    return text


def _validate_evidence_bundle(*, evidence_bundle: dict, expected_round: int) -> dict[str, object]:
    ok = bool(evidence_bundle.get('ok', True))
    return {'ok': ok, 'reason': 'passed' if ok else 'precompletion_evidence_missing', 'expected_round': expected_round}


def _coerce_checks(value) -> dict[str, object]:  # noqa: ANN001
    return dict(value or {}) if isinstance(value, dict) else {}


def _coerce_paths(value) -> list[str]:  # noqa: ANN001
    if isinstance(value, list):
        return [str(v) for v in value if str(v).strip()]
    return []


def test_evidence_service_collects_manifest_and_regression(tmp_path: Path, monkeypatch):
    repo = _EvidenceRepo()
    artifacts = ArtifactStore(tmp_path)
    service = EvidenceService(
        repository=repo,
        artifact_store=artifacts,
        deps=EvidenceDeps(
            validate_artifact_task_id=_validate_artifact_task_id,
            validate_evidence_bundle=_validate_evidence_bundle,
            coerce_evidence_checks=_coerce_checks,
            coerce_evidence_paths=_coerce_paths,
        ),
    )

    ws = artifacts.create_task_workspace('task-1')
    ws.summary_md.write_text('# Summary\nok\n', encoding='utf-8')
    evidence_artifact = ws.artifacts_dir / 'evidence_bundle_round_1.json'
    evidence_artifact.write_text(json.dumps({'ok': True}), encoding='utf-8')

    collected = service.collect_task_artifacts(task_id='task-1')
    names = {item['name'] for item in collected}
    assert 'summary' in names
    assert 'evidence_bundle_round_1' in names

    row = {
        'project_path': str(tmp_path),
        'workspace_path': str(tmp_path),
        'test_command': 'py -m pytest -q',
        'lint_command': 'py -m ruff check .',
        'title': 'Fix bug',
        'description': 'desc',
    }
    manifest = service.write_evidence_manifest(
        task_id='task-1',
        row=row,
        workspace_root=tmp_path,
        rounds_completed=1,
        status='passed',
        reason='passed',
        preflight_guard={'ok': True},
        evidence_bundle={'ok': True, 'checks': {'tests_ok': True}, 'evidence_paths': ['src/a.py']},
        head_snapshot={'sha': 'abc'},
    )
    assert manifest['ok'] is True
    assert manifest['reason'] == 'passed'
    assert Path(str(manifest['artifact_path'])).exists()

    original_write_artifact_json = artifacts.write_artifact_json
    monkeypatch.setattr(
        artifacts,
        'write_artifact_json',
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError('disk full')),
    )
    failed_manifest = service.write_evidence_manifest(
        task_id='task-1',
        row=row,
        workspace_root=tmp_path,
        rounds_completed=1,
        status='failed_gate',
        reason='tests_failed',
        preflight_guard=None,
        evidence_bundle={'ok': False},
        head_snapshot=None,
    )
    assert failed_manifest['ok'] is False
    assert failed_manifest['gate_reason'] == 'precompletion_evidence_missing'
    monkeypatch.setattr(artifacts, 'write_artifact_json', original_write_artifact_json)

    assert service.emit_regression_case(
        task_id='task-1',
        row=row,
        status=TaskStatus.PASSED,
        reason='',
    ) is None

    regression = service.emit_regression_case(
        task_id='task-1',
        row=row,
        status=TaskStatus.FAILED_GATE,
        reason='review_blocker',
    )
    assert regression is not None
    assert any(item[1] == 'regression_case_recorded' for item in repo.events)
    regression_file = Path(tmp_path) / '.agents' / 'regressions' / 'failure_tasks.json'
    assert regression_file.exists()

    merged = service.emit_regression_case(
        task_id='task-1',
        row=row,
        status=TaskStatus.FAILED_GATE,
        reason='review_blocker',
    )
    assert merged is not None
    assert bool(merged['merged']) is True


def test_memory_service_persists_queries_and_clears(tmp_path: Path):
    events_by_task = {
        'task-1': [
            {
                'type': 'review',
                'payload': {
                    'output': 'Issue in src/awe_agentcheck/service.py and tests/unit/test_service.py',
                },
            },
            {
                'type': 'gate_failed',
                'payload': {'reason': 'review_blocker'},
            },
        ]
    }

    def _list_events(task_id: str) -> list[dict]:
        return list(events_by_task.get(task_id, []))

    def _read_artifact_json(task_id: str, name: str) -> dict | None:
        _ = (task_id, name)
        return None

    service = MemoryService(
        artifact_root=tmp_path / '.agents',
        deps=MemoryDeps(
            list_events=_list_events,
            read_artifact_json=_read_artifact_json,
        ),
    )

    row = {
        'task_id': 'task-1',
        'title': 'Audit service',
        'description': 'find critical issues',
        'project_path': str(tmp_path / 'repo'),
        'workspace_path': str(tmp_path / 'repo'),
        'repair_mode': 'balanced',
        'evolution_level': 1,
        'self_loop_mode': 1,
        'debate_mode': True,
        'auto_merge': True,
        'max_rounds': 2,
        'memory_mode': 'basic',
        'phase_timeout_seconds': {'review': 180},
    }

    pref = service.persist_task_preferences(row=row)
    assert pref is not None
    assert pref['memory_type'] == 'preference'

    outcomes = service.persist_task_outcome(
        task_id='task-1',
        row=row,
        status='failed_gate',
        reason='review_blocker',
    )
    assert outcomes
    assert any(item['memory_type'] == 'failure' for item in outcomes)

    contexts = service.build_stage_context(
        row=row,
        query_text='audit service reviewer blocker',
        memory_mode='basic',
    )
    assert contexts['mode'] == 'basic'
    assert isinstance(contexts['contexts'], dict)

    queried = service.query_entries(
        query='review blocker service.py',
        memory_mode='basic',
        project_path=row['project_path'],
        stage='review',
        limit=5,
    )
    assert queried
    first_id = str(queried[0]['memory_id'])
    pinned = service.set_pinned(memory_id=first_id, pinned=True)
    assert pinned is not None
    assert pinned['pinned'] is True

    clear_res = service.clear_entries(project_path=row['project_path'], include_pinned=False)
    assert clear_res['remaining'] >= 1
    clear_all = service.clear_entries(project_path=row['project_path'], include_pinned=True)
    assert clear_all['remaining'] == 0
