from __future__ import annotations

import logging
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

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


class FakeWorkflowEngineTwoRoundsWithChanges:
    def run(self, config, *, on_event, should_cancel):
        target = config.cwd / 'src' / 'round.txt'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('round-1\n', encoding='utf-8')
        on_event({'type': 'discussion', 'round': 1, 'provider': config.author.provider, 'output': 'plan-1'})
        on_event({'type': 'implementation', 'round': 1, 'provider': config.author.provider, 'output': 'impl-1'})
        on_event({'type': 'gate_failed', 'round': 1, 'reason': 'tests_failed'})

        target.write_text('round-2\n', encoding='utf-8')
        on_event({'type': 'discussion', 'round': 2, 'provider': config.author.provider, 'output': 'plan-2'})
        on_event({'type': 'implementation', 'round': 2, 'provider': config.author.provider, 'output': 'impl-2'})
        on_event({'type': 'gate_passed', 'round': 2, 'reason': 'passed'})
        return RunResult(status='passed', rounds=2, gate_reason='passed')


def build_client(
    tmp_path: Path,
    *,
    api_access_token: str | None = None,
    allow_remote_api: bool | None = None,
    workflow_engine=None,
    client: tuple[str, int] = ('testclient', 50000),
) -> TestClient:
    service = OrchestratorService(
        repository=InMemoryTaskRepository(),
        artifact_store=ArtifactStore(tmp_path / '.agents'),
        workflow_engine=workflow_engine or FakeWorkflowEngine(),
    )
    app = create_app(
        service=service,
        workspace_tree_safe_root=tmp_path,
        allow_remote_api=allow_remote_api,
        api_access_token=api_access_token,
    )
    return TestClient(app, client=client)


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
    assert body['plain_mode'] is True
    assert body['stream_mode'] is True
    assert body['debate_mode'] is True
    assert body['repair_mode'] == 'balanced'
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
    assert isinstance(rows[0].get('created_at'), str)
    assert ('+' in rows[0]['created_at']) or rows[0]['created_at'].endswith('Z')

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


def test_api_policy_templates_endpoint_returns_profile_and_templates(tmp_path: Path):
    client = build_client(tmp_path)
    project = tmp_path / 'policy-api-repo'
    project.mkdir()
    (project / 'README.md').write_text('hello\n', encoding='utf-8')

    resp = client.get('/api/policy-templates', params={'workspace_path': str(project)})
    assert resp.status_code == 200
    body = resp.json()
    assert body['recommended_template'] in {'balanced-default', 'safe-review', 'rapid-fix'}
    assert body['profile']['exists'] is True
    assert body['profile']['workspace_path']
    ids = {item['id'] for item in body['templates']}
    assert {'balanced-default', 'safe-review', 'rapid-fix', 'deep-evolve'}.issubset(ids)


def test_api_analytics_endpoint_returns_failure_and_drift_views(tmp_path: Path):
    client = build_client(tmp_path)
    created = client.post(
        '/api/tasks',
        json={
            'title': 'Analytics API Task',
            'description': 'analytics',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'sandbox_mode': False,
            'self_loop_mode': 1,
            'auto_start': False,
        },
    )
    task_id = created.json()['task_id']
    client.post(f'/api/tasks/{task_id}/start', json={'background': False})

    resp = client.get('/api/analytics', params={'limit': 100})
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body['failure_taxonomy'], list)
    assert isinstance(body['failure_taxonomy_trend'], list)
    assert isinstance(body['reviewer_drift'], list)
    assert 'adverse_rate' in body['reviewer_global']


