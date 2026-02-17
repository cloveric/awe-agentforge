from pathlib import Path
import pytest
import subprocess

from awe_agentcheck.adapters import DEFAULT_COMMANDS, ParticipantRunner, parse_verdict, parse_next_action
from awe_agentcheck.participants import parse_participant_id


def test_parse_verdict_from_control_line():
    output = "some analysis\nVERDICT: BLOCKER\n"
    assert parse_verdict(output) == 'blocker'


def test_parse_verdict_defaults_unknown():
    output = "no control line"
    assert parse_verdict(output) == 'unknown'


def test_parse_next_action_from_control_line():
    output = "NEXT_ACTION: retry\n"
    assert parse_next_action(output) == 'retry'


def test_default_commands_include_gemini_provider():
    assert 'gemini' in DEFAULT_COMMANDS
    assert 'gemini -p' in DEFAULT_COMMANDS['gemini']


def test_participant_runner_dry_run_returns_simulated_output(tmp_path: Path):
    runner = ParticipantRunner(dry_run=True)
    result = runner.run(
        participant=parse_participant_id('claude#author-A'),
        prompt='hello',
        cwd=tmp_path,
    )
    assert result.returncode == 0
    assert result.verdict == 'no_blocker'


def test_participant_runner_reports_command_not_found_with_provider_context(tmp_path: Path):
    runner = ParticipantRunner(command_overrides={'claude': 'this_binary_should_not_exist_12345'}, dry_run=False)
    with pytest.raises(RuntimeError, match='command_not_found'):
        runner.run(
            participant=parse_participant_id('claude#author-A'),
            prompt='hello',
            cwd=tmp_path,
        )


def test_participant_runner_retries_once_on_timeout_and_succeeds(tmp_path: Path, monkeypatch):
    calls = {'n': 0, 'inputs': []}

    def fake_run(*args, **kwargs):
        calls['n'] += 1
        calls['inputs'].append(kwargs.get('input', ''))
        if calls['n'] == 1:
            raise subprocess.TimeoutExpired(cmd='claude', timeout=1)
        return subprocess.CompletedProcess(args=['claude'], returncode=0, stdout='VERDICT: NO_BLOCKER', stderr='')

    monkeypatch.setattr('awe_agentcheck.adapters.subprocess.run', fake_run)
    runner = ParticipantRunner(command_overrides={'claude': 'claude -p'}, dry_run=False, timeout_retries=1)
    result = runner.run(
        participant=parse_participant_id('claude#author-A'),
        prompt='A' * 5000,
        cwd=tmp_path,
        timeout_seconds=1,
    )
    assert result.returncode == 0
    assert calls['n'] == 2
    assert len(calls['inputs'][1]) < len(calls['inputs'][0])


def test_participant_runner_raises_timeout_after_retries_exhausted(tmp_path: Path, monkeypatch):
    calls = {'n': 0}

    def fake_run(*args, **kwargs):
        calls['n'] += 1
        raise subprocess.TimeoutExpired(cmd='claude', timeout=1)

    monkeypatch.setattr('awe_agentcheck.adapters.subprocess.run', fake_run)
    runner = ParticipantRunner(command_overrides={'claude': 'claude -p'}, dry_run=False, timeout_retries=1)
    with pytest.raises(RuntimeError, match='command_timeout'):
        runner.run(
            participant=parse_participant_id('claude#author-A'),
            prompt='hello',
            cwd=tmp_path,
            timeout_seconds=1,
        )
    assert calls['n'] == 2


def test_participant_runner_raises_provider_limit_when_cli_reports_quota_message(tmp_path: Path, monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=['claude'],
            returncode=0,
            stdout="You've hit your limit · resets 2pm (Asia/Shanghai)",
            stderr='',
        )

    monkeypatch.setattr('awe_agentcheck.adapters.subprocess.run', fake_run)
    runner = ParticipantRunner(command_overrides={'claude': 'claude -p'}, dry_run=False)
    with pytest.raises(RuntimeError, match='provider_limit'):
        runner.run(
            participant=parse_participant_id('claude#author-A'),
            prompt='hello',
            cwd=tmp_path,
            timeout_seconds=1,
        )
