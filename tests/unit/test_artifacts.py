from __future__ import annotations

import json
from pathlib import Path

import pytest

from awe_agentcheck.storage.artifacts import ArtifactStore


def test_create_task_workspace_creates_expected_files(tmp_path: Path):
    store = ArtifactStore(root=tmp_path)

    workspace = store.create_task_workspace(task_id='task-123')

    assert workspace.root.exists()
    assert workspace.discussion_md.exists()
    assert workspace.summary_md.exists()
    assert workspace.final_report_md.exists()
    assert workspace.state_json.exists()
    assert workspace.decisions_json.exists()
    assert workspace.events_jsonl.exists()
    assert workspace.artifacts_dir.exists()

    state = json.loads(workspace.state_json.read_text(encoding='utf-8'))
    assert state['task_id'] == 'task-123'
    assert state['status'] == 'queued'


def test_write_artifact_json_writes_named_payload(tmp_path: Path):
    store = ArtifactStore(root=tmp_path)
    store.create_task_workspace(task_id='task-abc')
    path = store.write_artifact_json('task-abc', name='fusion_summary', payload={'ok': True})
    assert path.exists()
    payload = json.loads(path.read_text(encoding='utf-8'))
    assert payload == {'ok': True}


def test_create_task_workspace_rejects_traversal_like_task_id(tmp_path: Path):
    store = ArtifactStore(root=tmp_path)
    with pytest.raises(ValueError, match='invalid task_id'):
        store.create_task_workspace(task_id='../escape')


def test_remove_task_workspace_rejects_traversal_like_task_id(tmp_path: Path):
    store = ArtifactStore(root=tmp_path)
    with pytest.raises(ValueError, match='invalid task_id'):
        store.remove_task_workspace(task_id='../../escape')


def test_remove_task_workspace_allows_normal_task_id(tmp_path: Path):
    store = ArtifactStore(root=tmp_path)
    store.create_task_workspace(task_id='task-normal')
    assert store.remove_task_workspace(task_id='task-normal') is True


@pytest.mark.parametrize(
    'name',
    [
        '../escape',
        '..\\escape',
        'a\x00b',
        '..',
        '',
    ],
)
def test_write_artifact_json_rejects_unsafe_name(tmp_path: Path, name: str):
    store = ArtifactStore(root=tmp_path)
    store.create_task_workspace(task_id='task-safe')
    with pytest.raises(ValueError):
        store.write_artifact_json('task-safe', name=name, payload={'ok': False})


def test_write_artifact_json_sanitizes_platform_unsafe_chars(tmp_path: Path):
    store = ArtifactStore(root=tmp_path)
    store.create_task_workspace(task_id='task-safe')
    path = store.write_artifact_json(
        'task-safe',
        name='report:*?"<>| draft',
        payload={'ok': True},
    )
    assert path.name.endswith('.json')
    assert path.exists()
    assert all(ch not in path.name for ch in ':*?"<>|')
