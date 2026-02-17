from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from awe_agentcheck.adapters import AdapterResult
from awe_agentcheck.participants import parse_participant_id
from awe_agentcheck.workflow import CommandResult, RunConfig, WorkflowEngine


class FakeRunner:
    def __init__(self, outputs: list[AdapterResult]):
        self.outputs = outputs
        self.calls = 0
        self.timeouts: list[int] = []
        self.prompts: list[str] = []

    def run(self, *, participant, prompt, cwd, timeout_seconds=900):
        idx = min(self.calls, len(self.outputs) - 1)
        self.calls += 1
        self.timeouts.append(timeout_seconds)
        self.prompts.append(prompt)
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
    past = (datetime.now() - timedelta(minutes=1)).replace(microsecond=0).isoformat()

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
