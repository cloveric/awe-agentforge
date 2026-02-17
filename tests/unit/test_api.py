from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from awe_agentcheck.api import create_app
from awe_agentcheck.repository import InMemoryTaskRepository
from awe_agentcheck.service import OrchestratorService
from awe_agentcheck.storage.artifacts import ArtifactStore
from awe_agentcheck.workflow import RunResult


class FakeWorkflowEngine:
    def run(self, config, *, on_event, should_cancel):
        on_event({'type': 'discussion', 'round': 1, 'provider': config.author.provider, 'output': 'plan'})
        on_event({'type': 'implementation', 'round': 1, 'provider': config.author.provider, 'output': 'impl'})
        on_event({'type': 'review', 'round': 1, 'participant': config.reviewers[0].participant_id, 'verdict': 'no_blocker', 'output': 'ok'})
        on_event({'type': 'gate_passed', 'round': 1, 'reason': 'passed'})
        return RunResult(status='passed', rounds=1, gate_reason='passed')


def build_client(tmp_path: Path) -> TestClient:
    service = OrchestratorService(
        repository=InMemoryTaskRepository(),
        artifact_store=ArtifactStore(tmp_path / '.agents'),
        workflow_engine=FakeWorkflowEngine(),
    )
    app = create_app(service=service)
    return TestClient(app)


def test_api_create_start_and_get_task_roundtrip(tmp_path: Path):
    client = build_client(tmp_path)

    created = client.post(
        '/api/tasks',
        json={
            'title': 'Task One',
            'description': 'End to end run',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'auto_start': False,
        },
    )
    assert created.status_code == 201
    body = created.json()
    assert body['status'] == 'queued'
    assert body['sandbox_mode'] is True
    assert body['sandbox_generated'] is True
    assert body['sandbox_cleanup_on_pass'] is True
    assert body['self_loop_mode'] == 0
    assert '-lab' in body['workspace_path']
    assert body['project_path']
    assert body['merge_target_path'] == body['project_path']
    assert body['evolution_level'] == 0
    assert body['evolve_until'] is None
    assert body['auto_merge'] is True
    assert body['sandbox_workspace_path']

    started = client.post(f"/api/tasks/{body['task_id']}/start", json={'background': False})
    assert started.status_code == 200
    started_body = started.json()
    assert started_body['status'] == 'waiting_manual'

    approved = client.post(
        f"/api/tasks/{body['task_id']}/author-decision",
        json={'approve': True, 'note': 'ok', 'auto_start': False},
    )
    assert approved.status_code == 200
    assert approved.json()['status'] == 'queued'

    started_again = client.post(f"/api/tasks/{body['task_id']}/start", json={'background': False})
    assert started_again.status_code == 200
    assert started_again.json()['status'] == 'passed'
    assert not Path(body['workspace_path']).exists()

    fetched = client.get(f"/api/tasks/{body['task_id']}")
    assert fetched.status_code == 200
    fetched_body = fetched.json()
    assert fetched_body['task_id'] == body['task_id']
    assert fetched_body['status'] == 'passed'


def test_api_events_and_cancel(tmp_path: Path):
    client = build_client(tmp_path)

    created = client.post(
        '/api/tasks',
        json={
            'title': 'Task Two',
            'description': 'Run cancel path',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'sandbox_mode': False,
            'self_loop_mode': 1,
            'auto_start': False,
        },
    )
    task_id = created.json()['task_id']

    started = client.post(f"/api/tasks/{task_id}/start", json={'background': False})
    assert started.status_code == 200

    events = client.get(f"/api/tasks/{task_id}/events")
    assert events.status_code == 200
    rows = events.json()
    assert len(rows) >= 3

    canceled = client.post(f"/api/tasks/{task_id}/cancel")
    assert canceled.status_code == 200
    assert canceled.json()['cancel_requested'] is True

    stats = client.get('/api/stats')
    assert stats.status_code == 200
    body = stats.json()
    assert body['total_tasks'] >= 1
    assert isinstance(body['status_counts'], dict)
    assert isinstance(body['reason_bucket_counts'], dict)
    assert isinstance(body['provider_error_counts'], dict)
    assert isinstance(body['pass_rate_50'], float)
    assert isinstance(body['failed_gate_rate_50'], float)
    assert isinstance(body['failed_system_rate_50'], float)


