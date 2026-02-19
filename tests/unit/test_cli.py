from __future__ import annotations

from awe_agentcheck.cli import (
    _parse_participant_agent_overrides,
    _parse_provider_model_params,
    _parse_provider_models,
    build_parser,
)


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
            '2',
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
            '--no-plain-mode',
            '--merge-target-path',
            'C:/Users/hangw/awe-agentcheck',
        ]
    )

    assert args.command == 'run'
    assert args.author == 'claude#author-A'
    assert args.reviewer == ['codex#review-B', 'claude#review-C']
    assert args.evolution_level == 2
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
