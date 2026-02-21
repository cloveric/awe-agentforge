from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
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
    line = (
        f'VERDICT: {verdict.upper()}\n'
        'Issue: updated src/awe_agentcheck/service.py.\n'
        'Next: validate tests/unit/test_service.py.'
    )
    return AdapterResult(output=line, verdict=verdict, next_action=None, returncode=0, duration_seconds=0.1)


def _ok_result_no_evidence(verdict: str = 'no_blocker') -> AdapterResult:
    line = f'VERDICT: {verdict.upper()}\nIssue: summarized changes without file references.'
    return AdapterResult(output=line, verdict=verdict, next_action=None, returncode=0, duration_seconds=0.1)


def _error_result(reason: str) -> AdapterResult:
    return AdapterResult(
        output=reason,
        verdict='unknown',
        next_action='stop',
        returncode=2,
        duration_seconds=0.1,
    )


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
    assert any('Execution context:' in prompt for prompt in runner.prompts)


def test_workflow_applies_phase_timeouts_and_memory_context_per_stage(tmp_path: Path):
    runner = FakeRunner([
        _ok_result(),  # debate reviewer
        _ok_result(),  # discussion
        _ok_result(),  # implementation
        _ok_result(),  # final review
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()
    engine = WorkflowEngine(
        runner=runner,
        command_executor=executor,
        participant_timeout_seconds=3600,
        command_timeout_seconds=300,
    )

    result = engine.run(
        RunConfig(
            task_id='t-timeouts-memory',
            title='Timeout and memory',
            description='verify phase controls',
            author=parse_participant_id('codex#author-A'),
            reviewers=[parse_participant_id('claude#review-B')],
            evolution_level=2,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=1,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
            debate_mode=True,
            memory_mode='strict',
            memory_context={
                'proposal': 'MEMORY-PROPOSAL',
                'discussion': 'MEMORY-DISCUSSION',
                'implementation': 'MEMORY-IMPLEMENTATION',
                'review': 'MEMORY-REVIEW',
            },
            phase_timeout_seconds={
                'proposal': 555,
                'discussion': 111,
                'implementation': 222,
                'review': 333,
                'command': 444,
            },
        ),
        on_event=sink,
    )

    assert result.status == 'passed'
    assert runner.timeouts[:4] == [333, 111, 222, 333]
    assert executor.timeouts == [444, 444]
    assert len(runner.prompts) >= 4
    assert 'MEMORY-PROPOSAL' in runner.prompts[0]
    assert 'MEMORY-DISCUSSION' in runner.prompts[1]
    assert 'MEMORY-IMPLEMENTATION' in runner.prompts[2]
    assert 'MEMORY-REVIEW' in runner.prompts[3]


def test_workflow_blocks_when_author_discussion_runtime_fails(tmp_path: Path):
    runner = FakeRunner([
        _error_result('command_not_found provider=codex command=codex exec'),
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()
    engine = WorkflowEngine(runner=runner, command_executor=executor)

    result = engine.run(
        RunConfig(
            task_id='t-author-discussion-fail',
            title='Runtime fail',
            description='author command fails before implementation',
            author=parse_participant_id('codex#author-A'),
            reviewers=[parse_participant_id('claude#review-B')],
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
    assert result.gate_reason == 'command_not_found'
    assert any(
        str(e.get('type') or '') == 'gate_failed'
        and str(e.get('stage') or '') == 'discussion'
        and str(e.get('reason') or '') == 'command_not_found'
        for e in sink.events
    )


def test_workflow_blocks_when_author_implementation_runtime_fails(tmp_path: Path):
    runner = FakeRunner([
        _ok_result(),
        _error_result('command_timeout provider=codex command=codex exec timeout_seconds=240'),
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()
    engine = WorkflowEngine(runner=runner, command_executor=executor)

    result = engine.run(
        RunConfig(
            task_id='t-author-impl-fail',
            title='Runtime fail',
            description='author command times out in implementation',
            author=parse_participant_id('codex#author-A'),
            reviewers=[parse_participant_id('claude#review-B')],
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
    assert result.gate_reason == 'command_timeout'
    assert any(
        str(e.get('type') or '') == 'gate_failed'
        and str(e.get('stage') or '') == 'implementation'
        and str(e.get('reason') or '') == 'command_timeout'
        for e in sink.events
    )


def test_workflow_emits_precompletion_checklist_event(tmp_path: Path):
    runner = FakeRunner([
        _ok_result(),  # discussion
        _ok_result(),  # implementation
        _ok_result(),  # reviewer
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()

    engine = WorkflowEngine(runner=runner, command_executor=executor)
    result = engine.run(
        RunConfig(
            task_id='t1b',
            title='Checklist event',
            description='ensure precompletion checklist emits',
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
    checklist_events = [e for e in sink.events if e.get('type') == 'precompletion_checklist']
    assert checklist_events
    assert checklist_events[-1].get('passed') is True
    assert checklist_events[-1].get('reason') == 'passed'


def test_workflow_blocks_pass_when_evidence_paths_missing(tmp_path: Path):
    runner = FakeRunner([
        _ok_result_no_evidence(),  # discussion
        _ok_result_no_evidence(),  # implementation
        _ok_result_no_evidence(),  # reviewer
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()

    engine = WorkflowEngine(runner=runner, command_executor=executor)
    result = engine.run(
        RunConfig(
            task_id='t1c',
            title='Checklist evidence',
            description='verify evidence path hard gate',
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

    assert result.status == 'failed_gate'
    assert result.gate_reason == 'precompletion_evidence_missing'
    assert any(
        e.get('type') == 'gate_failed'
        and str(e.get('reason') or '') == 'precompletion_evidence_missing'
        for e in sink.events
    )


def test_workflow_emits_strategy_shifted_on_repeated_failures(tmp_path: Path):
    runner = FakeRunner([_ok_result_no_evidence()])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()

    engine = WorkflowEngine(runner=runner, command_executor=executor)
    result = engine.run(
        RunConfig(
            task_id='t1d',
            title='Loop shift',
            description='repeated precompletion failures should trigger strategy shift',
            author=parse_participant_id('codex#author-A'),
            reviewers=[parse_participant_id('claude#review-B')],
            evolution_level=0,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=3,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
        ),
        on_event=sink,
    )

    assert result.status == 'failed_gate'
    shifted = [e for e in sink.events if e.get('type') == 'strategy_shifted']
    assert shifted
    assert str(shifted[-1].get('hint') or '').strip()


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
    assert 'Output JSON only.' in prompt
    assert 'Keep output concise but complete enough to justify verdict.' in prompt


def test_review_prompt_includes_required_checklist_for_evolution_level_1(tmp_path: Path):
    cfg = RunConfig(
        task_id='t6-e1',
        title='Checklist test',
        description='review deeply',
        author=parse_participant_id('claude#author-A'),
        reviewers=[parse_participant_id('codex#review-B')],
        evolution_level=1,
        evolve_until=None,
        cwd=tmp_path,
        max_rounds=1,
        test_command='py -m pytest -q',
        lint_command='py -m ruff check .',
    )
    prompt = WorkflowEngine._review_prompt(cfg, 1, 'impl summary')
    assert 'Required checklist' in prompt
    assert 'architecture size/responsibility' in prompt
    assert 'cross-platform runtime/scripts' in prompt
    assert 'Required control output schema (JSON only' in prompt


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
    assert 'JSON only' in review_prompt


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


def test_discussion_prompt_includes_frontier_guidance_for_level_3(tmp_path: Path):
    cfg = RunConfig(
        task_id='t7-e3',
        title='Frontier evolve test',
        description='aggressive evolve',
        author=parse_participant_id('claude#author-A'),
        reviewers=[parse_participant_id('codex#review-B')],
        evolution_level=3,
        evolve_until=None,
        cwd=tmp_path,
        max_rounds=1,
        test_command='py -m pytest -q',
        lint_command='py -m ruff check .',
    )
    prompt = WorkflowEngine._discussion_prompt(cfg, 1, None)
    assert 'EvolutionLevel: 3' in prompt
    assert 'framework/runtime upgrades' in prompt
    assert 'EVOLUTION_PROPOSAL_1..N' in prompt


def test_review_prompt_includes_frontier_checklist_for_level_3(tmp_path: Path):
    cfg = RunConfig(
        task_id='t6-e3',
        title='Frontier checklist test',
        description='review deeply',
        author=parse_participant_id('claude#author-A'),
        reviewers=[parse_participant_id('codex#review-B')],
        evolution_level=3,
        evolve_until=None,
        cwd=tmp_path,
        max_rounds=1,
        test_command='py -m pytest -q',
        lint_command='py -m ruff check .',
    )
    prompt = WorkflowEngine._review_prompt(cfg, 1, 'impl summary')
    assert 'feature opportunity map' in prompt
    assert 'framework/runtime upgrade candidates' in prompt
    assert 'UI/UX upgrade ideas' in prompt


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
    expected_argv = ['py', '-m', 'pytest', '-q'] if os.name == 'nt' else ['python', '-m', 'pytest', '-q']
    assert captured['argv'] == expected_argv
    assert captured['kwargs']['shell'] is False


def test_shell_command_executor_prefers_workspace_src_in_pythonpath(monkeypatch, tmp_path: Path):
    captured: dict[str, object] = {}
    workspace_src = (tmp_path / 'src').resolve()
    workspace_src.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('PYTHONPATH', os.pathsep.join([r'C:\Users\hangw\awe-agentcheck\src', r'C:\shared\python']))

    def fake_run(argv, **kwargs):
        captured['argv'] = argv
        captured['kwargs'] = kwargs
        return subprocess.CompletedProcess(argv, 0, stdout='ok', stderr='')

    monkeypatch.setattr('awe_agentcheck.workflow.subprocess.run', fake_run)
    executor = ShellCommandExecutor()

    result = executor.run(['py', '-m', 'pytest', '-q'], cwd=tmp_path, timeout_seconds=10)

    assert result.ok is True
    env = dict(captured['kwargs'].get('env') or {})
    py_path = str(env.get('PYTHONPATH') or '')
    parts = [p for p in py_path.split(os.pathsep) if p]
    assert parts
    assert parts[0] == str(workspace_src)
    assert all(not p.replace('\\', '/').lower().endswith('/awe-agentcheck/src') for p in parts[1:])


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


def test_shell_command_executor_maps_timeout_exception_to_result(monkeypatch, tmp_path: Path):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=11, output='partial out', stderr='partial err')

    monkeypatch.setattr('awe_agentcheck.workflow.subprocess.run', fake_run)
    executor = ShellCommandExecutor()

    result = executor.run(['py', '-m', 'pytest', '-q'], cwd=tmp_path, timeout_seconds=11)

    assert result.ok is False
    assert result.returncode == 124
    assert 'command_timeout provider=shell' in result.stderr
    assert 'timeout_seconds=11' in result.stderr


def test_shell_command_executor_maps_missing_binary_exception_to_result(monkeypatch, tmp_path: Path):
    def fake_run(argv, **kwargs):
        raise FileNotFoundError(2, 'No such file or directory', argv[0])

    monkeypatch.setattr('awe_agentcheck.workflow.subprocess.run', fake_run)
    executor = ShellCommandExecutor()

    result = executor.run(['py', '-m', 'pytest', '-q'], cwd=tmp_path, timeout_seconds=11)

    assert result.ok is False
    assert result.returncode == 127
    assert 'command_not_found provider=shell' in result.stderr


def test_shell_command_executor_preserves_windows_drive_path(monkeypatch):
    import awe_agentcheck.workflow as workflow_module

    fake_os = type('FakeOS', (), {'name': 'nt'})()
    monkeypatch.setattr(workflow_module, 'os', fake_os, raising=False)

    argv = ShellCommandExecutor._normalize_command(r'py -m pytest C:\repo\tests -q')
    assert argv[3] == r'C:\repo\tests'


def test_shell_command_executor_preserves_windows_relative_path(monkeypatch):
    import awe_agentcheck.workflow as workflow_module

    fake_os = type('FakeOS', (), {'name': 'nt'})()
    monkeypatch.setattr(workflow_module, 'os', fake_os, raising=False)

    argv = ShellCommandExecutor._normalize_command(r'py -m pytest .\tests -q')
    assert argv[3] == r'.\tests'


def test_shell_command_executor_remaps_py_launcher_on_non_windows(monkeypatch):
    import awe_agentcheck.workflow as workflow_module

    fake_os = type('FakeOS', (), {'name': 'posix'})()
    monkeypatch.setattr(workflow_module, 'os', fake_os, raising=False)

    argv = ShellCommandExecutor._normalize_command('py -m pytest -q')
    assert argv[0] == 'python'


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


def test_workflow_passes_provider_models_and_agent_toggles_to_runner(tmp_path: Path):
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
            codex_multi_agents=True,
            codex_multi_agents_overrides={'codex#review-B': False},
        )
    )

    assert result.status == 'passed'
    assert len(runner.call_options) == 3
    assert runner.call_options[0].get('model') == 'claude-sonnet-4-5'
    assert runner.call_options[1].get('model') == 'claude-sonnet-4-5'
    assert runner.call_options[2].get('model') == 'gpt-5-codex'
    assert runner.call_options[0].get('claude_team_agents') is True
    assert runner.call_options[0].get('codex_multi_agents') is True
    assert runner.call_options[2].get('codex_multi_agents') is False


def test_workflow_supports_participant_agent_override_when_global_toggle_off(tmp_path: Path):
    runner = FakeRunner([_ok_result(), _ok_result(), _ok_result()])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    engine = WorkflowEngine(runner=runner, command_executor=executor)

    result = engine.run(
        RunConfig(
            task_id='t-agent-override',
            title='Participant agent override',
            description='override should enable codex multi-agent for reviewer only',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B')],
            evolution_level=0,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=1,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
            claude_team_agents=False,
            codex_multi_agents=False,
            codex_multi_agents_overrides={'codex#review-B': True},
        )
    )

    assert result.status == 'passed'
    assert len(runner.call_options) == 3
    assert runner.call_options[0].get('codex_multi_agents') is False
    assert runner.call_options[1].get('codex_multi_agents') is False
    assert runner.call_options[2].get('codex_multi_agents') is True


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


def test_workflow_prefers_participant_model_overrides_over_provider_defaults(tmp_path: Path):
    runner = FakeRunner([_ok_result(), _ok_result(), _ok_result()])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    engine = WorkflowEngine(runner=runner, command_executor=executor)

    result = engine.run(
        RunConfig(
            task_id='t-participant-models',
            title='Participant model overrides',
            description='author/reviewer should be independently configurable',
            author=parse_participant_id('codex#author-A'),
            reviewers=[parse_participant_id('codex#review-B')],
            evolution_level=0,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=1,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
            provider_models={'codex': 'gpt-5.3-codex'},
            provider_model_params={'codex': '-c model_reasoning_effort=high'},
            participant_models={
                'codex#author-A': 'gpt-5.3-codex',
                'codex#review-B': 'gpt-5.3-codex',
            },
            participant_model_params={
                'codex#author-A': '-c model_reasoning_effort=high',
                'codex#review-B': '-c model_reasoning_effort=xhigh',
            },
        )
    )

    assert result.status == 'passed'
    assert len(runner.call_options) == 3
    assert runner.participants == ['codex#author-A', 'codex#author-A', 'codex#review-B']
    assert runner.call_options[0].get('model_params') == '-c model_reasoning_effort=high'
    assert runner.call_options[1].get('model_params') == '-c model_reasoning_effort=high'
    assert runner.call_options[2].get('model_params') == '-c model_reasoning_effort=xhigh'


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


def test_discussion_after_reviewer_prompt_keeps_reviewer_constraints_and_repair_policy_level_2(tmp_path: Path):
    cfg = RunConfig(
        task_id='t-reviewer-first-level2',
        title='Reviewer-first follow-up',
        description='keep reviewer constraints visible',
        author=parse_participant_id('codex#author-A'),
        reviewers=[parse_participant_id('codex#review-B')],
        evolution_level=2,
        evolve_until=None,
        cwd=tmp_path,
        max_rounds=1,
        test_command='py -m pytest -q',
        lint_command='py -m ruff check .',
        repair_mode='balanced',
    )
    reviewer_context = 'security: n/a\ncorrectness: check auth flow in src/awe_agentcheck/workflow.py'
    prompt = WorkflowEngine._discussion_after_reviewer_prompt(cfg, 1, reviewer_context)
    assert 'Reviewer-first mode: reviewers have provided pre-implementation findings.' in prompt
    assert 'Reviewer is primary in this phase: do not invent unrelated change themes.' in prompt
    assert 'Repair policy: balanced fix (default).' in prompt
    assert 'RepairMode: balanced' in prompt
    assert reviewer_context in prompt


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


def test_workflow_emits_prompt_cache_probe_events_with_prefix_reuse_signal(tmp_path: Path):
    runner = FakeRunner([
        _ok_result(),              # round1 discussion
        _ok_result(),              # round1 implementation
        _ok_result('blocker'),     # round1 review -> gate fail
        _ok_result(),              # round2 discussion
        _ok_result(),              # round2 implementation
        _ok_result('no_blocker'),  # round2 review -> gate pass
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()
    engine = WorkflowEngine(runner=runner, command_executor=executor)

    result = engine.run(
        RunConfig(
            task_id='t-cache-probe',
            title='Cache probe',
            description='exercise two rounds for probe reuse',
            author=parse_participant_id('codex#author-A'),
            reviewers=[parse_participant_id('claude#review-B')],
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
    probes = [e for e in sink.events if str(e.get('type')) == 'prompt_cache_probe']
    assert probes
    assert any(bool(e.get('prefix_reuse_eligible')) and bool(e.get('prefix_reused')) for e in probes)
    breaks = [e for e in sink.events if str(e.get('type')) == 'prompt_cache_break']
    assert not breaks


def test_prompt_cache_probe_emits_break_event_when_model_changes():
    state: dict = {}
    participant = parse_participant_id('codex#author-A')
    probe1, breaks1 = WorkflowEngine._record_prompt_cache_probe(
        cache_state=state,
        round_no=1,
        stage='discussion',
        participant=participant,
        model='gpt-5.3-codex',
        model_params='reasoning_effort=high',
        claude_team_agents=False,
        codex_multi_agents=False,
        prompt='Static instruction header\nContext: round1',
    )
    probe2, breaks2 = WorkflowEngine._record_prompt_cache_probe(
        cache_state=state,
        round_no=2,
        stage='discussion',
        participant=participant,
        model='gpt-5.3-codex-spark',
        model_params='reasoning_effort=high',
        claude_team_agents=False,
        codex_multi_agents=False,
        prompt='Static instruction header\nContext: round2',
    )

    assert probe1['baseline'] is True
    assert probe2['model_reuse_eligible'] is True
    assert any(str(item.get('reason')) == 'model_changed' for item in breaks2)
    assert breaks1 == []


def test_workflow_evolution_level_1_emits_architecture_warnings_without_hard_fail(tmp_path: Path, monkeypatch):
    monkeypatch.delenv('AWE_ARCH_AUDIT_MODE', raising=False)
    huge = tmp_path / 'oversized.py'
    huge.write_text('\n'.join(['x = 1'] * 1305), encoding='utf-8')
    runner = FakeRunner([
        _ok_result(),  # discussion
        _ok_result(),  # implementation
        _ok_result(),  # review
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()
    engine = WorkflowEngine(runner=runner, command_executor=executor)

    result = engine.run(
        RunConfig(
            task_id='t-e1-arch',
            title='Architecture warning',
            description='scan and improve',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B')],
            evolution_level=1,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=1,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
        ),
        on_event=sink,
    )

    assert result.status == 'passed'
    audits = [e for e in sink.events if e.get('type') == 'architecture_audit']
    assert audits
    last = audits[-1]
    assert last.get('mode') == 'warn'
    assert last.get('passed') is False
    assert str(last.get('reason') or '') == 'architecture_threshold_warning'


def test_workflow_evolution_level_2_emits_architecture_warnings_without_hard_fail(tmp_path: Path, monkeypatch):
    monkeypatch.delenv('AWE_ARCH_AUDIT_MODE', raising=False)
    huge = tmp_path / 'oversized.py'
    huge.write_text('\n'.join(['x = 1'] * 1305), encoding='utf-8')
    runner = FakeRunner([
        _ok_result(),  # discussion
        _ok_result(),  # implementation
        _ok_result(),  # review
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()
    engine = WorkflowEngine(runner=runner, command_executor=executor)

    result = engine.run(
        RunConfig(
            task_id='t-e2-arch',
            title='Architecture hard gate',
            description='scan and improve',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B')],
            evolution_level=2,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=1,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
        ),
        on_event=sink,
    )

    assert result.status == 'passed'
    audits = [e for e in sink.events if e.get('type') == 'architecture_audit']
    assert audits
    last = audits[-1]
    assert last.get('mode') == 'warn'
    assert last.get('passed') is False
    assert str(last.get('reason') or '') == 'architecture_threshold_warning'


def test_workflow_architecture_thresholds_support_env_override(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('AWE_ARCH_AUDIT_MODE', 'hard')
    monkeypatch.setenv('AWE_ARCH_PYTHON_FILE_LINES_MAX', '20')
    oversized = tmp_path / 'tiny_over_limit.py'
    oversized.write_text('\n'.join(['x = 1'] * 25), encoding='utf-8')
    runner = FakeRunner([
        _ok_result(),
        _ok_result(),
        _ok_result(),
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()
    engine = WorkflowEngine(runner=runner, command_executor=executor)

    result = engine.run(
        RunConfig(
            task_id='t-arch-env',
            title='Architecture env override',
            description='enforce custom threshold',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B')],
            evolution_level=1,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=1,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
        ),
        on_event=sink,
    )

    assert result.status == 'failed_gate'
    assert result.gate_reason == 'architecture_threshold_exceeded'
    audit = [e for e in sink.events if e.get('type') == 'architecture_audit'][-1]
    assert int(audit.get('thresholds', {}).get('python_file_lines_max', 0)) == 20


def test_workflow_architecture_audit_flags_cross_platform_script_gap(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('AWE_ARCH_AUDIT_MODE', 'hard')
    scripts = tmp_path / 'scripts'
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / 'start_api.ps1').write_text('Write-Host "start"', encoding='utf-8')
    runner = FakeRunner([
        _ok_result(),
        _ok_result(),
        _ok_result(),
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()
    engine = WorkflowEngine(runner=runner, command_executor=executor)

    result = engine.run(
        RunConfig(
            task_id='t-arch-scripts',
            title='Cross platform scripts',
            description='detect script variant gap',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B')],
            evolution_level=1,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=1,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
        ),
        on_event=sink,
    )

    assert result.status == 'failed_gate'
    audit = [e for e in sink.events if e.get('type') == 'architecture_audit'][-1]
    assert any(str(v.get('kind') or '') == 'script_cross_platform_gap' for v in audit.get('violations', []))


def test_workflow_architecture_audit_flags_runtimeerror_raises_in_adapter(tmp_path: Path, monkeypatch):
    monkeypatch.setenv('AWE_ARCH_AUDIT_MODE', 'hard')
    src_dir = tmp_path / 'src' / 'awe_agentcheck'
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / 'adapters.py').write_text(
        'def bad():\n'
        '    raise RuntimeError("provider_limit provider=claude")\n',
        encoding='utf-8',
    )
    runner = FakeRunner([
        _ok_result(),
        _ok_result(),
        _ok_result(),
    ])
    executor = FakeCommandExecutor(tests_ok=True, lint_ok=True)
    sink = EventSink()
    engine = WorkflowEngine(runner=runner, command_executor=executor)

    result = engine.run(
        RunConfig(
            task_id='t-arch-adapter-raise',
            title='Adapter runtime handling',
            description='detect raw runtime raise behavior',
            author=parse_participant_id('claude#author-A'),
            reviewers=[parse_participant_id('codex#review-B')],
            evolution_level=1,
            evolve_until=None,
            cwd=tmp_path,
            max_rounds=1,
            test_command='py -m pytest -q',
            lint_command='py -m ruff check .',
        ),
        on_event=sink,
    )

    assert result.status == 'failed_gate'
    audit = [e for e in sink.events if e.get('type') == 'architecture_audit'][-1]
    assert any(str(v.get('kind') or '') == 'adapter_runtime_raise_detected' for v in audit.get('violations', []))


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