def test_api_force_fail_marks_task_failed_system(tmp_path: Path):
    client = build_client(tmp_path)

    created = client.post(
        '/api/tasks',
        json={
            'title': 'Task Force Fail',
            'description': 'trigger manual fail',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'auto_start': False,
        },
    )
    task_id = created.json()['task_id']

    failed = client.post(
        f'/api/tasks/{task_id}/force-fail',
        json={'reason': 'watchdog_timeout: task exceeded 1800s'},
    )
    assert failed.status_code == 200
    body = failed.json()
    assert body['status'] == 'failed_system'
    assert 'watchdog_timeout' in (body['last_gate_reason'] or '')


def test_api_index_serves_monitor_layout(tmp_path: Path):
    client = build_client(tmp_path)
    resp = client.get('/')
    assert resp.status_code == 200
    body = resp.text
    assert 'id="projectTree"' in body
    assert 'id="projectSelect"' in body
    assert 'id="roleList"' in body
    assert 'id="dialogue"' in body


def test_api_create_task_rejects_missing_workspace(tmp_path: Path):
    client = build_client(tmp_path)
    resp = client.post(
        '/api/tasks',
        json={
            'title': 'Task bad path',
            'description': 'should fail',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'workspace_path': str(tmp_path / 'missing-folder'),
            'auto_start': False,
        },
    )
    assert resp.status_code == 400


def test_api_create_task_rejects_invalid_merge_target_when_auto_merge_enabled(tmp_path: Path):
    client = build_client(tmp_path)
    missing = tmp_path / 'missing-merge-target'
    resp = client.post(
        '/api/tasks',
        json={
            'title': 'Task bad merge path',
            'description': 'should fail',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'auto_merge': True,
            'merge_target_path': str(missing),
            'auto_start': False,
        },
    )
    assert resp.status_code == 400


