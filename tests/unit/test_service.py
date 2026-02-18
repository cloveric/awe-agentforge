from __future__ import annotations

import os
from pathlib import Path

import pytest

from awe_agentcheck.adapters import AdapterResult
from awe_agentcheck.participants import parse_participant_id
from awe_agentcheck.repository import InMemoryTaskRepository
from awe_agentcheck.service import CreateTaskInput, InputValidationError, OrchestratorService
from awe_agentcheck.storage.artifacts import ArtifactStore
from awe_agentcheck.workflow import RunConfig, RunResult


class FakeWorkflowEngine:
    def __init__(self):
        self.calls = 0

    def run(self, config, *, on_event, should_cancel):
        self.calls += 1
        on_event({'type': 'discussion', 'round': 1, 'provider': config.author.provider, 'output': 'plan'})
        on_event({'type': 'implementation', 'round': 1, 'provider': config.author.provider, 'output': 'implemented'})
        on_event({'type': 'review', 'round': 1, 'participant': config.reviewers[0].participant_id, 'verdict': 'no_blocker', 'output': 'ok'})
        on_event({'type': 'gate_passed', 'round': 1, 'reason': 'passed'})
        return RunResult(status='passed', rounds=1, gate_reason='passed')


class FakeCanceledWorkflowEngine:
    def run(self, config, *, on_event, should_cancel):
        on_event({'type': 'task_started', 'round': 0})
        return RunResult(status='canceled', rounds=0, gate_reason='canceled')


class FakeFailingWorkflowEngine:
    def run(self, config, *, on_event, should_cancel):
        raise RuntimeError('boom')


class FakeForceFailedWorkflowEngine:
    def __init__(self):
        self.service = None

    def run(self, config, *, on_event, should_cancel):
        assert self.service is not None
        self.service.force_fail_task(config.task_id, reason='watchdog_timeout: test')
        return RunResult(status='passed', rounds=1, gate_reason='passed')


class FakeWorkflowEngineWithFileChange:
    def run(self, config, *, on_event, should_cancel):
        target = config.cwd / 'src' / 'hello.txt'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('hello fusion\n', encoding='utf-8')
        on_event({'type': 'discussion', 'round': 1, 'provider': config.author.provider, 'output': 'plan'})
        on_event({'type': 'implementation', 'round': 1, 'provider': config.author.provider, 'output': 'changed file'})
        on_event({'type': 'gate_passed', 'round': 1, 'reason': 'passed'})
        return RunResult(status='passed', rounds=1, gate_reason='passed')


class ProposalRunnerWithReviewerFailure:
    def run(self, *, participant, prompt, cwd, timeout_seconds=900, **kwargs):
        if participant.participant_id == 'gemini#review-C':
            raise RuntimeError('provider_limit provider=gemini command=gemini -m gemini-3-pro-preview')
        if participant.participant_id == 'codex#author-A':
            return AdapterResult(
                output='Plan proposal from author',
                verdict='unknown',
                next_action=None,
                returncode=0,
                duration_seconds=0.1,
            )
        return AdapterResult(
            output='VERDICT: NO_BLOCKER',
            verdict='no_blocker',
            next_action=None,
            returncode=0,
            duration_seconds=0.1,
        )


class ProposalOnlyWorkflowEngine:
    def __init__(self):
        self.runner = ProposalRunnerWithReviewerFailure()
        self.participant_timeout_seconds = 20


class ProposalRunnerOrderProbe:
    def __init__(self):
        self.calls: list[str] = []
        self.timeouts: list[int] = []

    def run(self, *, participant, prompt, cwd, timeout_seconds=900, **kwargs):
        self.calls.append(str(participant.participant_id))
        self.timeouts.append(int(timeout_seconds))
        if str(participant.participant_id).startswith('codex#author'):
            return AdapterResult(
                output='Author revised proposal after reviewer-first feedback',
                verdict='unknown',
                next_action=None,
                returncode=0,
                duration_seconds=0.1,
            )
        return AdapterResult(
            output='VERDICT: NO_BLOCKER\nReviewer notes',
            verdict='no_blocker',
            next_action=None,
            returncode=0,
            duration_seconds=0.1,
        )


class ProposalOrderWorkflowEngine:
    def __init__(self):
        self.runner = ProposalRunnerOrderProbe()
        self.participant_timeout_seconds = 20


class AutoConsensusWorkflowEngine(FakeWorkflowEngine):
    def __init__(self):
        super().__init__()
        self.runner = ProposalRunnerOrderProbe()
        self.participant_timeout_seconds = 20


class ProposalRunnerPrecheckUnavailable:
    def __init__(self):
        self.calls: list[str] = []

    def run(self, *, participant, prompt, cwd, timeout_seconds=900, **kwargs):
        pid = str(participant.participant_id)
        self.calls.append(pid)
        if pid.startswith('codex#author'):
            raise AssertionError('author should not run when proposal precheck is unavailable')
        raise RuntimeError('command_timeout provider=claude command=claude -p timeout_seconds=240')


class ProposalPrecheckUnavailableWorkflowEngine:
    def __init__(self):
        self.runner = ProposalRunnerPrecheckUnavailable()
        self.participant_timeout_seconds = 240


class ProposalRunnerReviewUnavailable:
    def __init__(self):
        self.calls: list[str] = []

    def run(self, *, participant, prompt, cwd, timeout_seconds=900, **kwargs):
        pid = str(participant.participant_id)
        self.calls.append(pid)
        if pid.startswith('codex#author'):
            return AdapterResult(
                output='Author aligns with reviewer suggestions and provides revision plan.',
                verdict='unknown',
                next_action=None,
                returncode=0,
                duration_seconds=0.1,
            )
        if 'Stage: precheck.' in str(prompt):
            return AdapterResult(
                output='VERDICT: NO_BLOCKER\nIssue: Scope ready.\nImpact: Review can proceed.\nNext: Provide concrete edits.',
                verdict='no_blocker',
                next_action=None,
                returncode=0,
                duration_seconds=0.1,
            )
        return AdapterResult(
            output='',
            verdict='no_blocker',
            next_action=None,
            returncode=0,
            duration_seconds=0.1,
        )


class ProposalReviewUnavailableWorkflowEngine:
    def __init__(self):
        self.runner = ProposalRunnerReviewUnavailable()
        self.participant_timeout_seconds = 60


