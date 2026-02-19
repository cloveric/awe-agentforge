from __future__ import annotations

import os
from pathlib import Path

import pytest

from awe_agentcheck.adapters import AdapterResult
from awe_agentcheck.participants import parse_participant_id, set_extra_providers
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


class ProposalRunnerRetryThenPass:
    def __init__(self):
        self.calls: list[str] = []
        self.review_attempts = 0

    def run(self, *, participant, prompt, cwd, timeout_seconds=900, **kwargs):
        pid = str(participant.participant_id)
        self.calls.append(pid)
        if pid.startswith('codex#author'):
            return AdapterResult(
                output='Author revised proposal after reviewer blocker feedback.',
                verdict='unknown',
                next_action=None,
                returncode=0,
                duration_seconds=0.1,
            )
        if 'Stage: proposal review.' in str(prompt):
            self.review_attempts += 1
            verdict = 'blocker' if self.review_attempts == 1 else 'no_blocker'
            return AdapterResult(
                output=f'VERDICT: {verdict.upper()}\nReviewer attempt={self.review_attempts}',
                verdict=verdict,
                next_action=None,
                returncode=0,
                duration_seconds=0.1,
            )
        return AdapterResult(
            output='VERDICT: NO_BLOCKER\nReviewer precheck ok.',
            verdict='no_blocker',
            next_action=None,
            returncode=0,
            duration_seconds=0.1,
        )


class ProposalRetryThenPassWorkflowEngine:
    def __init__(self):
        self.runner = ProposalRunnerRetryThenPass()
        self.participant_timeout_seconds = 60


class ProposalRunnerAlwaysBlocker:
    def __init__(self):
        self.calls: list[str] = []
        self.review_attempts = 0

    def run(self, *, participant, prompt, cwd, timeout_seconds=900, **kwargs):
        pid = str(participant.participant_id)
        self.calls.append(pid)
        if pid.startswith('codex#author'):
            return AdapterResult(
                output=f'Author proposal iteration {self.review_attempts + 1}.',
                verdict='unknown',
                next_action=None,
                returncode=0,
                duration_seconds=0.1,
            )
        if 'Stage: proposal review.' in str(prompt):
            self.review_attempts += 1
            return AdapterResult(
                output='VERDICT: BLOCKER\nIssue: unresolved auth validation gap persists.',
                verdict='blocker',
                next_action=None,
                returncode=0,
                duration_seconds=0.1,
            )
        return AdapterResult(
            output='VERDICT: NO_BLOCKER\nPrecheck scope established.',
            verdict='no_blocker',
            next_action=None,
            returncode=0,
            duration_seconds=0.1,
        )


class ProposalAlwaysBlockerWorkflowEngine:
    def __init__(self):
        self.runner = ProposalRunnerAlwaysBlocker()
        self.participant_timeout_seconds = 60


class ProposalRunnerRepeatedRoundIssue:
    def __init__(self):
        self.calls: list[str] = []

    def run(self, *, participant, prompt, cwd, timeout_seconds=900, **kwargs):
        pid = str(participant.participant_id)
        self.calls.append(pid)
        if pid.startswith('codex#author'):
            return AdapterResult(
                output='Author proposal revision focused on auth validation coverage.',
                verdict='unknown',
                next_action=None,
                returncode=0,
                duration_seconds=0.1,
            )
        if 'Stage: proposal review.' in str(prompt):
            return AdapterResult(
                output='VERDICT: NO_BLOCKER\nIssue focus remains unchanged: auth validation coverage.',
                verdict='no_blocker',
                next_action=None,
                returncode=0,
                duration_seconds=0.1,
            )
        return AdapterResult(
            output='VERDICT: NO_BLOCKER\nPrecheck scope established.',
            verdict='no_blocker',
            next_action=None,
            returncode=0,
            duration_seconds=0.1,
        )


class ProposalRepeatedRoundIssueWorkflowEngine:
    def __init__(self):
        self.runner = ProposalRunnerRepeatedRoundIssue()
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


