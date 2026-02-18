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
    assert 'gemini --yolo' in DEFAULT_COMMANDS['gemini']


def test_default_commands_set_codex_reasoning_to_xhigh():
    assert 'codex' in DEFAULT_COMMANDS
    assert 'model_reasoning_effort=xhigh' in DEFAULT_COMMANDS['codex']


def test_default_commands_set_claude_model_to_opus_4_6():
    assert 'claude' in DEFAULT_COMMANDS
    assert '--model claude-opus-4-6' in DEFAULT_COMMANDS['claude']


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


def test_participant_runner_stops_retry_when_total_timeout_budget_is_exhausted(tmp_path: Path, monkeypatch):
    clock = {'now': 100.0}
    calls = {'n': 0, 'timeouts': []}

    def fake_monotonic():
        return clock['now']

    def fake_sleep(seconds: float):
        clock['now'] += float(seconds)

    def fake_run(*args, **kwargs):
        calls['n'] += 1
        timeout = float(kwargs.get('timeout', 0.0))
        calls['timeouts'].append(timeout)
        # Simulate a timeout that also consumes the remaining budget.
        clock['now'] += timeout + 10.0
        raise subprocess.TimeoutExpired(cmd='claude', timeout=timeout)

    monkeypatch.setattr('awe_agentcheck.adapters.time.monotonic', fake_monotonic)
    monkeypatch.setattr('awe_agentcheck.adapters.time.sleep', fake_sleep)
    monkeypatch.setattr('awe_agentcheck.adapters.subprocess.run', fake_run)
    runner = ParticipantRunner(command_overrides={'claude': 'claude -p'}, dry_run=False, timeout_retries=1)

    with pytest.raises(RuntimeError, match='command_timeout'):
        runner.run(
            participant=parse_participant_id('claude#author-A'),
            prompt='hello',
            cwd=tmp_path,
            timeout_seconds=4,
        )

    assert calls['n'] == 1
    assert calls['timeouts'] == [pytest.approx(2.0)]


def test_compute_attempt_timeout_does_not_exceed_remaining_budget():
    timeout = ParticipantRunner._compute_attempt_timeout_seconds(remaining_budget=0.01, attempts_left=3)
    assert timeout == pytest.approx(0.01)


def test_participant_runner_keeps_retry_slice_when_backoff_would_consume_budget(tmp_path: Path, monkeypatch):
    clock = {'now': 300.0}
    calls = {'n': 0, 'timeouts': []}
    sleeps = []

    def fake_monotonic():
        return clock['now']

    def fake_sleep(seconds: float):
        sleeps.append(float(seconds))
        clock['now'] += float(seconds)

    def fake_run(*args, **kwargs):
        calls['n'] += 1
        timeout = float(kwargs.get('timeout', 0.0))
        calls['timeouts'].append(timeout)
        if calls['n'] == 1:
            clock['now'] += timeout
            raise subprocess.TimeoutExpired(cmd='claude', timeout=timeout)
        return subprocess.CompletedProcess(args=['claude'], returncode=0, stdout='VERDICT: NO_BLOCKER', stderr='')

    monkeypatch.setattr('awe_agentcheck.adapters.time.monotonic', fake_monotonic)
    monkeypatch.setattr('awe_agentcheck.adapters.time.sleep', fake_sleep)
    monkeypatch.setattr('awe_agentcheck.adapters.random.uniform', lambda _a, _b: 0.0)
    monkeypatch.setattr('awe_agentcheck.adapters.subprocess.run', fake_run)
    runner = ParticipantRunner(command_overrides={'claude': 'claude -p'}, dry_run=False, timeout_retries=1)

    result = runner.run(
        participant=parse_participant_id('claude#author-A'),
        prompt='hello',
        cwd=tmp_path,
        timeout_seconds=0.2,
    )

    assert result.returncode == 0
    assert calls['n'] == 2
    assert calls['timeouts'][0] == pytest.approx(0.1)
    assert calls['timeouts'][1] > 0
    assert calls['timeouts'][1] <= 0.05 + 1e-9
    assert sleeps
    assert sleeps[0] <= 0.05 + 1e-9


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


@pytest.mark.parametrize(
    ('participant_id', 'base_command', 'model', 'expected_flag'),
    [
        ('claude#author-A', 'claude -p', 'claude-sonnet-4-5', '--model'),
        ('codex#review-B', 'codex exec', 'gpt-5-codex', '-m'),
        ('gemini#review-C', 'gemini -p', 'gemini-2.5-pro', '-m'),
    ],
)
def test_participant_runner_appends_model_flag_per_provider(
    tmp_path: Path,
    monkeypatch,
    participant_id: str,
    base_command: str,
    model: str,
    expected_flag: str,
):
    captured = {'argv': None}

    def fake_run(argv, **kwargs):
        captured['argv'] = list(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout='VERDICT: NO_BLOCKER', stderr='')

    monkeypatch.setattr('awe_agentcheck.adapters.subprocess.run', fake_run)
    participant = parse_participant_id(participant_id)
    runner = ParticipantRunner(command_overrides={participant.provider: base_command}, dry_run=False)
    runner.run(
        participant=participant,
        prompt='hello',
        cwd=tmp_path,
        timeout_seconds=1,
        model=model,
    )

    assert captured['argv'] is not None
    assert expected_flag in captured['argv']
    assert model in captured['argv']


