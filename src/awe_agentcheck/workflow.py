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
    conversation_language: str = 'en'
    provider_models: dict[str, str] | None = None
    provider_model_params: dict[str, str] | None = None
    claude_team_agents: bool = False
    repair_mode: str = 'balanced'
    plain_mode: bool = True
    stream_mode: bool = False
    debate_mode: bool = False


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
        provider_models = self._normalize_provider_models(config.provider_models)
        provider_model_params = self._normalize_provider_model_params(config.provider_model_params)
        stream_mode = bool(config.stream_mode)
        debate_mode = bool(config.debate_mode) and bool(config.reviewers)
        review_timeout_seconds = self._review_timeout_seconds(self.participant_timeout_seconds)
        deadline_mode = deadline is not None
        round_no = 0
        while True:
            round_no += 1
            if debate_mode and check_cancel():
                emit({'type': 'canceled', 'round': round_no})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')
            if deadline is not None and datetime.now() >= deadline:
                emit({'type': 'deadline_reached', 'round': round_no, 'deadline': deadline.isoformat()})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='deadline_reached')

            set_task_context(task_id=config.task_id, round_no=round_no)
            _log.info('round_started round=%d', round_no)
            emit({'type': 'round_started', 'round': round_no})

            implementation_context = self._debate_seed_context(config, round_no, previous_gate_reason)
            if debate_mode:
                emit(
                    {
                        'type': 'debate_started',
                        'round': round_no,
                        'mode': 'reviewer_first',
                        'reviewer_count': len(config.reviewers),
                    }
                )
                for reviewer in config.reviewers:
                    if check_cancel():
                        emit({'type': 'canceled', 'round': round_no})
                        return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')

                    emit(
                        {
                            'type': 'debate_review_started',
                            'round': round_no,
                            'participant': reviewer.participant_id,
                            'provider': reviewer.provider,
                            'timeout_seconds': review_timeout_seconds,
                        }
                    )
                    try:
                        debate_review = self.runner.run(
                            participant=reviewer,
                            prompt=self._debate_review_prompt(config, round_no, implementation_context, reviewer.participant_id),
                            cwd=config.cwd,
                            timeout_seconds=review_timeout_seconds,
                            model=provider_models.get(reviewer.provider),
                            model_params=provider_model_params.get(reviewer.provider),
                            claude_team_agents=bool(config.claude_team_agents),
                            on_stream=(
                                self._stream_emitter(
                                    emit=emit,
                                    round_no=round_no,
                                    stage='debate_review',
                                    participant=reviewer.participant_id,
                                    provider=reviewer.provider,
                                )
                                if stream_mode
                                else None
                            ),
                        )
                        review_text = str(debate_review.output or '').strip()
                    except Exception as exc:
                        review_text = f'[debate_review_error] {str(exc or "review_failed").strip() or "review_failed"}'
                        emit(
                            {
                                'type': 'debate_review_error',
                                'round': round_no,
                                'participant': reviewer.participant_id,
                                'provider': reviewer.provider,
                                'output': review_text,
                            }
                        )

                    emit(
                        {
                            'type': 'debate_review',
                            'round': round_no,
                            'participant': reviewer.participant_id,
                            'provider': reviewer.provider,
                            'output': review_text,
                        }
                    )
                    implementation_context = self._append_debate_line(
                        implementation_context,
                        speaker=reviewer.participant_id,
                        text=review_text,
                    )

                emit({'type': 'debate_completed', 'round': round_no})

            if check_cancel():
                emit({'type': 'canceled', 'round': round_no})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')

            with self._span(tracer, 'workflow.discussion', {'task.id': config.task_id, 'round': round_no}):
                emit(
                    {
                        'type': 'discussion_started',
                        'round': round_no,
                        'participant': config.author.participant_id,
                        'provider': config.author.provider,
                        'timeout_seconds': self.participant_timeout_seconds,
                    }
                )
                discussion_prompt = (
                    self._discussion_after_reviewer_prompt(config, round_no, implementation_context)
                    if debate_mode
                    else self._discussion_prompt(config, round_no, previous_gate_reason)
                )
                discussion = self.runner.run(
                    participant=config.author,
                    prompt=discussion_prompt,
                    cwd=config.cwd,
                    timeout_seconds=self.participant_timeout_seconds,
                    model=provider_models.get(config.author.provider),
                    model_params=provider_model_params.get(config.author.provider),
                    claude_team_agents=bool(config.claude_team_agents),
                    on_stream=(
                        self._stream_emitter(
                            emit=emit,
                            round_no=round_no,
                            stage='discussion',
                            participant=config.author.participant_id,
                            provider=config.author.provider,
                        )
                        if stream_mode
                        else None
                    ),
                )
            emit(
                {
                    'type': 'discussion',
                    'round': round_no,
                    'participant': config.author.participant_id,
                    'provider': config.author.provider,
                    'output': discussion.output,
                    'duration_seconds': discussion.duration_seconds,
                }
            )
            discussion_output = str(discussion.output or '').strip()
            if discussion_output:
                implementation_context = discussion_output

            if check_cancel():
                emit({'type': 'canceled', 'round': round_no})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')

            with self._span(tracer, 'workflow.implementation', {'task.id': config.task_id, 'round': round_no}):
                emit(
                    {
                        'type': 'implementation_started',
                        'round': round_no,
                        'participant': config.author.participant_id,
                        'provider': config.author.provider,
                        'timeout_seconds': self.participant_timeout_seconds,
                    }
                )
                implementation = self.runner.run(
                    participant=config.author,
                    prompt=self._implementation_prompt(config, round_no, implementation_context),
                    cwd=config.cwd,
                    timeout_seconds=self.participant_timeout_seconds,
                    model=provider_models.get(config.author.provider),
                    model_params=provider_model_params.get(config.author.provider),
                    claude_team_agents=bool(config.claude_team_agents),
                    on_stream=(
                        self._stream_emitter(
                            emit=emit,
                            round_no=round_no,
                            stage='implementation',
                            participant=config.author.participant_id,
                            provider=config.author.provider,
                        )
                        if stream_mode
                        else None
                    ),
                )
            emit(
                {
                    'type': 'implementation',
                    'round': round_no,
                    'participant': config.author.participant_id,
                    'provider': config.author.provider,
                    'output': implementation.output,
                    'duration_seconds': implementation.duration_seconds,
                }
            )

            if check_cancel():
                emit({'type': 'canceled', 'round': round_no})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')

            verdicts: list[ReviewVerdict] = []
            for reviewer in config.reviewers:
                with self._span(tracer, 'workflow.review', {'task.id': config.task_id, 'round': round_no, 'participant': reviewer.participant_id}):
                    emit(
                        {
                            'type': 'review_started',
                            'round': round_no,
                            'participant': reviewer.participant_id,
                            'timeout_seconds': review_timeout_seconds,
                        }
                    )
                    try:
                        review = self.runner.run(
                            participant=reviewer,
                            prompt=self._review_prompt(config, round_no, implementation.output),
                            cwd=config.cwd,
                            timeout_seconds=review_timeout_seconds,
                            model=provider_models.get(reviewer.provider),
                            model_params=provider_model_params.get(reviewer.provider),
                            claude_team_agents=bool(config.claude_team_agents),
                            on_stream=(
                                self._stream_emitter(
                                    emit=emit,
                                    round_no=round_no,
                                    stage='review',
                                    participant=reviewer.participant_id,
                                    provider=reviewer.provider,
                                )
                                if stream_mode
                                else None
                            ),
                        )
                    except Exception as exc:
                        reason = str(exc or 'review_failed').strip() or 'review_failed'
                        emit(
                            {
                                'type': 'review_error',
                                'round': round_no,
                                'participant': reviewer.participant_id,
                                'reason': reason,
                            }
                        )
                        verdict = ReviewVerdict.UNKNOWN
                        verdicts.append(verdict)
                        emit(
                            {
                                'type': 'review',
                                'round': round_no,
                                'participant': reviewer.participant_id,
                                'verdict': verdict.value,
                                'output': f'[review_error] {reason}',
                                'duration_seconds': 0.0,
                            }
                        )
                        continue
                verdict = self._normalize_verdict(review.verdict)
                verdicts.append(verdict)
                emit(
                    {
                        'type': 'review',
                        'round': round_no,
                        'participant': reviewer.participant_id,
                        'provider': reviewer.provider,
                        'verdict': verdict.value,
                        'output': review.output,
                        'duration_seconds': review.duration_seconds,
                    }
                )

            if check_cancel():
                emit({'type': 'canceled', 'round': round_no})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')

            with self._span(tracer, 'workflow.verify', {'task.id': config.task_id, 'round': round_no}):
                emit(
                    {
                        'type': 'verification_started',
                        'round': round_no,
                        'test_command': config.test_command,
                        'lint_command': config.lint_command,
                        'timeout_seconds': self.command_timeout_seconds,
                    }
                )
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
            # Deadline takes priority over round cap. If a deadline is set,
            # keep iterating until deadline/cancel/pass.
            if not deadline_mode and round_no >= config.max_rounds:
                return RunResult(status='failed_gate', rounds=round_no, gate_reason=gate.reason)

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
    def _stream_emitter(
        *,
        emit: Callable[[dict], None],
        round_no: int,
        stage: str,
        participant: str,
        provider: str,
    ) -> Callable[[str, str], None]:
        def _callback(stream_name: str, chunk: str) -> None:
            text = str(chunk or '')
            if not text:
                return
            emit(
                {
                    'type': 'participant_stream',
                    'round': round_no,
                    'stage': stage,
                    'stream': str(stream_name or 'stdout'),
                    'participant': participant,
                    'provider': provider,
                    'chunk': text,
                }
            )

        return _callback

    @staticmethod
    def _append_debate_line(base: str, *, speaker: str, text: str) -> str:
        payload = str(text or '').strip()
        if not payload:
            return base
        merged = f'{str(base or "").rstrip()}\n\n[{speaker}]\n{payload}'.strip()
        return WorkflowEngine._clip_text(merged, max_chars=5000)

    @staticmethod
    def _discussion_prompt(config: RunConfig, round_no: int, previous_gate_reason: str | None = None) -> str:
        level = max(0, min(2, int(config.evolution_level)))
        repair_mode = WorkflowEngine._normalize_repair_mode(config.repair_mode)
        language_instruction = WorkflowEngine._conversation_language_instruction(config.conversation_language)
        repair_guidance = WorkflowEngine._repair_mode_guidance(repair_mode)
        plain_mode_instruction = WorkflowEngine._plain_mode_instruction(bool(config.plain_mode))
        base = (
            f"Task: {config.title}\n"
            f"Round: {round_no}\n"
            f"EvolutionLevel: {level}\n"
            f"RepairMode: {repair_mode}\n"
            f"Description: {config.description}\n"
            f"{language_instruction}\n"
            f"{repair_guidance}\n"
            f"{plain_mode_instruction}\n"
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
    def _debate_seed_context(config: RunConfig, round_no: int, previous_gate_reason: str | None = None) -> str:
        level = max(0, min(2, int(config.evolution_level)))
        repair_mode = WorkflowEngine._normalize_repair_mode(config.repair_mode)
        language_instruction = WorkflowEngine._conversation_language_instruction(config.conversation_language)
        repair_guidance = WorkflowEngine._repair_mode_guidance(repair_mode)
        plain_mode_instruction = WorkflowEngine._plain_mode_instruction(bool(config.plain_mode))
        base = (
            f"Task: {config.title}\n"
            f"Round: {round_no}\n"
            f"EvolutionLevel: {level}\n"
            f"RepairMode: {repair_mode}\n"
            f"Description: {config.description}\n"
            f"{language_instruction}\n"
            f"{repair_guidance}\n"
            f"{plain_mode_instruction}\n"
            "Reviewer-first precheck context."
        )
        if round_no > 1 and previous_gate_reason:
            base += f"\nPrevious gate failure reason: {previous_gate_reason}"
        return base

    @staticmethod
    def _discussion_after_reviewer_prompt(config: RunConfig, round_no: int, reviewer_context: str) -> str:
        clipped = WorkflowEngine._clip_text(reviewer_context, max_chars=3200)
        level = max(0, min(2, int(config.evolution_level)))
        repair_mode = WorkflowEngine._normalize_repair_mode(config.repair_mode)
        language_instruction = WorkflowEngine._conversation_language_instruction(config.conversation_language)
        repair_guidance = WorkflowEngine._repair_mode_guidance(repair_mode)
        plain_mode_instruction = WorkflowEngine._plain_mode_instruction(bool(config.plain_mode))
        return (
            f"Task: {config.title}\n"
            f"Round: {round_no}\n"
            f"EvolutionLevel: {level}\n"
            f"RepairMode: {repair_mode}\n"
            f"{language_instruction}\n"
            f"{repair_guidance}\n"
            f"{plain_mode_instruction}\n"
            "Reviewer-first mode: reviewers have provided pre-implementation findings.\n"
            "Produce the author execution plan for this round and explicitly address reviewer concerns.\n"
            "Do not ask follow-up questions. Keep response concise.\n"
            f"Reviewer context:\n{clipped}\n"
        )

    @staticmethod
    def _implementation_prompt(config: RunConfig, round_no: int, discussion_output: str) -> str:
        clipped = WorkflowEngine._clip_text(discussion_output, max_chars=3000)
        level = max(0, min(2, int(config.evolution_level)))
        repair_mode = WorkflowEngine._normalize_repair_mode(config.repair_mode)
        language_instruction = WorkflowEngine._conversation_language_instruction(config.conversation_language)
        repair_guidance = WorkflowEngine._repair_mode_guidance(repair_mode)
        plain_mode_instruction = WorkflowEngine._plain_mode_instruction(bool(config.plain_mode))
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
            f"RepairMode: {repair_mode}\n"
            f"{language_instruction}\n"
            f"{repair_guidance}\n"
            f"{plain_mode_instruction}\n"
            "Implement based on this plan and summarize what changed.\n"
            f"Plan:\n{clipped}\n"
            f"{mode_guidance}\n"
            "Include explicit assumptions and risks.\n"
            "Do not ask follow-up questions. Keep response concise."
        )

    @staticmethod
    def _debate_review_prompt(
        config: RunConfig,
        round_no: int,
        discussion_context: str,
        reviewer_id: str,
    ) -> str:
        clipped = WorkflowEngine._clip_text(discussion_context, max_chars=3200)
        language_instruction = WorkflowEngine._conversation_language_instruction(config.conversation_language)
        plain_mode_instruction = WorkflowEngine._plain_mode_instruction(bool(config.plain_mode))
        return (
            f"Task: {config.title}\n"
            f"Round: {round_no}\n"
            f"Reviewer: {reviewer_id}\n"
            f"{language_instruction}\n"
            f"{plain_mode_instruction}\n"
            "Debate mode step: review the current plan/context and provide concise, concrete concerns.\n"
            "Focus on correctness, regression risk, reliability, security, and test gaps.\n"
            "Do not output VERDICT/NEXT_ACTION lines in this step.\n"
            "Provide plain text only: findings first, then suggested fixes.\n"
            f"Current context:\n{clipped}\n"
        )

    @staticmethod
    def _debate_reply_prompt(
        config: RunConfig,
        round_no: int,
        discussion_context: str,
        reviewer_id: str,
        reviewer_feedback: str,
    ) -> str:
        clipped_context = WorkflowEngine._clip_text(discussion_context, max_chars=2600)
        clipped_feedback = WorkflowEngine._clip_text(reviewer_feedback, max_chars=1400)
        language_instruction = WorkflowEngine._conversation_language_instruction(config.conversation_language)
        plain_mode_instruction = WorkflowEngine._plain_mode_instruction(bool(config.plain_mode))
        return (
            f"Task: {config.title}\n"
            f"Round: {round_no}\n"
            f"{language_instruction}\n"
            f"{plain_mode_instruction}\n"
            f"Debate mode step: respond to reviewer feedback from {reviewer_id}.\n"
            "Update the execution direction with explicit decisions.\n"
            "Format: accepted points, rejected points(with reason), and revised implementation focus.\n"
            "Do not output VERDICT/NEXT_ACTION lines.\n"
            f"Current context:\n{clipped_context}\n"
            f"Reviewer feedback:\n{clipped_feedback}\n"
        )

    @staticmethod
    def _review_prompt(config: RunConfig, round_no: int, implementation_output: str) -> str:
        clipped = WorkflowEngine._clip_text(implementation_output, max_chars=3000)
        level = max(0, min(2, int(config.evolution_level)))
        repair_mode = WorkflowEngine._normalize_repair_mode(config.repair_mode)
        language_instruction = WorkflowEngine._conversation_language_instruction(config.conversation_language)
        repair_guidance = WorkflowEngine._repair_mode_guidance(repair_mode)
        plain_mode_instruction = WorkflowEngine._plain_mode_instruction(bool(config.plain_mode))
        plain_review_format = WorkflowEngine._plain_review_format_instruction(
            enabled=bool(config.plain_mode),
            language=config.conversation_language,
        )
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
            f"RepairMode: {repair_mode}\n"
            f"{language_instruction}\n"
            f"{repair_guidance}\n"
            f"{plain_mode_instruction}\n"
            "Review the implementation summary and decide blocker status.\n"
            "Mark BLOCKER only for correctness, regression, security, or data-loss risks.\n"
            "Do not mark BLOCKER for style-only, process-only, or preference-only feedback.\n"
            "Hard limits: keep response short (<= 6 lines, <= 450 chars).\n"
            "Do not include command logs, internal process narration, or tool/skill references.\n"
            "If evidence is insufficient, return VERDICT: UNKNOWN quickly.\n"
            f"{mode_guidance}"
            "Output must include one line: VERDICT: NO_BLOCKER or VERDICT: BLOCKER or VERDICT: UNKNOWN.\n"
            f"{plain_review_format}\n"
            "Do not ask follow-up questions. Keep response concise.\n"
            f"Implementation summary:\n{clipped}\n"
        )

    @staticmethod
    def _review_timeout_seconds(participant_timeout_seconds: int) -> int:
        base = max(1, int(participant_timeout_seconds))
        # Review should fail fast to avoid long-running non-converging scans.
        return min(base, 75)

    @staticmethod
    def _normalize_verdict(raw: str) -> ReviewVerdict:
        v = (raw or '').strip().lower()
        if v == 'no_blocker':
            return ReviewVerdict.NO_BLOCKER
        if v == 'blocker':
            return ReviewVerdict.BLOCKER
        return ReviewVerdict.UNKNOWN

    @staticmethod
    def _conversation_language_instruction(raw: str | None) -> str:
        lang = str(raw or '').strip().lower()
        if lang == 'zh':
            return 'Language: respond in Simplified Chinese; keep control keywords (VERDICT/NEXT_ACTION) in English.'
        return 'Language: respond in English.'

    @staticmethod
    def _parse_deadline(value: str | None) -> datetime | None:
        text = (value or '').strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace(' ', 'T'))
        except ValueError:
            return None

    @staticmethod
    def _normalize_provider_models(value: dict[str, str] | None) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, raw in (value or {}).items():
            provider = str(key or '').strip().lower()
            model = str(raw or '').strip()
            if not provider or not model:
                continue
            out[provider] = model
        return out

    @staticmethod
    def _normalize_provider_model_params(value: dict[str, str] | None) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, raw in (value or {}).items():
            provider = str(key or '').strip().lower()
            params = str(raw or '').strip()
            if not provider or not params:
                continue
            out[provider] = params
        return out

    @staticmethod
    def _normalize_repair_mode(value: str | None) -> str:
        mode = str(value or '').strip().lower()
        if mode in {'minimal', 'balanced', 'structural'}:
            return mode
        return 'balanced'

    @staticmethod
    def _repair_mode_guidance(mode: str) -> str:
        normalized = WorkflowEngine._normalize_repair_mode(mode)
        if normalized == 'minimal':
            return (
                'Repair policy: minimal patch only. Restrict changes to immediate blockers; '
                'avoid broad refactors, file moves, and nonessential scope.'
            )
        if normalized == 'structural':
            return (
                'Repair policy: structural fix allowed. Resolve root causes even when it requires '
                'module refactor, API reshaping, and broader regression tests.'
            )
        return (
            'Repair policy: balanced fix (default). Resolve root cause with moderate scope and add '
            'targeted tests; avoid unnecessary architectural churn.'
        )

    @staticmethod
    def _plain_mode_instruction(enabled: bool) -> str:
        if not bool(enabled):
            return 'Plain Mode: disabled.'
        return (
            'Plain Mode: enabled. Write for small/beginner readers in short sentences. '
            'Avoid internal process jargon, hidden prompt mechanics, or tool/skill self-reference. '
            'Avoid legalistic wording such as abstract blocker-policy debates. '
            'Use concrete language focused on what is wrong, why it matters, and what to do next.'
        )

    @staticmethod
    def _plain_review_format_instruction(*, enabled: bool, language: str | None) -> str:
        if not bool(enabled):
            return (
                "After VERDICT line, provide concise rationale and critical risks."
            )
        lang = str(language or '').strip().lower()
        if lang == 'zh':
            return (
                "After VERDICT line, write exactly 3 short lines:\n"
                "问题: <一句话>\n"
                "影响: <一句话>\n"
                "下一步: <一句话>\n"
                "Each line should be simple and concrete; no internal workflow terms."
            )
        return (
            "After VERDICT line, write exactly 3 short lines:\n"
            "Issue: <one sentence>\n"
            "Impact: <one sentence>\n"
            "Next: <one sentence>\n"
            "Keep wording simple and concrete; no internal workflow terms."
        )
