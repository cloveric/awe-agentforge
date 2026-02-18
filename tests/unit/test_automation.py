from datetime import datetime
import awe_agentcheck.automation as automation
import pytest

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


def test_parse_until_supports_common_formats():
    dt = parse_until('2026-02-12 07:00')
    assert dt == datetime(2026, 2, 12, 7, 0)


def test_parse_until_supports_iso_format():
    dt = parse_until('2026-02-12T07:00:00')
    assert dt == datetime(2026, 2, 12, 7, 0, 0)


def test_should_switch_to_fallback_when_failed_system_mentions_claude():
    assert should_switch_to_fallback('failed_system', 'workflow_error: Command failed (1): claude -p') is True


def test_should_not_switch_for_non_system_failures():
    assert should_switch_to_fallback('failed_gate', 'review_blocker') is False


def test_should_switch_to_fallback_on_claude_command_not_found():
    assert (
        should_switch_to_fallback(
            'failed_system',
            'workflow_error: command_not_found provider=claude command=claude -p',
        )
        is True
    )


def test_should_switch_back_to_primary_on_codex_timeout():
    assert (
        should_switch_back_to_primary(
            'failed_system',
            'workflow_error: command_timeout provider=codex command=codex exec timeout_seconds=90',
        )
        is True
    )


def test_should_not_switch_back_to_primary_for_non_codex_reason():
    assert (
        should_switch_back_to_primary(
            'failed_system',
            'workflow_error: command_not_found provider=claude command=claude -p',
        )
        is False
    )


def test_should_switch_to_fallback_on_claude_provider_limit():
    assert (
        should_switch_to_fallback(
            'failed_system',
            'workflow_error: provider_limit provider=claude command=claude -p',
        )
        is True
    )


def test_should_switch_back_to_primary_on_codex_provider_limit():
    assert (
        should_switch_back_to_primary(
            'failed_system',
            'workflow_error: provider_limit provider=codex command=codex exec',
        )
        is True
    )


def test_is_provider_limit_reason_detects_provider_scoped_limit():
    reason = 'workflow_error: provider_limit provider=claude command=claude -p'
    assert is_provider_limit_reason(reason, provider='claude') is True
    assert is_provider_limit_reason(reason, provider='codex') is False


def test_should_retry_start_for_queued_concurrency_limit():
    assert should_retry_start_for_concurrency_limit('queued', 'concurrency_limit') is True
    assert should_retry_start_for_concurrency_limit('running', 'concurrency_limit') is False


def test_recommend_process_followup_topic_for_watchdog_timeout():
    topic = recommend_process_followup_topic('failed_system', 'watchdog_timeout: task exceeded 1200s')
    assert topic is not None
    assert 'watchdog' in topic.lower()


def test_recommend_process_followup_topic_for_concurrency_limit():
    topic = recommend_process_followup_topic('queued', 'concurrency_limit')
    assert topic is not None
    assert 'concurrency' in topic.lower()


def test_summarize_actionable_text_skips_noise_headers():
    text = (
        'OpenAI Codex v0.101.0\n'
        'VERDICT: BLOCKER\n'
        'Issue: API can deadlock when cancel races with start.\n'
    )
    summary = summarize_actionable_text(text)
    assert 'deadlock' in summary.lower()


def test_extract_self_followup_topic_prefers_blocker_review():
    events = [
        {
            'type': 'review',
            'payload': {
                'verdict': 'blocker',
                'output': 'Issue: start/cancel transition can race and leave task stuck running.',
            },
        }
    ]
    topic = extract_self_followup_topic(events)
    assert topic is not None
    assert 'reviewer concern' in topic.lower()


def test_extract_self_followup_topic_review_gate_prefers_reviewer_summary():
    events = [
        {
            'type': 'review',
            'payload': {
                'verdict': 'blocker',
                'output': 'Issue: API can deadlock when cancel races with start.',
            },
        },
        {'type': 'gate_failed', 'payload': {'reason': 'review_blocker'}},
    ]
    topic = extract_self_followup_topic(events)
    assert topic is not None
    assert topic.lower().startswith('address reviewer concern:')
    assert 'deadlock' in topic.lower()


def test_extract_self_followup_topic_non_review_gate_stays_gate_reason():
    events = [
        {
            'type': 'review',
            'payload': {
                'verdict': 'blocker',
                'output': 'Issue: start/cancel transition can race and leave task stuck running.',
            },
        },
        {'type': 'gate_failed', 'payload': {'reason': 'tests_failed'}},
    ]
    topic = extract_self_followup_topic(events)
    assert topic is not None
    assert topic.lower().startswith('address gate failure cause:')
    assert 'tests_failed' in topic.lower()


def test_extract_self_followup_topic_review_gate_falls_back_without_summary():
    events = [
        {'type': 'review', 'payload': {'verdict': 'blocker', 'output': '   '}},
        {'type': 'gate_failed', 'payload': {'reason': 'review_blocker'}},
    ]
    topic = extract_self_followup_topic(events)
    assert topic == 'Address gate failure cause: review_blocker'


def test_extract_self_followup_topic_from_runtime_error():
    events = [
        {
            'type': 'proposal_discussion_error',
            'payload': {'reason': 'command_timeout provider=codex command=codex exec timeout_seconds=240'},
        }
    ]
    topic = extract_self_followup_topic(events)
    assert topic is not None
    assert 'runtime error' in topic.lower()


def test_acquire_single_instance_creates_and_releases_lock(tmp_path):
    lock = tmp_path / 'overnight.lock'
    pid = 888

    with acquire_single_instance(lock, pid=pid):
        assert lock.exists() is True
        assert str(pid) in lock.read_text(encoding='utf-8')

    assert lock.exists() is False


def test_acquire_single_instance_rejects_existing_live_pid(tmp_path):
    lock = tmp_path / 'overnight.lock'
    lock.write_text('123\n', encoding='utf-8')

    with pytest.raises(RuntimeError, match='pid=123'):
        with acquire_single_instance(lock, pid=888, pid_exists=lambda p: p == 123):
            pass


def test_acquire_single_instance_reclaims_stale_lock(tmp_path):
    lock = tmp_path / 'overnight.lock'
    lock.write_text('123\n', encoding='utf-8')

    with acquire_single_instance(lock, pid=888, pid_exists=lambda p: False):
        assert lock.exists() is True
        assert lock.read_text(encoding='utf-8').startswith('888')


def test_acquire_single_instance_race_on_open_returns_stable_error(tmp_path, monkeypatch):
    lock = tmp_path / 'overnight.lock'

    def raise_file_exists(*args, **kwargs):  # noqa: ANN002, ANN003
        raise FileExistsError()

    monkeypatch.setattr(automation.os, 'open', raise_file_exists)

    with pytest.raises(RuntimeError, match='^lock already held$'):
        with acquire_single_instance(lock, pid=888, pid_exists=lambda p: False):
            pass
