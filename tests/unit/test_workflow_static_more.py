from __future__ import annotations

import types


import awe_agentcheck.workflow as workflow
from awe_agentcheck.participants import parse_participant_id
from awe_agentcheck.workflow import RunConfig, RunResult, WorkflowEngine


class _NoopRunner:
    def run(self, **_kwargs):  # noqa: ANN003
        raise AssertionError('not expected in static tests')


class _NoopCommandExecutor:
    def run(self, command, cwd, timeout_seconds):  # noqa: ANN001, ANN201
        raise AssertionError('not expected in static tests')


def _config(tmp_path, *, title='scan repo', description='check bugs'):
    return RunConfig(
        task_id='task-static',
        title=title,
        description=description,
        author=parse_participant_id('codex#author-A'),
        reviewers=[parse_participant_id('claude#review-B')],
        evolution_level=1,
        evolve_until=None,
        cwd=tmp_path,
        max_rounds=2,
        test_command='py -m pytest -q',
        lint_command='py -m ruff check .',
        conversation_language='zh',
        plain_mode=True,
        debate_mode=True,
    )


def test_workflow_langgraph_finish_and_route_helpers():
    assert WorkflowEngine._langgraph_should_finish_round(
        result=RunResult(status='passed', rounds=1, gate_reason='passed'),
        round_no=1,
        max_rounds=3,
        deadline=None,
    ) is True
    assert WorkflowEngine._langgraph_should_finish_round(
        result=RunResult(status='failed_gate', rounds=1, gate_reason='review_blocker'),
        round_no=1,
        max_rounds=3,
        deadline=None,
    ) is False
    assert WorkflowEngine._langgraph_should_finish_round(
        result=RunResult(status='failed_gate', rounds=1, gate_reason='review_blocker'),
        round_no=3,
        max_rounds=3,
        deadline=None,
    ) is True
    assert WorkflowEngine._langgraph_should_finish_round(
        result=RunResult(status='failed_gate', rounds=1, gate_reason='review_blocker'),
        round_no=1,
        max_rounds=3,
        deadline=object(),  # non-None sentinel
    ) is False

    assert WorkflowEngine._langgraph_round_route({'result': RunResult('passed', 1, 'passed')}) == 'finalize'
    assert WorkflowEngine._langgraph_round_route({'last_round_result': RunResult('failed_gate', 1, 'x')}) == 'round'
    assert WorkflowEngine._langgraph_round_route({'last_round_result': 'bad'}) == 'finalize'


def test_workflow_langgraph_finalize_node_cases(tmp_path):
    engine = WorkflowEngine(runner=_NoopRunner(), command_executor=_NoopCommandExecutor(), workflow_backend='classic')
    out1 = engine._langgraph_finalize_node({'preflight_ok': False, 'preflight_error': 'boom'})
    assert out1['result'].status == 'failed_system'
    assert out1['result'].gate_reason == 'boom'

    rr = RunResult(status='passed', rounds=1, gate_reason='passed')
    out2 = engine._langgraph_finalize_node({'result': rr})
    assert out2['result'] is rr

    out3 = engine._langgraph_finalize_node({'last_round_result': rr})
    assert out3['result'] is rr

    out4 = engine._langgraph_finalize_node({})
    assert out4['result'].status == 'failed_system'


def test_workflow_backend_normalization_and_tracer_helpers(monkeypatch):
    monkeypatch.setattr(workflow, 'StateGraph', None)
    assert WorkflowEngine._normalize_workflow_backend('langgraph') == 'classic'
    assert WorkflowEngine._normalize_workflow_backend('classic') == 'classic'
    assert WorkflowEngine._normalize_workflow_backend('unknown') == 'classic'

    # ImportError branch
    real_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: ANN001
        if name == 'opentelemetry':
            raise ImportError('no otel')
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr('builtins.__import__', fake_import)
    assert WorkflowEngine._get_tracer() is None

    # Generic Exception branch (non-ImportError)
    def fake_import2(name, globals=None, locals=None, fromlist=(), level=0):  # noqa: ANN001
        if name == 'opentelemetry':
            raise RuntimeError('unexpected')
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr('builtins.__import__', fake_import2)
    assert WorkflowEngine._get_tracer() is None


def test_workflow_span_wrapper_covers_attribute_failure():
    class _Ctx:
        def __init__(self):
            self.attrs = {}
            self.calls = 0

        def set_attribute(self, key, value):
            self.calls += 1
            if key == 'bad':
                raise TypeError('bad attr')
            self.attrs[key] = value

    class _Span:
        def __init__(self):
            self.ctx = _Ctx()

        def __enter__(self):
            return self.ctx

        def __exit__(self, exc_type, exc, tb):
            return False

    class _Tracer:
        def start_as_current_span(self, _name):
            return _Span()

    with WorkflowEngine._span(_Tracer(), 'test', {'good': 1, 'bad': object()}) as span_ctx:
        assert span_ctx.attrs['good'] == 1
    with WorkflowEngine._span(None, 'noop', {}) as _ctx:
        assert _ctx is None