class FailingArtifactStore(ArtifactStore):
    def create_task_workspace(self, task_id: str):  # type: ignore[override]
        raise RuntimeError('artifact store failure')


def build_service(tmp_path: Path, workflow_engine=None, *, max_concurrent_running_tasks: int = 1) -> OrchestratorService:
    return OrchestratorService(
        repository=InMemoryTaskRepository(),
        artifact_store=ArtifactStore(tmp_path / '.agents'),
        workflow_engine=workflow_engine or FakeWorkflowEngine(),
        max_concurrent_running_tasks=max_concurrent_running_tasks,
    )


def test_service_create_task_sets_queued_status(tmp_path: Path):
    svc = build_service(tmp_path)

    task = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            max_rounds=2,
        )
    )

    assert task.status.value == 'queued'
    assert task.max_rounds == 2
    assert task.workspace_path
    assert task.evolution_level == 0
    assert task.evolve_until is None
    assert task.plain_mode is True
    assert task.stream_mode is True
    assert task.debate_mode is True
    assert task.auto_merge is True
    assert task.merge_target_path is None


def test_service_create_task_accepts_evolution_fields(tmp_path: Path):
    svc = build_service(tmp_path)
    task = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            evolution_level=2,
            evolve_until='2026-02-13 06:00',
        )
    )
    assert task.evolution_level == 2
    assert task.evolve_until == '2026-02-13T06:00:00'


def test_service_create_task_accepts_provider_models_and_claude_team_agents(tmp_path: Path):
    svc = build_service(tmp_path)
    task = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Model config',
            description='provider model and team agents',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B', 'gemini#review-C'],
            provider_models={'claude': 'claude-sonnet-4-5', 'codex': 'gpt-5-codex', 'gemini': 'gemini-2.5-pro'},
            claude_team_agents=True,
        )
    )
    assert task.provider_models.get('claude') == 'claude-sonnet-4-5'
    assert task.provider_models.get('codex') == 'gpt-5-codex'
    assert task.provider_models.get('gemini') == 'gemini-2.5-pro'
    assert task.claude_team_agents is True


def test_service_create_task_accepts_provider_model_params(tmp_path: Path):
    svc = build_service(tmp_path)
    task = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Model params config',
            description='provider model params',
            author_participant='codex#author-A',
            reviewer_participants=['gemini#review-B'],
            provider_models={'codex': 'gpt-5.3-codex', 'gemini': 'gemini-3-pro-preview'},
            provider_model_params={
                'codex': '-c model_reasoning_effort=high',
                'gemini': '--approval-mode yolo',
            },
        )
    )
    assert task.provider_model_params.get('codex') == '-c model_reasoning_effort=high'
    assert task.provider_model_params.get('gemini') == '--approval-mode yolo'


def test_service_create_task_accepts_conversation_language(tmp_path: Path):
    svc = build_service(tmp_path)
    task = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Language config',
            description='zh conversation',
            author_participant='codex#author-A',
            reviewer_participants=['gemini#review-B'],
            conversation_language='zh',
        )
    )
    assert task.conversation_language == 'zh'


def test_service_create_task_accepts_repair_mode(tmp_path: Path):
    svc = build_service(tmp_path)
    task = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Repair config',
            description='structural repair',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            repair_mode='structural',
        )
    )
    assert task.repair_mode == 'structural'


def test_service_create_task_accepts_plain_mode_disabled(tmp_path: Path):
    svc = build_service(tmp_path)
    task = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Plain mode config',
            description='disable plain mode',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            plain_mode=False,
        )
    )
    assert task.plain_mode is False


def test_service_create_task_accepts_stream_and_debate_modes_disabled(tmp_path: Path):
    svc = build_service(tmp_path)
    task = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Stream/debate mode config',
            description='disable stream/debate mode',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            stream_mode=False,
            debate_mode=False,
        )
    )
    assert task.stream_mode is False
    assert task.debate_mode is False


def test_service_create_task_rejects_invalid_conversation_language(tmp_path: Path):
    svc = build_service(tmp_path)
    with pytest.raises(ValueError, match='invalid conversation_language'):
        svc.create_task(
            CreateTaskInput(
                sandbox_mode=False,
                self_loop_mode=1,
                title='Language invalid',
                description='invalid language',
                author_participant='codex#author-A',
                reviewer_participants=['gemini#review-B'],
                conversation_language='jp',
            )
        )


def test_service_create_task_rejects_invalid_repair_mode(tmp_path: Path):
    svc = build_service(tmp_path)
    with pytest.raises(ValueError, match='invalid repair_mode'):
        svc.create_task(
            CreateTaskInput(
                sandbox_mode=False,
                self_loop_mode=1,
                title='Repair invalid',
                description='invalid repair mode',
                author_participant='codex#author-A',
                reviewer_participants=['gemini#review-B'],
                repair_mode='aggressive',
            )
        )


def test_service_provider_model_catalog_has_multiple_defaults(tmp_path: Path):
    svc = build_service(tmp_path)
    catalog = svc.get_provider_models_catalog()
    assert 'claude-opus-4-6' in catalog.get('claude', [])
    assert 'claude-sonnet-4-6' in catalog.get('claude', [])
    assert 'gpt-5.3-codex' in catalog.get('codex', [])
    assert 'gemini-3-pro-preview' in catalog.get('gemini', [])
    assert len(catalog.get('claude', [])) >= 3
    assert len(catalog.get('codex', [])) >= 3
    assert len(catalog.get('gemini', [])) >= 3


def test_service_create_task_rejects_unknown_provider_model_key(tmp_path: Path):
    svc = build_service(tmp_path)
    with pytest.raises(ValueError, match='invalid provider_models key'):
        svc.create_task(
            CreateTaskInput(
                sandbox_mode=False,
                self_loop_mode=1,
                title='Bad provider model key',
                description='provider model validation',
                author_participant='claude#author-A',
                reviewer_participants=['codex#review-B'],
                provider_models={'unknown': 'model-x'},
            )
        )