def test_service_create_task_forces_fresh_sandbox_for_multi_round_no_auto_merge(tmp_path: Path):
    project = tmp_path / 'proj-force-sandbox'
    project.mkdir()
    (project / 'README.md').write_text('hello\n', encoding='utf-8')
    requested = tmp_path / 'requested-lab'
    requested.mkdir()

    svc = build_service(tmp_path)
    task = svc.create_task(
        CreateTaskInput(
            title='Force sandbox',
            description='multi round no auto merge',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            workspace_path=str(project),
            sandbox_mode=False,
            sandbox_workspace_path=str(requested),
            auto_merge=False,
            max_rounds=2,
            self_loop_mode=1,
        )
    )

    assert task.sandbox_mode is True
    assert task.workspace_path != str(project)
    assert task.workspace_path != str(requested)
    assert Path(task.workspace_path).exists()
    assert '-lab' in Path(task.workspace_path).as_posix()


def test_service_create_task_accepts_provider_models_and_team_agent_toggles(tmp_path: Path):
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
            codex_multi_agents=True,
        )
    )
    assert task.provider_models.get('claude') == 'claude-sonnet-4-5'
    assert task.provider_models.get('codex') == 'gpt-5-codex'
    assert task.provider_models.get('gemini') == 'gemini-2.5-pro'
    assert task.claude_team_agents is True
    assert task.codex_multi_agents is True


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
    assert 'gemini-3-flash-preview' in catalog.get('gemini', [])
    assert 'gemini-3-pro-preview' in catalog.get('gemini', [])
    assert 'gemini-flash-latest' in catalog.get('gemini', [])
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


def test_service_sandbox_ignore_filters_windows_reserved_device_names():
    assert OrchestratorService._is_sandbox_ignored('nul')
    assert OrchestratorService._is_sandbox_ignored('NUL.txt')
    assert OrchestratorService._is_sandbox_ignored('src/COM1.log')
    assert OrchestratorService._is_sandbox_ignored('docs/lpt9.md')
    assert OrchestratorService._is_sandbox_ignored('aux.')
    assert not OrchestratorService._is_sandbox_ignored('null.txt')
    assert not OrchestratorService._is_sandbox_ignored('docs/com10.log')
    assert not OrchestratorService._is_sandbox_ignored('src/lpt-notes.txt')


def test_service_sandbox_bootstrap_skips_windows_reserved_filenames(tmp_path: Path, monkeypatch):
    project = tmp_path / 'proj-reserved-bootstrap'
    sandbox = tmp_path / 'sandbox-reserved-bootstrap'
    project.mkdir()
    sandbox.mkdir()

    copied: list[tuple[Path, Path]] = []

    def fake_walk(_root):
        yield str(project), [], ['README.md', 'nul', 'COM1.txt', 'notes.txt']

    def fake_copy2(src, dst):
        copied.append((Path(src), Path(dst)))

    monkeypatch.setattr('awe_agentcheck.service.os.walk', fake_walk)
    monkeypatch.setattr('awe_agentcheck.service.shutil.copy2', fake_copy2)

    OrchestratorService._bootstrap_sandbox_workspace(project, sandbox)

    copied_rel = sorted(dst.relative_to(sandbox).as_posix() for _, dst in copied)
    assert copied_rel == ['README.md', 'notes.txt']


def test_service_sandbox_bootstrap_skips_claude_directory(tmp_path: Path):
    project = tmp_path / 'proj-claude-filter'
    sandbox = tmp_path / 'sandbox-claude-filter'
    project.mkdir()
    sandbox.mkdir()
    (project / 'README.md').write_text('keep\n', encoding='utf-8')
    claude_dir = project / '.claude'
    claude_dir.mkdir()
    (claude_dir / 'memory.json').write_text('{"k":"v"}\n', encoding='utf-8')

    OrchestratorService._bootstrap_sandbox_workspace(project, sandbox)

    assert (sandbox / 'README.md').exists()
    assert not (sandbox / '.claude').exists()


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


