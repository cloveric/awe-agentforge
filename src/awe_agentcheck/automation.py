from __future__ import annotations

import ctypes
from datetime import datetime
from contextlib import contextmanager
import os
from pathlib import Path
import re
from typing import Callable, Iterator


def parse_until(value: str) -> datetime:
    text = (value or '').strip()
    if not text:
        raise ValueError('until datetime cannot be empty')

    for fmt in ('%Y-%m-%d %H:%M', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue

    try:
        # Accept ISO style like 2026-02-12T07:00:00
        return datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f'invalid datetime format: {value}') from exc


def should_switch_to_fallback(status: str, reason: str | None) -> bool:
    s = (status or '').strip().lower()
    r = (reason or '').strip().lower()
    if s != 'failed_system':
        return False
    return 'claude' in r or 'command failed' in r


def should_switch_back_to_primary(status: str, reason: str | None) -> bool:
    s = (status or '').strip().lower()
    r = (reason or '').strip().lower()
    if s != 'failed_system':
        return False
    if 'provider=codex' in r and ('command_timeout' in r or 'command_not_found' in r or 'provider_limit' in r):
        return True
    return False


def is_provider_limit_reason(reason: str | None, *, provider: str | None = None) -> bool:
    text = (reason or '').strip().lower()
    if 'provider_limit' not in text:
        return False
    if provider:
        return f'provider={provider.strip().lower()}' in text
    return True


def should_retry_start_for_concurrency_limit(status: str, reason: str | None) -> bool:
    s = (status or '').strip().lower()
    r = (reason or '').strip().lower()
    return s == 'queued' and 'concurrency_limit' in r


def recommend_process_followup_topic(status: str, reason: str | None) -> str | None:
    s = (status or '').strip().lower()
    r = (reason or '').strip().lower()

    if not s and not r:
        return None
    if 'watchdog_stall' in r or 'watchdog_timeout' in r:
        return 'Harden watchdog stall/timeout recovery and task cancellation'
    if 'concurrency_limit' in r:
        return 'Improve start queue handoff and running-slot concurrency flow'
    if 'provider_limit' in r:
        return 'Improve provider-limit fallback, cooldown, and retry behavior'
    if 'command_timeout' in r:
        return 'Reduce participant command timeout risk and improve retry strategy'
    if 'command_not_found' in r:
        return 'Harden CLI executable detection and command bootstrapping'
    if 'auto_merge_error' in r:
        return 'Improve auto-merge failure recovery and snapshot/changelog safety'
    if 'proposal_consensus_stalled' in r or ('proposal_consensus_not_reached' in r):
        return 'Improve proposal consensus loop clarity and disagreement resolution'
    if s == 'failed_system':
        return 'Stabilize workflow system-failure handling and recovery'
    return None


_NOISE_LINE_PATTERNS = (
    r'^\s*VERDICT\s*:\s*',
    r'^\s*NEXT_ACTION\s*:\s*',
    r'^\s*OpenAI Codex v',
    r'^\s*Reading prompt from stdin',
    r'^\s*tokens used\s*$',
    r'^\s*workdir\s*:',
    r'^\s*model\s*:',
    r'^\s*provider\s*:',
    r'^\s*approval\s*:',
    r'^\s*sandbox\s*:',
    r'^\s*reasoning effort\s*:',
    r'^\s*reasoning summaries\s*:',
    r'^\s*session id\s*:',
    r'^\s*mcp\s*:',
    r'^\s*[-]{4,}\s*$',
    r'^\s*[{}\[\],]+\s*$',
)
_NOISE_LINE_REGEX = [re.compile(p, re.IGNORECASE) for p in _NOISE_LINE_PATTERNS]


def summarize_actionable_text(text: str, *, max_chars: int = 180) -> str:
    content = str(text or '').replace('\r\n', '\n')
    if not content.strip():
        return ''

    for raw in content.split('\n'):
        line = str(raw or '').strip()
        if not line:
            continue
        if len(line) < 8:
            continue
        if any(rx.match(line) for rx in _NOISE_LINE_REGEX):
            continue
        if line.lower() in {'n/a', 'none', 'null', 'unknown'}:
            continue
        cleaned = re.sub(r'\s+', ' ', line).strip()
        if len(cleaned) > max_chars:
            return cleaned[: max_chars - 3].rstrip() + '...'
        return cleaned

    compact = re.sub(r'\s+', ' ', content).strip()
    if len(compact) > max_chars:
        return compact[: max_chars - 3].rstrip() + '...'
    return compact


