from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import subprocess
import time
from typing import Callable
from contextlib import nullcontext

from awe_agentcheck.adapters import ParticipantRunner
from awe_agentcheck.domain.gate import evaluate_medium_gate
from awe_agentcheck.domain.models import ReviewVerdict
from awe_agentcheck.observability import get_logger, set_task_context
from awe_agentcheck.participants import Participant

_log = get_logger('awe_agentcheck.workflow')


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    command: str
    returncode: int
    stdout: str
    stderr: str


class ShellCommandExecutor:
    def run(self, command: str, cwd: Path, timeout_seconds: int) -> CommandResult:
        started = time.monotonic()
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout_seconds,
        )
        elapsed = time.monotonic() - started
        _log.debug('shell_command command=%s ok=%s duration=%.2fs',
                   command, completed.returncode == 0, elapsed)
        return CommandResult(
            ok=completed.returncode == 0,
            command=command,
            returncode=completed.returncode,
            stdout=completed.stdout or '',
            stderr=completed.stderr or '',
        )


@dataclass(frozen=True)
class RunConfig:
    task_id: str
    title: str
    description: str
    author: Participant
    reviewers: list[Participant]
    evolution_level: int
    evolve_until: str | None
    cwd: Path
    max_rounds: int
    test_command: str
    lint_command: str


@dataclass(frozen=True)
class RunResult:
    status: str
    rounds: int
    gate_reason: str


