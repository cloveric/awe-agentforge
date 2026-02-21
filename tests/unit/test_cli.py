from __future__ import annotations

from types import SimpleNamespace

import awe_agentcheck.cli as cli_module
from awe_agentcheck.cli import (
    _parse_phase_timeouts,
    _parse_participant_agent_overrides,
    _parse_provider_model_params,
    _parse_provider_models,
    _supported_provider_set,
    build_parser,
)
import pytest


def test_cli_parser_run_subcommand_accepts_author_and_reviewers():
    parser = build_parser()
    args = parser.parse_args(
        [
            'run',
            '--task',
            'Implement feature X',
            '--author',
            'claude#author-A',
            '--reviewer',
            'codex#review-B',
            '--reviewer',
            'claude#review-C',
            '--evolution-level',
            '3',
            '--evolve-until',
            '2026-02-13 06:00',
            '--conversation-language',
            'zh',
            '--sandbox-mode',
            '1',
            '--sandbox-workspace-path',
            'C:/Users/hangw/awe-agentcheck-lab',
            '--self-loop-mode',
            '0',
            '--provider-model',
            'claude=claude-sonnet-4-5',
            '--provider-model',
            'codex=gpt-5-codex',
            '--provider-model-param',
            'codex=-c model_reasoning_effort=high',
            '--claude-team-agents',
            '1',
            '--codex-multi-agents',
            '1',
            '--claude-team-agent-override',
            'claude#author-A=1',
            '--codex-multi-agent-override',
            'codex#review-B=0',
            '--memory-mode',
            'strict',
            '--phase-timeout',
            'proposal=120',
            '--phase-timeout',
            'review=180',
            '--no-plain-mode',
            '--merge-target-path',
            'C:/Users/hangw/awe-agentcheck',
        ]
    )

    assert args.command == 'run'
    assert args.author == 'claude#author-A'
    assert args.reviewer == ['codex#review-B', 'claude#review-C']
    assert args.evolution_level == 3
    assert args.evolve_until == '2026-02-13 06:00'
    assert args.conversation_language == 'zh'
    assert args.sandbox_mode == 1
    assert args.sandbox_workspace_path == 'C:/Users/hangw/awe-agentcheck-lab'
    assert args.self_loop_mode == 0
    assert args.provider_model == ['claude=claude-sonnet-4-5', 'codex=gpt-5-codex']
    assert args.provider_model_param == ['codex=-c model_reasoning_effort=high']
    assert args.claude_team_agents == 1
    assert args.codex_multi_agents == 1
    assert args.claude_team_agent_override == ['claude#author-A=1']
    assert args.codex_multi_agent_override == ['codex#review-B=0']
    assert args.memory_mode == 'strict'
    assert args.phase_timeout == ['proposal=120', 'review=180']
    assert args.plain_mode is False
    assert args.stream_mode is True
    assert args.debate_mode is True
    assert args.auto_merge is True
    assert args.merge_target_path == 'C:/Users/hangw/awe-agentcheck'


def test_cli_parser_run_supports_disabling_auto_merge():
    parser = build_parser()
    args = parser.parse_args(
        [
            'run',
            '--task',
            'Task',
            '--author',
            'claude#author-A',
            '--reviewer',
            'codex#review-B',
            '--no-auto-merge',
        ]
    )
    assert args.auto_merge is False


def test_cli_parser_run_supports_disabling_stream_and_debate_modes():
    parser = build_parser()
    args = parser.parse_args(
        [
            'run',
            '--task',
            'Task',
            '--author',
            'claude#author-A',
            '--reviewer',
            'codex#review-B',
            '--no-stream-mode',
            '--no-debate-mode',
        ]
    )
    assert args.stream_mode is False
    assert args.debate_mode is False


def test_cli_parser_supports_start_command():
    parser = build_parser()
    args = parser.parse_args(['start', 'task-1', '--background'])
    assert args.command == 'start'
    assert args.task_id == 'task-1'
    assert args.background is True