def extract_self_followup_topic(events: list[dict]) -> str | None:
    all_events = list(events or [])

    def latest_blocker_review_summary() -> str:
        for candidate_raw in reversed(all_events):
            if not isinstance(candidate_raw, dict):
                continue
            candidate_type = str(candidate_raw.get('type') or '').strip().lower()
            if candidate_type not in {'review', 'proposal_review', 'debate_review'}:
                continue
            candidate_payload = candidate_raw.get('payload')
            candidate_payload_data = candidate_payload if isinstance(candidate_payload, dict) else {}
            verdict = str(candidate_payload_data.get('verdict') or '').strip().lower()
            if verdict not in {'blocker', 'unknown'}:
                continue
            summary = summarize_actionable_text(str(candidate_payload_data.get('output') or candidate_raw.get('output') or ''))
            if summary:
                return summary
        return ''

    for raw in reversed(all_events):
        if not isinstance(raw, dict):
            continue
        event_type = str(raw.get('type') or '').strip().lower()
        payload = raw.get('payload')
        payload_data = payload if isinstance(payload, dict) else {}

        if event_type in {
            'review_error',
            'discussion_error',
            'implementation_error',
            'proposal_discussion_error',
            'proposal_precheck_review_error',
            'proposal_review_error',
        }:
            reason = summarize_actionable_text(str(payload_data.get('reason') or raw.get('reason') or ''))
            if reason:
                return f'Fix loop runtime error: {reason}'

        if event_type == 'gate_failed':
            reason = summarize_actionable_text(str(payload_data.get('reason') or raw.get('reason') or ''))
            if reason:
                if reason.lower() in {'review_blocker', 'review_unknown'}:
                    summary = latest_blocker_review_summary()
                    if summary:
                        return f'Address reviewer concern: {summary}'
                return f'Address gate failure cause: {reason}'

        if event_type in {'review', 'proposal_review', 'debate_review'}:
            verdict = str(payload_data.get('verdict') or '').strip().lower()
            if verdict not in {'blocker', 'unknown'}:
                continue
            summary = summarize_actionable_text(str(payload_data.get('output') or raw.get('output') or ''))
            if summary:
                return f'Address reviewer concern: {summary}'

    return None


def _pid_exists_default(pid: int) -> bool:
    if pid <= 0:
        return False
    if os.name == 'nt':
        process_query_limited_information = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(process_query_limited_information, False, pid)
        if handle == 0:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _read_lock_pid(lock_path: Path) -> int | None:
    try:
        content = lock_path.read_text(encoding='utf-8').strip()
    except FileNotFoundError:
        return None
    if not content:
        return None
    first_line = content.splitlines()[0].strip()
    try:
        return int(first_line)
    except ValueError:
        return None


@contextmanager
def acquire_single_instance(
    lock_path: Path,
    *,
    pid: int | None = None,
    pid_exists: Callable[[int], bool] | None = None,
) -> Iterator[None]:
    target = Path(lock_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    current_pid = pid or os.getpid()
    pid_exists_fn = pid_exists or _pid_exists_default

    existing_pid = _read_lock_pid(target)
    if existing_pid is not None and pid_exists_fn(existing_pid):
        raise RuntimeError(f'lock already held by pid={existing_pid}')
    if target.exists():
        target.unlink()

    try:
        fd = os.open(target, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise RuntimeError('lock already held') from exc
    try:
        payload = f'{current_pid}\n{datetime.now().isoformat()}\n'
        os.write(fd, payload.encode('utf-8'))
    finally:
        os.close(fd)

    try:
        yield
    finally:
        owner_pid = _read_lock_pid(target)
        if owner_pid is None or owner_pid == current_pid:
            try:
                target.unlink()
            except FileNotFoundError:
                pass