def test_service_create_task_rejects_unknown_provider_model_param_key(tmp_path: Path):
    svc = build_service(tmp_path)
    with pytest.raises(ValueError, match='invalid provider_model_params key'):
        svc.create_task(
            CreateTaskInput(
                sandbox_mode=False,
                self_loop_mode=1,
                title='Bad provider model param key',
                description='provider model param validation',
                author_participant='claude#author-A',
                reviewer_participants=['codex#review-B'],
                provider_model_params={'unknown': '--foo bar'},
            )
        )


def test_service_create_task_defaults_to_sandbox_workspace(tmp_path: Path, monkeypatch):
    project = tmp_path / 'proj'
    project.mkdir()
    (project / 'README.md').write_text('hello\n', encoding='utf-8')
    monkeypatch.setenv('AWE_SANDBOX_BASE', str(tmp_path / 'sandbox-root'))

    svc = build_service(tmp_path)
    task = svc.create_task(
        CreateTaskInput(
            title='Sandbox default',
            description='sandbox',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
        )
    )

    assert task.sandbox_mode is True
    assert task.self_loop_mode == 0
    assert task.project_path == str(project)
    assert 'proj-lab' in Path(task.workspace_path).as_posix()
    assert task.sandbox_generated is True
    assert task.sandbox_cleanup_on_pass is True
    assert task.merge_target_path == str(project)
    assert (Path(task.workspace_path) / 'README.md').exists()


def test_service_create_task_default_sandbox_respects_configured_base(tmp_path: Path, monkeypatch):
    project = tmp_path / 'proj-base'
    project.mkdir()
    (project / 'README.md').write_text('hello\n', encoding='utf-8')
    base = tmp_path / 'custom-sandbox-root'
    monkeypatch.setenv('AWE_SANDBOX_BASE', str(base))

    svc = build_service(tmp_path)
    task = svc.create_task(
        CreateTaskInput(
            title='Sandbox base env',
            description='sandbox',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
        )
    )

    workspace = Path(task.workspace_path).resolve()
    expected_root = (base / 'proj-base-lab').resolve()
    assert workspace.parent == expected_root


def test_service_default_sandbox_path_uses_private_home_base_by_default(tmp_path: Path, monkeypatch):
    project = tmp_path / 'proj-private-default'
    project.mkdir()
    fake_home = tmp_path / 'fake-home'
    fake_home.mkdir()
    monkeypatch.delenv('AWE_SANDBOX_BASE', raising=False)
    monkeypatch.delenv('AWE_SANDBOX_USE_PUBLIC_BASE', raising=False)
    monkeypatch.setattr(Path, 'home', staticmethod(lambda: fake_home))

    path = Path(OrchestratorService._default_sandbox_path(project)).resolve()
    expected_root = (fake_home / '.awe-agentcheck' / 'sandboxes' / 'proj-private-default-lab').resolve()
    assert path.parent == expected_root


def test_service_default_sandbox_path_shared_base_requires_opt_in(tmp_path: Path, monkeypatch):
    project = tmp_path / 'proj-shared-base'
    project.mkdir()
    monkeypatch.delenv('AWE_SANDBOX_BASE', raising=False)
    monkeypatch.setenv('AWE_SANDBOX_USE_PUBLIC_BASE', '1')

    if os.name == 'nt':
        public_root = tmp_path / 'public-home'
        monkeypatch.setenv('PUBLIC', str(public_root))
        expected_root = (public_root / 'awe-agentcheck-sandboxes' / 'proj-shared-base-lab').resolve()
    else:
        expected_root = (Path('/tmp/awe-agentcheck-sandboxes') / 'proj-shared-base-lab').resolve()

    path = Path(OrchestratorService._default_sandbox_path(project)).resolve()
    assert path.parent == expected_root


def test_service_create_task_default_sandbox_avoids_agents_ancestor(tmp_path: Path, monkeypatch):
    home_like = tmp_path / 'home-like'
    project = home_like / 'proj-agents'
    project.mkdir(parents=True)
    (project / 'README.md').write_text('hello\n', encoding='utf-8')
    (home_like / 'AGENTS.md').write_text('root agents\n', encoding='utf-8')
    monkeypatch.delenv('AWE_SANDBOX_BASE', raising=False)
    monkeypatch.delenv('AWE_SANDBOX_USE_PUBLIC_BASE', raising=False)
    fake_home = tmp_path / 'fake-home-agents'
    fake_home.mkdir()
    monkeypatch.setattr(Path, 'home', staticmethod(lambda: fake_home))

    svc = build_service(tmp_path)
    task = svc.create_task(
        CreateTaskInput(
            title='Sandbox agents ancestor',
            description='sandbox',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
        )
    )

    workspace = Path(task.workspace_path).resolve()
    default_home_like_root = (project.parent / 'proj-agents-lab').resolve()
    assert workspace.parent != default_home_like_root
    assert 'proj-agents-lab' in workspace.parent.as_posix()


def test_service_create_task_uses_unique_default_sandbox_per_task(tmp_path: Path, monkeypatch):
    project = tmp_path / 'proj-unique'
    project.mkdir()
    (project / 'README.md').write_text('hello\n', encoding='utf-8')
    monkeypatch.setenv('AWE_SANDBOX_BASE', str(tmp_path / 'sandbox-root'))
    svc = build_service(tmp_path)

    t1 = svc.create_task(
        CreateTaskInput(
            title='T1',
            description='sandbox one',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
        )
    )
    t2 = svc.create_task(
        CreateTaskInput(
            title='T2',
            description='sandbox two',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
        )
    )

    assert t1.workspace_path != t2.workspace_path
    assert Path(t1.workspace_path).exists()
    assert Path(t2.workspace_path).exists()


def test_service_create_task_with_invalid_merge_target_does_not_leave_sandbox(tmp_path: Path, monkeypatch):
    project = tmp_path / 'proj-invalid-merge'
    project.mkdir()
    (project / 'README.md').write_text('hello\n', encoding='utf-8')
    sandbox_base = tmp_path / 'sandbox-base'
    missing_target = tmp_path / 'missing-target'
    monkeypatch.setenv('AWE_SANDBOX_BASE', str(sandbox_base))

    svc = build_service(tmp_path)
    with pytest.raises(ValueError, match='merge_target_path'):
        svc.create_task(
            CreateTaskInput(
                title='Invalid merge target',
                description='sandbox should not be created',
                author_participant='claude#author-A',
                reviewer_participants=['codex#review-B'],
                workspace_path=str(project),
                sandbox_mode=True,
                auto_merge=True,
                merge_target_path=str(missing_target),
            )
        )

    assert not sandbox_base.exists()