def test_cli_parser_supports_stats_command():
    parser = build_parser()
    args = parser.parse_args(['stats'])
    assert args.command == 'stats'


def test_cli_parser_supports_analytics_command():
    parser = build_parser()
    args = parser.parse_args(['analytics', '--limit', '120'])
    assert args.command == 'analytics'
    assert args.limit == 120


def test_cli_parser_supports_policy_templates_command():
    parser = build_parser()
    args = parser.parse_args(['policy-templates', '--workspace-path', 'C:/repo'])
    assert args.command == 'policy-templates'
    assert args.workspace_path == 'C:/repo'


def test_cli_parser_supports_benchmark_command():
    parser = build_parser()
    args = parser.parse_args(
        [
            'benchmark',
            '--workspace-path',
            'C:/repo',
            '--regression-file',
            'C:/repo/.agents/regressions/failure_tasks.json',
            '--variant-a-name',
            'baseline',
            '--variant-b-name',
            'candidate',
            '--reviewer',
            'claude#review-B',
            '--reviewer',
            'codex#review-C',
        ]
    )
    assert args.command == 'benchmark'
    assert args.workspace_path == 'C:/repo'
    assert args.regression_file == 'C:/repo/.agents/regressions/failure_tasks.json'
    assert args.include_regression is True
    assert args.variant_a_name == 'baseline'
    assert args.variant_b_name == 'candidate'
    assert args.reviewer == ['claude#review-B', 'codex#review-C']


def test_cli_benchmark_main_forwards_regression_flags(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_run(cmd, cwd):
        captured['cmd'] = list(cmd)
        captured['cwd'] = cwd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli_module.subprocess, 'run', _fake_run)
    exit_code = cli_module.main(
        [
            'benchmark',
            '--workspace-path',
            'C:/repo',
            '--regression-file',
            'C:/repo/.agents/regressions/failure_tasks.json',
        ]
    )

    assert exit_code == 0
    cmd = list(captured.get('cmd') or [])
    assert '--regression-file' in cmd
    assert cmd[cmd.index('--regression-file') + 1] == 'C:/repo/.agents/regressions/failure_tasks.json'
    assert '--include-regression' in cmd


def test_cli_benchmark_main_forwards_no_include_regression_flag(monkeypatch):
    captured: dict[str, object] = {}

    def _fake_run(cmd, cwd):
        captured['cmd'] = list(cmd)
        captured['cwd'] = cwd
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cli_module.subprocess, 'run', _fake_run)
    exit_code = cli_module.main(
        [
            'benchmark',
            '--workspace-path',
            'C:/repo',
            '--no-include-regression',
        ]
    )

    assert exit_code == 0
    cmd = list(captured.get('cmd') or [])
    assert '--no-include-regression' in cmd
    assert '--include-regression' not in cmd


def test_cli_parser_supports_force_fail_command():
    parser = build_parser()
    args = parser.parse_args(['force-fail', 'task-1', '--reason', 'watchdog_timeout'])
    assert args.command == 'force-fail'
    assert args.task_id == 'task-1'
    assert args.reason == 'watchdog_timeout'


def test_cli_parser_supports_tree_command():
    parser = build_parser()
    args = parser.parse_args(['tree', '--workspace-path', 'C:/repo', '--max-depth', '3', '--max-entries', '120'])
    assert args.command == 'tree'
    assert args.workspace_path == 'C:/repo'
    assert args.max_depth == 3
    assert args.max_entries == 120


def test_cli_parser_supports_github_summary_command():
    parser = build_parser()
    args = parser.parse_args(['github-summary', 'task-9'])
    assert args.command == 'github-summary'
    assert args.task_id == 'task-9'