def test_service_author_revise_requeues_for_next_consensus(tmp_path: Path):
    project = tmp_path / 'revise-proj'
    project.mkdir()
    engine = FakeWorkflowEngine()
    svc = build_service(tmp_path, workflow_engine=engine)
    task = svc.create_task(
        CreateTaskInput(
            title='Revise path',
            description='needs manual revise',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
            sandbox_mode=False,
            self_loop_mode=0,
        )
    )
    waiting = svc.start_task(task.task_id)
    assert waiting.status.value == 'waiting_manual'

    revised = svc.submit_author_decision(task.task_id, decision='revise', note='Please focus on auth and evidence paths')
    assert revised.status.value == 'queued'
    assert revised.last_gate_reason == 'author_feedback_requested'

    events = svc.list_events(task.task_id)
    assert any(e['type'] == 'author_feedback_requested' for e in events)


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


def test_service_multi_round_no_auto_merge_writes_round_artifacts_and_supports_promote(tmp_path: Path):
    project = tmp_path / 'project-round-artifacts'
    project.mkdir()
    (project / 'README.md').write_text('base\n', encoding='utf-8')

    svc = build_service(tmp_path, workflow_engine=FakeWorkflowEngineTwoRoundsWithChanges())
    created = svc.create_task(
        CreateTaskInput(
            title='Round artifacts',
            description='capture patches and promote selected round',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            workspace_path=str(project),
            sandbox_mode=False,
            auto_merge=False,
            max_rounds=2,
            self_loop_mode=1,
        )
    )
    assert created.sandbox_mode is True

    result = svc.start_task(created.task_id)
    assert result.status.value == 'passed'

    rounds_root = tmp_path / '.agents' / 'threads' / created.task_id / 'artifacts' / 'rounds'
    assert (rounds_root / 'round-1.patch').exists()
    assert (rounds_root / 'round-1.md').exists()
    assert (rounds_root / 'round-2.patch').exists()
    assert (rounds_root / 'round-2.md').exists()
    assert (rounds_root / 'round-000-snapshot').exists()
    assert (rounds_root / 'round-001-snapshot').exists()
    assert (rounds_root / 'round-002-snapshot').exists()

    events = svc.list_events(created.task_id)
    ready_events = [e for e in events if e['type'] == 'round_artifact_ready']
    assert len(ready_events) >= 2
    assert {int(e.get('round') or 0) for e in ready_events} >= {1, 2}

    promoted_round_1 = svc.promote_selected_round(
        created.task_id,
        round_number=1,
        merge_target_path=str(project),
    )
    assert promoted_round_1['round'] == 1
    assert (project / 'src' / 'round.txt').read_text(encoding='utf-8') == 'round-1\n'

    promoted_round_2 = svc.promote_selected_round(
        created.task_id,
        round_number=2,
        merge_target_path=str(project),
    )
    assert promoted_round_2['round'] == 2
    assert (project / 'src' / 'round.txt').read_text(encoding='utf-8') == 'round-2\n'


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
    assert started.status.value == 'waiting_manual'
    assert started.last_gate_reason == 'author_confirmation_required'

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
    assert any(e['type'] == 'proposal_review_partial' for e in events)
    assert not any(e['type'] == 'proposal_consensus_failed' for e in events)


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
    assert started.last_gate_reason == 'proposal_review_unavailable'
    assert engine.runner.calls[0] == 'claude#review-B'

    events = svc.list_events(created.task_id)
    assert any(e['type'] == 'proposal_review_unavailable' for e in events)
    assert not any(e['type'] == 'task_started' for e in events)


def test_service_manual_mode_keeps_retrying_same_round_until_proposal_consensus(tmp_path: Path):
    engine = ProposalRetryThenPassWorkflowEngine()
    svc = OrchestratorService(
        repository=InMemoryTaskRepository(),
        artifact_store=ArtifactStore(tmp_path / '.agents'),
        workflow_engine=engine,
    )
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=0,
            title='Retry proposal until consensus',
            description='reviewer blocks first, then agrees',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            max_rounds=1,
        )
    )

    started = svc.start_task(created.task_id)
    assert started.status.value == 'waiting_manual'
    assert started.last_gate_reason == 'author_confirmation_required'

    events = svc.list_events(created.task_id)
    retries = [e for e in events if e['type'] == 'proposal_consensus_retry']
    reached = [e for e in events if e['type'] == 'proposal_consensus_reached']
    assert retries
    assert reached
    assert int(reached[-1].get('payload', {}).get('attempt', 0)) >= 2