def test_service_create_task_cleans_generated_sandbox_when_later_step_fails(tmp_path: Path, monkeypatch):
    project = tmp_path / 'proj-create-failure-cleanup'
    project.mkdir()
    (project / 'README.md').write_text('hello\n', encoding='utf-8')
    sandbox_base = tmp_path / 'sandbox-base'
    monkeypatch.setenv('AWE_SANDBOX_BASE', str(sandbox_base))

    svc = OrchestratorService(
        repository=InMemoryTaskRepository(),
        artifact_store=FailingArtifactStore(tmp_path / '.agents'),
        workflow_engine=FakeWorkflowEngine(),
    )
    with pytest.raises(RuntimeError, match='artifact store failure'):
        svc.create_task(
            CreateTaskInput(
                title='Create failure cleanup',
                description='sandbox cleanup',
                author_participant='claude#author-A',
                reviewer_participants=['codex#review-B'],
                workspace_path=str(project),
                sandbox_mode=True,
            )
        )

    project_root = sandbox_base / 'proj-create-failure-cleanup-lab'
    assert not project_root.exists() or not list(project_root.iterdir())


def test_service_create_task_sandbox_bootstrap_skips_secret_files(tmp_path: Path, monkeypatch):
    project = tmp_path / 'proj-secret-filter'
    project.mkdir()
    (project / 'README.md').write_text('keep\n', encoding='utf-8')
    (project / '.env').write_text('SECRET=1\n', encoding='utf-8')
    (project / '.env.local').write_text('LOCAL_SECRET=1\n', encoding='utf-8')
    (project / 'api-token.txt').write_text('token\n', encoding='utf-8')
    (project / 'service.key').write_text('key\n', encoding='utf-8')
    (project / 'service.pem').write_text('pem\n', encoding='utf-8')
    src_dir = project / 'src'
    src_dir.mkdir()
    (src_dir / 'app.py').write_text('print("ok")\n', encoding='utf-8')
    sandbox_base = tmp_path / 'sandbox-base'
    monkeypatch.setenv('AWE_SANDBOX_BASE', str(sandbox_base))

    svc = build_service(tmp_path)
    task = svc.create_task(
        CreateTaskInput(
            title='Sandbox secrets filter',
            description='do not copy secrets',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
            auto_merge=False,
        )
    )

    sandbox = Path(task.workspace_path)
    assert (sandbox / 'README.md').exists()
    assert (sandbox / 'src' / 'app.py').exists()
    assert not (sandbox / '.env').exists()
    assert not (sandbox / '.env.local').exists()
    assert not (sandbox / 'api-token.txt').exists()
    assert not (sandbox / 'service.key').exists()
    assert not (sandbox / 'service.pem').exists()


def test_service_start_task_default_sandbox_is_cleaned_after_passed_auto_merge(tmp_path: Path, monkeypatch):
    project = tmp_path / 'proj-cleanup'
    project.mkdir()
    (project / 'README.md').write_text('base\n', encoding='utf-8')
    monkeypatch.setenv('AWE_SANDBOX_BASE', str(tmp_path / 'sandbox-root'))
    svc = build_service(tmp_path, workflow_engine=FakeWorkflowEngineWithFileChange())
    task = svc.create_task(
        CreateTaskInput(
            title='Cleanup task',
            description='cleanup',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
            self_loop_mode=1,
        )
    )
    sandbox_path = Path(task.workspace_path)
    assert sandbox_path.exists()

    result = svc.start_task(task.task_id)
    assert result.status.value == 'passed'
    assert (project / 'src' / 'hello.txt').exists()
    assert not sandbox_path.exists()
    events = svc.list_events(task.task_id)
    assert any(e['type'] == 'sandbox_cleanup_completed' for e in events)


def test_service_start_task_custom_sandbox_is_not_auto_cleaned(tmp_path: Path):
    project = tmp_path / 'proj-custom-sandbox'
    custom = tmp_path / 'my-custom-lab'
    project.mkdir()
    custom.mkdir()
    (project / 'README.md').write_text('base\n', encoding='utf-8')
    svc = build_service(tmp_path, workflow_engine=FakeWorkflowEngineWithFileChange())
    task = svc.create_task(
        CreateTaskInput(
            title='Custom sandbox task',
            description='custom',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
            sandbox_workspace_path=str(custom),
            self_loop_mode=1,
        )
    )

    result = svc.start_task(task.task_id)
    assert result.status.value == 'passed'
    assert custom.exists()
    events = svc.list_events(task.task_id)
    assert not any(e['type'] == 'sandbox_cleanup_completed' for e in events)


def test_service_start_task_waits_for_author_confirmation_when_self_loop_manual(tmp_path: Path):
    project = tmp_path / 'manual-proj'
    project.mkdir()
    engine = FakeWorkflowEngine()
    svc = build_service(tmp_path, workflow_engine=engine)
    task = svc.create_task(
        CreateTaskInput(
            title='Manual approve',
            description='need approve',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
            sandbox_mode=False,
            self_loop_mode=0,
        )
    )

    waiting = svc.start_task(task.task_id)
    assert waiting.status.value == 'waiting_manual'
    assert waiting.last_gate_reason == 'author_confirmation_required'
    assert engine.calls == 0
    events = svc.list_events(task.task_id)
    assert any(e['type'] == 'task_running' for e in events)


def test_service_author_approve_requeues_and_can_run(tmp_path: Path):
    project = tmp_path / 'approve-proj'
    project.mkdir()
    engine = FakeWorkflowEngine()
    svc = build_service(tmp_path, workflow_engine=engine)
    task = svc.create_task(
        CreateTaskInput(
            title='Approve path',
            description='approve',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
            sandbox_mode=False,
            self_loop_mode=0,
        )
    )
    svc.start_task(task.task_id)

    queued = svc.submit_author_decision(task.task_id, approve=True, note='ship it')
    assert queued.status.value == 'queued'
    assert queued.last_gate_reason == 'author_approved'

    passed = svc.start_task(task.task_id)
    assert passed.status.value == 'passed'
    assert engine.calls == 1