def test_api_create_task_accepts_evolution_fields(tmp_path: Path):
    client = build_client(tmp_path)
    resp = client.post(
        '/api/tasks',
        json={
            'title': 'Task evolution',
            'description': 'evolution',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'evolution_level': 2,
            'evolve_until': '2026-02-13 06:00',
            'sandbox_mode': False,
            'self_loop_mode': 1,
            'auto_merge': False,
            'merge_target_path': str(tmp_path),
            'auto_start': False,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body['evolution_level'] == 2
    assert body['evolve_until'] == '2026-02-13T06:00:00'
    assert body['sandbox_mode'] is False
    assert body['sandbox_generated'] is False
    assert body['sandbox_cleanup_on_pass'] is True
    assert body['self_loop_mode'] == 1
    assert body['auto_merge'] is False
    assert body['merge_target_path'] == str(tmp_path)


def test_api_create_task_accepts_provider_models_and_claude_team_agents(tmp_path: Path):
    client = build_client(tmp_path)
    resp = client.post(
        '/api/tasks',
        json={
            'title': 'Task models',
            'description': 'provider model routing',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B', 'gemini#review-C'],
            'conversation_language': 'zh',
            'provider_models': {
                'claude': 'claude-sonnet-4-5',
                'codex': 'gpt-5-codex',
                'gemini': 'gemini-2.5-pro',
            },
            'provider_model_params': {
                'codex': '-c model_reasoning_effort=high',
                'gemini': '--approval-mode yolo',
            },
            'claude_team_agents': True,
            'sandbox_mode': False,
            'self_loop_mode': 1,
            'auto_start': False,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body['provider_models']['claude'] == 'claude-sonnet-4-5'
    assert body['provider_models']['codex'] == 'gpt-5-codex'
    assert body['provider_models']['gemini'] == 'gemini-2.5-pro'
    assert body['provider_model_params']['codex'] == '-c model_reasoning_effort=high'
    assert body['provider_model_params']['gemini'] == '--approval-mode yolo'
    assert body['conversation_language'] == 'zh'
    assert body['claude_team_agents'] is True


def test_api_create_task_rejects_unknown_provider_model_key(tmp_path: Path):
    client = build_client(tmp_path)
    resp = client.post(
        '/api/tasks',
        json={
            'title': 'Task bad provider key',
            'description': 'provider model validation',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'provider_models': {'unknown': 'model-x'},
            'sandbox_mode': False,
            'self_loop_mode': 1,
            'auto_start': False,
        },
    )
    assert resp.status_code == 400


def test_api_create_task_rejects_unknown_provider_model_param_key(tmp_path: Path):
    client = build_client(tmp_path)
    resp = client.post(
        '/api/tasks',
        json={
            'title': 'Task bad provider param key',
            'description': 'provider model param validation',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'provider_model_params': {'unknown': '--foo bar'},
            'sandbox_mode': False,
            'self_loop_mode': 1,
            'auto_start': False,
        },
    )
    assert resp.status_code == 400


def test_api_create_task_rejects_invalid_conversation_language(tmp_path: Path):
    client = build_client(tmp_path)
    resp = client.post(
        '/api/tasks',
        json={
            'title': 'Task bad conversation language',
            'description': 'language validation',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'conversation_language': 'jp',
            'sandbox_mode': False,
            'self_loop_mode': 1,
            'auto_start': False,
        },
    )
    assert resp.status_code == 400


def test_api_provider_models_endpoint_includes_defaults_and_observed_models(tmp_path: Path):
    client = build_client(tmp_path)
    created = client.post(
        '/api/tasks',
        json={
            'title': 'Task model catalog seed',
            'description': 'seed observed models',
            'author_participant': 'codex#author-A',
            'reviewer_participants': ['gemini#review-B'],
            'provider_models': {
                'codex': 'gpt-5.3-codex',
                'gemini': 'gemini-3-pro-preview',
            },
            'sandbox_mode': False,
            'self_loop_mode': 1,
            'auto_start': False,
        },
    )
    assert created.status_code == 201

    resp = client.get('/api/provider-models')
    assert resp.status_code == 200
    body = resp.json()
    providers = body.get('providers', {})
    assert isinstance(providers, dict)
    assert 'codex' in providers
    assert 'gemini' in providers
    assert 'claude' in providers
    assert 'claude-opus-4-6' in providers['claude']
    assert 'gpt-5.3-codex' in providers['codex']
    assert 'gemini-3-pro-preview' in providers['gemini']
    assert len(providers['claude']) >= 3


def test_api_workspace_tree_lists_children(tmp_path: Path):
    root = tmp_path / 'repo'
    root.mkdir()
    (root / 'src').mkdir()
    (root / 'src' / 'main.py').write_text('print(1)\n', encoding='utf-8')
    (root / 'README.md').write_text('# hi\n', encoding='utf-8')

    client = build_client(tmp_path)
    resp = client.get(
        '/api/workspace-tree',
        params={'workspace_path': str(root), 'max_depth': 3, 'max_entries': 100},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body['workspace_path'] == str(root)
    assert body['total_entries'] >= 2
    paths = {n['path'] for n in body['nodes']}
    assert 'src' in paths
    assert 'src/main.py' in paths


def test_api_project_history_returns_records_for_project_scope(tmp_path: Path):
    client = build_client(tmp_path)
    created = client.post(
        '/api/tasks',
        json={
            'title': 'History task',
            'description': 'history smoke',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'workspace_path': str(tmp_path),
            'sandbox_mode': False,
            'self_loop_mode': 1,
            'auto_merge': False,
            'auto_start': False,
        },
    )
    assert created.status_code == 201
    project_path = created.json()['project_path']

    resp = client.get('/api/project-history', params={'project_path': project_path, 'limit': 20})
    assert resp.status_code == 200
    body = resp.json()
    assert body['project_path'] == project_path
    assert body['total'] >= 1
    assert isinstance(body['items'], list)
    item = body['items'][0]
    assert item['task_id']
    assert item['project_path'] == project_path
    assert isinstance(item['core_findings'], list)
    assert isinstance(item['disputes'], list)
    assert isinstance(item['next_steps'], list)
    assert isinstance(item['revisions'], dict)
    assert 'auto_merge' in item['revisions']


def test_api_create_task_rejects_invalid_evolve_until(tmp_path: Path):
    client = build_client(tmp_path)
    resp = client.post(
        '/api/tasks',
        json={
            'title': 'Task bad until',
            'description': 'bad until',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'evolve_until': 'not-a-time',
            'auto_start': False,
        },
    )
    assert resp.status_code == 400


def test_api_author_decision_reject_cancels_waiting_task(tmp_path: Path):
    client = build_client(tmp_path)
    created = client.post(
        '/api/tasks',
        json={
            'title': 'Task Reject',
            'description': 'manual reject',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'workspace_path': str(tmp_path),
            'sandbox_mode': False,
            'self_loop_mode': 0,
            'auto_start': False,
        },
    )
    assert created.status_code == 201
    task_id = created.json()['task_id']

    waiting = client.post(f'/api/tasks/{task_id}/start', json={'background': False})
    assert waiting.status_code == 200
    assert waiting.json()['status'] == 'waiting_manual'

    rejected = client.post(
        f'/api/tasks/{task_id}/author-decision',
        json={'approve': False, 'note': 'reject for now'},
    )
    assert rejected.status_code == 200
    assert rejected.json()['status'] == 'canceled'