def test_participant_runner_appends_claude_team_agents_flag_when_enabled(tmp_path: Path, monkeypatch):
    captured = {'argv': None}

    def fake_run(argv, **kwargs):
        captured['argv'] = list(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout='VERDICT: NO_BLOCKER', stderr='')

    monkeypatch.setattr('awe_agentcheck.adapters.subprocess.run', fake_run)
    runner = ParticipantRunner(command_overrides={'claude': 'claude -p'}, dry_run=False)
    runner.run(
        participant=parse_participant_id('claude#author-A'),
        prompt='hello',
        cwd=tmp_path,
        timeout_seconds=1,
        claude_team_agents=True,
    )

    assert captured['argv'] is not None
    assert '--agents' in captured['argv']


def test_participant_runner_appends_codex_multi_agent_flag_when_enabled(tmp_path: Path, monkeypatch):
    captured = {'argv': None}

    def fake_run(argv, **kwargs):
        captured['argv'] = list(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout='VERDICT: NO_BLOCKER', stderr='')

    monkeypatch.setattr('awe_agentcheck.adapters.subprocess.run', fake_run)
    runner = ParticipantRunner(command_overrides={'codex': 'codex exec --skip-git-repo-check'}, dry_run=False)
    runner.run(
        participant=parse_participant_id('codex#author-A'),
        prompt='hello',
        cwd=tmp_path,
        timeout_seconds=1,
        codex_multi_agents=True,
    )

    assert captured['argv'] is not None
    assert '--enable' in captured['argv']
    assert 'multi_agent' in captured['argv']


def test_participant_runner_appends_provider_model_params_tokens(tmp_path: Path, monkeypatch):
    captured = {'argv': None}

    def fake_run(argv, **kwargs):
        captured['argv'] = list(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout='VERDICT: NO_BLOCKER', stderr='')

    monkeypatch.setattr('awe_agentcheck.adapters.subprocess.run', fake_run)
    runner = ParticipantRunner(command_overrides={'codex': 'codex exec'}, dry_run=False)
    runner.run(
        participant=parse_participant_id('codex#author-A'),
        prompt='hello',
        cwd=tmp_path,
        timeout_seconds=1,
        model='gpt-5.3-codex',
        model_params='-c model_reasoning_effort=high --temperature 0.1',
    )

    assert captured['argv'] is not None
    assert '-m' in captured['argv']
    assert 'gpt-5.3-codex' in captured['argv']
    assert '-c' in captured['argv']
    assert 'model_reasoning_effort=high' in captured['argv']
    assert '--temperature' in captured['argv']
    assert '0.1' in captured['argv']


def test_participant_runner_deduplicates_conflicting_gemini_approval_flags(tmp_path: Path, monkeypatch):
    captured = {'argv': None}

    def fake_run(argv, **kwargs):
        captured['argv'] = list(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout='VERDICT: NO_BLOCKER', stderr='')

    monkeypatch.setattr('awe_agentcheck.adapters.subprocess.run', fake_run)
    runner = ParticipantRunner(command_overrides={'gemini': 'gemini --yolo'}, dry_run=False)
    runner.run(
        participant=parse_participant_id('gemini#review-B'),
        prompt='hello',
        cwd=tmp_path,
        timeout_seconds=1,
        model='gemini-3-pro-preview',
        model_params='--approval-mode yolo',
    )

    assert captured['argv'] is not None
    assert '--approval-mode' in captured['argv']
    assert 'yolo' in captured['argv']
    assert '--yolo' not in captured['argv']
    assert '-y' not in captured['argv']


def test_participant_runner_uses_gemini_prompt_flag_when_missing(tmp_path: Path, monkeypatch):
    captured = {'argv': None, 'input': None}

    def fake_run(argv, **kwargs):
        captured['argv'] = list(argv)
        captured['input'] = kwargs.get('input')
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout='VERDICT: NO_BLOCKER', stderr='')

    monkeypatch.setattr('awe_agentcheck.adapters.subprocess.run', fake_run)
    runner = ParticipantRunner(command_overrides={'gemini': 'gemini --approval-mode yolo'}, dry_run=False)
    runner.run(
        participant=parse_participant_id('gemini#review-B'),
        prompt='please review this',
        cwd=tmp_path,
        timeout_seconds=1,
        model='gemini-2.5-pro',
    )

    assert captured['argv'] is not None
    assert '--prompt' in captured['argv']
    assert 'please review this' in captured['argv']
    assert captured['input'] == ''