def test_service_author_reject_cancels(tmp_path: Path):
    project = tmp_path / 'reject-proj'
    project.mkdir()
    svc = build_service(tmp_path, workflow_engine=FakeWorkflowEngine())
    task = svc.create_task(
        CreateTaskInput(
            title='Reject path',
            description='reject',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
            sandbox_mode=False,
            self_loop_mode=0,
        )
    )
    svc.start_task(task.task_id)

    canceled = svc.submit_author_decision(task.task_id, approve=False, note='not now')
    assert canceled.status.value == 'canceled'
    assert canceled.last_gate_reason == 'author_rejected'


def test_service_start_task_auto_merge_copies_changes_and_writes_changelog_snapshot(tmp_path: Path):
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    (source / 'README.md').write_text('base\n', encoding='utf-8')

    svc = build_service(tmp_path, workflow_engine=FakeWorkflowEngineWithFileChange())
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Fusion task',
            description='auto merge',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(source),
            auto_merge=True,
            merge_target_path=str(target),
        )
    )

    result = svc.start_task(created.task_id)
    assert result.status.value == 'passed'

    merged_file = target / 'src' / 'hello.txt'
    assert merged_file.exists()
    assert merged_file.read_text(encoding='utf-8') == 'hello fusion\n'

    changelog = target / 'CHANGELOG.auto.md'
    assert changelog.exists()
    assert created.task_id in changelog.read_text(encoding='utf-8')

    snapshots = list((tmp_path / '.agents' / 'snapshots').glob(f'{created.task_id}-*.zip'))
    assert snapshots

    events = svc.list_events(created.task_id)
    assert any(e['type'] == 'auto_merge_completed' for e in events)


def test_service_start_task_can_disable_auto_merge(tmp_path: Path):
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    (source / 'README.md').write_text('base\n', encoding='utf-8')

    svc = build_service(tmp_path, workflow_engine=FakeWorkflowEngineWithFileChange())
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='No fusion task',
            description='auto merge off',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(source),
            auto_merge=False,
            merge_target_path=str(target),
        )
    )

    result = svc.start_task(created.task_id)
    assert result.status.value == 'passed'
    assert not (target / 'src' / 'hello.txt').exists()
    assert not (target / 'CHANGELOG.auto.md').exists()


def test_service_start_task_runs_workflow_and_records_events(tmp_path: Path):
    svc = build_service(tmp_path)
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )

    result = svc.start_task(created.task_id)
    events = svc.list_events(created.task_id)

    assert result.status.value == 'passed'
    assert result.rounds_completed == 1
    assert len(events) >= 3


@pytest.mark.parametrize('task_id', ['..', '..%5Coutside', '..\\outside', '../outside'])
def test_service_list_events_rejects_traversal_task_ids(tmp_path: Path, task_id: str):
    svc = build_service(tmp_path)
    with pytest.raises(InputValidationError) as exc:
        svc.list_events(task_id)
    assert exc.value.field == 'task_id'


def test_service_cancel_request_marks_flag(tmp_path: Path):
    svc = build_service(tmp_path)
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )

    canceled = svc.request_cancel(created.task_id)
    assert canceled.cancel_requested is True


def test_service_start_task_on_terminal_status_is_idempotent(tmp_path: Path):
    engine = FakeWorkflowEngine()
    svc = build_service(tmp_path, workflow_engine=engine)
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )

    first = svc.start_task(created.task_id)
    second = svc.start_task(created.task_id)

    assert first.status.value == 'passed'
    assert second.status.value == 'passed'
    assert engine.calls == 1


def test_service_start_task_clears_cancel_flag_in_returned_view(tmp_path: Path):
    svc = build_service(tmp_path, workflow_engine=FakeCanceledWorkflowEngine())
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    svc.request_cancel(created.task_id)

    result = svc.start_task(created.task_id)
    assert result.status.value == 'canceled'
    assert result.cancel_requested is False


def test_service_start_task_marks_failed_system_on_workflow_exception(tmp_path: Path):
    svc = build_service(tmp_path, workflow_engine=FakeFailingWorkflowEngine())
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )

    result = svc.start_task(created.task_id)
    assert result.status.value == 'failed_system'
    assert 'workflow_error' in (result.last_gate_reason or '')


def test_service_mark_failed_system_updates_status(tmp_path: Path):
    svc = build_service(tmp_path)
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )

    failed = svc.mark_failed_system(created.task_id, reason='boom')
    assert failed.status.value == 'failed_system'
    assert failed.last_gate_reason == 'boom'


def test_service_force_fail_task_sets_status_and_cancel_requested(tmp_path: Path):
    svc = build_service(tmp_path)
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )

    failed = svc.force_fail_task(created.task_id, reason='watchdog_timeout: task exceeded 1800s')
    assert failed.status.value == 'failed_system'
    assert failed.cancel_requested is True
    assert 'watchdog_timeout' in (failed.last_gate_reason or '')
    events = svc.list_events(created.task_id)
    assert any(e['type'] == 'force_failed' for e in events)


def test_service_start_task_does_not_override_external_force_fail(tmp_path: Path):
    engine = FakeForceFailedWorkflowEngine()
    svc = build_service(tmp_path, workflow_engine=engine)
    engine.service = svc
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )

    result = svc.start_task(created.task_id)
    assert result.status.value == 'failed_system'
    assert 'watchdog_timeout' in (result.last_gate_reason or '')


def test_service_force_fail_noop_when_already_passed(tmp_path: Path):
    """force_fail_task should not overwrite a PASSED status (race guard)."""
    svc = build_service(tmp_path)
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    # Run the task to completion (PASSED).
    svc.start_task(created.task_id)
    task = svc.get_task(created.task_id)
    assert task.status.value == 'passed'

    # A late force_fail should be a no-op and preserve PASSED.
    result = svc.force_fail_task(created.task_id, reason='watchdog_timeout: late')
    assert result.status.value == 'passed'
    assert result.cancel_requested is False


