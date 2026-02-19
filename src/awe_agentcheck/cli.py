from __future__ import annotations

import argparse
import json
import sys

import httpx

from awe_agentcheck.config import load_settings
from awe_agentcheck.participants import get_supported_providers, set_extra_providers


def _supported_provider_set() -> set[str]:
    try:
        settings = load_settings()
        set_extra_providers(set(settings.extra_provider_commands.keys()))
    except Exception:
        pass
    return get_supported_providers()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog='awe-agentcheck', description='Run multi-CLI orchestration tasks')
    parser.add_argument('--api-base', default='http://127.0.0.1:8000', help='Orchestrator API base URL')

    sub = parser.add_subparsers(dest='command', required=True)

    run = sub.add_parser('run', help='Create a task')
    run.add_argument('--task', required=True, help='Task title')
    run.add_argument('--description', default='', help='Optional task description')
    run.add_argument('--author', required=True, help='Author participant id')
    run.add_argument('--reviewer', action='append', required=True, help='Reviewer participant id (repeatable)')
    run.add_argument('--evolution-level', type=int, default=0, choices=[0, 1, 2], help='0=fix-only, 1=guided evolve, 2=proactive evolve')
    run.add_argument('--evolve-until', default='', help='Optional local datetime deadline, e.g. 2026-02-13 06:00')
    run.add_argument('--conversation-language', default='en', choices=['en', 'zh'], help='Conversation output language: en or zh')
    run.add_argument('--sandbox-mode', type=int, default=1, choices=[0, 1], help='1=run in sandbox workspace (default), 0=run in main workspace')
    run.add_argument('--sandbox-workspace-path', default='', help='Optional sandbox path override (default: <workspace>-lab)')
    run.add_argument('--self-loop-mode', type=int, default=0, choices=[0, 1], help='0=manual author confirmation (default), 1=autonomous loop')
    run.add_argument('--provider-model', action='append', default=[], help='Provider model override in provider=model format (repeatable)')
    run.add_argument('--provider-model-param', action='append', default=[], help='Provider model params in provider=args format (repeatable)')
    run.add_argument('--claude-team-agents', type=int, default=0, choices=[0, 1], help='Enable Claude --agents mode for Claude participants')
    run.add_argument('--codex-multi-agents', type=int, default=0, choices=[0, 1], help='Enable Codex --enable multi_agent mode for Codex participants')
    run.add_argument(
        '--claude-team-agent-override',
        action='append',
        default=[],
        help='Participant override in participant=0|1 format (repeatable)',
    )
    run.add_argument(
        '--codex-multi-agent-override',
        action='append',
        default=[],
        help='Participant override in participant=0|1 format (repeatable)',
    )
    run.add_argument('--repair-mode', default='balanced', choices=['minimal', 'balanced', 'structural'], help='Repair policy: minimal, balanced, structural')
    run.add_argument('--plain-mode', action=argparse.BooleanOptionalAction, default=True, help='Enable beginner-friendly plain output formatting (default: on)')
    run.add_argument('--stream-mode', action=argparse.BooleanOptionalAction, default=True, help='Enable streaming conversation events (default: on)')
    run.add_argument('--debate-mode', action=argparse.BooleanOptionalAction, default=True, help='Enable pre-implementation reviewer/author debate (default: on)')
    run.add_argument('--auto-merge', action=argparse.BooleanOptionalAction, default=True, help='Enable auto-fusion/changelog/snapshot after passed (default: on)')
    run.add_argument('--merge-target-path', default='', help='Optional path to receive auto-merged changes')
    run.add_argument('--workspace-path', default='.', help='Target repository/workspace path')
    run.add_argument('--max-rounds', type=int, default=3)
    run.add_argument('--test-command', default='py -m pytest -q')
    run.add_argument('--lint-command', default='py -m ruff check .')
    run.add_argument('--auto-start', action='store_true')

    status = sub.add_parser('status', help='Get task status')
    status.add_argument('task_id', help='Task id')

    tasks = sub.add_parser('tasks', help='List tasks')
    tasks.add_argument('--limit', type=int, default=20)

    sub.add_parser('stats', help='Show aggregated stats')

    analytics = sub.add_parser('analytics', help='Show advanced analytics')
    analytics.add_argument('--limit', type=int, default=300)

    policy = sub.add_parser('policy-templates', help='List policy templates and recommended profile')
    policy.add_argument('--workspace-path', default='.', help='Workspace path')

    start = sub.add_parser('start', help='Start an existing task')
    start.add_argument('task_id', help='Task id')
    start.add_argument('--background', action='store_true')

    cancel = sub.add_parser('cancel', help='Cancel a task')
    cancel.add_argument('task_id', help='Task id')

    force_fail = sub.add_parser('force-fail', help='Force mark task as failed_system')
    force_fail.add_argument('task_id', help='Task id')
    force_fail.add_argument('--reason', required=True, help='Failure reason')

    promote_round = sub.add_parser('promote-round', help='Promote one round artifact into merge target')
    promote_round.add_argument('task_id', help='Task id')
    promote_round.add_argument('--round', dest='round_number', required=True, type=int, help='Round number to promote')
    promote_round.add_argument('--merge-target-path', default='', help='Optional merge target path override')

    events = sub.add_parser('events', help='List task events')
    events.add_argument('task_id', help='Task id')

    gh = sub.add_parser('github-summary', help='Generate GitHub/PR summary markdown for a task')
    gh.add_argument('task_id', help='Task id')

    tree = sub.add_parser('tree', help='Show workspace tree')
    tree.add_argument('--workspace-path', default='.', help='Workspace path')
    tree.add_argument('--max-depth', type=int, default=4)
    tree.add_argument('--max-entries', type=int, default=500)

    gate = sub.add_parser('gate', help='Submit manual gate result')
    gate.add_argument('task_id', help='Task id')
    gate.add_argument('--tests-ok', action='store_true')
    gate.add_argument('--lint-ok', action='store_true')
    gate.add_argument('--verdict', action='append', required=True, help='Reviewer verdict: no_blocker|blocker|unknown')

    decide = sub.add_parser('decide', help='Submit author decision for waiting_manual task')
    decide.add_argument('task_id', help='Task id')
    decide.add_argument('--decision', choices=['approve', 'reject', 'revise'], default='', help='Explicit decision action')
    decide.add_argument('--approve', action='store_true', help='Approve proposal and queue task')
    decide.add_argument('--note', default='', help='Optional note')
    decide.add_argument('--auto-start', action='store_true', help='Auto-start after approve')

    return parser


