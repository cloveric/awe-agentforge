from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from awe_agentcheck.service_layers.task_management import TaskManagementService


class _ValidationError(Exception):
    def __init__(self, message: str, *, field: str):
        super().__init__(message)
        self.field = field


class _Repo:
    def __init__(self):
        self.records = []
        self.rows = {}

    def create_task_record(self, record):
        self.records.append(record)
        row = {
            'task_id': 'task-1',
            'status': 'queued',
            'rounds_completed': 0,
            'cancel_requested': False,
            'conversation_language': record.conversation_language,
            'provider_models': dict(record.provider_models),
            'provider_model_params': dict(record.provider_model_params),
            'participant_models': dict(record.participant_models),
            'participant_model_params': dict(record.participant_model_params),
            'claude_team_agents': bool(record.claude_team_agents),
            'codex_multi_agents': bool(record.codex_multi_agents),
            'claude_team_agents_overrides': dict(record.claude_team_agents_overrides or {}),
            'codex_multi_agents_overrides': dict(record.codex_multi_agents_overrides or {}),
            'repair_mode': record.repair_mode,
            'plain_mode': record.plain_mode,
            'stream_mode': record.stream_mode,
            'debate_mode': record.debate_mode,
            'sandbox_mode': record.sandbox_mode,
            'sandbox_workspace_path': record.sandbox_workspace_path,
            'sandbox_generated': record.sandbox_generated,
            'sandbox_cleanup_on_pass': record.sandbox_cleanup_on_pass,
            'self_loop_mode': record.self_loop_mode,
            'project_path': record.project_path,
            'auto_merge': record.auto_merge,
            'merge_target_path': record.merge_target_path,
            'workspace_fingerprint': dict(record.workspace_fingerprint or {}),
        }
        self.rows[row['task_id']] = row
        return row

    def list_tasks(self, *, limit=100):
        return list(self.rows.values())[:limit]

    def get_task(self, task_id: str):
        return self.rows.get(task_id)


class _Artifacts:
    def __init__(self):
        self.created = []
        self.states = []

    def create_task_workspace(self, task_id: str):
        self.created.append(task_id)
        return {'task_id': task_id}

    def update_state(self, task_id: str, payload: dict):
        self.states.append((task_id, dict(payload)))