def test_service_manual_mode_marks_pending_when_proposal_consensus_stalls_in_round(tmp_path: Path):
    engine = ProposalAlwaysBlockerWorkflowEngine()
    svc = OrchestratorService(
        repository=InMemoryTaskRepository(),
        artifact_store=ArtifactStore(tmp_path / '.agents'),
        workflow_engine=engine,
    )
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=0,
            title='Stall in same round',
            description='reviewer keeps finding same blocking issue',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            max_rounds=1,
        )
    )

    started = svc.start_task(created.task_id)
    assert started.status.value == 'waiting_manual'
    assert started.last_gate_reason == 'proposal_consensus_stalled_in_round'

    events = svc.list_events(created.task_id)
    stall_events = [e for e in events if e['type'] == 'proposal_consensus_stalled']
    assert stall_events
    payload = stall_events[-1].get('payload', {})
    assert str(payload.get('stall_kind')) == 'in_round'
    assert int(payload.get('attempt') or 0) >= 10
    assert int(payload.get('retry_limit') or 0) == 10
    assert any(e['type'] == 'author_confirmation_required' for e in events)

    stall_artifact = (
        tmp_path
        / '.agents'
        / 'threads'
        / created.task_id
        / 'artifacts'
        / 'consensus_stall.json'
    )
    assert stall_artifact.exists()


def test_service_manual_mode_marks_pending_when_same_issue_repeats_across_rounds(tmp_path: Path):
    engine = ProposalRepeatedRoundIssueWorkflowEngine()
    svc = OrchestratorService(
        repository=InMemoryTaskRepository(),
        artifact_store=ArtifactStore(tmp_path / '.agents'),
        workflow_engine=engine,
    )
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=0,
            title='Repeated issue across rounds',
            description='same issue should not loop forever across rounds',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            max_rounds=5,
        )
    )

    started = svc.start_task(created.task_id)
    assert started.status.value == 'waiting_manual'
    assert started.last_gate_reason == 'proposal_consensus_stalled_across_rounds'
    assert int(started.rounds_completed) >= 4

    events = svc.list_events(created.task_id)
    stall_events = [e for e in events if e['type'] == 'proposal_consensus_stalled']
    assert stall_events
    payload = stall_events[-1].get('payload', {})
    assert str(payload.get('stall_kind')) == 'across_rounds'
    assert int(payload.get('repeated_rounds') or 0) >= 4
    assert str(payload.get('round_signature') or '').strip()
    assert any(e['type'] == 'author_confirmation_required' for e in events)


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


def test_service_clear_project_history_include_non_terminal_removes_all_scoped_tasks(tmp_path: Path):
    svc = build_service(tmp_path)
    project_root = tmp_path / 'repo-all-clear'
    project_root.mkdir()

    t1 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='History all-clear A',
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
            title='History all-clear B',
            description='waiting manual',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            workspace_path=str(project_root),
        )
    )
    t3 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='History all-clear C',
            description='running',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            workspace_path=str(project_root),
        )
    )

    svc.repository.update_task_status(t1.task_id, status='passed', reason='passed', rounds_completed=1)
    svc.repository.update_task_status(t2.task_id, status='waiting_manual', reason='author_confirmation_required', rounds_completed=0)
    svc.repository.update_task_status(t3.task_id, status='running', reason=None, rounds_completed=0)

    svc.artifact_store.append_event(t1.task_id, {'type': 'seed'})
    svc.artifact_store.append_event(t2.task_id, {'type': 'seed'})
    svc.artifact_store.append_event(t3.task_id, {'type': 'seed'})

    result = svc.clear_project_history(project_path=str(project_root), include_non_terminal=True)
    assert result['project_path'] == str(project_root)
    assert result['deleted_tasks'] == 3
    assert result['deleted_artifacts'] == 3
    assert result['skipped_non_terminal'] == 0

    assert svc.get_task(t1.task_id) is None
    assert svc.get_task(t2.task_id) is None
    assert svc.get_task(t3.task_id) is None

    thread_root = svc.artifact_store.root / 'threads'
    assert not (thread_root / t1.task_id).exists()
    assert not (thread_root / t2.task_id).exists()
    assert not (thread_root / t3.task_id).exists()