def test_service_force_fail_races_with_concurrent_status_change(tmp_path: Path):
    """force_fail_task returns current state if status changed between read and CAS."""
    svc = build_service(tmp_path)
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    # Manually set status to RUNNING to simulate in-progress task.
    svc.repository.update_task_status(
        created.task_id, status='running', reason=None, rounds_completed=0,
    )
    # Simulate a concurrent transition: change status to passed *between*
    # the get_task read and the CAS write inside force_fail_task.
    original_get = svc.repository.get_task

    def patched_get(task_id):
        row = original_get(task_id)
        if row and row['status'] == 'running':
            # Simulate concurrent workflow completion.
            svc.repository.update_task_status(
                task_id, status='passed', reason='passed', rounds_completed=1,
            )
        return row

    svc.repository.get_task = patched_get

    result = svc.force_fail_task(created.task_id, reason='watchdog_timeout: race')
    # Should honour the concurrent PASSED, not overwrite it.
    assert result.status.value == 'passed'


def test_service_mark_failed_system_does_not_overwrite_force_fail(tmp_path: Path):
    """mark_failed_system should not overwrite a force_fail that already transitioned the task."""
    svc = build_service(tmp_path)
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    # Move task to RUNNING.
    svc.repository.update_task_status(
        created.task_id, status='running', reason=None, rounds_completed=0,
    )
    # External force_fail transitions to FAILED_SYSTEM first.
    svc.force_fail_task(created.task_id, reason='watchdog_timeout: 1800s')

    # Now the workflow exception handler calls mark_failed_system – it should
    # NOT overwrite the force_fail reason.
    result = svc.mark_failed_system(created.task_id, reason='workflow_error: connection reset')
    assert result.status.value == 'failed_system'
    assert 'watchdog_timeout' in (result.last_gate_reason or '')


def test_service_mark_failed_system_cas_race_honours_concurrent_pass(tmp_path: Path):
    """mark_failed_system should honour a concurrent PASSED transition."""
    svc = build_service(tmp_path)
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    svc.repository.update_task_status(
        created.task_id, status='running', reason=None, rounds_completed=0,
    )
    # Patch get_task to simulate concurrent pass between read and CAS.
    original_get = svc.repository.get_task

    def patched_get(task_id):
        row = original_get(task_id)
        if row and row['status'] == 'running':
            svc.repository.update_task_status(
                task_id, status='passed', reason='all_passed', rounds_completed=1,
            )
        return row

    svc.repository.get_task = patched_get

    result = svc.mark_failed_system(created.task_id, reason='workflow_error: late crash')
    assert result.status.value == 'passed'


def test_service_create_task_rejects_missing_workspace(tmp_path: Path):
    svc = build_service(tmp_path)
    missing = tmp_path / 'does-not-exist'

    try:
        svc.create_task(
            CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
                title='Build parser',
                description='Implement parser for feed',
                author_participant='claude#author-A',
                reviewer_participants=['codex#review-B'],
                workspace_path=str(missing),
            )
        )
    except ValueError as exc:
        assert 'workspace_path' in str(exc)
    else:
        raise AssertionError('expected ValueError')


def test_service_create_task_rejects_invalid_evolve_until(tmp_path: Path):
    svc = build_service(tmp_path)
    try:
        svc.create_task(
            CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
                title='Build parser',
                description='Implement parser for feed',
                author_participant='claude#author-A',
                reviewer_participants=['codex#review-B'],
                evolve_until='bad-value',
            )
        )
    except ValueError as exc:
        assert 'evolve_until' in str(exc)
    else:
        raise AssertionError('expected ValueError')


def test_service_create_task_rejects_invalid_merge_target_when_auto_merge_enabled(tmp_path: Path):
    svc = build_service(tmp_path)
    missing = tmp_path / 'missing-merge-target'
    try:
        svc.create_task(
            CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
                title='Build parser',
                description='Implement parser for feed',
                author_participant='claude#author-A',
                reviewer_participants=['codex#review-B'],
                auto_merge=True,
                merge_target_path=str(missing),
            )
        )
    except ValueError as exc:
        assert 'merge_target_path' in str(exc)
    else:
        raise AssertionError('expected ValueError')


def test_service_stats_include_reason_and_provider_error_breakdown(tmp_path: Path):
    svc = build_service(tmp_path)

    t1 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T1',
            description='d1',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    svc.mark_failed_system(
        t1.task_id,
        reason='workflow_error: command_timeout provider=codex command=codex exec timeout_seconds=240',
    )

    t2 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T2',
            description='d2',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    svc.mark_failed_system(
        t2.task_id,
        reason='workflow_error: command_not_found provider=claude command=claude -p',
    )

    stats = svc.get_stats()
    assert stats.reason_bucket_counts.get('command_timeout') == 1
    assert stats.reason_bucket_counts.get('command_not_found') == 1
    assert stats.provider_error_counts.get('codex') == 1
    assert stats.provider_error_counts.get('claude') == 1


def test_service_stats_do_not_bucket_passed_reason(tmp_path: Path):
    svc = build_service(tmp_path)
    t1 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T1',
            description='d1',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    svc.repository.update_task_status(t1.task_id, status='passed', reason='passed', rounds_completed=1)

    stats = svc.get_stats()
    assert stats.reason_bucket_counts.get('other') is None


def test_service_stats_bucket_review_unknown(tmp_path: Path):
    svc = build_service(tmp_path)
    t1 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T1',
            description='d1',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    svc.repository.update_task_status(t1.task_id, status='failed_gate', reason='review_unknown', rounds_completed=1)
    stats = svc.get_stats()
    assert stats.reason_bucket_counts.get('review_unknown') == 1


def test_service_stats_bucket_provider_limit(tmp_path: Path):
    svc = build_service(tmp_path)
    t1 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T1',
            description='d1',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    svc.mark_failed_system(
        t1.task_id,
        reason='workflow_error: provider_limit provider=claude command=claude -p',
    )
    stats = svc.get_stats()
    assert stats.reason_bucket_counts.get('provider_limit') == 1


def test_service_stats_bucket_watchdog_timeout(tmp_path: Path):
    svc = build_service(tmp_path)
    t1 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T1',
            description='d1',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    svc.mark_failed_system(
        t1.task_id,
        reason='watchdog_timeout: task exceeded 1800s without terminal status',
    )
    stats = svc.get_stats()
    assert stats.reason_bucket_counts.get('watchdog_timeout') == 1