def test_api_github_summary_endpoint_returns_markdown_payload(tmp_path: Path):
    client = build_client(tmp_path)
    created = client.post(
        '/api/tasks',
        json={
            'title': 'GitHub Summary API Task',
            'description': 'summary',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'sandbox_mode': False,
            'self_loop_mode': 1,
            'auto_start': False,
        },
    )
    task_id = created.json()['task_id']
    client.post(f'/api/tasks/{task_id}/start', json={'background': False})

    resp = client.get(f'/api/tasks/{task_id}/github-summary')
    assert resp.status_code == 200
    body = resp.json()
    assert body['task_id'] == task_id
    assert 'AWE-AgentForge Task Summary' in body['summary_markdown']
    assert isinstance(body['artifacts'], list)


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
    missing_path = str(tmp_path / 'missing-folder')
    resp = client.post(
        '/api/tasks',
        json={
            'title': 'Task bad path',
            'description': 'should fail',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'workspace_path': missing_path,
            'auto_start': False,
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body['code'] == 'validation_error'
    assert body['field'] == 'workspace_path'
    assert isinstance(body['message'], str)
    assert missing_path not in body['message']


def test_api_create_task_body_validation_returns_stable_400_schema(tmp_path: Path):
    client = build_client(tmp_path)
    resp = client.post(
        '/api/tasks',
        json={
            'description': 'missing title',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'auto_start': False,
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body['code'] == 'validation_error'
    assert body['field'] == 'title'
    assert isinstance(body['message'], str)
    assert body['message']


def test_api_create_task_service_validation_returns_stable_400_schema(tmp_path: Path):
    client = build_client(tmp_path)
    resp = client.post(
        '/api/tasks',
        json={
            'title': 'Task bad repair mode',
            'description': 'repair mode validation',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'repair_mode': 'aggressive',
            'sandbox_mode': False,
            'self_loop_mode': 1,
            'auto_start': False,
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body['code'] == 'validation_error'
    assert body['field'] == 'repair_mode'
    assert isinstance(body['message'], str)
    assert body['message']


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
            'max_rounds': 1,
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


def test_api_create_task_forces_sandbox_for_multi_round_no_auto_merge(tmp_path: Path):
    client = build_client(tmp_path)
    project = tmp_path / 'repo-force-sandbox'
    project.mkdir()
    (project / 'README.md').write_text('base\n', encoding='utf-8')

    resp = client.post(
        '/api/tasks',
        json={
            'title': 'Force sandbox mode',
            'description': 'multi round no auto merge',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'workspace_path': str(project),
            'sandbox_mode': False,
            'auto_merge': False,
            'max_rounds': 2,
            'self_loop_mode': 1,
            'auto_start': False,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body['sandbox_mode'] is True
    assert body['workspace_path'] != str(project)
    assert body['sandbox_generated'] is True


def test_api_promote_round_endpoint_merges_selected_round(tmp_path: Path):
    project = tmp_path / 'repo-promote-round'
    project.mkdir()
    (project / 'README.md').write_text('base\n', encoding='utf-8')
    client = build_client(tmp_path, workflow_engine=FakeWorkflowEngineTwoRoundsWithChanges())

    created = client.post(
        '/api/tasks',
        json={
            'title': 'Promote round task',
            'description': 'promote selected round',
            'author_participant': 'codex#author-A',
            'reviewer_participants': ['claude#review-B'],
            'workspace_path': str(project),
            'sandbox_mode': False,
            'auto_merge': False,
            'max_rounds': 2,
            'self_loop_mode': 1,
            'auto_start': False,
        },
    )
    assert created.status_code == 201
    task_id = created.json()['task_id']

    started = client.post(f'/api/tasks/{task_id}/start', json={'background': False})
    assert started.status_code == 200
    assert started.json()['status'] == 'passed'

    promoted_1 = client.post(
        f'/api/tasks/{task_id}/promote-round',
        json={'round': 1, 'merge_target_path': str(project)},
    )
    assert promoted_1.status_code == 200
    assert promoted_1.json()['round'] == 1
    assert (project / 'src' / 'round.txt').read_text(encoding='utf-8') == 'round-1\n'

    promoted_2 = client.post(
        f'/api/tasks/{task_id}/promote-round',
        json={'round': 2, 'merge_target_path': str(project)},
    )
    assert promoted_2.status_code == 200
    assert promoted_2.json()['round'] == 2
    assert (project / 'src' / 'round.txt').read_text(encoding='utf-8') == 'round-2\n'


def test_api_create_task_accepts_provider_models_and_team_agent_toggles(tmp_path: Path):
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
            'codex_multi_agents': True,
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
    assert body['codex_multi_agents'] is True


def test_api_create_task_accepts_repair_mode(tmp_path: Path):
    client = build_client(tmp_path)
    resp = client.post(
        '/api/tasks',
        json={
            'title': 'Task repair mode',
            'description': 'repair mode validation',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'repair_mode': 'structural',
            'sandbox_mode': False,
            'self_loop_mode': 1,
            'auto_start': False,
        },
    )
    assert resp.status_code == 201
    assert resp.json()['repair_mode'] == 'structural'


def test_api_create_task_accepts_plain_mode_disabled(tmp_path: Path):
    client = build_client(tmp_path)
    resp = client.post(
        '/api/tasks',
        json={
            'title': 'Task plain mode',
            'description': 'plain mode validation',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'plain_mode': False,
            'sandbox_mode': False,
            'self_loop_mode': 1,
            'auto_start': False,
        },
    )
    assert resp.status_code == 201
    assert resp.json()['plain_mode'] is False


def test_api_create_task_accepts_stream_and_debate_mode_disabled(tmp_path: Path):
    client = build_client(tmp_path)
    resp = client.post(
        '/api/tasks',
        json={
            'title': 'Task stream debate mode',
            'description': 'stream/debate validation',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'stream_mode': False,
            'debate_mode': False,
            'sandbox_mode': False,
            'self_loop_mode': 1,
            'auto_start': False,
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body['stream_mode'] is False
    assert body['debate_mode'] is False


def test_api_create_task_rejects_invalid_repair_mode(tmp_path: Path):
    client = build_client(tmp_path)
    resp = client.post(
        '/api/tasks',
        json={
            'title': 'Task bad repair mode',
            'description': 'repair mode validation',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'repair_mode': 'aggressive',
            'sandbox_mode': False,
            'self_loop_mode': 1,
            'auto_start': False,
        },
    )
    assert resp.status_code == 400


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
    assert 'claude-sonnet-4-6' in providers['claude']
    assert 'gpt-5.3-codex' in providers['codex']
    assert 'gpt-5.3-codex-spark' in providers['codex']
    assert 'gemini-3-pro-preview' in providers['gemini']
    assert len(providers['claude']) >= 3


def test_api_blocks_non_local_clients_by_default(tmp_path: Path):
    client = build_client(tmp_path, client=('203.0.113.7', 50000))
    resp = client.get('/api/stats')
    assert resp.status_code == 403
    body = resp.json()
    assert body['code'] == 'forbidden'
    assert body['message'] == 'api access denied'


def test_api_token_mode_blocks_missing_and_invalid_token(tmp_path: Path):
    client = build_client(tmp_path, api_access_token='secret-token')

    missing = client.get('/api/stats')
    assert missing.status_code == 401
    missing_body = missing.json()
    assert missing_body['code'] == 'unauthorized'
    assert missing_body['message'] == 'invalid api token'

    invalid = client.get('/api/stats', headers={'x-awe-api-token': 'wrong-token'})
    assert invalid.status_code == 401
    invalid_body = invalid.json()
    assert invalid_body['code'] == 'unauthorized'
    assert invalid_body['message'] == 'invalid api token'


def test_api_token_mode_allows_valid_token(tmp_path: Path):
    client = build_client(tmp_path, api_access_token='secret-token')
    resp = client.get('/api/stats', headers={'x-awe-api-token': 'secret-token'})
    assert resp.status_code == 200


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


def test_api_workspace_tree_rejects_dot_dot_path(tmp_path: Path):
    root = tmp_path / 'repo'
    root.mkdir()
    client = build_client(tmp_path)
    resp = client.get('/api/workspace-tree', params={'workspace_path': str(root / '..')})
    assert resp.status_code == 400
    body = resp.json()
    assert body['detail'] == 'workspace_path is invalid'


def test_api_workspace_tree_rejects_absolute_path_outside_allowed_root(tmp_path: Path):
    outside = tmp_path.parent / 'outside-workspace-root'
    outside.mkdir(exist_ok=True)

    client = build_client(tmp_path)
    resp = client.get('/api/workspace-tree', params={'workspace_path': str(outside)})
    assert resp.status_code == 400
    body = resp.json()
    assert body['detail'] == 'workspace_path is outside allowed root'
    assert str(outside) not in str(body)


def test_api_workspace_tree_validation_error_reports_query_field_name(tmp_path: Path):
    client = build_client(tmp_path)
    resp = client.get('/api/workspace-tree', params={'max_depth': 'oops'})
    assert resp.status_code == 400
    body = resp.json()
    assert body['code'] == 'validation_error'
    assert body['field'] == 'max_depth'


def test_api_gate_validation_error_reports_nested_reviewer_verdict_index(tmp_path: Path):
    client = build_client(tmp_path)
    created = client.post(
        '/api/tasks',
        json={
            'title': 'Task gate validation',
            'description': 'invalid second verdict',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'auto_start': False,
        },
    )
    assert created.status_code == 201
    task_id = created.json()['task_id']

    resp = client.post(
        f'/api/tasks/{task_id}/gate',
        json={
            'tests_ok': True,
            'lint_ok': True,
            'reviewer_verdicts': ['no_blocker', 'oops'],
        },
    )
    assert resp.status_code == 400
    body = resp.json()
    assert body['code'] == 'validation_error'
    assert body['field'] == 'reviewer_verdicts[1]'


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


def test_api_project_history_clear_removes_terminal_records_only(tmp_path: Path):
    client = build_client(tmp_path)
    payload = {
        'description': 'history clear',
        'author_participant': 'codex#author-A',
        'reviewer_participants': ['claude#review-B'],
        'workspace_path': str(tmp_path),
        'sandbox_mode': False,
        'self_loop_mode': 1,
        'auto_merge': False,
        'auto_start': False,
    }
    a = client.post('/api/tasks', json={**payload, 'title': 'History clear A'})
    b = client.post('/api/tasks', json={**payload, 'title': 'History clear B'})
    c = client.post('/api/tasks', json={**payload, 'title': 'History clear C'})
    assert a.status_code == 201
    assert b.status_code == 201
    assert c.status_code == 201

    task_a = a.json()
    task_b = b.json()
    task_c = c.json()
    project_path = task_a['project_path']

    started_a = client.post(f"/api/tasks/{task_a['task_id']}/start", json={'background': False})
    started_b = client.post(f"/api/tasks/{task_b['task_id']}/start", json={'background': False})
    assert started_a.status_code == 200
    assert started_b.status_code == 200
    assert started_a.json()['status'] == 'passed'
    assert started_b.json()['status'] == 'passed'

    cleared = client.post(
        '/api/project-history/clear',
        json={'project_path': project_path, 'include_non_terminal': False},
    )
    assert cleared.status_code == 200
    body = cleared.json()
    assert body['project_path'] == project_path
    assert body['deleted_tasks'] == 2
    assert body['deleted_artifacts'] == 2
    assert body['skipped_non_terminal'] >= 1

    tasks = client.get('/api/tasks', params={'limit': 20})
    assert tasks.status_code == 200
    ids = {item['task_id'] for item in tasks.json()}
    assert task_a['task_id'] not in ids
    assert task_b['task_id'] not in ids
    assert task_c['task_id'] in ids


def test_api_project_history_clear_include_non_terminal_removes_live_tasks_too(tmp_path: Path):
    client = build_client(tmp_path)
    payload = {
        'description': 'history clear all',
        'author_participant': 'codex#author-A',
        'reviewer_participants': ['claude#review-B'],
        'workspace_path': str(tmp_path),
        'sandbox_mode': False,
        'self_loop_mode': 1,
        'auto_merge': False,
        'auto_start': False,
    }
    a = client.post('/api/tasks', json={**payload, 'title': 'History all A'})
    b = client.post('/api/tasks', json={**payload, 'title': 'History all B'})
    c = client.post('/api/tasks', json={**payload, 'title': 'History all C'})
    assert a.status_code == 201
    assert b.status_code == 201
    assert c.status_code == 201

    task_a = a.json()
    task_b = b.json()
    task_c = c.json()
    project_path = task_a['project_path']

    started_a = client.post(f"/api/tasks/{task_a['task_id']}/start", json={'background': False})
    assert started_a.status_code == 200
    assert started_a.json()['status'] == 'passed'
    # Keep B/C queued (non-terminal) to verify include_non_terminal behavior.

    cleared = client.post(
        '/api/project-history/clear',
        json={'project_path': project_path, 'include_non_terminal': True},
    )
    assert cleared.status_code == 200
    body = cleared.json()
    assert body['project_path'] == project_path
    assert body['deleted_tasks'] == 3
    assert body['skipped_non_terminal'] == 0

    tasks = client.get('/api/tasks', params={'limit': 50})
    assert tasks.status_code == 200
    ids = {item['task_id'] for item in tasks.json()}
    assert task_a['task_id'] not in ids
    assert task_b['task_id'] not in ids
    assert task_c['task_id'] not in ids


def test_api_events_fallback_to_artifact_history_when_task_missing_from_repository(tmp_path: Path):
    repository = InMemoryTaskRepository()
    artifact_store = ArtifactStore(tmp_path / '.agents')
    service = OrchestratorService(
        repository=repository,
        artifact_store=artifact_store,
        workflow_engine=FakeWorkflowEngine(),
    )
    client = TestClient(create_app(service=service))

    task_id = 'task-history-only-1'
    artifact_store.update_state(
        task_id,
        {
            'task_id': task_id,
            'status': 'passed',
            'project_path': str(tmp_path),
            'workspace_path': str(tmp_path),
        },
    )
    artifact_store.append_event(
        task_id,
        {
            'seq': 1,
            'task_id': task_id,
            'type': 'discussion',
            'round': 1,
            'payload': {
                'participant': 'codex#author-A',
                'output': 'history plan',
            },
            'created_at': '2026-02-17T09:26:35Z',
        },
    )
    artifact_store.append_event(
        task_id,
        {
            'type': 'review',
            'round': 1,
            'participant': 'claude#review-B',
            'verdict': 'no_blocker',
            'output': 'history review',
            'created_at': '2026-02-17T09:27:00Z',
        },
    )

    resp = client.get(f'/api/tasks/{task_id}/events')
    assert resp.status_code == 200
    rows = resp.json()
    assert len(rows) == 2
    assert rows[0]['task_id'] == task_id
    assert rows[0]['type'] == 'discussion'
    assert rows[1]['type'] == 'review'
    assert rows[1]['payload']['participant'] == 'claude#review-B'
    assert rows[1]['payload']['verdict'] == 'no_blocker'
    assert rows[1]['payload']['output'] == 'history review'


def test_api_background_worker_logs_start_and_mark_fail_errors(tmp_path: Path, monkeypatch, caplog):
    client = build_client(tmp_path)
    created = client.post(
        '/api/tasks',
        json={
            'title': 'Background failure',
            'description': 'worker should log both failures',
            'author_participant': 'claude#author-A',
            'reviewer_participants': ['codex#review-B'],
            'auto_start': False,
        },
    )
    assert created.status_code == 201
    task_id = created.json()['task_id']

    service = client.app.state.container.service
    recorded_reasons: list[str] = []

    class EmptyBackgroundError(Exception):
        pass

    def fail_start(_task_id: str):
        raise EmptyBackgroundError()

    def fail_mark(_task_id: str, *, reason: str):
        recorded_reasons.append(reason)
        raise RuntimeError('write failed')

    monkeypatch.setattr(service, 'start_task', fail_start)
    monkeypatch.setattr(service, 'mark_failed_system', fail_mark)

    with caplog.at_level(logging.ERROR, logger='awe_agentcheck.api'):
        resp = client.post(f'/api/tasks/{task_id}/start', json={'background': True})

    assert resp.status_code == 200
    assert recorded_reasons == ['background_error: EmptyBackgroundError']
    messages = [record.getMessage() for record in caplog.records]
    assert any('background worker failed task_id=' in message for message in messages)
    assert any('failed to mark task as failed' in message for message in messages)


@pytest.mark.parametrize(
    ('task_path', 'expected_statuses'),
    [
        ('%2E%2E', {400}),
        ('%2E%2E%5Coutside', {400}),
        ('%2E%2E%2Foutside', {400, 404}),
    ],
)
def test_api_events_rejects_task_id_traversal_attempts(tmp_path: Path, task_path: str, expected_statuses: set[int]):
    client = build_client(tmp_path)
    resp = client.get(f'/api/tasks/{task_path}/events')
    assert resp.status_code in expected_statuses
    if resp.status_code == 400:
        body = resp.json()
        assert body['code'] == 'validation_error'
        assert body['field'] == 'task_id'


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


def test_api_author_decision_revise_requeues_waiting_task(tmp_path: Path):
    client = build_client(tmp_path)
    created = client.post(
        '/api/tasks',
        json={
            'title': 'Task Revise',
            'description': 'manual revise',
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

    revised = client.post(
        f'/api/tasks/{task_id}/author-decision',
        json={'decision': 'revise', 'note': 'Need concrete file-level plan', 'auto_start': False},
    )
    assert revised.status_code == 200
    body = revised.json()
    assert body['status'] == 'queued'
    assert body['last_gate_reason'] == 'author_feedback_requested'