class WorkflowEngine:
    def __init__(
        self,
        *,
        runner: ParticipantRunner,
        command_executor: ShellCommandExecutor,
        participant_timeout_seconds: int = 240,
        command_timeout_seconds: int = 300,
    ):
        self.runner = runner
        self.command_executor = command_executor
        self.participant_timeout_seconds = max(1, int(participant_timeout_seconds))
        self.command_timeout_seconds = max(1, int(command_timeout_seconds))

    def run(
        self,
        config: RunConfig,
        *,
        on_event: Callable[[dict], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> RunResult:
        emit = on_event or (lambda event: None)
        check_cancel = should_cancel or (lambda: False)

        emit({'type': 'task_started', 'task_id': config.task_id})
        set_task_context(task_id=config.task_id)
        _log.info('workflow_started task_id=%s max_rounds=%d', config.task_id, config.max_rounds)
        tracer = self._get_tracer()
        previous_gate_reason: str | None = None
        deadline = self._parse_deadline(config.evolve_until)

        for round_no in range(1, config.max_rounds + 1):
            if check_cancel():
                emit({'type': 'canceled', 'round': round_no})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')
            if deadline is not None and datetime.now() >= deadline:
                emit({'type': 'deadline_reached', 'round': round_no, 'deadline': deadline.isoformat()})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='deadline_reached')

            set_task_context(task_id=config.task_id, round_no=round_no)
            _log.info('round_started round=%d', round_no)
            emit({'type': 'round_started', 'round': round_no})

            with self._span(tracer, 'workflow.discussion', {'task.id': config.task_id, 'round': round_no}):
                discussion = self.runner.run(
                    participant=config.author,
                    prompt=self._discussion_prompt(config, round_no, previous_gate_reason),
                    cwd=config.cwd,
                    timeout_seconds=self.participant_timeout_seconds,
                )
            emit({'type': 'discussion', 'round': round_no, 'provider': config.author.provider, 'output': discussion.output, 'duration_seconds': discussion.duration_seconds})

            if check_cancel():
                emit({'type': 'canceled', 'round': round_no})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')

            with self._span(tracer, 'workflow.implementation', {'task.id': config.task_id, 'round': round_no}):
                implementation = self.runner.run(
                    participant=config.author,
                    prompt=self._implementation_prompt(config, round_no, discussion.output),
                    cwd=config.cwd,
                    timeout_seconds=self.participant_timeout_seconds,
                )
            emit({'type': 'implementation', 'round': round_no, 'provider': config.author.provider, 'output': implementation.output, 'duration_seconds': implementation.duration_seconds})

            if check_cancel():
                emit({'type': 'canceled', 'round': round_no})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')

            verdicts: list[ReviewVerdict] = []
            for reviewer in config.reviewers:
                with self._span(tracer, 'workflow.review', {'task.id': config.task_id, 'round': round_no, 'participant': reviewer.participant_id}):
                    review = self.runner.run(
                        participant=reviewer,
                        prompt=self._review_prompt(config, round_no, implementation.output),
                        cwd=config.cwd,
                        timeout_seconds=self.participant_timeout_seconds,
                    )
                verdict = self._normalize_verdict(review.verdict)
                verdicts.append(verdict)
                emit(
                    {
                        'type': 'review',
                        'round': round_no,
                        'participant': reviewer.participant_id,
                        'verdict': verdict.value,
                        'output': review.output,
                        'duration_seconds': review.duration_seconds,
                    }
                )

            if check_cancel():
                emit({'type': 'canceled', 'round': round_no})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')

            with self._span(tracer, 'workflow.verify', {'task.id': config.task_id, 'round': round_no}):
                test_result = self.command_executor.run(
                    config.test_command,
                    cwd=config.cwd,
                    timeout_seconds=self.command_timeout_seconds,
                )
                lint_result = self.command_executor.run(
                    config.lint_command,
                    cwd=config.cwd,
                    timeout_seconds=self.command_timeout_seconds,
                )
            emit(
                {
                    'type': 'verification',
                    'round': round_no,
                    'tests_ok': test_result.ok,
                    'lint_ok': lint_result.ok,
                    'test_stdout': self._clip_text(test_result.stdout, max_chars=500),
                    'lint_stdout': self._clip_text(lint_result.stdout, max_chars=500),
                }
            )

            if check_cancel():
                emit({'type': 'canceled', 'round': round_no})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')

            gate = evaluate_medium_gate(
                tests_ok=test_result.ok,
                lint_ok=lint_result.ok,
                reviewer_verdicts=verdicts,
            )
            if gate.passed:
                _log.info('gate_passed round=%d reason=%s', round_no, gate.reason)
                emit({'type': 'gate_passed', 'round': round_no, 'reason': gate.reason})
                return RunResult(status='passed', rounds=round_no, gate_reason=gate.reason)

            _log.warning('gate_failed round=%d reason=%s', round_no, gate.reason)
            emit({'type': 'gate_failed', 'round': round_no, 'reason': gate.reason})
            previous_gate_reason = gate.reason
            if round_no >= config.max_rounds:
                return RunResult(status='failed_gate', rounds=round_no, gate_reason=gate.reason)

        return RunResult(status='failed_gate', rounds=config.max_rounds, gate_reason='max_rounds_exhausted')

    @staticmethod
    def _get_tracer():
        try:
            from opentelemetry import trace
            return trace.get_tracer('awe_agentcheck.workflow')
        except Exception:
            _log.debug('OpenTelemetry tracer unavailable', exc_info=True)
            return None

    @staticmethod
    def _span(tracer, name: str, attributes: dict):
        if tracer is None:
            return nullcontext()
        span = tracer.start_as_current_span(name)
        ctx = span.__enter__()
        for key, value in attributes.items():
            try:
                ctx.set_attribute(key, value)
            except Exception:
                pass
        class _Wrapper:
            def __enter__(self_inner):
                return ctx
            def __exit__(self_inner, exc_type, exc, tb):
                return span.__exit__(exc_type, exc, tb)
        return _Wrapper()

    @staticmethod
    def _clip_text(text: str, *, max_chars: int = 3000) -> str:
        source = text or ''
        if len(source) <= max_chars:
            return source
        dropped = len(source) - max_chars
        return source[:max_chars] + f'\n...[truncated {dropped} chars]'

    @staticmethod
    def _discussion_prompt(config: RunConfig, round_no: int, previous_gate_reason: str | None = None) -> str:
        level = max(0, min(2, int(config.evolution_level)))
        base = (
            f"Task: {config.title}\n"
            f"Round: {round_no}\n"
            f"EvolutionLevel: {level}\n"
            f"Description: {config.description}\n"
            "Produce a concise execution plan for this round.\n"
            "Do not ask follow-up questions. Keep response concise."
        )
        if level == 1:
            base += (
                "\nMode guidance: prioritize bug/risk fixes first, and optionally propose one small safe evolution.\n"
                "If proposing, include one line: EVOLUTION_PROPOSAL: <small enhancement>."
            )
        elif level == 2:
            base += (
                "\nMode guidance: prioritize bug/risk fixes first, then proactively propose 1-2 evolution directions.\n"
                "If proposing, include lines: EVOLUTION_PROPOSAL_1: ... and optional EVOLUTION_PROPOSAL_2: ...\n"
                "Ensure rollout stays incremental and testable."
            )
        if round_no > 1 and previous_gate_reason:
            base += f"\nPrevious gate failure reason: {previous_gate_reason}\nAddress this explicitly."
        return base

    @staticmethod
    def _implementation_prompt(config: RunConfig, round_no: int, discussion_output: str) -> str:
        clipped = WorkflowEngine._clip_text(discussion_output, max_chars=3000)
        level = max(0, min(2, int(config.evolution_level)))
        mode_guidance = "Focus on resolving blockers and reliability issues."
        if level == 1:
            mode_guidance = (
                "Resolve blockers first, then you may implement one small evolution proposal if it is low-risk."
            )
        elif level == 2:
            mode_guidance = (
                "Resolve blockers first, then proactively implement one incremental evolution direction "
                "from discussion if tests/lint can remain green."
            )
        return (
            f"Task: {config.title}\n"
            f"Round: {round_no}\n"
            f"EvolutionLevel: {level}\n"
            "Implement based on this plan and summarize what changed.\n"
            f"Plan:\n{clipped}\n"
            f"{mode_guidance}\n"
            "Include explicit assumptions and risks.\n"
            "Do not ask follow-up questions. Keep response concise."
        )

    @staticmethod
    def _review_prompt(config: RunConfig, round_no: int, implementation_output: str) -> str:
        clipped = WorkflowEngine._clip_text(implementation_output, max_chars=3000)
        level = max(0, min(2, int(config.evolution_level)))
        mode_guidance = ''
        if level >= 1:
            mode_guidance = (
                "For evolution proposals, block only if there is correctness/regression/security/data-loss risk.\n"
                "Do not block solely because an optional enhancement exists.\n"
            )
        return (
            f"Task: {config.title}\n"
            f"Round: {round_no}\n"
            f"EvolutionLevel: {level}\n"
            "Review the implementation summary and decide blocker status.\n"
            "Mark BLOCKER only for correctness, regression, security, or data-loss risks.\n"
            "Do not mark BLOCKER for style-only, process-only, or preference-only feedback.\n"
            f"{mode_guidance}"
            "Output must include one line: VERDICT: NO_BLOCKER or VERDICT: BLOCKER or VERDICT: UNKNOWN.\n"
            "Do not ask follow-up questions. Keep response concise.\n"
            f"Implementation summary:\n{clipped}\n"
        )

    @staticmethod
    def _normalize_verdict(raw: str) -> ReviewVerdict:
        v = (raw or '').strip().lower()
        if v == 'no_blocker':
            return ReviewVerdict.NO_BLOCKER
        if v == 'blocker':
            return ReviewVerdict.BLOCKER
        return ReviewVerdict.UNKNOWN

    @staticmethod
    def _parse_deadline(value: str | None) -> datetime | None:
        text = (value or '').strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace(' ', 'T'))
        except ValueError:
            return None