def test_service_start_task_is_deferred_when_running_limit_reached(tmp_path: Path):
    svc = build_service(tmp_path, max_concurrent_running_tasks=1)
    t1 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T1',
            description='d1',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    t2 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T2',
            description='d2',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )

    svc.repository.update_task_status(
        t1.task_id,
        status='running',
        reason=None,
        rounds_completed=0,
    )

    deferred = svc.start_task(t2.task_id)
    assert deferred.status.value == 'queued'
    assert deferred.last_gate_reason == 'concurrency_limit'
    events = svc.list_events(t2.task_id)
    assert any(e['type'] == 'start_deferred' for e in events)
    stats = svc.get_stats()
    assert stats.reason_bucket_counts.get('concurrency_limit') == 1


def test_service_stats_include_recent_rates_and_duration(tmp_path: Path):
    svc = build_service(tmp_path)
    t1 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T1',
            description='d1',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    t2 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T2',
            description='d2',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    t3 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T3',
            description='d3',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    svc.repository.update_task_status(t1.task_id, status='passed', reason='passed', rounds_completed=1)
    svc.repository.update_task_status(t2.task_id, status='failed_gate', reason='review_blocker', rounds_completed=1)
    svc.repository.update_task_status(t3.task_id, status='failed_system', reason='workflow_error: command_timeout provider=codex', rounds_completed=0)

    svc.repository.items[t1.task_id]['created_at'] = '2026-02-12T00:00:00+00:00'
    svc.repository.items[t1.task_id]['updated_at'] = '2026-02-12T00:01:00+00:00'
    svc.repository.items[t2.task_id]['created_at'] = '2026-02-12T00:00:00+00:00'
    svc.repository.items[t2.task_id]['updated_at'] = '2026-02-12T00:03:00+00:00'
    svc.repository.items[t3.task_id]['created_at'] = '2026-02-12T00:00:00+00:00'
    svc.repository.items[t3.task_id]['updated_at'] = '2026-02-12T00:02:00+00:00'

    stats = svc.get_stats()
    assert stats.recent_terminal_total == 3
    assert stats.pass_rate_50 == 1 / 3
    assert stats.failed_gate_rate_50 == 1 / 3
    assert stats.failed_system_rate_50 == 1 / 3
    assert stats.mean_task_duration_seconds_50 == 120.0


def test_service_proposal_review_reviewer_failure_degrades_to_unknown(tmp_path: Path):
    svc = OrchestratorService(
        repository=InMemoryTaskRepository(),
        artifact_store=ArtifactStore(tmp_path / '.agents'),
        workflow_engine=ProposalOnlyWorkflowEngine(),
    )
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=0,
            title='Proposal reviewer resilience',
            description='proposal stage should survive single reviewer failure',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B', 'gemini#review-C'],
        )
    )

    started = svc.start_task(created.task_id)
    assert started.status.value == 'failed_gate'
    assert started.last_gate_reason == 'proposal_consensus_not_reached'

    events = svc.list_events(created.task_id)
    assert any(
        e['type'] == 'proposal_precheck_review_error'
        and str(e.get('payload', {}).get('participant')) == 'gemini#review-C'
        for e in events
    )
    assert any(
        e['type'] in {'proposal_precheck_review', 'proposal_review'}
        and str(e.get('payload', {}).get('participant')) == 'gemini#review-C'
        and str(e.get('payload', {}).get('verdict')) == 'unknown'
        for e in events
    )
    assert any(e['type'] == 'proposal_consensus_failed' for e in events)


def test_service_manual_mode_uses_reviewer_first_before_author_proposal(tmp_path: Path):
    engine = ProposalOrderWorkflowEngine()
    svc = OrchestratorService(
        repository=InMemoryTaskRepository(),
        artifact_store=ArtifactStore(tmp_path / '.agents'),
        workflow_engine=engine,
    )
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=0,
            title='Reviewer-first proposal order',
            description='reviewer should go first in manual mode',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
        )
    )

    started = svc.start_task(created.task_id)
    assert started.status.value == 'waiting_manual'

    calls = engine.runner.calls
    assert calls
    assert calls[0] == 'claude#review-B'
    assert 'codex#author-A' in calls
    assert calls.index('codex#author-A') < len(calls) - 1


def test_service_self_loop_auto_mode_still_uses_reviewer_consensus_first(tmp_path: Path):
    engine = AutoConsensusWorkflowEngine()
    svc = OrchestratorService(
        repository=InMemoryTaskRepository(),
        artifact_store=ArtifactStore(tmp_path / '.agents'),
        workflow_engine=engine,
    )
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Self-loop reviewer-first',
            description='review and fix',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            max_rounds=1,
        )
    )

    started = svc.start_task(created.task_id)
    assert started.status.value == 'passed'

    calls = engine.runner.calls
    assert calls
    assert calls[0] == 'claude#review-B'
    assert 'codex#author-A' in calls

    events = svc.list_events(created.task_id)
    assert any(e['type'] == 'author_confirmation_required' for e in events)
    assert any(
        e['type'] == 'author_decision'
        and str(e.get('payload', {}).get('decision')) == 'approved'
        for e in events
    )


def test_service_manual_mode_fails_fast_when_precheck_reviews_unavailable(tmp_path: Path):
    engine = ProposalPrecheckUnavailableWorkflowEngine()
    svc = OrchestratorService(
        repository=InMemoryTaskRepository(),
        artifact_store=ArtifactStore(tmp_path / '.agents'),
        workflow_engine=engine,
    )
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=0,
            title='Review audit precheck unavailable',
            description='scan repository bugs',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            max_rounds=1,
        )
    )

    started = svc.start_task(created.task_id)
    assert started.status.value == 'failed_gate'
    assert started.last_gate_reason == 'proposal_precheck_unavailable'
    assert engine.runner.calls == ['claude#review-B']

    events = svc.list_events(created.task_id)
    assert any(e['type'] == 'proposal_precheck_review_error' for e in events)
    assert any(e['type'] == 'proposal_precheck_unavailable' for e in events)
    assert not any(e['type'] == 'proposal_discussion_started' for e in events)