def _print_json(payload) -> None:
    print(json.dumps(payload, ensure_ascii=True, indent=2))


def _parse_provider_models(values: list[str] | None) -> dict[str, str]:
    supported = _supported_provider_set()
    out: dict[str, str] = {}
    for raw in values or []:
        text = str(raw or '').strip()
        if not text:
            continue
        if '=' not in text:
            raise ValueError(f'invalid --provider-model value: {text} (expected provider=model)')
        provider_raw, model_raw = text.split('=', 1)
        provider = provider_raw.strip().lower()
        model = model_raw.strip()
        if provider not in supported:
            raise ValueError(f'invalid --provider-model provider: {provider}')
        if not model:
            raise ValueError(f'invalid --provider-model model for provider: {provider}')
        out[provider] = model
    return out


def _parse_provider_model_params(values: list[str] | None) -> dict[str, str]:
    supported = _supported_provider_set()
    out: dict[str, str] = {}
    for raw in values or []:
        text = str(raw or '').strip()
        if not text:
            continue
        if '=' not in text:
            raise ValueError(f'invalid --provider-model-param value: {text} (expected provider=args)')
        provider_raw, params_raw = text.split('=', 1)
        provider = provider_raw.strip().lower()
        params = params_raw.strip()
        if provider not in supported:
            raise ValueError(f'invalid --provider-model-param provider: {provider}')
        if not params:
            raise ValueError(f'invalid --provider-model-param params for provider: {provider}')
        out[provider] = params
    return out


