from __future__ import annotations

from pathlib import Path
from string import Template
from types import SimpleNamespace

import pytest

from awe_agentcheck.participants import parse_participant_id
from awe_agentcheck.workflow import RunConfig, RunResult, WorkflowEngine
from awe_agentcheck.workflow_architecture import run_architecture_audit
from awe_agentcheck.workflow_prompting import load_prompt_template
from awe_agentcheck.workflow_runtime import normalize_participant_model_params


class _NoopRunner:
    def run(self, **_kwargs):  # noqa: ANN003
        raise AssertionError('not used')


class _NoopExecutor:
    def run(self, command, cwd, timeout_seconds):  # noqa: ANN001, ANN201
        raise AssertionError('not used')


def _config(tmp_path: Path) -> RunConfig:
    return RunConfig(
        task_id='task-touchup',
        title='touchup',
        description='touchup',
        author=parse_participant_id('codex#author-A'),
        reviewers=[parse_participant_id('claude#review-B')],
        evolution_level=0,
        evolve_until=None,
        cwd=tmp_path,
        max_rounds=1,
        test_command='py -m pytest -q',
        lint_command='py -m ruff check .',
    )


def test_architecture_audit_passed_reason(tmp_path: Path, monkeypatch):
    root = tmp_path / 'repo'
    (root / 'src' / 'awe_agentcheck').mkdir(parents=True, exist_ok=True)
    (root / 'src' / 'awe_agentcheck' / 'tiny.py').write_text('x=1\n', encoding='utf-8')
    monkeypatch.setenv('AWE_ARCH_AUDIT_MODE', 'warn')
    result = run_architecture_audit(cwd=root, evolution_level=1)
    assert result.enabled is True
    assert result.passed is True
    assert result.reason == 'passed'


def test_workflow_prompting_relative_to_error_branch(tmp_path: Path, monkeypatch):
    template_dir = tmp_path / 'templates'
    template_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / 'x.txt').write_text('Hello $name', encoding='utf-8')
    cache: dict[str, Template] = {}
    original_relative_to = Path.relative_to

    def fake_relative_to(self, *args, **kwargs):  # noqa: ANN001
        raise ValueError('forced')

    monkeypatch.setattr(Path, 'relative_to', fake_relative_to)
    with pytest.raises(ValueError, match='invalid prompt template path'):
        load_prompt_template(template_name='x.txt', template_dir=template_dir, cache=cache)
    monkeypatch.setattr(Path, 'relative_to', original_relative_to)


def test_workflow_runtime_skip_empty_participant_model_params():
    out = normalize_participant_model_params({'': 'x', 'a': '', 'b': ' p '})
    assert out == {'b': 'p'}


def test_workflow_run_branches_cover_classic_and_langgraph_missing_result(tmp_path: Path, monkeypatch):
    engine = WorkflowEngine(runner=_NoopRunner(), command_executor=_NoopExecutor(), workflow_backend='classic')
    monkeypatch.setattr(engine, '_run_classic', lambda config, **kwargs: RunResult('passed', 1, 'passed'))
    result = engine.run(_config(tmp_path))
    assert result.status == 'passed'

    engine2 = WorkflowEngine(runner=_NoopRunner(), command_executor=_NoopExecutor(), workflow_backend='classic')
    monkeypatch.setattr(engine2, 'workflow_backend', 'langgraph')
    monkeypatch.setattr(engine2, '_get_langgraph', lambda: SimpleNamespace(invoke=lambda _payload: {}))
    with pytest.raises(RuntimeError, match='missing RunResult payload'):
        engine2.run(_config(tmp_path))