def test_workflow_debate_reply_prompt_and_runtime_reason_helpers(tmp_path):
    cfg = _config(tmp_path)
    prompt = WorkflowEngine._debate_reply_prompt(
        cfg,
        1,
        'context ' * 200,
        'claude#review-B',
        'feedback ' * 200,
        environment_context='env-tree',
        strategy_hint='switch strategy',
    )
    assert 'env-tree' in prompt
    assert 'Strategy shift hint:' in prompt

    assert WorkflowEngine._is_actionable_debate_review_text('') is False
    assert WorkflowEngine._is_actionable_debate_review_text('[debate_review_error] timeout') is False
    assert WorkflowEngine._is_actionable_debate_review_text('provider_limit provider=codex') is False
    assert WorkflowEngine._is_actionable_debate_review_text('command_timeout provider=codex') is False
    assert WorkflowEngine._is_actionable_debate_review_text('command_not_found provider=codex') is False
    assert WorkflowEngine._is_actionable_debate_review_text('command_failed provider=codex') is False
    assert WorkflowEngine._is_actionable_debate_review_text('command_not_configured provider=codex') is False
    assert WorkflowEngine._is_actionable_debate_review_text('valid reviewer feedback') is True

    assert WorkflowEngine._runtime_error_reason_from_text('provider_limit provider=claude') == 'provider_limit'
    assert WorkflowEngine._runtime_error_reason_from_text('command_timeout provider=claude') == 'command_timeout'
    assert WorkflowEngine._runtime_error_reason_from_text('command_not_found provider=claude') == 'command_not_found'
    assert WorkflowEngine._runtime_error_reason_from_text('command_not_configured provider=claude') == 'command_not_configured'
    assert WorkflowEngine._runtime_error_reason_from_text('command_failed provider=claude') == 'command_failed'
    assert WorkflowEngine._runtime_error_reason_from_text('') is None
    assert WorkflowEngine._runtime_error_reason_from_text('normal text') is None

    result_like = types.SimpleNamespace(output='normal text', returncode='x')
    assert WorkflowEngine._runtime_error_reason_from_result(result_like) is None
    result_like_bad = types.SimpleNamespace(output='normal text', returncode=2)
    assert WorkflowEngine._runtime_error_reason_from_result(result_like_bad) == 'participant_runtime_error'
    result_like_limit = types.SimpleNamespace(output='provider_limit provider=claude', returncode=0)
    assert WorkflowEngine._runtime_error_reason_from_result(result_like_limit) == 'provider_limit'


def test_workflow_strategy_hint_matrix_and_audit_discovery(tmp_path):
    assert 'file evidence' in WorkflowEngine._strategy_hint_from_reason(
        gate_reason='precompletion_evidence_missing',
        gate_repeat=1,
        impl_repeat=1,
        review_repeat=1,
        verify_repeat=1,
    )
    assert 'test-first' in WorkflowEngine._strategy_hint_from_reason(
        gate_reason='tests_failed',
        gate_repeat=1,
        impl_repeat=1,
        review_repeat=1,
        verify_repeat=1,
    )
    assert 'runtime configuration' in WorkflowEngine._strategy_hint_from_reason(
        gate_reason='command_timeout',
        gate_repeat=1,
        impl_repeat=1,
        review_repeat=1,
        verify_repeat=1,
    )
    assert 'Reviewer concern' in WorkflowEngine._strategy_hint_from_reason(
        gate_reason='review_blocker',
        gate_repeat=1,
        impl_repeat=1,
        review_repeat=1,
        verify_repeat=1,
    )
    assert 'Architecture audit failed' in WorkflowEngine._strategy_hint_from_reason(
        gate_reason='architecture_threshold_exceeded',
        gate_repeat=1,
        impl_repeat=1,
        review_repeat=1,
        verify_repeat=1,
    )
    assert 'warning-level debt' in WorkflowEngine._strategy_hint_from_reason(
        gate_reason='architecture_threshold_warning',
        gate_repeat=1,
        impl_repeat=1,
        review_repeat=1,
        verify_repeat=1,
    )
    fallback = WorkflowEngine._strategy_hint_from_reason(
        gate_reason='other',
        gate_repeat=3,
        impl_repeat=4,
        review_repeat=5,
        verify_repeat=6,
    )
    assert 'Loop detected' in fallback

    assert WorkflowEngine._is_audit_discovery_task(_config(tmp_path, title='Audit this repo', description='scan bugs')) is True
    assert WorkflowEngine._is_audit_discovery_task(_config(tmp_path, title='Rename variable', description='small change')) is False