def test_participant_runner_detects_gemini_capacity_output_as_provider_limit(tmp_path: Path, monkeypatch):
    def fake_run(argv, **kwargs):
        return subprocess.CompletedProcess(
            args=argv,
            returncode=1,
            stdout='',
            stderr='No capacity available for model gemini-3-pro-preview on the server',
        )

    monkeypatch.setattr('awe_agentcheck.adapters.subprocess.run', fake_run)
    runner = ParticipantRunner(command_overrides={'gemini': 'gemini --approval-mode yolo'}, dry_run=False)
    with pytest.raises(RuntimeError, match='provider_limit'):
        runner.run(
            participant=parse_participant_id('gemini#review-B'),
            prompt='hello',
            cwd=tmp_path,
            timeout_seconds=1,
            model='gemini-3-pro-preview',
        )


def test_participant_runner_resolves_executable_with_shutil_which(tmp_path: Path, monkeypatch):
    captured = {'argv': None}

    def fake_which(name: str):
        if name == 'codex':
            return r'C:\Users\hangw\AppData\Roaming\npm\codex.cmd'
        return None

    def fake_run(argv, **kwargs):
        captured['argv'] = list(argv)
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout='VERDICT: NO_BLOCKER', stderr='')

    monkeypatch.setattr('awe_agentcheck.adapters.shutil.which', fake_which)
    monkeypatch.setattr('awe_agentcheck.adapters.subprocess.run', fake_run)
    runner = ParticipantRunner(command_overrides={'codex': 'codex exec'}, dry_run=False)
    runner.run(
        participant=parse_participant_id('codex#author-A'),
        prompt='hello',
        cwd=tmp_path,
        timeout_seconds=1,
    )

    assert captured['argv'] is not None
    assert captured['argv'][0].lower().endswith('codex.cmd')


def test_participant_runner_stream_callback_receives_chunks(tmp_path: Path, monkeypatch):
    captured = []

    def fake_streaming(*, argv, runtime_input, cwd, timeout_seconds, on_stream):
        on_stream('stdout', 'line-1\n')
        on_stream('stderr', 'warn-1\n')
        return subprocess.CompletedProcess(
            args=argv,
            returncode=0,
            stdout='line-1\nVERDICT: NO_BLOCKER\n',
            stderr='warn-1\n',
        )

    monkeypatch.setattr('awe_agentcheck.adapters.ParticipantRunner._run_streaming', staticmethod(fake_streaming))
    runner = ParticipantRunner(command_overrides={'claude': 'claude -p'}, dry_run=False)
    result = runner.run(
        participant=parse_participant_id('claude#author-A'),
        prompt='hello',
        cwd=tmp_path,
        timeout_seconds=1,
        on_stream=lambda stream_name, chunk: captured.append((stream_name, chunk)),
    )

    assert result.returncode == 0
    assert result.verdict == 'no_blocker'
    assert ('stdout', 'line-1\n') in captured
    assert ('stderr', 'warn-1\n') in captured


def test_participant_runner_streaming_timeout_retry_shares_budget_and_adds_backoff(tmp_path: Path, monkeypatch):
    clock = {'now': 200.0}
    calls = {'n': 0, 'timeouts': [], 'inputs': []}
    sleeps = []
    streamed = []

    def fake_monotonic():
        return clock['now']

    def fake_sleep(seconds: float):
        sleeps.append(float(seconds))
        clock['now'] += float(seconds)

    def fake_streaming(*, argv, runtime_input, cwd, timeout_seconds, on_stream):
        calls['n'] += 1
        calls['timeouts'].append(float(timeout_seconds))
        calls['inputs'].append(runtime_input)
        if calls['n'] == 1:
            clock['now'] += float(timeout_seconds)
            raise subprocess.TimeoutExpired(cmd='claude', timeout=timeout_seconds)
        on_stream('stdout', 'VERDICT: NO_BLOCKER\n')
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout='VERDICT: NO_BLOCKER\n', stderr='')

    monkeypatch.setattr('awe_agentcheck.adapters.time.monotonic', fake_monotonic)
    monkeypatch.setattr('awe_agentcheck.adapters.time.sleep', fake_sleep)
    monkeypatch.setattr('awe_agentcheck.adapters.ParticipantRunner._run_streaming', staticmethod(fake_streaming))
    runner = ParticipantRunner(command_overrides={'claude': 'claude -p'}, dry_run=False, timeout_retries=1)

    result = runner.run(
        participant=parse_participant_id('claude#author-A'),
        prompt='A' * 5000,
        cwd=tmp_path,
        timeout_seconds=4,
        on_stream=lambda stream_name, chunk: streamed.append((stream_name, chunk)),
    )

    assert result.returncode == 0
    assert calls['n'] == 2
    assert calls['timeouts'][0] == pytest.approx(2.0)
    assert calls['timeouts'][1] < calls['timeouts'][0]
    assert sleeps
    assert 0.05 <= sleeps[0] <= 1.0
    assert len(calls['inputs'][1]) < len(calls['inputs'][0])
    assert ('stdout', 'VERDICT: NO_BLOCKER\n') in streamed
