from __future__ import annotations

import argparse
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from datetime import timedelta
import sys
import time
from pathlib import Path
from typing import Deque

import httpx

from awe_agentcheck.automation import (
    acquire_single_instance,
    extract_self_followup_topic,
    is_provider_limit_reason,
    parse_until,
    recommend_process_followup_topic,
    summarize_actionable_text,
    should_retry_start_for_concurrency_limit,
    should_switch_back_to_primary,
    should_switch_to_fallback,
)


TERMINAL_STATUSES = {'passed', 'failed_gate', 'failed_system', 'canceled'}


@dataclass
class ParticipantPlan:
    author: str
    reviewers: list[str]


def build_task_description(
    topic: str,
    *,
    process_signal: str | None = None,
    self_signal: str | None = None,
) -> str:
    lines: list[str] = [
        'You are in continuous self-improvement mode.',
        f'Primary topic: {str(topic or "").strip()}',
        'Do one concrete improvement only, then verify with tests and lint.',
        'Output should include: issue, impact, root cause, fix, and validation summary.',
    ]
    process_text = str(process_signal or '').strip()
    if process_text:
        lines.append(f'Process issue signal from previous run: {process_text}')
    self_text = str(self_signal or '').strip()
    if self_text:
        lines.append(f'Self-loop finding from previous run: {self_text}')
    lines.append('Prefer smallest reliable change that improves stability and clarity.')
    return '\n'.join(lines)