def test_service_clear_project_history_removes_history_only_artifact_records(tmp_path: Path):
    svc = build_service(tmp_path)
    project_root = tmp_path / 'repo-history-only'
    project_root.mkdir()

    history_only_id = 'task-historyonly01'
    svc.artifact_store.update_state(
        history_only_id,
        {
            'task_id': history_only_id,
            'project_path': str(project_root),
            'workspace_path': str(project_root),
            'status': 'passed',
        },
    )
    svc.artifact_store.append_event(
        history_only_id,
        {
            'type': 'gate_passed',
            'reason': 'passed',
        },
    )

    assert (svc.artifact_store.root / 'threads' / history_only_id).exists()
    assert svc.repository.get_task(history_only_id) is None

    result = svc.clear_project_history(project_path=str(project_root), include_non_terminal=False)
    assert result['deleted_tasks'] == 0
    assert result['deleted_artifacts'] == 1
    assert result['skipped_non_terminal'] == 0

    assert not (svc.artifact_store.root / 'threads' / history_only_id).exists()
    assert svc.list_project_history(project_path=str(project_root), limit=20) == []


def test_service_create_task_uses_uuid_like_task_id_in_inmemory_repo(tmp_path: Path):
    svc = build_service(tmp_path)
    project_root = tmp_path / 'repo-id-format'
    project_root.mkdir()

    a = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='ID format A',
            description='check id format',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            workspace_path=str(project_root),
        )
    )
    b = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='ID format B',
            description='check id format',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            workspace_path=str(project_root),
        )
    )

    for task_id in [a.task_id, b.task_id]:
        assert task_id.startswith('task-')
        suffix = task_id[5:]
        assert len(suffix) == 12
        int(suffix, 16)
    assert a.task_id != b.task_id


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


def test_service_policy_templates_return_recommended_profile(tmp_path: Path):
    svc = build_service(tmp_path)
    project = tmp_path / 'policy-repo'
    project.mkdir()
    (project / 'src').mkdir()
    for i in range(8):
        (project / 'src' / f'module_{i}.py').write_text('print(1)\n', encoding='utf-8')
    (project / 'deploy-prod.yaml').write_text('kind: Deployment\n', encoding='utf-8')

    payload = svc.get_policy_templates(workspace_path=str(project))
    assert payload['recommended_template'] in {'balanced-default', 'safe-review', 'rapid-fix'}
    assert payload['profile']['exists'] is True
    assert payload['profile']['workspace_path']
    assert payload['profile']['file_count'] >= 1
    ids = {item['id'] for item in payload['templates']}
    assert {'balanced-default', 'safe-review', 'rapid-fix', 'deep-evolve'}.issubset(ids)


def test_service_analytics_reports_failure_taxonomy_and_reviewer_drift(tmp_path: Path):
    svc = build_service(tmp_path)
    project = tmp_path / 'analytics-repo'
    project.mkdir()
    (project / 'README.md').write_text('base\n', encoding='utf-8')

    task = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            auto_merge=False,
            title='Analytics task',
            description='collect analytics',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            workspace_path=str(project),
            max_rounds=1,
        )
    )
    svc.repository.update_task_status(task.task_id, status='failed_gate', reason='review_blocker', rounds_completed=1)
    svc.repository.append_event(
        task.task_id,
        event_type='review',
        payload={'participant': 'claude#review-B', 'verdict': 'blocker', 'output': 'security issue'},
        round_number=1,
    )

    analytics = svc.get_analytics(limit=100)
    assert analytics['window_tasks'] >= 1
    assert analytics['window_failed_gate'] >= 1
    assert any(item['bucket'] == 'review_blocker' for item in analytics['failure_taxonomy'])
    assert any(item['participant'] == 'claude#review-B' for item in analytics['reviewer_drift'])