def test_cli_parser_supports_author_decide_command():
    parser = build_parser()
    args = parser.parse_args(['decide', 'task-7', '--approve', '--note', 'ship', '--auto-start'])
    assert args.command == 'decide'
    assert args.task_id == 'task-7'
    assert args.approve is True
    assert args.note == 'ship'
    assert args.auto_start is True


def test_cli_parser_supports_author_decide_with_explicit_decision():
    parser = build_parser()
    args = parser.parse_args(['decide', 'task-8', '--decision', 'revise', '--note', 'need stronger plan'])
    assert args.command == 'decide'
    assert args.task_id == 'task-8'
    assert args.decision == 'revise'
    assert args.note == 'need stronger plan'


def test_cli_parse_provider_model_supports_extra_provider_from_env(monkeypatch):
    monkeypatch.setenv('AWE_PROVIDER_ADAPTERS_JSON', '{"qwen":"qwen-cli --yolo"}')
    parsed = _parse_provider_models(['qwen=qwen-max'])
    assert parsed['qwen'] == 'qwen-max'


def test_cli_parse_provider_model_params_supports_extra_provider_from_env(monkeypatch):
    monkeypatch.setenv('AWE_PROVIDER_ADAPTERS_JSON', '{"qwen":"qwen-cli --yolo"}')
    parsed = _parse_provider_model_params(['qwen=--temperature 0.2'])
    assert parsed['qwen'] == '--temperature 0.2'


def test_cli_parse_participant_agent_overrides_accepts_boolean_values():
    parsed = _parse_participant_agent_overrides(
        ['claude#author-A=true', 'codex#review-B=0'],
        flag_name='--claude-team-agent-override',
    )
    assert parsed['claude#author-A'] is True
    assert parsed['codex#review-B'] is False


def test_supported_provider_set_handles_settings_error(monkeypatch):
    monkeypatch.setattr(cli_module, 'load_settings', lambda: (_ for _ in ()).throw(RuntimeError('boom')))
    providers = _supported_provider_set()
    assert 'claude' in providers
    assert 'codex' in providers
    assert 'gemini' in providers


def test_parse_provider_models_and_params_validation_errors():
    with pytest.raises(ValueError):
        _parse_provider_models(['bad-format'])
    with pytest.raises(ValueError):
        _parse_provider_models(['claude='])
    with pytest.raises(ValueError):
        _parse_provider_models(['unknown=model'])

    with pytest.raises(ValueError):
        _parse_provider_model_params(['bad-format'])
    with pytest.raises(ValueError):
        _parse_provider_model_params(['codex='])
    with pytest.raises(ValueError):
        _parse_provider_model_params(['unknown=--x'])

    with pytest.raises(ValueError):
        _parse_participant_agent_overrides(['invalid'], flag_name='--flag')
    with pytest.raises(ValueError):
        _parse_participant_agent_overrides([' =1'], flag_name='--flag')
    with pytest.raises(ValueError):
        _parse_participant_agent_overrides(['claude#author-A=maybe'], flag_name='--flag')

    with pytest.raises(ValueError):
        _parse_phase_timeouts(['bad'])
    with pytest.raises(ValueError):
        _parse_phase_timeouts(['unknown=10'])
    with pytest.raises(ValueError):
        _parse_phase_timeouts(['proposal=bad'])
    with pytest.raises(ValueError):
        _parse_phase_timeouts(['review=0'])
    assert _parse_phase_timeouts(['impl=120', 'verification=30']) == {
        'implementation': 120,
        'command': 30,
    }


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text='ok'):
        self.status_code = int(status_code)
        self._payload = payload if payload is not None else {'ok': True}
        self.text = text

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, response=None):
        self.calls = []
        self._response = response or _FakeResponse()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def get(self, url, params=None):
        self.calls.append(('GET', url, params, None))
        return self._response

    def post(self, url, json=None):
        self.calls.append(('POST', url, None, json))
        return self._response