def _normalize_topic_key(topic: str) -> str:
    return ' '.join(str(topic or '').strip().lower().split())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description='Run continuous overnight self-evolution tasks against awe-agentcheck API.')
    parser.add_argument('--api-base', default='http://127.0.0.1:8000')
    parser.add_argument('--until', required=True, help='Local datetime, e.g. "2026-02-12 07:00"')
    parser.add_argument('--workspace-path', default='C:/Users/hangw/awe-agentcheck')
    parser.add_argument('--sandbox-mode', type=int, default=1, choices=[0, 1])
    parser.add_argument('--sandbox-workspace-path', default='')
    parser.add_argument('--self-loop-mode', type=int, default=1, choices=[0, 1])
    parser.add_argument('--plain-mode', type=int, default=1, choices=[0, 1])
    parser.add_argument('--stream-mode', type=int, default=0, choices=[0, 1])
    parser.add_argument('--debate-mode', type=int, default=0, choices=[0, 1])
    parser.add_argument('--repair-mode', default='balanced')
    parser.add_argument('--auto-merge', action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument('--merge-target-path', default='')
    parser.add_argument('--author', default='claude#author-A')
    parser.add_argument('--reviewer', action='append', default=None)
    parser.add_argument('--fallback-author', default='codex#author-A')
    parser.add_argument('--fallback-reviewer', action='append', default=None)
    parser.add_argument('--evolution-level', type=int, default=0)
    parser.add_argument('--evolve-until', default='')
    parser.add_argument('--max-rounds', type=int, default=3)
    parser.add_argument('--poll-seconds', type=int, default=5)
    parser.add_argument('--idle-seconds', type=int, default=5)
    parser.add_argument('--task-timeout-seconds', type=int, default=1800)
    parser.add_argument('--stall-timeout-seconds', type=int, default=360)
    parser.add_argument('--event-probe-seconds', type=int, default=45)
    parser.add_argument('--max-consecutive-system-failures', type=int, default=5)
    parser.add_argument('--cooldown-seconds', type=int, default=45)
    parser.add_argument('--primary-disable-seconds', type=int, default=3600)
    parser.add_argument('--max-followup-topics', type=int, default=24)
    parser.add_argument('--test-command', default='py -m pytest -q')
    parser.add_argument('--lint-command', default='py -m ruff check .')
    parser.add_argument('--topic-file', default='')
    parser.add_argument('--log-dir', default='C:/Users/hangw/awe-agentcheck/.agents/overnight')
    parser.add_argument('--lock-file', default='C:/Users/hangw/awe-agentcheck/.agents/overnight/overnight.lock')
    return parser


def load_topics(path: str) -> list[str]:
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    lines = [line.strip() for line in p.read_text(encoding='utf-8').splitlines()]
    return [line for line in lines if line and not line.startswith('#')]


def ensure_log_file(log_dir: Path) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
    path = log_dir / f'overnight-{stamp}.md'
    header = (
        '# Overnight Auto-Evolve Log\n\n'
        f'Started: {datetime.now().isoformat()}\n\n'
        '| Iteration | Task ID | Status | Rounds | Reason | Participants |\n'
        '|---|---|---|---|---|---|\n'
    )
    path.write_text(header, encoding='utf-8')
    return path


def append_log(path: Path, *, iteration: int, task_id: str, status: str, rounds: int, reason: str | None, participants: ParticipantPlan) -> None:
    row = (
        f'| {iteration} | {task_id} | {status} | {rounds} | {(reason or "")[:140]} '
        f'| {participants.author} -> {", ".join(participants.reviewers)} |\n'
    )
    with path.open('a', encoding='utf-8') as f:
        f.write(row)


def create_task(
    client: httpx.Client,
    *,
    api_base: str,
    topic: str,
    description: str,
    workspace_path: str,
    sandbox_mode: int,
    sandbox_workspace_path: str | None,
    self_loop_mode: int,
    plain_mode: int,
    stream_mode: int,
    debate_mode: int,
    repair_mode: str,
    auto_merge: bool,
    merge_target_path: str | None,
    participants: ParticipantPlan,
    evolution_level: int,
    evolve_until: str | None,
    max_rounds: int,
    test_command: str,
    lint_command: str,
) -> dict:
    payload = {
        'title': f'AutoEvolve: {topic[:90]}',
        'description': str(description or '').strip() or build_task_description(topic),
        'author_participant': participants.author,
        'reviewer_participants': participants.reviewers,
        'evolution_level': int(max(0, min(2, int(evolution_level)))),
        'evolve_until': (str(evolve_until).strip() if evolve_until else None),
        'workspace_path': workspace_path,
        'sandbox_mode': int(sandbox_mode) == 1,
        'sandbox_workspace_path': (str(sandbox_workspace_path).strip() if sandbox_workspace_path else None),
        'self_loop_mode': int(max(0, min(1, int(self_loop_mode)))),
        'plain_mode': int(max(0, min(1, int(plain_mode)))) == 1,
        'stream_mode': int(max(0, min(1, int(stream_mode)))) == 1,
        'debate_mode': int(max(0, min(1, int(debate_mode)))) == 1,
        'repair_mode': str(repair_mode or 'balanced').strip() or 'balanced',
        'auto_merge': bool(auto_merge),
        'merge_target_path': (str(merge_target_path).strip() if merge_target_path else None),
        'max_rounds': max_rounds,
        'test_command': test_command,
        'lint_command': lint_command,
        'auto_start': True,
    }
    resp = client.post(f'{api_base}/api/tasks', json=payload, timeout=120)
    resp.raise_for_status()
    return resp.json()


def force_fail_for_reason(
    client: httpx.Client,
    *,
    api_base: str,
    task_id: str,
    reason: str,
) -> dict | None:
    try:
        client.post(
            f'{api_base}/api/tasks/{task_id}/cancel',
            timeout=120,
        )
    except Exception:
        pass
    try:
        resp = client.post(
            f'{api_base}/api/tasks/{task_id}/force-fail',
            json={'reason': reason},
            timeout=120,
        )
        if resp.status_code < 400:
            return resp.json()
    except Exception:
        pass
    return None


def force_fail_for_watchdog_timeout(
    client: httpx.Client,
    *,
    api_base: str,
    task_id: str,
    timeout_seconds: int,
) -> dict | None:
    reason = f'watchdog_timeout: task exceeded {timeout_seconds}s without terminal status'
    return force_fail_for_reason(
        client,
        api_base=api_base,
        task_id=task_id,
        reason=reason,
    )


def fetch_events(client: httpx.Client, *, api_base: str, task_id: str) -> list[dict]:
    try:
        resp = client.get(f'{api_base}/api/tasks/{task_id}/events', timeout=120)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            return [event for event in data if isinstance(event, dict)]
    except Exception:
        return []
    return []


def wait_terminal(
    client: httpx.Client,
    *,
    api_base: str,
    task_id: str,
    poll_seconds: int,
    task_timeout_seconds: int,
    stall_timeout_seconds: int,
    event_probe_seconds: int,
) -> tuple[dict, list[dict]]:
    timeout_window = max(0, int(task_timeout_seconds))
    stall_window = max(0, int(stall_timeout_seconds))
    probe_window = max(5, int(event_probe_seconds))
    started_at = time.monotonic()
    watchdog_last_attempt = 0.0
    last_event_probe = 0.0
    last_event_count: int | None = None
    last_event_change = started_at
    last_non_stream_count: int | None = None
    last_non_stream_change = started_at
    cached_events: list[dict] = []

    while True:
        now = time.monotonic()
        if timeout_window > 0 and (now - started_at) >= timeout_window and (now - watchdog_last_attempt) >= max(1, poll_seconds):
            watchdog_last_attempt = now
            forced = force_fail_for_watchdog_timeout(
                client,
                api_base=api_base,
                task_id=task_id,
                timeout_seconds=timeout_window,
            )
            if forced is not None:
                return forced, cached_events

        try:
            resp = client.get(f'{api_base}/api/tasks/{task_id}', timeout=120)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError:
            time.sleep(max(1, poll_seconds))
            continue
        status = str(data.get('status', ''))
        reason = data.get('last_gate_reason')
        if should_retry_start_for_concurrency_limit(status, reason):
            try:
                client.post(
                    f'{api_base}/api/tasks/{task_id}/start',
                    json={'background': True},
                    timeout=120,
                )
            except Exception:
                pass
            time.sleep(max(1, poll_seconds))
            continue
        if data.get('status') in TERMINAL_STATUSES:
            events = fetch_events(client, api_base=api_base, task_id=task_id)
            if events:
                cached_events = events
            return data, cached_events

        if stall_window > 0 and status == 'running' and (now - last_event_probe) >= probe_window:
            last_event_probe = now
            events = fetch_events(client, api_base=api_base, task_id=task_id)
            if events:
                cached_events = events
                count = len(events)
                if last_event_count is None or count > last_event_count:
                    last_event_count = count
                    last_event_change = now
                elif (now - last_event_change) >= stall_window and (now - watchdog_last_attempt) >= max(1, poll_seconds):
                    watchdog_last_attempt = now
                    forced = force_fail_for_reason(
                        client,
                        api_base=api_base,
                        task_id=task_id,
                        reason=f'watchdog_stall: no new task events for {stall_window}s',
                    )
                    if forced is not None:
                        return forced, cached_events

                non_stream_count = sum(1 for ev in events if str(ev.get('type') or '') != 'participant_stream')
                if last_non_stream_count is None or non_stream_count > last_non_stream_count:
                    last_non_stream_count = non_stream_count
                    last_non_stream_change = now
                elif (now - last_non_stream_change) >= stall_window and (now - watchdog_last_attempt) >= max(1, poll_seconds):
                    last_non_stream = None
                    for ev in reversed(events):
                        if str(ev.get('type') or '') != 'participant_stream':
                            last_non_stream = ev
                            break
                    phase_hint = ''
                    if isinstance(last_non_stream, dict):
                        phase_hint = str(last_non_stream.get('type') or '').strip().lower()
                    watchdog_last_attempt = now
                    forced = force_fail_for_reason(
                        client,
                        api_base=api_base,
                        task_id=task_id,
                        reason=f'watchdog_phase_stall: no lifecycle progress for {stall_window}s (last={phase_hint or "unknown"})',
                    )
                    if forced is not None:
                        return forced, cached_events
        time.sleep(max(1, poll_seconds))


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    deadline = parse_until(args.until)
    topics = load_topics(args.topic_file)
    if not topics:
        topics = [
            'Improve reliability of task start/cancel transitions',
            'Refine API validation and error messages',
            'Increase observability signal quality in workflow traces',
            'Improve web panel operator ergonomics and event replay clarity',
            'Find and fix one bug in service or repository layer',
        ]

    api_base = args.api_base.rstrip('/')
    log_path = ensure_log_file(Path(args.log_dir))

    primary_reviewers = list(args.reviewer) if args.reviewer else ['codex#review-B']
    fallback_reviewers = list(args.fallback_reviewer) if args.fallback_reviewer else ['codex#review-B']

    primary = ParticipantPlan(author=args.author, reviewers=primary_reviewers)
    fallback = ParticipantPlan(author=args.fallback_author, reviewers=fallback_reviewers)
    active = primary
    consecutive_system_failures = 0
    primary_disabled_until: datetime | None = None
    followup_queue: Deque[tuple[str, str, str]] = deque()
    followup_keys: set[str] = set()

    def enqueue_followup(topic: str, description: str, *, front: bool) -> None:
        topic_text = str(topic or '').strip()
        if not topic_text:
            return
        topic_text = topic_text[:180]
        key = _normalize_topic_key(topic_text)
        if not key or key in followup_keys:
            return
        while len(followup_queue) >= max(1, int(args.max_followup_topics)):
            dropped = followup_queue.pop()
            followup_keys.discard(dropped[2])
        item = (topic_text, str(description or '').strip(), key)
        if front:
            followup_queue.appendleft(item)
        else:
            followup_queue.append(item)
        followup_keys.add(key)
        direction = 'front' if front else 'tail'
        print(f'[overnight] queued follow-up ({direction}): {topic_text}')

    print(f'[overnight] running until {deadline.isoformat()}')
    print(f'[overnight] log file: {log_path}')
    print(f'[overnight] lock file: {args.lock_file}')

    iteration = 0
    topic_index = 0
    try:
        with acquire_single_instance(Path(args.lock_file)):
            transport = httpx.HTTPTransport(retries=1)
            with httpx.Client(transport=transport, headers={'Connection': 'close'}) as client:
                while datetime.now() < deadline:
                    if primary_disabled_until and datetime.now() >= primary_disabled_until:
                        print('[overnight] primary participant cooldown expired')
                        primary_disabled_until = None

                    if active == primary and primary_disabled_until and datetime.now() < primary_disabled_until:
                        active = fallback

                    iteration += 1
                    if followup_queue:
                        topic, description, queued_key = followup_queue.popleft()
                        followup_keys.discard(queued_key)
                        topic_source = 'followup'
                    else:
                        topic = topics[topic_index % len(topics)]
                        description = build_task_description(topic)
                        topic_index += 1
                        topic_source = 'base'
                    print(f'[overnight] iteration={iteration} topic_source={topic_source} topic={topic[:120]}')

                    try:
                        current_task_id = 'n/a'
                        created = create_task(
                            client,
                            api_base=api_base,
                            topic=topic,
                            description=description,
                            workspace_path=args.workspace_path,
                            sandbox_mode=args.sandbox_mode,
                            sandbox_workspace_path=(args.sandbox_workspace_path.strip() or None),
                            self_loop_mode=args.self_loop_mode,
                            plain_mode=args.plain_mode,
                            stream_mode=args.stream_mode,
                            debate_mode=args.debate_mode,
                            repair_mode=args.repair_mode,
                            auto_merge=bool(args.auto_merge),
                            merge_target_path=(args.merge_target_path.strip() or None),
                            participants=active,
                            evolution_level=args.evolution_level,
                            evolve_until=(args.evolve_until.strip() or None),
                            max_rounds=args.max_rounds,
                            test_command=args.test_command,
                            lint_command=args.lint_command,
                        )
                        task_id = created['task_id']
                        current_task_id = task_id
                        print(f'[overnight] iteration={iteration} task={task_id} created')

                        final_state, final_events = wait_terminal(
                            client,
                            api_base=api_base,
                            task_id=task_id,
                            poll_seconds=args.poll_seconds,
                            task_timeout_seconds=args.task_timeout_seconds,
                            stall_timeout_seconds=args.stall_timeout_seconds,
                            event_probe_seconds=args.event_probe_seconds,
                        )
                        status = str(final_state.get('status', 'unknown'))
                        reason = final_state.get('last_gate_reason')
                        rounds = int(final_state.get('rounds_completed') or 0)
                        append_log(
                            log_path,
                            iteration=iteration,
                            task_id=task_id,
                            status=status,
                            rounds=rounds,
                            reason=reason,
                            participants=active,
                        )
                        print(f'[overnight] task={task_id} status={status} rounds={rounds} reason={reason}')

                        process_followup = recommend_process_followup_topic(status, reason)
                        if process_followup:
                            process_signal = summarize_actionable_text(str(reason or status))
                            process_description = build_task_description(
                                process_followup,
                                process_signal=process_signal,
                            )
                            enqueue_followup(process_followup, process_description, front=True)

                        self_followup = extract_self_followup_topic(final_events)
                        if self_followup:
                            self_signal = summarize_actionable_text(self_followup)
                            self_description = build_task_description(
                                self_followup,
                                self_signal=self_signal,
                            )
                            enqueue_followup(self_followup, self_description, front=False)

                        if should_switch_to_fallback(status, reason):
                            active = fallback
                            print('[overnight] switched to fallback participants due to system failure signal')
                            if is_provider_limit_reason(reason, provider='claude'):
                                primary_disabled_until = datetime.now() + timedelta(seconds=max(60, int(args.primary_disable_seconds)))
                                print(
                                    '[overnight] primary participants temporarily disabled until '
                                    f'{primary_disabled_until.isoformat()} due to claude provider_limit'
                                )
                        elif should_switch_back_to_primary(status, reason):
                            if primary_disabled_until and datetime.now() < primary_disabled_until:
                                print('[overnight] primary still in cooldown window, staying on fallback participants')
                            else:
                                active = primary
                                print('[overnight] switched back to primary participants due to codex failure signal')

                        if status == 'failed_system':
                            consecutive_system_failures += 1
                        else:
                            consecutive_system_failures = 0

                        if consecutive_system_failures >= max(1, int(args.max_consecutive_system_failures)):
                            print(
                                f'[overnight] cooling down for {args.cooldown_seconds}s after '
                                f'{consecutive_system_failures} consecutive system failures'
                            )
                            time.sleep(max(1, int(args.cooldown_seconds)))
                            consecutive_system_failures = 0

                    except Exception as exc:
                        print(f'[overnight] iteration={iteration} error={exc}', file=sys.stderr)
                        append_log(
                            log_path,
                            iteration=iteration,
                            task_id=current_task_id,
                            status='driver_error',
                            rounds=0,
                            reason=str(exc),
                            participants=active,
                        )
                        if 'claude' in str(exc).lower():
                            active = fallback
                            print('[overnight] switched to fallback participants due to claude-related error')
                            if is_provider_limit_reason(str(exc), provider='claude'):
                                primary_disabled_until = datetime.now() + timedelta(seconds=max(60, int(args.primary_disable_seconds)))
                                print(
                                    '[overnight] primary participants temporarily disabled until '
                                    f'{primary_disabled_until.isoformat()} due to claude provider_limit'
                                )
                        elif 'codex' in str(exc).lower():
                            if primary_disabled_until and datetime.now() < primary_disabled_until:
                                print('[overnight] primary still in cooldown window, staying on fallback participants')
                            else:
                                active = primary
                                print('[overnight] switched back to primary participants due to codex-related error')
                        process_followup = recommend_process_followup_topic('failed_system', str(exc))
                        if process_followup:
                            process_signal = summarize_actionable_text(str(exc))
                            process_description = build_task_description(
                                process_followup,
                                process_signal=process_signal,
                            )
                            enqueue_followup(process_followup, process_description, front=True)

                    time.sleep(max(1, args.idle_seconds))
    except RuntimeError as exc:
        print(f'[overnight] {exc}', file=sys.stderr)
        return 2

    print('[overnight] completed')
    print(f'[overnight] results: {log_path}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