def test_service_build_github_summary_includes_markdown_and_artifacts(tmp_path: Path):
    svc = build_service(tmp_path)
    project = tmp_path / 'gh-summary-repo'
    project.mkdir()
    (project / 'README.md').write_text('base\n', encoding='utf-8')

    task = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            auto_merge=False,
            title='GitHub summary',
            description='summary generation',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            workspace_path=str(project),
        )
    )
    svc.repository.update_task_status(task.task_id, status='failed_gate', reason='review_blocker', rounds_completed=1)
    svc.artifact_store.write_summary(task.task_id, 'Found blocker in API auth flow.\n')

    summary = svc.build_github_pr_summary(task.task_id)
    assert summary['task_id'] == task.task_id
    assert 'AWE-AgentForge Task Summary' in summary['summary_markdown']
    assert 'Core Findings' in summary['summary_markdown']
    assert isinstance(summary['artifacts'], list)


def test_service_promote_selected_round_blocks_on_promotion_guard(tmp_path: Path, monkeypatch):
    project = tmp_path / 'repo-promote-guard'
    project.mkdir()
    (project / 'README.md').write_text('base\n', encoding='utf-8')
    svc = build_service(tmp_path, workflow_engine=FakeWorkflowEngineTwoRoundsWithChanges())

    created = svc.create_task(
        CreateTaskInput(
            title='Promote guard task',
            description='promotion guard blocked',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            workspace_path=str(project),
            sandbox_mode=False,
            auto_merge=False,
            max_rounds=2,
            self_loop_mode=1,
        )
    )
    started = svc.start_task(created.task_id)
    assert started.status.value == 'passed'

    monkeypatch.setattr(
        svc,
        '_evaluate_promotion_guard',
        lambda *, target_root: {
            'enabled': True,
            'target_path': str(target_root),
            'allowed_branches': ['main'],
            'require_clean': True,
            'is_git_repo': True,
            'branch': 'feature/demo',
            'worktree_clean': True,
            'remote_origin': None,
            'guard_allowed': False,
            'guard_reason': 'branch_not_allowed:feature/demo',
        },
    )
    with pytest.raises(InputValidationError, match='promotion guard blocked'):
        svc.promote_selected_round(created.task_id, round_number=1, merge_target_path=str(project))


def test_service_auto_merge_fails_when_promotion_guard_blocks(tmp_path: Path, monkeypatch):
    project = tmp_path / 'repo-auto-merge-guard'
    project.mkdir()
    (project / 'README.md').write_text('base\n', encoding='utf-8')
    svc = build_service(tmp_path, workflow_engine=FakeWorkflowEngineWithFileChange())

    created = svc.create_task(
        CreateTaskInput(
            title='Auto merge guard task',
            description='guard blocks merge',
            author_participant='codex#author-A',
            reviewer_participants=['claude#review-B'],
            workspace_path=str(project),
            sandbox_mode=False,
            auto_merge=True,
            self_loop_mode=1,
            max_rounds=1,
        )
    )
    monkeypatch.setattr(
        svc,
        '_evaluate_promotion_guard',
        lambda *, target_root: {
            'enabled': True,
            'target_path': str(target_root),
            'allowed_branches': ['main'],
            'require_clean': True,
            'is_git_repo': True,
            'branch': 'feature/demo',
            'worktree_clean': True,
            'remote_origin': None,
            'guard_allowed': False,
            'guard_reason': 'branch_not_allowed:feature/demo',
        },
    )

    finished = svc.start_task(created.task_id)
    assert finished.status.value == 'failed_gate'
    assert 'promotion_guard_blocked' in str(finished.last_gate_reason or '')


def test_service_accepts_extra_registered_provider_participants(tmp_path: Path):
    svc = build_service(tmp_path)
    set_extra_providers({'qwen'})
    try:
        task = svc.create_task(
            CreateTaskInput(
                sandbox_mode=False,
                self_loop_mode=1,
                title='Extra provider task',
                description='use qwen adapter',
                author_participant='qwen#author-A',
                reviewer_participants=['claude#review-B'],
                workspace_path=str(tmp_path),
            )
        )
        assert task.author_participant == 'qwen#author-A'
    finally:
        set_extra_providers(set())