def test_cli_main_routes_http_commands(monkeypatch, capsys):
    fake = _FakeClient(response=_FakeResponse(status_code=200, payload={'ok': True}))
    monkeypatch.setattr(cli_module.httpx, 'Client', lambda timeout=60: fake)

    cases = [
        (['status', 'task-1'], 'GET', '/api/tasks/task-1'),
        (['tasks', '--limit', '5'], 'GET', '/api/tasks'),
        (['stats'], 'GET', '/api/stats'),
        (['analytics', '--limit', '12'], 'GET', '/api/analytics'),
        (['policy-templates', '--workspace-path', 'C:/repo'], 'GET', '/api/policy-templates'),
        (['start', 'task-1', '--background'], 'POST', '/api/tasks/task-1/start'),
        (['cancel', 'task-1'], 'POST', '/api/tasks/task-1/cancel'),
        (['force-fail', 'task-1', '--reason', 'timeout'], 'POST', '/api/tasks/task-1/force-fail'),
        (['promote-round', 'task-1', '--round', '2', '--merge-target-path', 'C:/target'], 'POST', '/api/tasks/task-1/promote-round'),
        (['events', 'task-1'], 'GET', '/api/tasks/task-1/events'),
        (['github-summary', 'task-1'], 'GET', '/api/tasks/task-1/github-summary'),
        (['tree', '--workspace-path', 'C:/repo', '--max-depth', '2', '--max-entries', '10'], 'GET', '/api/workspace-tree'),
        (['gate', 'task-1', '--tests-ok', '--lint-ok', '--verdict', 'no_blocker'], 'POST', '/api/tasks/task-1/gate'),
        (['decide', 'task-1', '--approve', '--note', 'ship', '--auto-start'], 'POST', '/api/tasks/task-1/author-decision'),
    ]

    for argv, method, endpoint in cases:
        fake.calls.clear()
        code = cli_module.main(argv)
        assert code == 0
        assert fake.calls
        call = fake.calls[-1]
        assert call[0] == method
        assert call[1].endswith(endpoint)
        assert '"ok": true' in capsys.readouterr().out.lower()


def test_cli_main_run_posts_task_payload(monkeypatch):
    fake = _FakeClient(response=_FakeResponse(status_code=200, payload={'task_id': 'task-1'}))
    monkeypatch.setattr(cli_module.httpx, 'Client', lambda timeout=60: fake)

    code = cli_module.main(
        [
            'run',
            '--task',
            'Fix bug',
            '--description',
            'desc',
            '--author',
            'codex#author-A',
            '--reviewer',
            'claude#review-B',
            '--provider-model',
            'codex=gpt-5.3-codex',
            '--provider-model-param',
            'codex=-c model_reasoning_effort=xhigh',
            '--claude-team-agent-override',
            'claude#review-B=1',
            '--codex-multi-agent-override',
            'codex#author-A=0',
            '--sandbox-mode',
            '1',
            '--self-loop-mode',
            '1',
            '--max-rounds',
            '2',
        ]
    )
    assert code == 0
    assert fake.calls
    method, url, _params, payload = fake.calls[-1]
    assert method == 'POST'
    assert url.endswith('/api/tasks')
    assert payload['title'] == 'Fix bug'
    assert payload['provider_models']['codex'] == 'gpt-5.3-codex'
    assert payload['provider_model_params']['codex'].startswith('-c')
    assert payload['self_loop_mode'] == 1
    assert payload['memory_mode'] == 'basic'
    assert payload['phase_timeout_seconds'] == {}


def test_cli_main_http_error_returns_non_zero(monkeypatch, capsys):
    fake = _FakeClient(response=_FakeResponse(status_code=500, payload={'ok': False}, text='error'))
    monkeypatch.setattr(cli_module.httpx, 'Client', lambda timeout=60: fake)
    code = cli_module.main(['status', 'task-1'])
    assert code == 1
    assert 'HTTP 500' in capsys.readouterr().err
