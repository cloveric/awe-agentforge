from __future__ import annotations

from pathlib import Path

from awe_agentcheck.adapters import ParticipantRunner
from awe_agentcheck.repository import InMemoryTaskRepository
from awe_agentcheck.service import CreateTaskInput, OrchestratorService
from awe_agentcheck.storage.artifacts import ArtifactStore
from awe_agentcheck.workflow import ShellCommandExecutor, WorkflowEngine


def _build_service(tmp_path: Path) -> OrchestratorService:
    workflow = WorkflowEngine(
        runner=ParticipantRunner(dry_run=True),
        command_executor=ShellCommandExecutor(),
        participant_timeout_seconds=60,
        command_timeout_seconds=60,
    )
    return OrchestratorService(
        repository=InMemoryTaskRepository(),
        artifact_store=ArtifactStore(tmp_path / '.agents'),
        workflow_engine=workflow,
    )


def _seed_workspace(workspace: Path) -> None:
    (workspace / 'src').mkdir(parents=True, exist_ok=True)
    (workspace / 'tests').mkdir(parents=True, exist_ok=True)
    (workspace / 'src' / 'app.py').write_text(
        'def add(a: int, b: int) -> int:\n'
        '    return a + b\n',
        encoding='utf-8',
    )
    (workspace / 'tests' / 'test_smoke.py').write_text(
        'def test_smoke() -> None:\n'
        '    assert True\n',
        encoding='utf-8',
    )


def test_integration_create_start_roundtrip_pass(tmp_path: Path):
    workspace = tmp_path / 'repo-pass'
    workspace.mkdir(parents=True, exist_ok=True)
    _seed_workspace(workspace)

    service = _build_service(tmp_path)
    created = service.create_task(
        CreateTaskInput(
            title='integration pass',
            description='integration flow should pass',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            sandbox_mode=False,
            auto_merge=False,
            self_loop_mode=1,
            max_rounds=1,
            workspace_path=str(workspace),
            test_command='python -m pytest -q',
            lint_command='python -m ruff check .',
        )
    )

    started = service.start_task(created.task_id)
    assert started.status.value == 'passed'
    events = service.list_events(created.task_id)
    assert any(str(item.get('type')) == 'task_started' for item in events)
    assert any(str(item.get('type')) == 'gate_passed' for item in events)


def test_integration_architecture_audit_can_fail_gate_at_level_3(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('AWE_ARCH_AUDIT_MODE', 'hard')
    workspace = tmp_path / 'repo-arch-fail'
    workspace.mkdir(parents=True, exist_ok=True)
    _seed_workspace(workspace)
    oversized_module = workspace / 'src' / 'oversized.py'
    oversized_module.write_text(
        '\n'.join([f'def f{i}() -> int:\n    return {i}' for i in range(0, 1100)]),
        encoding='utf-8',
    )

    service = _build_service(tmp_path)
    created = service.create_task(
        CreateTaskInput(
            title='integration architecture fail',
            description='architecture audit hard gate should fail',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            sandbox_mode=False,
            auto_merge=False,
            self_loop_mode=1,
            evolution_level=3,
            max_rounds=1,
            workspace_path=str(workspace),
            test_command='python -m pytest -q',
            lint_command='python -m ruff check .',
        )
    )

    started = service.start_task(created.task_id)
    assert started.status.value == 'failed_gate'
    assert started.last_gate_reason == 'architecture_threshold_exceeded'
    events = service.list_events(created.task_id)
    assert any(str(item.get('type')) == 'architecture_audit' for item in events)