def _parse_participant_agent_overrides(values: list[str] | None, *, flag_name: str) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for raw in values or []:
        text = str(raw or '').strip()
        if not text:
            continue
        if '=' not in text:
            raise ValueError(f'invalid {flag_name} value: {text} (expected participant=0|1)')
        participant_raw, enabled_raw = text.split('=', 1)
        participant = participant_raw.strip()
        enabled_text = enabled_raw.strip().lower()
        if not participant:
            raise ValueError(f'invalid {flag_name} participant: {text}')
        if enabled_text in {'1', 'true', 'yes', 'on'}:
            enabled = True
        elif enabled_text in {'0', 'false', 'no', 'off'}:
            enabled = False
        else:
            raise ValueError(f'invalid {flag_name} enabled value for {participant}: {enabled_raw}')
        out[participant] = enabled
    return out


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    base = args.api_base.rstrip('/')

    with httpx.Client(timeout=60) as client:
        if args.command == 'run':
            try:
                provider_models = _parse_provider_models(args.provider_model)
                provider_model_params = _parse_provider_model_params(args.provider_model_param)
                claude_team_agents_overrides = _parse_participant_agent_overrides(
                    args.claude_team_agent_override,
                    flag_name='--claude-team-agent-override',
                )
                codex_multi_agents_overrides = _parse_participant_agent_overrides(
                    args.codex_multi_agent_override,
                    flag_name='--codex-multi-agent-override',
                )
            except ValueError as exc:
                parser.error(str(exc))
                return 2
            response = client.post(
                f'{base}/api/tasks',
                json={
                    'title': args.task,
                    'description': args.description or args.task,
                    'author_participant': args.author,
                    'reviewer_participants': args.reviewer,
                    'evolution_level': int(args.evolution_level),
                    'evolve_until': (args.evolve_until.strip() or None),
                    'conversation_language': str(args.conversation_language).strip().lower() or 'en',
                    'provider_models': provider_models,
                    'provider_model_params': provider_model_params,
                    'claude_team_agents': int(args.claude_team_agents) == 1,
                    'codex_multi_agents': int(args.codex_multi_agents) == 1,
                    'claude_team_agents_overrides': claude_team_agents_overrides,
                    'codex_multi_agents_overrides': codex_multi_agents_overrides,
                    'repair_mode': str(args.repair_mode).strip().lower() or 'balanced',
                    'plain_mode': bool(args.plain_mode),
                    'stream_mode': bool(args.stream_mode),
                    'debate_mode': bool(args.debate_mode),
                    'sandbox_mode': int(args.sandbox_mode) == 1,
                    'sandbox_workspace_path': (args.sandbox_workspace_path.strip() or None),
                    'self_loop_mode': int(args.self_loop_mode),
                    'auto_merge': bool(args.auto_merge),
                    'merge_target_path': (args.merge_target_path.strip() or None),
                    'workspace_path': args.workspace_path,
                    'max_rounds': int(args.max_rounds),
                    'test_command': args.test_command,
                    'lint_command': args.lint_command,
                    'auto_start': bool(args.auto_start),
                },
            )
        elif args.command == 'status':
            response = client.get(f'{base}/api/tasks/{args.task_id}')
        elif args.command == 'tasks':
            response = client.get(f'{base}/api/tasks', params={'limit': int(args.limit)})
        elif args.command == 'stats':
            response = client.get(f'{base}/api/stats')
        elif args.command == 'analytics':
            response = client.get(f'{base}/api/analytics', params={'limit': int(args.limit)})
        elif args.command == 'policy-templates':
            response = client.get(
                f'{base}/api/policy-templates',
                params={'workspace_path': args.workspace_path},
            )
        elif args.command == 'start':
            response = client.post(f'{base}/api/tasks/{args.task_id}/start', json={'background': bool(args.background)})
        elif args.command == 'cancel':
            response = client.post(f'{base}/api/tasks/{args.task_id}/cancel')
        elif args.command == 'force-fail':
            response = client.post(
                f'{base}/api/tasks/{args.task_id}/force-fail',
                json={'reason': args.reason},
            )
        elif args.command == 'promote-round':
            response = client.post(
                f'{base}/api/tasks/{args.task_id}/promote-round',
                json={
                    'round': int(args.round_number),
                    'merge_target_path': (args.merge_target_path.strip() or None),
                },
            )
        elif args.command == 'events':
            response = client.get(f'{base}/api/tasks/{args.task_id}/events')
        elif args.command == 'github-summary':
            response = client.get(f'{base}/api/tasks/{args.task_id}/github-summary')
        elif args.command == 'tree':
            response = client.get(
                f'{base}/api/workspace-tree',
                params={
                    'workspace_path': args.workspace_path,
                    'max_depth': int(args.max_depth),
                    'max_entries': int(args.max_entries),
                },
            )
        elif args.command == 'gate':
            response = client.post(
                f'{base}/api/tasks/{args.task_id}/gate',
                json={
                    'tests_ok': bool(args.tests_ok),
                    'lint_ok': bool(args.lint_ok),
                    'reviewer_verdicts': args.verdict,
                },
            )
        elif args.command == 'decide':
            decision_text = str(args.decision or '').strip().lower()
            if not decision_text:
                decision_text = 'approve' if bool(args.approve) else 'reject'
            response = client.post(
                f'{base}/api/tasks/{args.task_id}/author-decision',
                json={
                    'approve': bool(args.approve),
                    'decision': decision_text,
                    'note': (args.note.strip() or None),
                    'auto_start': bool(args.auto_start),
                },
            )
        else:
            parser.error(f'unsupported command: {args.command}')
            return 2

    if response.status_code >= 400:
        print(f'HTTP {response.status_code}: {response.text}', file=sys.stderr)
        return 1

    _print_json(response.json())
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