def test_service_manual_mode_requires_actionable_proposal_review_before_execution(tmp_path: Path):
    engine = ProposalReviewUnavailableWorkflowEngine()
    svc = OrchestratorService(
        repository=InMemoryTaskRepository(),
        artifact_store=ArtifactStore(tmp_path / '.agents'),
        workflow_engine=engine,
    )
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=0,
            title='Fix concrete parser bug',
            description='review and implement a parser fix',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            max_rounds=1,
        )
    )

    started = svc.start_task(created.task_id)
    assert started.status.value == 'failed_gate'
    assert started.last_gate_reason == 'proposal_consensus_not_reached'
    assert engine.runner.calls[0] == 'claude#review-B'

    events = svc.list_events(created.task_id)
    assert any(e['type'] == 'proposal_review_unavailable' for e in events)
    assert not any(e['type'] == 'task_started' for e in events)


def test_service_manual_mode_proposal_reviewer_timeout_follows_participant_timeout(tmp_path: Path):
    engine = ProposalOrderWorkflowEngine()
    engine.participant_timeout_seconds = 240
    svc = OrchestratorService(
        repository=InMemoryTaskRepository(),
        artifact_store=ArtifactStore(tmp_path / '.agents'),
        workflow_engine=engine,
    )
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=0,
            title='Reviewer timeout cap',
            description='reviewer should use capped timeout in proposal phase',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            max_rounds=1,
        )
    )

    started = svc.start_task(created.task_id)
    assert started.status.value == 'waiting_manual'

    # reviewer-precheck, author-discussion, reviewer-proposal
    assert engine.runner.timeouts == [240, 240, 240]

    events = svc.list_events(created.task_id)
    review_started = [
        e for e in events
        if e['type'] in {'proposal_precheck_review_started', 'proposal_review_started'}
    ]
    assert review_started
    assert all(int(e.get('payload', {}).get('timeout_seconds') or 0) == 240 for e in review_started)


def test_service_proposal_review_prompt_supports_audit_depth_guidance(tmp_path: Path):
    cfg = RunConfig(
        task_id='t-proposal-short',
        title='Proposal short format',
        description='proposal prompt style',
        author=parse_participant_id('codex#author-A'),
        reviewers=[parse_participant_id('claude#review-B')],
        evolution_level=0,
        evolve_until=None,
        cwd=tmp_path,
        max_rounds=1,
        test_command='py -m pytest -q',
        lint_command='py -m ruff check .',
        conversation_language='zh',
        plain_mode=True,
    )
    prompt = OrchestratorService._proposal_review_prompt(
        cfg,
        discussion_output='检查不完善点并提出改进方向。',
        stage='proposal_precheck_review',
    )
    assert 'repository checks as needed' in prompt
    assert 'Keep output concise but complete enough to justify verdict.' in prompt
    assert 'VERDICT: NO_BLOCKER or VERDICT: BLOCKER or VERDICT: UNKNOWN' in prompt



def test_service_scope_ambiguity_is_non_blocking_for_proposal_review(tmp_path: Path):
    cfg = RunConfig(
        task_id='t-proposal-scope-ambiguity',
        title='Review project bugs',
        description='check and improve quality',
        author=parse_participant_id('codex#author-A'),
        reviewers=[parse_participant_id('claude#review-B')],
        evolution_level=0,
        evolve_until=None,
        cwd=tmp_path,
        max_rounds=1,
        test_command='py -m pytest -q',
        lint_command='py -m ruff check .',
        conversation_language='zh',
        plain_mode=True,
    )
    verdict, output = OrchestratorService._normalize_proposal_reviewer_result(
        config=cfg,
        stage='proposal_precheck_review',
        verdict='unknown',
        review_text='VERDICT: UNKNOWN\n问题: 计划太泛，缺少具体范围。\n影响: 无法判断改动。\n下一步: 请先说明具体bug。',
    )
    assert verdict == 'no_blocker'
    assert 'Scope ambiguity is non-blocking' in output


def test_service_clear_project_history_removes_terminal_tasks_only(tmp_path: Path):
    svc = build_service(tmp_path)
    project_root = tmp_path / 'repo'
    project_root.mkdir()

    t1 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='History A',
            description='terminal pass',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            workspace_path=str(project_root),
        )
    )
    t2 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='History B',
            description='terminal fail',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            workspace_path=str(project_root),
        )
    )
    t3 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='History C',
            description='active',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            workspace_path=str(project_root),
        )
    )

    svc.repository.update_task_status(t1.task_id, status='passed', reason='passed', rounds_completed=1)
    svc.repository.update_task_status(t2.task_id, status='failed_gate', reason='review_blocker', rounds_completed=1)
    svc.repository.update_task_status(t3.task_id, status='running', reason=None, rounds_completed=0)

    svc.artifact_store.append_event(t1.task_id, {'type': 'seed'})
    svc.artifact_store.append_event(t2.task_id, {'type': 'seed'})
    svc.artifact_store.append_event(t3.task_id, {'type': 'seed'})

    result = svc.clear_project_history(project_path=str(project_root))
    assert result['project_path'] == str(project_root)
    assert result['deleted_tasks'] == 2
    assert result['deleted_artifacts'] == 2
    assert result['skipped_non_terminal'] == 1

    assert svc.get_task(t1.task_id) is None
    assert svc.get_task(t2.task_id) is None
    assert svc.get_task(t3.task_id) is not None

    thread_root = svc.artifact_store.root / 'threads'
    assert not (thread_root / t1.task_id).exists()
    assert not (thread_root / t2.task_id).exists()
    assert (thread_root / t3.task_id).exists()


def test_create_task_rejects_invalid_author_participant(tmp_path: Path):
    svc = build_service(tmp_path)
    with pytest.raises(ValueError, match='invalid author_participant'):
        svc.create_task(
            CreateTaskInput(
                sandbox_mode=False,
                self_loop_mode=1,
                title='Bad author',
                description='desc',
                author_participant='no-hash-here',
                reviewer_participants=['codex#rev'],
                workspace_path=str(tmp_path),
            )
        )


def test_create_task_rejects_invalid_reviewer_participant(tmp_path: Path):
    svc = build_service(tmp_path)
    with pytest.raises(ValueError, match=r'invalid reviewer_participants\[1\]'):
        svc.create_task(
            CreateTaskInput(
                sandbox_mode=False,
                self_loop_mode=1,
                title='Bad reviewer',
                description='desc',
                author_participant='claude#author',
                reviewer_participants=['codex#ok', 'bad-format'],
                workspace_path=str(tmp_path),
            )
        )
