from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import subprocess

from awe_agentcheck.adapters import AdapterResult
from awe_agentcheck.participants import parse_participant_id
from awe_agentcheck.workflow import CommandResult, RunConfig, ShellCommandExecutor, WorkflowEngine


class FakeRunner:
    def __init__(self, outputs: list[AdapterResult]):
        self.outputs = outputs
        self.calls = 0
        self.timeouts: list[int] = []
        self.prompts: list[str] = []
        self.participants: list[str] = []
        self.call_options: list[dict] = []

    def run(self, *, participant, prompt, cwd, timeout_seconds=900, **kwargs):
        idx = min(self.calls, len(self.outputs) - 1)
        self.calls += 1
        self.timeouts.append(timeout_seconds)
        self.prompts.append(prompt)
        self.participants.append(str(getattr(participant, 'participant_id', '')))
        self.call_options.append(dict(kwargs))
        return self.outputs[idx]


class FakeCommandExecutor:
    def __init__(self, *, tests_ok=True, lint_ok=True):
        self.tests_ok = tests_ok
        self.lint_ok = lint_ok
        self.timeouts: list[int] = []

    def run(self, command: str, cwd: Path, timeout_seconds: int) -> CommandResult:
        self.timeouts.append(timeout_seconds)
        if 'pytest' in command:
            return CommandResult(ok=self.tests_ok, command=command, returncode=0 if self.tests_ok else 1, stdout='', stderr='')
        return CommandResult(ok=self.lint_ok, command=command, returncode=0 if self.lint_ok else 1, stdout='', stderr='')


class ReviewerFailureRunner:
    def __init__(self):
        self.calls = 0

    def run(self, *, participant, prompt, cwd, timeout_seconds=900, **kwargs):
        self.calls += 1
        if participant.participant_id == 'gemini#review-C':
            raise RuntimeError('provider_limit provider=gemini command=gemini -m gemini-3-pro-preview')
        return _ok_result('no_blocker')


class StreamingRunner:
    def __init__(self, outputs: list[AdapterResult]):
        self.outputs = outputs
        self.calls = 0

    def run(self, *, participant, prompt, cwd, timeout_seconds=900, **kwargs):
        callback = kwargs.get('on_stream')
        if callable(callback):
            callback('stdout', f'{participant.participant_id}: stream-line\n')
        idx = min(self.calls, len(self.outputs) - 1)
        self.calls += 1
        return self.outputs[idx]


class DebateReviewerTimeoutRunner:
    def __init__(self):
        self.calls = 0
        self.participants: list[str] = []

    def run(self, *, participant, prompt, cwd, timeout_seconds=900, **kwargs):
        self.calls += 1
        self.participants.append(str(getattr(participant, 'participant_id', '')))
        if self.calls == 1:
            raise RuntimeError('command_timeout provider=claude command=claude -p timeout_seconds=75')
        return _ok_result()


class EventSink:
    def __init__(self):
        self.events = []

    def __call__(self, event: dict):
        self.events.append(event)


def _ok_result(verdict: str = 'no_blocker') -> AdapterResult:
    line = f'VERDICT: {verdict.upper()}'
    return AdapterResult(output=line, verdict=verdict, next_action=None, returncode=0, duration_seconds=0.1)