def _payload(workspace_path: str, **overrides):
    data = dict(
        title='test title',
        description='test desc',
        author_participant='codex#author-A',
        reviewer_participants=['claude#review-B'],
        evolution_level=1,
        evolve_until='',
        conversation_language='en',
        provider_models={},
        provider_model_params={},
        participant_models={},
        participant_model_params={},
        claude_team_agents=False,
        codex_multi_agents=False,
        claude_team_agents_overrides={},
        codex_multi_agents_overrides={},
        repair_mode='balanced',
        plain_mode=True,
        stream_mode=True,
        debate_mode=True,
        sandbox_mode=False,
        sandbox_workspace_path='',
        self_loop_mode=1,
        sandbox_cleanup_on_pass=False,
        auto_merge=True,
        merge_target_path='',
        max_rounds=1,
        workspace_path=workspace_path,
        test_command='py -m pytest -q',
        lint_command='py -m ruff check .',
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def test_task_management_create_task_success_and_accessors(tmp_path: Path):
    repo = _Repo()
    artifacts = _Artifacts()
    service = TaskManagementService(
        repository=repo,
        artifact_store=artifacts,
        validation_error_cls=_ValidationError,
    )
    row = service.create_task(_payload(str(tmp_path), sandbox_mode=False, auto_merge=False))
    assert row['task_id'] == 'task-1'
    assert artifacts.created == ['task-1']
    assert artifacts.states
    assert service.list_tasks(limit=10)
    assert service.get_task('task-1')['task_id'] == 'task-1'


def test_task_management_create_task_validation_errors(tmp_path: Path):
    repo = _Repo()
    artifacts = _Artifacts()
    service = TaskManagementService(
        repository=repo,
        artifact_store=artifacts,
        validation_error_cls=_ValidationError,
    )
    with pytest.raises(_ValidationError) as e_author:
        service.create_task(_payload(str(tmp_path), author_participant='bad'))
    assert e_author.value.field == 'author_participant'

    with pytest.raises(_ValidationError) as e_reviewer:
        service.create_task(_payload(str(tmp_path), reviewer_participants=['bad']))
    assert e_reviewer.value.field.startswith('reviewer_participants[')

    with pytest.raises(_ValidationError) as e_workspace:
        service.create_task(_payload(str(tmp_path / 'missing')))
    assert e_workspace.value.field == 'workspace_path'


def test_task_management_wrapper_normalizers_raise_validation_field_mapping(tmp_path: Path):
    service = TaskManagementService(
        repository=_Repo(),
        artifact_store=_Artifacts(),
        validation_error_cls=_ValidationError,
    )

    with pytest.raises(_ValidationError) as e1:
        service._normalize_evolve_until('bad-time')
    assert e1.value.field == 'evolve_until'

    with pytest.raises(_ValidationError) as e2:
        service._normalize_conversation_language('invalid', strict=True)
    assert e2.value.field == 'conversation_language'

    with pytest.raises(_ValidationError) as e3:
        service._normalize_repair_mode('invalid', strict=True)
    assert e3.value.field == 'repair_mode'

    with pytest.raises(_ValidationError) as e4:
        service._normalize_provider_models({'codex': ''})
    assert e4.value.field.startswith('provider_models[')

    with pytest.raises(_ValidationError) as e5:
        service._normalize_provider_model_params({'codex': ''})
    assert e5.value.field.startswith('provider_model_params[')

    known = {'codex#author-A', 'claude#review-B'}
    with pytest.raises(_ValidationError) as e6:
        service._normalize_participant_models({'unknown#x': 'm'}, known_participants=known)
    assert e6.value.field == 'participant_models'

    with pytest.raises(_ValidationError) as e7:
        service._normalize_participant_model_params({'unknown#x': 'p'}, known_participants=known)
    assert e7.value.field == 'participant_model_params'

    with pytest.raises(_ValidationError) as e8:
        service._normalize_participant_agent_overrides(
            {'claude#review-B': True},
            known_participants=known,
            required_provider='codex',
            field='codex_multi_agents_overrides',
        )
    assert e8.value.field.startswith('codex_multi_agents_overrides')

    with pytest.raises(_ValidationError) as e9:
        service._coerce_bool_override_value('maybe', field='flag')
    assert e9.value.field == 'flag'


def test_task_management_path_and_signature_helpers(tmp_path: Path, monkeypatch):
    service = TaskManagementService(
        repository=_Repo(),
        artifact_store=_Artifacts(),
        validation_error_cls=_ValidationError,
    )
    assert service._normalize_fingerprint_path('') == ''

    folder = tmp_path / 'root'
    folder.mkdir(parents=True, exist_ok=True)
    (folder / '.git').mkdir(exist_ok=True)
    (folder / 'a.txt').write_text('x', encoding='utf-8')
    (folder / '.env').write_text('x', encoding='utf-8')

    sig = service._workspace_head_signature(folder)
    assert sig and sig not in {'missing', 'empty', 'unreadable'}

    missing_sig = service._workspace_head_signature(folder / 'missing')
    assert missing_sig == 'missing'

    # Trigger unreadable branch.
    monkeypatch.setattr(Path, 'iterdir', lambda self: (_ for _ in ()).throw(OSError('boom')))
    assert service._workspace_head_signature(folder) == 'unreadable'


def test_task_management_sandbox_default_path_and_cleanup(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('AWE_SANDBOX_BASE', str(tmp_path / 'custom-base'))
    path1 = TaskManagementService._default_sandbox_path(tmp_path / 'repo')
    assert 'custom-base' in path1.replace('\\', '/')

    monkeypatch.delenv('AWE_SANDBOX_BASE', raising=False)
    monkeypatch.setenv('AWE_SANDBOX_USE_PUBLIC_BASE', '1')
    path2 = TaskManagementService._default_sandbox_path(tmp_path / 'repo')
    assert path2

    # cleanup early returns
    TaskManagementService._cleanup_create_task_sandbox_failure(
        sandbox_mode=False,
        sandbox_generated=True,
        project_root=tmp_path,
        sandbox_root=tmp_path / 'lab',
    )
    TaskManagementService._cleanup_create_task_sandbox_failure(
        sandbox_mode=True,
        sandbox_generated=False,
        project_root=tmp_path,
        sandbox_root=tmp_path / 'lab',
    )
    TaskManagementService._cleanup_create_task_sandbox_failure(
        sandbox_mode=True,
        sandbox_generated=True,
        project_root=tmp_path,
        sandbox_root=None,
    )

    lab = tmp_path / 'lab'
    lab.mkdir(parents=True, exist_ok=True)
    TaskManagementService._cleanup_create_task_sandbox_failure(
        sandbox_mode=True,
        sandbox_generated=True,
        project_root=tmp_path / 'repo',
        sandbox_root=lab,
    )
    assert not lab.exists()


def test_task_management_ignore_rules_and_windows_reserved_names():
    assert TaskManagementService._is_sandbox_ignored('.git/config') is True
    assert TaskManagementService._is_sandbox_ignored('node_modules/x') is True
    assert TaskManagementService._is_sandbox_ignored('foo.pyc') is True
    assert TaskManagementService._is_sandbox_ignored('.env.local') is True
    assert TaskManagementService._is_sandbox_ignored('private.key') is True
    assert TaskManagementService._is_sandbox_ignored('api-token.txt') is True
    assert TaskManagementService._is_sandbox_ignored('src/main.py') is False

    assert TaskManagementService._is_windows_reserved_device_name('CON') is True
    assert TaskManagementService._is_windows_reserved_device_name('lpt1.txt') is True
    assert TaskManagementService._is_windows_reserved_device_name('normal.txt') is False
    assert TaskManagementService._is_windows_reserved_device_name('') is False


def test_task_management_bootstrap_sandbox_workspace(tmp_path: Path, monkeypatch):
    project = tmp_path / 'project'
    project.mkdir(parents=True, exist_ok=True)
    (project / 'src').mkdir(parents=True, exist_ok=True)
    (project / 'src' / 'keep.py').write_text('x=1', encoding='utf-8')
    (project / '.git').mkdir(parents=True, exist_ok=True)
    (project / '.git' / 'config').write_text('x', encoding='utf-8')
    (project / '.env').write_text('SECRET=1', encoding='utf-8')

    sandbox = tmp_path / 'sandbox'
    sandbox.mkdir(parents=True, exist_ok=True)
    TaskManagementService._bootstrap_sandbox_workspace(project, sandbox)
    assert (sandbox / 'src' / 'keep.py').exists()
    assert not (sandbox / '.git').exists()
    assert not (sandbox / '.env').exists()

    # Existing entries -> early return
    (sandbox / 'existing.txt').write_text('x', encoding='utf-8')
    before = sorted(str(p.relative_to(sandbox)) for p in sandbox.rglob('*'))
    TaskManagementService._bootstrap_sandbox_workspace(project, sandbox)
    after = sorted(str(p.relative_to(sandbox)) for p in sandbox.rglob('*'))
    assert before == after

    # Trigger OSError branch in initial iterdir.
    sandbox2 = tmp_path / 'sandbox2'
    sandbox2.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(Path, 'iterdir', lambda self: (_ for _ in ()).throw(OSError('boom')))
    TaskManagementService._bootstrap_sandbox_workspace(project, sandbox2)