def test_workflow_passes_on_first_round(tmp_path: Path):
    runner = FakeRunner([
        _ok_result(),  # discussion
        _ok_result(),  # implementation
        _ok_result(),  # reviewer A
        _ok_result(),  # reviewer B
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()

    engine = WorkflowEngine(runner=runner, command_executor=executor)
    result = engine.run(
        RunConfig(
            task_id='t1',
            title='Implement parser',
            description='do it',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B'), parse_participant_id('claude#review-C')],
            evolution_level=0,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=2,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
        ),
        on_event=sink,
    )

    assert result.status == 'passed'
    assert result.rounds == 1
    assert any(e['type'] == 'gate_passed' for e in sink.events)
    assert any(e['type'] == 'discussion_started' for e in sink.events)
    assert any(e['type'] == 'implementation_started' for e in sink.events)
    assert any(e['type'] == 'review_started' for e in sink.events)
    assert any(e['type'] == 'verification_started' for e in sink.events)


def test_workflow_retries_then_passes(tmp_path: Path):
    runner = FakeRunner([
        _ok_result(),
        _ok_result(),
        _ok_result('blocker'),
        _ok_result('no_blocker'),
        _ok_result(),
        _ok_result(),
        _ok_result('no_blocker'),
        _ok_result('no_blocker'),
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()

    engine = WorkflowEngine(runner=runner, command_executor=executor)
    result = engine.run(
        RunConfig(
            task_id='t2',
            title='Implement parser',
            description='do it',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B'), parse_participant_id('claude#review-C')],
            evolution_level=0,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=3,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
        ),
        on_event=sink,
    )

    assert result.status == 'passed'
    assert result.rounds == 2
    assert any(e['type'] == 'gate_failed' for e in sink.events)


def test_workflow_second_round_discussion_receives_previous_gate_reason(tmp_path: Path):
    runner = FakeRunner([
        _ok_result(),
        _ok_result(),
        _ok_result('blocker'),
        _ok_result('no_blocker'),
        _ok_result(),
        _ok_result(),
        _ok_result('no_blocker'),
        _ok_result('no_blocker'),
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    engine = WorkflowEngine(runner=runner, command_executor=executor)

    result = engine.run(
        RunConfig(
            task_id='t2x',
            title='Implement parser',
            description='do it',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B'), parse_participant_id('claude#review-C')],
            evolution_level=0,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=3,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
        ),
    )

    assert result.status == 'passed'
    assert 'Previous gate failure reason: review_blocker' in runner.prompts[4]


def test_workflow_honors_cancellation(tmp_path: Path):
    runner = FakeRunner([_ok_result(), _ok_result(), _ok_result('blocker')])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()

    checks = {'n': 0}

    def should_cancel():
        checks['n'] += 1
        return checks['n'] >= 2

    engine = WorkflowEngine(runner=runner, command_executor=executor)
    result = engine.run(
        RunConfig(
            task_id='t3',
            title='Implement parser',
            description='do it',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B')],
            evolution_level=0,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=5,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
        ),
        on_event=sink,
        should_cancel=should_cancel,
    )

    assert result.status == 'canceled'
    assert any(e['type'] == 'canceled' for e in sink.events)


def test_workflow_uses_configured_timeouts(tmp_path: Path):
    runner = FakeRunner([
        _ok_result(),
        _ok_result(),
        _ok_result(),
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    engine = WorkflowEngine(
        runner=runner,
        command_executor=executor,
        participant_timeout_seconds=11,
        command_timeout_seconds=22,
    )

    result = engine.run(
        RunConfig(
            task_id='t4',
            title='Timeout config',
            description='verify timeouts',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B')],
            evolution_level=0,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=1,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
        )
    )

    assert result.status == 'passed'
    assert runner.timeouts == [11, 11, 11]
    assert executor.timeouts == [22, 22]


def test_workflow_review_timeout_follows_participant_timeout_when_large(tmp_path: Path):
    runner = FakeRunner([
        _ok_result(),  # discussion
        _ok_result(),  # implementation
        _ok_result(),  # review
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()
    engine = WorkflowEngine(
        runner=runner,
        command_executor=executor,
        participant_timeout_seconds=240,
        command_timeout_seconds=22,
    )

    result = engine.run(
        RunConfig(
            task_id='t4b',
            title='Review timeout cap',
            description='verify reviewer stage timeout cap',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B')],
            evolution_level=0,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=1,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
        ),
        on_event=sink,
    )

    assert result.status == 'passed'
    assert runner.timeouts == [240, 240, 240]
    review_started = [e for e in sink.events if e.get('type') == 'review_started']
    assert review_started
    assert int(review_started[-1].get('timeout_seconds') or 0) == 240


def test_workflow_prompts_clip_large_inputs(tmp_path: Path):
    cfg = RunConfig(
        task_id='t5',
        title='Clip test',
        description='clip',
        author=parse_participant_id('claude#author-A'),
        reviewers=[parse_participant_id('codex#review-B')],
        evolution_level=0,
        evolve_until=None,
        cwd=tmp_path,
        max_rounds=1,
        test_command='py -m pytest -q',
        lint_command='py -m ruff check .',
    )
    long_text = 'A' * 9000
    impl_prompt = WorkflowEngine._implementation_prompt(cfg, 1, long_text)
    review_prompt = WorkflowEngine._review_prompt(cfg, 1, long_text)

    assert '[truncated' in impl_prompt
    assert '[truncated' in review_prompt


def test_review_prompt_includes_strict_blocker_criteria(tmp_path: Path):
    cfg = RunConfig(
        task_id='t6',
        title='Criteria test',
        description='criteria',
        author=parse_participant_id('claude#author-A'),
        reviewers=[parse_participant_id('codex#review-B')],
        evolution_level=0,
        evolve_until=None,
        cwd=tmp_path,
        max_rounds=1,
        test_command='py -m pytest -q',
        lint_command='py -m ruff check .',
    )
    prompt = WorkflowEngine._review_prompt(cfg, 1, 'impl summary')
    assert 'correctness' in prompt
    assert 'security' in prompt
    assert 'style-only' in prompt
    assert 'VERDICT: NO_BLOCKER or VERDICT: BLOCKER or VERDICT: UNKNOWN' in prompt
    assert 'Keep output concise but complete enough to justify verdict.' in prompt


def test_prompts_include_language_instruction_for_chinese(tmp_path: Path):
    cfg = RunConfig(
        task_id='t6-zh',
        title='Language test',
        description='language',
        author=parse_participant_id('claude#author-A'),
        reviewers=[parse_participant_id('codex#review-B')],
        evolution_level=0,
        evolve_until=None,
        cwd=tmp_path,
        max_rounds=1,
        test_command='py -m pytest -q',
        lint_command='py -m ruff check .',
        conversation_language='zh',
    )
    discussion_prompt = WorkflowEngine._discussion_prompt(cfg, 1, None)
    review_prompt = WorkflowEngine._review_prompt(cfg, 1, 'impl summary')
    assert 'Simplified Chinese' in discussion_prompt
    assert 'VERDICT' in review_prompt


def test_discussion_prompt_includes_evolution_guidance_for_level_2(tmp_path: Path):
    cfg = RunConfig(
        task_id='t7',
        title='Evolve test',
        description='evolve',
        author=parse_participant_id('claude#author-A'),
        reviewers=[parse_participant_id('codex#review-B')],
        evolution_level=2,
        evolve_until=None,
        cwd=tmp_path,
        max_rounds=1,
        test_command='py -m pytest -q',
        lint_command='py -m ruff check .',
    )
    prompt = WorkflowEngine._discussion_prompt(cfg, 1, None)
    assert 'EvolutionLevel: 2' in prompt
    assert 'EVOLUTION_PROPOSAL_1' in prompt


def test_workflow_stops_when_deadline_reached(tmp_path: Path):
    runner = FakeRunner([_ok_result(), _ok_result(), _ok_result()])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    engine = WorkflowEngine(runner=runner, command_executor=executor)
    past = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(microsecond=0).isoformat()

    result = engine.run(
        RunConfig(
            task_id='t8',
            title='Deadline test',
            description='deadline',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B')],
            evolution_level=0,
            evolve_until=past,
            cwd=tmp_path,
            max_rounds=2,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
        )
    )

    assert result.status == 'canceled'
    assert result.gate_reason == 'deadline_reached'


def test_workflow_deadline_takes_priority_over_max_rounds(tmp_path: Path):
    runner = FakeRunner([
        _ok_result(),              # round1 discussion
        _ok_result(),              # round1 implementation
        _ok_result('blocker'),     # round1 review -> gate fail
        _ok_result(),              # round2 discussion
        _ok_result(),              # round2 implementation
        _ok_result('no_blocker'),  # round2 review -> gate pass
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    engine = WorkflowEngine(runner=runner, command_executor=executor)
    future = (datetime.now() + timedelta(minutes=5)).replace(microsecond=0).isoformat()

    result = engine.run(
        RunConfig(
            task_id='t8b',
            title='Deadline over rounds',
            description='deadline priority',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B')],
            evolution_level=0,
            evolve_until=future,
            cwd=tmp_path,
            max_rounds=1,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
        )
    )

    assert result.status == 'passed'
    assert result.rounds == 2


def test_workflow_stops_when_deadline_with_timezone_offset_reached(tmp_path: Path):
    runner = FakeRunner([_ok_result(), _ok_result(), _ok_result()])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    engine = WorkflowEngine(runner=runner, command_executor=executor)
    past_utc = (datetime.now(timezone.utc) - timedelta(minutes=1)).replace(microsecond=0).isoformat()

    result = engine.run(
        RunConfig(
            task_id='t8tz',
            title='Deadline timezone test',
            description='deadline with tz offset',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B')],
            evolution_level=0,
            evolve_until=past_utc,
            cwd=tmp_path,
            max_rounds=2,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
        )
    )

    assert result.status == 'canceled'
    assert result.gate_reason == 'deadline_reached'


def test_shell_command_executor_uses_shell_false(monkeypatch, tmp_path: Path):
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured['argv'] = argv
        captured['kwargs'] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout='ok', stderr='')

    monkeypatch.setattr('awe_agentcheck.workflow.subprocess.run', fake_run)
    executor = ShellCommandExecutor()

    result = executor.run(['py', '-m', 'pytest', '-q'], cwd=tmp_path, timeout_seconds=10)

    assert result.ok is True
    assert captured['argv'] == ['py', '-m', 'pytest', '-q']
    assert captured['kwargs']['shell'] is False


def test_shell_command_executor_treats_shell_metachar_as_literal(monkeypatch, tmp_path: Path):
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured['argv'] = argv
        captured['kwargs'] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout='ok', stderr='')

    monkeypatch.setattr('awe_agentcheck.workflow.subprocess.run', fake_run)
    executor = ShellCommandExecutor()

    result = executor.run('py -m pytest -q && echo injected', cwd=tmp_path, timeout_seconds=10)

    assert result.ok is True
    assert '&&' in captured['argv']
    assert captured['kwargs']['shell'] is False


def test_workflow_cancels_mid_phase_after_discussion(tmp_path: Path):
    """Cancel fires after discussion completes, before implementation starts."""
    runner = FakeRunner([_ok_result(), _ok_result(), _ok_result()])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()

    # Cancel on 2nd check_cancel call (after discussion, before implementation)
    call_count = {'n': 0}

    def should_cancel():
        call_count['n'] += 1
        # 1st call: round start check -> False
        # 2nd call: after discussion -> True
        return call_count['n'] >= 2

    engine = WorkflowEngine(runner=runner, command_executor=executor)
    result = engine.run(
        RunConfig(
            task_id='t-mid',
            title='Mid-phase cancel',
            description='cancel between phases',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B')],
            evolution_level=0,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=5,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
        ),
        on_event=sink,
        should_cancel=should_cancel,
    )

    assert result.status == 'canceled'
    # Only discussion ran, not implementation
    assert runner.calls == 1
    event_types = [e['type'] for e in sink.events]
    assert 'discussion' in event_types
    assert 'implementation' not in event_types


def test_workflow_passes_provider_models_and_claude_team_agents_to_runner(tmp_path: Path):
    runner = FakeRunner([_ok_result(), _ok_result(), _ok_result()])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    engine = WorkflowEngine(runner=runner, command_executor=executor)

    result = engine.run(
        RunConfig(
            task_id='t-model',
            title='Provider model propagation',
            description='propagate provider model config',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B')],
            evolution_level=0,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=1,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
            provider_models={'claude': 'claude-sonnet-4-5', 'codex': 'gpt-5-codex'},
            claude_team_agents=True,
        )
    )

    assert result.status == 'passed'
    assert len(runner.call_options) == 3
    assert runner.call_options[0].get('model') == 'claude-sonnet-4-5'
    assert runner.call_options[1].get('model') == 'claude-sonnet-4-5'
    assert runner.call_options[2].get('model') == 'gpt-5-codex'
    assert runner.call_options[0].get('claude_team_agents') is True


def test_workflow_passes_provider_model_params_to_runner(tmp_path: Path):
    runner = FakeRunner([_ok_result(), _ok_result(), _ok_result()])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    engine = WorkflowEngine(runner=runner, command_executor=executor)

    result = engine.run(
        RunConfig(
            task_id='t-model-params',
            title='Provider model params propagation',
            description='propagate provider model params',
            author=parse_participant_id('codex#author-A'),
            reviewers=[parse_participant_id('gemini#review-B')],
            evolution_level=0,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=1,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
            provider_models={'codex': 'gpt-5.3-codex', 'gemini': 'gemini-3-pro-preview'},
            provider_model_params={
                'codex': '-c model_reasoning_effort=high',
                'gemini': '--approval-mode yolo',
            },
        )
    )

    assert result.status == 'passed'
    assert len(runner.call_options) == 3
    assert runner.call_options[0].get('model_params') == '-c model_reasoning_effort=high'
    assert runner.call_options[1].get('model_params') == '-c model_reasoning_effort=high'
    assert runner.call_options[2].get('model_params') == '--approval-mode yolo'


def test_workflow_degrades_reviewer_exception_to_unknown_instead_of_crashing(tmp_path: Path):
    runner = ReviewerFailureRunner()
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()
    engine = WorkflowEngine(runner=runner, command_executor=executor)

    result = engine.run(
        RunConfig(
            task_id='t-reviewer-fail',
            title='Reviewer failure resilience',
            description='gemini may fail intermittently',
            author=parse_participant_id('codex#author-A'),
            reviewers=[parse_participant_id('claude#review-B'), parse_participant_id('gemini#review-C')],
            evolution_level=0,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=1,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
        ),
        on_event=sink,
    )

    assert result.status == 'failed_gate'
    assert result.gate_reason == 'review_unknown'
    assert any(e['type'] == 'review_error' and e.get('participant') == 'gemini#review-C' for e in sink.events)
    assert any(
        e['type'] == 'review' and e.get('participant') == 'gemini#review-C' and e.get('verdict') == 'unknown'
        for e in sink.events
    )


def test_workflow_prompts_include_repair_mode_guidance(tmp_path: Path):
    cfg = RunConfig(
        task_id='t-repair',
        title='Repair mode prompt',
        description='verify prompt policy',
        author=parse_participant_id('claude#author-A'),
        reviewers=[parse_participant_id('codex#review-B')],
        evolution_level=0,
        evolve_until=None,
        cwd=tmp_path,
        max_rounds=1,
        test_command='py -m pytest -q',
        lint_command='py -m ruff check .',
        repair_mode='structural',
    )
    prompt = WorkflowEngine._implementation_prompt(cfg, 1, 'plan')
    assert 'RepairMode: structural' in prompt
    assert 'structural fix allowed' in prompt.lower()


def test_workflow_prompts_include_plain_mode_guidance_by_default(tmp_path: Path):
    cfg = RunConfig(
        task_id='t-plain',
        title='Plain mode prompt',
        description='verify plain mode policy',
        author=parse_participant_id('claude#author-A'),
        reviewers=[parse_participant_id('codex#review-B')],
        evolution_level=0,
        evolve_until=None,
        cwd=tmp_path,
        max_rounds=1,
        test_command='py -m pytest -q',
        lint_command='py -m ruff check .',
    )
    prompt = WorkflowEngine._discussion_prompt(cfg, 1, None)
    assert 'Plain Mode' in prompt
    assert 'small' in prompt.lower() or 'beginner' in prompt.lower()


def test_workflow_debate_mode_adds_reviewer_author_exchange(tmp_path: Path):
    runner = FakeRunner([
        _ok_result(),  # debate review
        _ok_result(),  # discussion
        _ok_result(),  # implementation
        _ok_result(),  # gate review
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()
    engine = WorkflowEngine(runner=runner, command_executor=executor)

    result = engine.run(
        RunConfig(
            task_id='t-debate',
            title='Debate mode',
            description='verify reviewer-author exchange',
            author=parse_participant_id('codex#author-A'),
            reviewers=[parse_participant_id('claude#review-B')],
            evolution_level=0,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=1,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
            debate_mode=True,
        ),
        on_event=sink,
    )

    assert result.status == 'passed'
    assert runner.calls == 4
    assert runner.participants[0] == 'claude#review-B'
    assert runner.participants[1] == 'codex#author-A'
    assert runner.participants[2] == 'codex#author-A'
    event_types = [str(e.get('type')) for e in sink.events]
    assert 'debate_started' in event_types
    assert 'debate_review' in event_types
    assert 'debate_completed' in event_types


def test_workflow_debate_mode_stops_when_all_reviewer_precheck_unavailable(tmp_path: Path):
    runner = DebateReviewerTimeoutRunner()
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()
    engine = WorkflowEngine(runner=runner, command_executor=executor)

    result = engine.run(
        RunConfig(
            task_id='t-debate-timeout',
            title='Debate timeout',
            description='reviewer precheck unavailable',
            author=parse_participant_id('codex#author-A'),
            reviewers=[parse_participant_id('claude#review-B')],
            evolution_level=0,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=1,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
            debate_mode=True,
        ),
        on_event=sink,
    )

    assert result.status == 'failed_gate'
    assert result.gate_reason == 'debate_review_unavailable'
    assert runner.calls == 1
    event_types = [str(e.get('type')) for e in sink.events]
    assert 'debate_review_error' in event_types
    assert 'discussion_started' not in event_types
    assert any(
        str(e.get('type')) == 'gate_failed'
        and str(e.get('reason') or e.get('payload', {}).get('reason') or '') == 'debate_review_unavailable'
        for e in sink.events
    )


def test_debate_review_prompt_supports_audit_depth_guidance(tmp_path: Path):
    cfg = RunConfig(
        task_id='t-debate-prompt',
            title='Debate review audit',
            description='scan repository bugs and security risks',
        author=parse_participant_id('codex#author-A'),
        reviewers=[parse_participant_id('claude#review-B')],
        evolution_level=0,
        evolve_until=None,
        cwd=tmp_path,
        max_rounds=1,
        test_command='py -m pytest -q',
        lint_command='py -m ruff check .',
        conversation_language='zh',
        plain_mode=True,
        debate_mode=True,
    )
    context = WorkflowEngine._debate_seed_context(cfg, 1, None)
    prompt = WorkflowEngine._debate_review_prompt(cfg, 1, context, 'claude#review-B')
    assert 'repository-wide checks as needed' in prompt
    assert 'include 1-3 evidence points with file paths' in prompt
    assert 'insufficient_context:' in prompt


def test_workflow_stream_mode_emits_participant_stream_events(tmp_path: Path):
    runner = StreamingRunner([
        _ok_result(),  # discussion
        _ok_result(),  # implementation
        _ok_result(),  # review
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()
    engine = WorkflowEngine(runner=runner, command_executor=executor)

    result = engine.run(
        RunConfig(
            task_id='t-stream',
            title='Stream mode',
            description='verify streaming events',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B')],
            evolution_level=0,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=1,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
            stream_mode=True,
        ),
        on_event=sink,
    )

    assert result.status == 'passed'
    assert any(e.get('type') == 'participant_stream' for e in sink.events)


def test_workflow_langgraph_backend_executes_classic_flow(tmp_path: Path):
    runner = FakeRunner([
        _ok_result(),  # discussion
        _ok_result(),  # implementation
        _ok_result(),  # review
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()
    engine = WorkflowEngine(
        runner=runner,
        command_executor=executor,
        workflow_backend='langgraph',
    )

    result = engine.run(
        RunConfig(
            task_id='t-langgraph',
            title='LangGraph backend smoke',
            description='verify backend dispatch',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B')],
            evolution_level=0,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=1,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
        ),
        on_event=sink,
    )

    assert result.status == 'passed'
    assert any(e.get('type') == 'task_started' for e in sink.events)
    assert runner.calls == 3
