from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import os
import re
import shlex
import subprocess
import time
from typing import Callable
from contextlib import nullcontext

from awe_agentcheck.adapters import ParticipantRunner
from awe_agentcheck.domain.gate import evaluate_medium_gate
from awe_agentcheck.domain.models import ReviewVerdict
from awe_agentcheck.observability import get_logger, set_task_context
from awe_agentcheck.participants import Participant

try:
    from langgraph.graph import END, StateGraph
except Exception:  # pragma: no cover - optional import fallback
    END = None
    StateGraph = None

_log = get_logger('awe_agentcheck.workflow')


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    command: str
    returncode: int
    stdout: str
    stderr: str


class ShellCommandExecutor:
    _ALLOWED_COMMAND_PREFIXES: tuple[tuple[str, ...], ...] = (
        ('py', '-m', 'pytest'),
        ('py', '-m', 'ruff'),
        ('python', '-m', 'pytest'),
        ('python', '-m', 'ruff'),
        ('python3', '-m', 'pytest'),
        ('python3', '-m', 'ruff'),
        ('pytest',),
        ('ruff',),
    )

    @classmethod
    def _normalize_command(cls, command: str | list[str]) -> list[str]:
        if isinstance(command, list):
            argv = [str(part).strip() for part in command if str(part).strip()]
        else:
            argv = shlex.split(str(command or '').strip(), posix=(os.name != 'nt'))
        if not argv:
            raise ValueError('command is empty')
        lowered = [part.lower() for part in argv]
        allowed = any(lowered[:len(prefix)] == list(prefix) for prefix in cls._ALLOWED_COMMAND_PREFIXES)
        if not allowed:
            raise ValueError(f'command prefix is not allowed: {argv[0]}')
        return argv

    def run(self, command: str | list[str], cwd: Path, timeout_seconds: int) -> CommandResult:
        display_command = str(command)
        try:
            argv = self._normalize_command(command)
            display_command = ' '.join(argv)
        except ValueError as exc:
            return CommandResult(
                ok=False,
                command=display_command,
                returncode=2,
                stdout='',
                stderr=str(exc),
            )
        started = time.monotonic()
        completed = subprocess.run(
            argv,
            shell=False,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout_seconds,
            env=self._build_subprocess_env(cwd),
        )
        elapsed = time.monotonic() - started
        _log.debug('shell_command command=%s ok=%s duration=%.2fs',
                   display_command, completed.returncode == 0, elapsed)
        return CommandResult(
            ok=completed.returncode == 0,
            command=display_command,
            returncode=completed.returncode,
            stdout=completed.stdout or '',
            stderr=completed.stderr or '',
        )

    @staticmethod
    def _build_subprocess_env(cwd: Path) -> dict[str, str]:
        env = dict(os.environ)
        workspace_src = (Path(cwd) / 'src').resolve(strict=False)
        if not workspace_src.is_dir():
            return env

        current_raw = str(env.get('PYTHONPATH', '') or '').strip()
        current_items = [item for item in current_raw.split(os.pathsep) if str(item).strip()]
        workspace_norm = str(workspace_src).replace('\\', '/').lower()
        ordered: list[str] = [str(workspace_src)]
        for item in current_items:
            text = str(item).strip()
            if not text:
                continue
            resolved = str(Path(text).resolve(strict=False))
            resolved_norm = resolved.replace('\\', '/').lower()
            if resolved_norm == workspace_norm:
                continue
            if resolved_norm.endswith('/awe-agentcheck/src'):
                continue
            ordered.append(text)
        env['PYTHONPATH'] = os.pathsep.join(ordered)
        return env


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
    test_command: str | list[str]
    lint_command: str | list[str]
    conversation_language: str = 'en'
    provider_models: dict[str, str] | None = None
    provider_model_params: dict[str, str] | None = None
    participant_models: dict[str, str] | None = None
    participant_model_params: dict[str, str] | None = None
    claude_team_agents: bool = False
    codex_multi_agents: bool = False
    claude_team_agents_overrides: dict[str, bool] | None = None
    codex_multi_agents_overrides: dict[str, bool] | None = None
    repair_mode: str = 'balanced'
    plain_mode: bool = True
    stream_mode: bool = False
    debate_mode: bool = False


@dataclass(frozen=True)
class RunResult:
    status: str
    rounds: int
    gate_reason: str


@dataclass(frozen=True)
class PreCompletionChecklistResult:
    passed: bool
    reason: str
    checks: dict[str, bool]
    evidence_paths: list[str]


@dataclass(frozen=True)
class ArchitectureAuditResult:
    enabled: bool
    passed: bool
    mode: str
    reason: str
    thresholds: dict[str, int]
    violations: list[dict[str, object]]
    scanned_files: int


class WorkflowEngine:
    def __init__(
        self,
        *,
        runner: ParticipantRunner,
        command_executor: ShellCommandExecutor,
        participant_timeout_seconds: int = 3600,
        command_timeout_seconds: int = 300,
        workflow_backend: str = 'langgraph',
    ):
        self.runner = runner
        self.command_executor = command_executor
        self.participant_timeout_seconds = max(1, int(participant_timeout_seconds))
        self.command_timeout_seconds = max(1, int(command_timeout_seconds))
        self.workflow_backend = self._normalize_workflow_backend(workflow_backend)
        self._langgraph_compiled = None

    def run(
        self,
        config: RunConfig,
        *,
        on_event: Callable[[dict], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> RunResult:
        if self.workflow_backend == 'langgraph':
            return self._run_langgraph(config, on_event=on_event, should_cancel=should_cancel)
        return self._run_classic(config, on_event=on_event, should_cancel=should_cancel)

    def _run_langgraph(
        self,
        config: RunConfig,
        *,
        on_event: Callable[[dict], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> RunResult:
        graph = self._get_langgraph()
        state = graph.invoke(
            {
                'config': config,
                'on_event': on_event,
                'should_cancel': should_cancel,
            }
        )
        result = state.get('result')
        if not isinstance(result, RunResult):
            raise RuntimeError('langgraph_execution_error: missing RunResult payload')
        return result

    def _run_classic(
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
        participant_models = self._normalize_participant_models(config.participant_models)
        participant_model_params = self._normalize_participant_model_params(config.participant_model_params)
        claude_team_agents_overrides = self._normalize_participant_agent_overrides(
            config.claude_team_agents_overrides
        )
        codex_multi_agents_overrides = self._normalize_participant_agent_overrides(
            config.codex_multi_agents_overrides
        )
        environment_context = self._environment_context(config)
        loop_tracker = self._new_loop_tracker()
        strategy_hint: str | None = None
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
            if deadline is not None and datetime.now(timezone.utc) >= deadline:
                emit({'type': 'deadline_reached', 'round': round_no, 'deadline': deadline.isoformat()})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='deadline_reached')

            set_task_context(task_id=config.task_id, round_no=round_no)
            _log.info('round_started round=%d', round_no)
            emit({'type': 'round_started', 'round': round_no})

            implementation_context = self._debate_seed_context(
                config,
                round_no,
                previous_gate_reason,
                environment_context=environment_context,
                strategy_hint=strategy_hint,
            )
            if debate_mode:
                debate_review_total = 0
                debate_review_usable = 0
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
                            prompt=self._debate_review_prompt(
                                config,
                                round_no,
                                implementation_context,
                                reviewer.participant_id,
                                environment_context=environment_context,
                                strategy_hint=strategy_hint,
                            ),
                            cwd=config.cwd,
                            timeout_seconds=review_timeout_seconds,
                            model=self._resolve_model_for_participant(
                                participant=reviewer,
                                provider_models=provider_models,
                                participant_models=participant_models,
                            ),
                            model_params=self._resolve_model_params_for_participant(
                                participant=reviewer,
                                provider_model_params=provider_model_params,
                                participant_model_params=participant_model_params,
                            ),
                            claude_team_agents=self._resolve_agent_toggle_for_participant(
                                participant=reviewer,
                                global_enabled=bool(config.claude_team_agents),
                                overrides=claude_team_agents_overrides,
                            ),
                            codex_multi_agents=self._resolve_agent_toggle_for_participant(
                                participant=reviewer,
                                global_enabled=bool(config.codex_multi_agents),
                                overrides=codex_multi_agents_overrides,
                            ),
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
                        runtime_reason = self._runtime_error_reason_from_result(debate_review)
                        if runtime_reason:
                            review_text = f'[debate_review_error] {runtime_reason}'
                            usable = False
                            emit(
                                {
                                    'type': 'debate_review_error',
                                    'round': round_no,
                                    'participant': reviewer.participant_id,
                                    'provider': reviewer.provider,
                                    'output': review_text,
                                }
                            )
                        else:
                            usable = self._is_actionable_debate_review_text(review_text)
                    except Exception as exc:
                        review_text = f'[debate_review_error] {str(exc or "review_failed").strip() or "review_failed"}'
                        usable = False
                        emit(
                            {
                                'type': 'debate_review_error',
                                'round': round_no,
                                'participant': reviewer.participant_id,
                                'provider': reviewer.provider,
                                'output': review_text,
                            }
                        )

                    debate_review_total += 1
                    if usable:
                        debate_review_usable += 1

                    emit(
                        {
                            'type': 'debate_review',
                            'round': round_no,
                            'participant': reviewer.participant_id,
                            'provider': reviewer.provider,
                            'output': review_text,
                            'usable': usable,
                        }
                    )
                    if usable:
                        implementation_context = self._append_debate_line(
                            implementation_context,
                            speaker=reviewer.participant_id,
                            text=review_text,
                        )

                emit(
                    {
                        'type': 'debate_completed',
                        'round': round_no,
                        'reviewers_total': debate_review_total,
                        'reviewers_usable': debate_review_usable,
                    }
                )
                if debate_review_total > 0 and debate_review_usable == 0:
                    reason = 'debate_review_unavailable'
                    emit(
                        {
                            'type': 'gate_failed',
                            'round': round_no,
                            'reason': reason,
                            'stage': 'debate_precheck',
                        }
                    )
                    return RunResult(status='failed_gate', rounds=round_no, gate_reason=reason)

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
                    self._discussion_after_reviewer_prompt(
                        config,
                        round_no,
                        implementation_context,
                        environment_context=environment_context,
                        strategy_hint=strategy_hint,
                    )
                    if debate_mode
                    else self._discussion_prompt(
                        config,
                        round_no,
                        previous_gate_reason,
                        environment_context=environment_context,
                        strategy_hint=strategy_hint,
                    )
                )
                discussion = self.runner.run(
                    participant=config.author,
                    prompt=discussion_prompt,
                    cwd=config.cwd,
                    timeout_seconds=self.participant_timeout_seconds,
                    model=self._resolve_model_for_participant(
                        participant=config.author,
                        provider_models=provider_models,
                        participant_models=participant_models,
                    ),
                    model_params=self._resolve_model_params_for_participant(
                        participant=config.author,
                        provider_model_params=provider_model_params,
                        participant_model_params=participant_model_params,
                    ),
                    claude_team_agents=self._resolve_agent_toggle_for_participant(
                        participant=config.author,
                        global_enabled=bool(config.claude_team_agents),
                        overrides=claude_team_agents_overrides,
                    ),
                    codex_multi_agents=self._resolve_agent_toggle_for_participant(
                        participant=config.author,
                        global_enabled=bool(config.codex_multi_agents),
                        overrides=codex_multi_agents_overrides,
                    ),
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
            discussion_runtime_reason = self._runtime_error_reason_from_result(discussion)
            if discussion_runtime_reason:
                emit(
                    {
                        'type': 'gate_failed',
                        'round': round_no,
                        'reason': discussion_runtime_reason,
                        'stage': 'discussion',
                    }
                )
                return RunResult(status='failed_gate', rounds=round_no, gate_reason=discussion_runtime_reason)
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
                    prompt=self._implementation_prompt(
                        config,
                        round_no,
                        implementation_context,
                        environment_context=environment_context,
                        strategy_hint=strategy_hint,
                    ),
                    cwd=config.cwd,
                    timeout_seconds=self.participant_timeout_seconds,
                    model=self._resolve_model_for_participant(
                        participant=config.author,
                        provider_models=provider_models,
                        participant_models=participant_models,
                    ),
                    model_params=self._resolve_model_params_for_participant(
                        participant=config.author,
                        provider_model_params=provider_model_params,
                        participant_model_params=participant_model_params,
                    ),
                    claude_team_agents=self._resolve_agent_toggle_for_participant(
                        participant=config.author,
                        global_enabled=bool(config.claude_team_agents),
                        overrides=claude_team_agents_overrides,
                    ),
                    codex_multi_agents=self._resolve_agent_toggle_for_participant(
                        participant=config.author,
                        global_enabled=bool(config.codex_multi_agents),
                        overrides=codex_multi_agents_overrides,
                    ),
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
            implementation_runtime_reason = self._runtime_error_reason_from_result(implementation)
            if implementation_runtime_reason:
                emit(
                    {
                        'type': 'gate_failed',
                        'round': round_no,
                        'reason': implementation_runtime_reason,
                        'stage': 'implementation',
                    }
                )
                return RunResult(status='failed_gate', rounds=round_no, gate_reason=implementation_runtime_reason)

            if check_cancel():
                emit({'type': 'canceled', 'round': round_no})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')

            verdicts: list[ReviewVerdict] = []
            review_outputs: list[str] = []
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
                            prompt=self._review_prompt(
                                config,
                                round_no,
                                implementation.output,
                                environment_context=environment_context,
                                strategy_hint=strategy_hint,
                            ),
                            cwd=config.cwd,
                            timeout_seconds=review_timeout_seconds,
                            model=self._resolve_model_for_participant(
                                participant=reviewer,
                                provider_models=provider_models,
                                participant_models=participant_models,
                            ),
                            model_params=self._resolve_model_params_for_participant(
                                participant=reviewer,
                                provider_model_params=provider_model_params,
                                participant_model_params=participant_model_params,
                            ),
                            claude_team_agents=self._resolve_agent_toggle_for_participant(
                                participant=reviewer,
                                global_enabled=bool(config.claude_team_agents),
                                overrides=claude_team_agents_overrides,
                            ),
                            codex_multi_agents=self._resolve_agent_toggle_for_participant(
                                participant=reviewer,
                                global_enabled=bool(config.codex_multi_agents),
                                overrides=codex_multi_agents_overrides,
                            ),
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
                        runtime_reason = self._runtime_error_reason_from_result(review)
                        if runtime_reason:
                            emit(
                                {
                                    'type': 'review_error',
                                    'round': round_no,
                                    'participant': reviewer.participant_id,
                                    'reason': runtime_reason,
                                }
                            )
                            verdict = ReviewVerdict.UNKNOWN
                            verdicts.append(verdict)
                            review_outputs.append(f'[review_error] {runtime_reason}')
                            emit(
                                {
                                    'type': 'review',
                                    'round': round_no,
                                    'participant': reviewer.participant_id,
                                    'verdict': verdict.value,
                                    'output': f'[review_error] {runtime_reason}',
                                    'duration_seconds': review.duration_seconds,
                                }
                            )
                            continue
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
                        review_outputs.append(f'[review_error] {reason}')
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
                review_outputs.append(str(review.output or ''))
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

            checklist = self._run_pre_completion_checklist(
                config=config,
                implementation_output=str(implementation.output or ''),
                review_outputs=review_outputs,
                test_result=test_result,
                lint_result=lint_result,
            )
            emit(
                {
                    'type': 'precompletion_checklist',
                    'round': round_no,
                    'passed': checklist.passed,
                    'reason': checklist.reason,
                    'checks': dict(checklist.checks),
                    'evidence_paths': list(checklist.evidence_paths),
                }
            )

            if check_cancel():
                emit({'type': 'canceled', 'round': round_no})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')

            if not checklist.passed:
                _log.warning('precompletion_failed round=%d reason=%s', round_no, checklist.reason)
                emit(
                    {
                        'type': 'gate_failed',
                        'round': round_no,
                        'reason': checklist.reason,
                        'stage': 'precompletion',
                    }
                )
                progress = self._assess_loop_progress(
                    loop_tracker=loop_tracker,
                    gate_reason=checklist.reason,
                    implementation_output=str(implementation.output or ''),
                    review_outputs=review_outputs,
                    tests_ok=bool(test_result.ok),
                    lint_ok=bool(lint_result.ok),
                )
                if progress.get('triggered'):
                    strategy_hint = str(progress.get('hint') or '').strip() or strategy_hint
                    emit(
                        {
                            'type': 'strategy_shifted',
                            'round': round_no,
                            'hint': strategy_hint,
                            'signals': dict(progress.get('signals') or {}),
                            'shift_count': int(progress.get('shift_count') or 0),
                        }
                    )
                previous_gate_reason = checklist.reason
                terminal_reason = str(progress.get('terminal_reason') or '').strip()
                if terminal_reason:
                    return RunResult(status='failed_gate', rounds=round_no, gate_reason=terminal_reason)
                if not deadline_mode and round_no >= config.max_rounds:
                    return RunResult(status='failed_gate', rounds=round_no, gate_reason=checklist.reason)
                continue

            architecture_audit = self._run_architecture_audit(config=config)
            emit(
                {
                    'type': 'architecture_audit',
                    'round': round_no,
                    'enabled': architecture_audit.enabled,
                    'passed': architecture_audit.passed,
                    'mode': architecture_audit.mode,
                    'severity': 'error' if architecture_audit.mode == 'hard' and not architecture_audit.passed else 'warning',
                    'reason': architecture_audit.reason,
                    'thresholds': dict(architecture_audit.thresholds),
                    'violations': list(architecture_audit.violations),
                    'scanned_files': architecture_audit.scanned_files,
                }
            )
            if architecture_audit.enabled and architecture_audit.mode == 'hard' and not architecture_audit.passed:
                _log.warning('architecture_audit_failed round=%d reason=%s', round_no, architecture_audit.reason)
                emit(
                    {
                        'type': 'gate_failed',
                        'round': round_no,
                        'reason': architecture_audit.reason,
                        'stage': 'architecture_audit',
                    }
                )
                progress = self._assess_loop_progress(
                    loop_tracker=loop_tracker,
                    gate_reason=architecture_audit.reason,
                    implementation_output=str(implementation.output or ''),
                    review_outputs=review_outputs,
                    tests_ok=bool(test_result.ok),
                    lint_ok=bool(lint_result.ok),
                )
                if progress.get('triggered'):
                    strategy_hint = str(progress.get('hint') or '').strip() or strategy_hint
                    emit(
                        {
                            'type': 'strategy_shifted',
                            'round': round_no,
                            'hint': strategy_hint,
                            'signals': dict(progress.get('signals') or {}),
                            'shift_count': int(progress.get('shift_count') or 0),
                        }
                    )
                previous_gate_reason = architecture_audit.reason
                terminal_reason = str(progress.get('terminal_reason') or '').strip()
                if terminal_reason:
                    return RunResult(status='failed_gate', rounds=round_no, gate_reason=terminal_reason)
                if not deadline_mode and round_no >= config.max_rounds:
                    return RunResult(status='failed_gate', rounds=round_no, gate_reason=architecture_audit.reason)
                continue

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
            progress = self._assess_loop_progress(
                loop_tracker=loop_tracker,
                gate_reason=gate.reason,
                implementation_output=str(implementation.output or ''),
                review_outputs=review_outputs,
                tests_ok=bool(test_result.ok),
                lint_ok=bool(lint_result.ok),
            )
            if progress.get('triggered'):
                strategy_hint = str(progress.get('hint') or '').strip() or strategy_hint
                emit(
                    {
                        'type': 'strategy_shifted',
                        'round': round_no,
                        'hint': strategy_hint,
                        'signals': dict(progress.get('signals') or {}),
                        'shift_count': int(progress.get('shift_count') or 0),
                    }
                )
            terminal_reason = str(progress.get('terminal_reason') or '').strip()
            if terminal_reason:
                return RunResult(status='failed_gate', rounds=round_no, gate_reason=terminal_reason)
            # Deadline takes priority over round cap. If a deadline is set,
            # keep iterating until deadline/cancel/pass.
            if not deadline_mode and round_no >= config.max_rounds:
                return RunResult(status='failed_gate', rounds=round_no, gate_reason=gate.reason)

    def _get_langgraph(self):
        if self._langgraph_compiled is not None:
            return self._langgraph_compiled
        if StateGraph is None or END is None:
            raise RuntimeError('langgraph_backend_unavailable: install "langgraph"')

        graph = StateGraph(dict)
        graph.add_node('preflight', self._langgraph_preflight_node)
        graph.add_node('setup', self._langgraph_setup_node)
        graph.add_node('round', self._langgraph_round_node)
        graph.add_node('finalize', self._langgraph_finalize_node)
        graph.set_entry_point('preflight')
        graph.add_conditional_edges(
            'preflight',
            self._langgraph_preflight_route,
            {
                'setup': 'setup',
                'finalize': 'finalize',
            },
        )
        graph.add_edge('setup', 'round')
        graph.add_conditional_edges(
            'round',
            self._langgraph_round_route,
            {
                'round': 'round',
                'finalize': 'finalize',
            },
        )
        graph.add_edge('finalize', END)
        self._langgraph_compiled = graph.compile()
        return self._langgraph_compiled

    def _langgraph_preflight_node(self, state: dict) -> dict:
        config = state.get('config')
        if not isinstance(config, RunConfig):
            return {
                'preflight_ok': False,
                'preflight_error': 'langgraph_execution_error: invalid config payload',
                'config': state.get('config'),
                'on_event': state.get('on_event'),
                'should_cancel': state.get('should_cancel'),
            }
        if not str(config.task_id or '').strip():
            return {
                'preflight_ok': False,
                'preflight_error': 'langgraph_preflight_error: missing task_id',
                'config': config,
                'on_event': state.get('on_event'),
                'should_cancel': state.get('should_cancel'),
            }
        if int(config.max_rounds) <= 0:
            return {
                'preflight_ok': False,
                'preflight_error': 'langgraph_preflight_error: invalid max_rounds',
                'config': config,
                'on_event': state.get('on_event'),
                'should_cancel': state.get('should_cancel'),
            }
        return {
            'preflight_ok': True,
            'config': config,
            'on_event': state.get('on_event'),
            'should_cancel': state.get('should_cancel'),
        }

    @staticmethod
    def _langgraph_preflight_route(state: dict) -> str:
        return 'setup' if bool(state.get('preflight_ok')) else 'finalize'

    def _langgraph_setup_node(self, state: dict) -> dict:
        config = state.get('config')
        if not isinstance(config, RunConfig):
            return {
                'preflight_ok': False,
                'preflight_error': 'langgraph_execution_error: invalid config payload',
                'config': state.get('config'),
                'on_event': state.get('on_event'),
                'should_cancel': state.get('should_cancel'),
                'result': RunResult(status='failed_system', rounds=0, gate_reason='langgraph_execution_error: invalid config payload'),
            }
        emit = state.get('on_event')
        if not callable(emit):
            def _noop_emit(event: dict) -> None:
                _ = event
                return None
            emit = _noop_emit
        emit({'type': 'task_started', 'task_id': config.task_id})
        set_task_context(task_id=config.task_id)
        _log.info('workflow_started task_id=%s max_rounds=%d backend=langgraph', config.task_id, config.max_rounds)
        return {
            'preflight_ok': True,
            'config': config,
            'on_event': state.get('on_event'),
            'should_cancel': state.get('should_cancel'),
            'deadline': self._parse_deadline(config.evolve_until),
            'round_no': 0,
            'result': None,
        }

    def _langgraph_round_node(self, state: dict) -> dict:
        config = state.get('config')
        if not isinstance(config, RunConfig):
            return {
                'result': RunResult(status='failed_system', rounds=0, gate_reason='langgraph_execution_error: invalid config payload')
            }
        emit = state.get('on_event')
        if not callable(emit):
            def _noop_emit(event: dict) -> None:
                _ = event
                return None
            emit = _noop_emit
        check_cancel = state.get('should_cancel')
        if not callable(check_cancel):
            def _noop_cancel() -> bool:
                return False
            check_cancel = _noop_cancel
        round_offset = 0

        def _emit_with_round_offset(event: dict) -> None:
            if not isinstance(event, dict):
                return
            payload = dict(event)
            if str(payload.get('type') or '').strip().lower() == 'task_started':
                return
            if 'round' in payload:
                try:
                    payload['round'] = int(payload.get('round') or 0) + round_offset
                except Exception:
                    pass
            emit(payload)

        # Keep LangGraph orchestration while running the workflow loop to completion
        # in one execution pass for smoother UX and event continuity.
        one_round = self._run_classic(
            config,
            on_event=_emit_with_round_offset,
            should_cancel=check_cancel,
        )
        return {
            **state,
            'round_no': int(one_round.rounds or 0),
            'result': RunResult(
                status=str(one_round.status or 'failed_system'),
                rounds=int(one_round.rounds or 0),
                gate_reason=str(one_round.gate_reason or ''),
            ),
            'last_round_result': RunResult(
                status=str(one_round.status or 'failed_system'),
                rounds=int(one_round.rounds or 0),
                gate_reason=str(one_round.gate_reason or ''),
            ),
        }

    @staticmethod
    def _langgraph_should_finish_round(
        *,
        result: RunResult,
        round_no: int,
        max_rounds: int,
        deadline: datetime | None,
    ) -> bool:
        status = str(result.status or '').strip().lower()
        if status in {'passed', 'failed_system', 'canceled'}:
            return True
        if status != 'failed_gate':
            return True
        # When deadline mode is enabled, deadline (not round cap) controls continuation.
        if deadline is not None:
            return False
        return int(round_no) >= max(1, int(max_rounds))

    @staticmethod
    def _langgraph_round_route(state: dict) -> str:
        return 'finalize' if isinstance(state.get('result'), RunResult) else 'round'

    def _langgraph_finalize_node(self, state: dict) -> dict:
        if ('preflight_ok' in state) and (not bool(state.get('preflight_ok'))):
            err = str(state.get('preflight_error') or 'langgraph_preflight_error')
            return {'result': RunResult(status='failed_system', rounds=0, gate_reason=err)}
        result = state.get('result')
        if isinstance(result, RunResult):
            return {'result': result}
        last_round = state.get('last_round_result')
        if isinstance(last_round, RunResult):
            return {'result': last_round}
        return {'result': RunResult(status='failed_system', rounds=0, gate_reason='langgraph_execution_error: missing RunResult payload')}

    @staticmethod
    def _normalize_workflow_backend(value: str | None) -> str:
        backend = str(value or '').strip().lower()
        if backend == 'langgraph' and StateGraph is None:
            _log.warning('langgraph backend requested but unavailable; falling back to classic backend')
            return 'classic'
        if backend == 'langgraph':
            return 'langgraph'
        return 'classic'

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
    def _environment_context(config: RunConfig) -> str:
        root = Path(config.cwd).resolve(strict=False)
        tree_excerpt = WorkflowEngine._workspace_tree_excerpt(root, max_depth=2, max_entries=42)
        lines = [
            'Execution context:',
            f'- Workspace root: {root}',
            f'- Test command: {config.test_command}',
            f'- Lint command: {config.lint_command}',
            '- Constraints: cite repo-relative evidence paths for key findings and edits.',
            '- Constraints: avoid hidden bypasses/default secrets; keep changes scoped and testable.',
            '- Workspace excerpt:',
            tree_excerpt,
        ]
        return '\n'.join(lines)

    @staticmethod
    def _workspace_tree_excerpt(root: Path, *, max_depth: int, max_entries: int) -> str:
        ignore_dirs = WorkflowEngine._default_ignore_dirs()
        lines: list[str] = []
        visited = 0
        truncated = False

        def walk(path: Path, depth: int) -> None:
            nonlocal visited, truncated
            if truncated or depth > max_depth:
                return
            try:
                entries = sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
            except OSError:
                return

            for entry in entries:
                if visited >= max_entries:
                    truncated = True
                    return
                is_dir = entry.is_dir()
                if is_dir and entry.name in ignore_dirs:
                    continue
                try:
                    rel = entry.relative_to(root).as_posix()
                except ValueError:
                    rel = entry.name
                indent = '  ' * max(0, depth)
                marker = 'D' if is_dir else 'F'
                lines.append(f'{indent}- [{marker}] {rel}')
                visited += 1
                if is_dir:
                    walk(entry, depth + 1)

        if root.exists() and root.is_dir():
            walk(root, 0)
        if not lines:
            return '- [n/a] workspace tree unavailable'
        if truncated:
            lines.append(f'- [truncated] showing first {max_entries} entries')
        return '\n'.join(lines)

    @staticmethod
    def _default_ignore_dirs() -> set[str]:
        return {
            '.git',
            '.agents',
            '.venv',
            '__pycache__',
            '.pytest_cache',
            '.ruff_cache',
            'node_modules',
        }

    @staticmethod
    def _architecture_thresholds_for_level(level: int) -> dict[str, int]:
        normalized = max(0, min(2, int(level)))
        if normalized >= 2:
            thresholds = {
                'python_file_lines_max': 1000,
                'frontend_file_lines_max': 2000,
                'python_responsibility_keywords_max': 8,
                'service_file_lines_max': 3500,
                'workflow_file_lines_max': 2200,
                'dashboard_js_lines_max': 3200,
                'prompt_builder_count_max': 10,
                'adapter_runtime_raise_max': 0,
            }
        else:
            thresholds = {
            'python_file_lines_max': 1200,
            'frontend_file_lines_max': 2500,
            'python_responsibility_keywords_max': 10,
            'service_file_lines_max': 4500,
            'workflow_file_lines_max': 2600,
            'dashboard_js_lines_max': 3800,
            'prompt_builder_count_max': 14,
            'adapter_runtime_raise_max': 0,
        }

        env_map = {
            'python_file_lines_max': ('AWE_ARCH_PYTHON_FILE_LINES_MAX', 10),
            'frontend_file_lines_max': ('AWE_ARCH_FRONTEND_FILE_LINES_MAX', 10),
            'python_responsibility_keywords_max': ('AWE_ARCH_RESPONSIBILITY_KEYWORDS_MAX', 1),
            'service_file_lines_max': ('AWE_ARCH_SERVICE_FILE_LINES_MAX', 10),
            'workflow_file_lines_max': ('AWE_ARCH_WORKFLOW_FILE_LINES_MAX', 10),
            'dashboard_js_lines_max': ('AWE_ARCH_DASHBOARD_JS_LINES_MAX', 10),
            'prompt_builder_count_max': ('AWE_ARCH_PROMPT_BUILDER_COUNT_MAX', 1),
            'adapter_runtime_raise_max': ('AWE_ARCH_ADAPTER_RUNTIME_RAISE_MAX', 0),
        }
        for key, (env_name, minimum) in env_map.items():
            raw = str(os.getenv(env_name, '') or '').strip()
            if not raw:
                continue
            try:
                parsed = int(raw)
            except ValueError:
                continue
            thresholds[key] = max(minimum, parsed)
        return thresholds

    @staticmethod
    def _run_architecture_audit(*, config: RunConfig) -> ArchitectureAuditResult:
        level = max(0, min(2, int(config.evolution_level)))
        if level < 1:
            return ArchitectureAuditResult(
                enabled=False,
                passed=True,
                mode='off',
                reason='skipped',
                thresholds={},
                violations=[],
                scanned_files=0,
            )

        root = Path(config.cwd).resolve(strict=False)
        thresholds = WorkflowEngine._architecture_thresholds_for_level(level)
        mode = WorkflowEngine._architecture_audit_mode(level)
        ignore_dirs = WorkflowEngine._default_ignore_dirs()
        frontend_ext = {'.html', '.css', '.scss', '.js', '.jsx', '.ts', '.tsx', '.vue', '.svelte'}
        responsibility_keywords = (
            'sandbox',
            'policy',
            'analytics',
            'evolution',
            'proposal',
            'merge',
            'history',
            'review',
            'workflow',
            'database',
            'api',
            'session',
            'theme',
            'avatar',
            'benchmark',
            'preflight',
            'runtime',
        )
        violations: list[dict[str, object]] = []
        scanned_files = 0

        if not root.exists() or not root.is_dir():
            return ArchitectureAuditResult(
                enabled=True,
                passed=False,
                mode=mode,
                reason='architecture_audit_workspace_missing',
                thresholds=thresholds,
                violations=[],
                scanned_files=0,
            )

        for dirpath, dirs, files in os.walk(root):
            base = Path(dirpath)
            dirs[:] = [d for d in dirs if d not in ignore_dirs]
            for name in files:
                path = base / name
                ext = path.suffix.lower()
                if ext not in {'.py', *frontend_ext}:
                    continue
                scanned_files += 1
                try:
                    file_text = path.read_text(encoding='utf-8', errors='ignore')
                except OSError:
                    continue
                line_count = int(file_text.count('\n') + 1) if file_text else 0
                try:
                    rel = path.relative_to(root).as_posix()
                except ValueError:
                    rel = path.as_posix()
                if ext == '.py' and line_count > int(thresholds['python_file_lines_max']):
                    violations.append(
                        {
                            'kind': 'python_file_too_large',
                            'path': rel,
                            'lines': int(line_count),
                            'limit': int(thresholds['python_file_lines_max']),
                            'suggestion': 'Split responsibilities into smaller modules.',
                        }
                    )
                if ext == '.py' and line_count > max(300, int(thresholds['python_file_lines_max']) // 2):
                    lowered = file_text.lower()
                    responsibility_hits = sum(1 for k in responsibility_keywords if k in lowered)
                    responsibility_limit = int(thresholds['python_responsibility_keywords_max'])
                    if responsibility_hits > responsibility_limit:
                        violations.append(
                            {
                                'kind': 'python_mixed_responsibilities',
                                'path': rel,
                                'lines': int(line_count),
                                'responsibility_hits': int(responsibility_hits),
                                'limit': int(responsibility_limit),
                                'suggestion': 'Extract domain concerns into focused modules.',
                            }
                        )
                if rel == 'src/awe_agentcheck/service.py' and line_count > int(thresholds['service_file_lines_max']):
                    violations.append(
                        {
                            'kind': 'service_monolith_too_large',
                            'path': rel,
                            'lines': int(line_count),
                            'limit': int(thresholds['service_file_lines_max']),
                            'suggestion': 'Split service lifecycle/orchestration concerns into focused modules.',
                        }
                    )
                if rel == 'src/awe_agentcheck/workflow.py' and line_count > int(thresholds['workflow_file_lines_max']):
                    violations.append(
                        {
                            'kind': 'workflow_monolith_too_large',
                            'path': rel,
                            'lines': int(line_count),
                            'limit': int(thresholds['workflow_file_lines_max']),
                            'suggestion': 'Extract prompt/phase controllers into smaller workflow modules.',
                        }
                    )
                if rel in {'src/awe_agentcheck/workflow.py', 'src/awe_agentcheck/service.py'} and ext == '.py':
                    prompt_builder_hits = int(file_text.count('_prompt('))
                    if prompt_builder_hits > int(thresholds['prompt_builder_count_max']):
                        violations.append(
                            {
                                'kind': 'prompt_assembly_hotspot',
                                'path': rel,
                                'prompt_builder_hits': prompt_builder_hits,
                                'limit': int(thresholds['prompt_builder_count_max']),
                                'suggestion': 'Move prompt templates into dedicated files and compose with data-only bindings.',
                            }
                        )
                if rel == 'src/awe_agentcheck/adapters.py':
                    runtime_raise_hits = len(re.findall(r'raise\s+RuntimeError\s*\(', file_text))
                    if runtime_raise_hits > int(thresholds['adapter_runtime_raise_max']):
                        violations.append(
                            {
                                'kind': 'adapter_runtime_raise_detected',
                                'path': rel,
                                'runtime_raise_hits': int(runtime_raise_hits),
                                'limit': int(thresholds['adapter_runtime_raise_max']),
                                'suggestion': 'Return structured adapter errors and let workflow decide retry/fallback/gate.',
                            }
                        )
                if ext in frontend_ext and line_count > int(thresholds['frontend_file_lines_max']):
                    violations.append(
                        {
                            'kind': 'frontend_file_too_large',
                            'path': rel,
                            'lines': int(line_count),
                            'limit': int(thresholds['frontend_file_lines_max']),
                            'suggestion': 'Split UI into smaller files/components.',
                        }
                    )
                if rel == 'web/assets/dashboard.js' and line_count > int(thresholds['dashboard_js_lines_max']):
                    violations.append(
                        {
                            'kind': 'dashboard_monolith_too_large',
                            'path': rel,
                            'lines': int(line_count),
                            'limit': int(thresholds['dashboard_js_lines_max']),
                            'suggestion': 'Split dashboard runtime by panel/feature modules.',
                        }
                    )

        scripts_dir = root / 'scripts'
        if scripts_dir.exists() and scripts_dir.is_dir():
            ps1_files = {
                p.stem.lower()
                for p in scripts_dir.glob('*.ps1')
                if p.is_file()
            }
            sh_files = {
                p.stem.lower()
                for p in scripts_dir.glob('*.sh')
                if p.is_file()
            }
            missing_shell = sorted(name for name in ps1_files if name not in sh_files)
            if missing_shell:
                violations.append(
                    {
                        'kind': 'script_cross_platform_gap',
                        'path': 'scripts',
                        'missing_shell_variants': missing_shell,
                        'suggestion': 'Add matching .sh wrappers for cross-platform usage.',
                    }
                )

        if not violations:
            reason = 'passed'
        elif mode == 'hard':
            reason = 'architecture_threshold_exceeded'
        else:
            reason = 'architecture_threshold_warning'
        return ArchitectureAuditResult(
            enabled=True,
            passed=not violations,
            mode=mode,
            reason=reason,
            thresholds=thresholds,
            violations=violations,
            scanned_files=int(scanned_files),
        )

    @staticmethod
    def _architecture_audit_mode(level: int) -> str:
        raw = str(os.getenv('AWE_ARCH_AUDIT_MODE', '') or '').strip().lower()
        if raw in {'off', 'warn', 'hard'}:
            return raw
        normalized = max(0, min(2, int(level)))
        return 'hard' if normalized >= 2 else 'warn'

    @staticmethod
    def _inject_prompt_extras(
        *,
        base: str,
        environment_context: str | None,
        strategy_hint: str | None,
    ) -> str:
        text = str(base or '')
        env = str(environment_context or '').strip()
        if env:
            text = f'{text}\n{env}'
        hint = str(strategy_hint or '').strip()
        if hint:
            text = f'{text}\nStrategy shift hint: {hint}'
        return text

    @staticmethod
    def _new_loop_tracker() -> dict:
        return {
            'last_gate_reason': '',
            'gate_repeat': 0,
            'last_impl_sig': '',
            'impl_repeat': 0,
            'last_review_sig': '',
            'review_repeat': 0,
            'last_verify_sig': '',
            'verify_repeat': 0,
            'strategy_shift_count': 0,
        }

    @staticmethod
    def _text_signature(text: str, *, max_chars: int = 1000) -> str:
        payload = re.sub(r'\s+', ' ', str(text or '').strip().lower())
        if not payload:
            return ''
        if len(payload) > max_chars:
            payload = payload[:max_chars]
        return hashlib.sha1(payload.encode('utf-8')).hexdigest()[:16]

    @staticmethod
    def _strategy_hint_from_reason(
        *,
        gate_reason: str,
        gate_repeat: int,
        impl_repeat: int,
        review_repeat: int,
        verify_repeat: int,
    ) -> str:
        reason = str(gate_reason or '').strip().lower()
        if reason == 'precompletion_evidence_missing':
            return (
                'Current summaries lack concrete file evidence. Next round must include explicit repo-relative paths '
                'for changed files, failed checks, and reviewer findings.'
            )
        if reason in {'tests_failed', 'lint_failed'}:
            return (
                'Verification is repeating failures. Switch to test-first micro-fix: isolate one failing area, '
                'change minimal files, rerun verification, then continue.'
            )
        if reason in {'command_timeout', 'command_not_found', 'command_not_configured', 'command_failed'}:
            return (
                'Agent runtime failed before producing reliable output. Fix CLI command/runtime configuration first, '
                'then rerun with a minimal reproducible scope.'
            )
        if reason in {'review_blocker', 'review_unknown'}:
            return (
                'Reviewer concern persists. Limit scope to reviewer blockers only, address each blocker with evidence, '
                'and avoid unrelated edits.'
            )
        if reason == 'architecture_threshold_exceeded':
            return (
                'Architecture audit failed on oversized files. Prioritize splitting large files by responsibility, '
                'add targeted tests around moved logic, then rerun verification.'
            )
        if reason == 'architecture_threshold_warning':
            return (
                'Architecture audit reports warning-level debt. Keep current fix scoped, then schedule a follow-up '
                'split plan with concrete module boundaries and validation.'
            )
        return (
            f'Loop detected (gate_repeat={gate_repeat}, impl_repeat={impl_repeat}, '
            f'review_repeat={review_repeat}, verify_repeat={verify_repeat}). '
            'Narrow scope, change approach, and provide concrete evidence paths.'
        )

    def _assess_loop_progress(
        self,
        *,
        loop_tracker: dict,
        gate_reason: str,
        implementation_output: str,
        review_outputs: list[str],
        tests_ok: bool,
        lint_ok: bool,
    ) -> dict:
        reason = str(gate_reason or '').strip().lower()
        impl_sig = self._text_signature(implementation_output)
        review_sig = self._text_signature('\n'.join(str(v or '') for v in review_outputs))
        verify_sig = self._text_signature(f'tests_ok={bool(tests_ok)} lint_ok={bool(lint_ok)} reason={reason}')

        if reason and reason == str(loop_tracker.get('last_gate_reason') or ''):
            loop_tracker['gate_repeat'] = int(loop_tracker.get('gate_repeat', 0)) + 1
        else:
            loop_tracker['last_gate_reason'] = reason
            loop_tracker['gate_repeat'] = 1 if reason else 0

        if impl_sig and impl_sig == str(loop_tracker.get('last_impl_sig') or ''):
            loop_tracker['impl_repeat'] = int(loop_tracker.get('impl_repeat', 0)) + 1
        else:
            loop_tracker['last_impl_sig'] = impl_sig
            loop_tracker['impl_repeat'] = 1 if impl_sig else 0

        if review_sig and review_sig == str(loop_tracker.get('last_review_sig') or ''):
            loop_tracker['review_repeat'] = int(loop_tracker.get('review_repeat', 0)) + 1
        else:
            loop_tracker['last_review_sig'] = review_sig
            loop_tracker['review_repeat'] = 1 if review_sig else 0

        if verify_sig and verify_sig == str(loop_tracker.get('last_verify_sig') or ''):
            loop_tracker['verify_repeat'] = int(loop_tracker.get('verify_repeat', 0)) + 1
        else:
            loop_tracker['last_verify_sig'] = verify_sig
            loop_tracker['verify_repeat'] = 1 if verify_sig else 0

        gate_repeat = int(loop_tracker.get('gate_repeat', 0))
        impl_repeat = int(loop_tracker.get('impl_repeat', 0))
        review_repeat = int(loop_tracker.get('review_repeat', 0))
        verify_repeat = int(loop_tracker.get('verify_repeat', 0))
        triggered = (
            gate_repeat >= 3
            or impl_repeat >= 3
            or review_repeat >= 3
            or verify_repeat >= 3
        )
        if triggered:
            loop_tracker['strategy_shift_count'] = int(loop_tracker.get('strategy_shift_count', 0)) + 1

        shift_count = int(loop_tracker.get('strategy_shift_count', 0))
        terminal_reason = ''
        if triggered and shift_count >= 5:
            terminal_reason = 'loop_no_progress'

        hint = ''
        if triggered:
            hint = self._strategy_hint_from_reason(
                gate_reason=reason,
                gate_repeat=gate_repeat,
                impl_repeat=impl_repeat,
                review_repeat=review_repeat,
                verify_repeat=verify_repeat,
            )

        return {
            'triggered': triggered,
            'hint': hint,
            'signals': {
                'gate_reason': reason,
                'gate_repeat': gate_repeat,
                'implementation_repeat': impl_repeat,
                'review_repeat': review_repeat,
                'verification_repeat': verify_repeat,
            },
            'shift_count': shift_count,
            'terminal_reason': terminal_reason,
        }

    def _run_pre_completion_checklist(
        self,
        *,
        config: RunConfig,
        implementation_output: str,
        review_outputs: list[str],
        test_result: CommandResult,
        lint_result: CommandResult,
    ) -> PreCompletionChecklistResult:
        test_command_configured = bool(str(config.test_command or '').strip())
        lint_command_configured = bool(str(config.lint_command or '').strip())
        verification_executed = True
        tests_ok = bool(test_result.ok)
        lint_ok = bool(lint_result.ok)

        evidence_source = '\n'.join(
            [
                str(implementation_output or ''),
                '\n'.join(str(item or '') for item in review_outputs),
                str(test_result.stdout or ''),
                str(test_result.stderr or ''),
                str(lint_result.stdout or ''),
                str(lint_result.stderr or ''),
            ]
        )
        evidence_paths = self._extract_evidence_paths(
            evidence_source,
            cwd=Path(config.cwd),
            max_items=12,
        )
        evidence_paths_present = len(evidence_paths) > 0

        checks = {
            'test_command_configured': test_command_configured,
            'lint_command_configured': lint_command_configured,
            'verification_executed': verification_executed,
            'tests_ok': tests_ok,
            'lint_ok': lint_ok,
            'evidence_paths_present': evidence_paths_present,
        }

        reason = 'passed'
        if not test_command_configured or not lint_command_configured:
            reason = 'precompletion_commands_missing'
        elif not verification_executed:
            reason = 'precompletion_verification_missing'
        elif not tests_ok:
            reason = 'tests_failed'
        elif not lint_ok:
            reason = 'lint_failed'
        elif not evidence_paths_present:
            reason = 'precompletion_evidence_missing'

        return PreCompletionChecklistResult(
            passed=(reason == 'passed'),
            reason=reason,
            checks=checks,
            evidence_paths=evidence_paths,
        )

    @staticmethod
    def _extract_evidence_paths(text: str, *, cwd: Path, max_items: int = 12) -> list[str]:
        pattern = re.compile(r'(?:[A-Za-z]:[\\/])?[A-Za-z0-9._\\/-]+\.[A-Za-z0-9]{1,8}')
        seen: set[str] = set()
        out: list[str] = []
        workspace_root = Path(cwd).resolve(strict=False)

        for raw in pattern.findall(str(text or '')):
            candidate = str(raw or '').strip().strip('.,;:()[]{}<>"\'')
            if not candidate:
                continue
            lowered = candidate.lower()
            if lowered.startswith(('http://', 'https://')):
                continue
            if len(candidate) < 5:
                continue
            normalized = candidate.replace('\\', '/')
            try:
                path_obj = Path(candidate)
                if path_obj.is_absolute():
                    resolved = path_obj.resolve(strict=False)
                    try:
                        rel = resolved.relative_to(workspace_root).as_posix()
                        normalized = rel
                    except ValueError:
                        normalized = resolved.as_posix()
            except Exception:
                normalized = candidate.replace('\\', '/')
            if normalized.startswith('./'):
                normalized = normalized[2:]
            if normalized in seen:
                continue
            seen.add(normalized)
            out.append(normalized)
            if len(out) >= max_items:
                break
        return out

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
    def _discussion_prompt(
        config: RunConfig,
        round_no: int,
        previous_gate_reason: str | None = None,
        *,
        environment_context: str | None = None,
        strategy_hint: str | None = None,
    ) -> str:
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
        return WorkflowEngine._inject_prompt_extras(
            base=base,
            environment_context=environment_context,
            strategy_hint=strategy_hint,
        )

    @staticmethod
    def _debate_seed_context(
        config: RunConfig,
        round_no: int,
        previous_gate_reason: str | None = None,
        *,
        environment_context: str | None = None,
        strategy_hint: str | None = None,
    ) -> str:
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
        return WorkflowEngine._inject_prompt_extras(
            base=base,
            environment_context=environment_context,
            strategy_hint=strategy_hint,
        )

    @staticmethod
    def _discussion_after_reviewer_prompt(
        config: RunConfig,
        round_no: int,
        reviewer_context: str,
        *,
        environment_context: str | None = None,
        strategy_hint: str | None = None,
    ) -> str:
        clipped = WorkflowEngine._clip_text(reviewer_context, max_chars=3200)
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
            f"{language_instruction}\n"
            f"{repair_guidance}\n"
            f"{plain_mode_instruction}\n"
            "Reviewer-first mode: reviewers have provided pre-implementation findings.\n"
            "Produce the author execution plan for this round and explicitly address reviewer concerns.\n"
            "Reviewer is primary in this phase: do not invent unrelated change themes.\n"
            "Only include revisions tied to reviewer findings and user intent.\n"
            "If you reject a reviewer suggestion, state reason and safer alternative.\n"
            "Do not ask follow-up questions. Keep response concise.\n"
            f"Reviewer context:\n{clipped}\n"
        )
        return WorkflowEngine._inject_prompt_extras(
            base=base,
            environment_context=environment_context,
            strategy_hint=strategy_hint,
        )

    @staticmethod
    def _implementation_prompt(
        config: RunConfig,
        round_no: int,
        discussion_output: str,
        *,
        environment_context: str | None = None,
        strategy_hint: str | None = None,
    ) -> str:
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
        base = (
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
            "Implement only agreed plan items; do not add unrelated changes.\n"
            "Do not introduce default secrets/tokens or hidden bypass behavior.\n"
            "Include explicit assumptions and risks.\n"
            "Include an Evidence section with repo-relative file paths touched or validated.\n"
            "Do not ask follow-up questions. Keep response concise."
        )
        return WorkflowEngine._inject_prompt_extras(
            base=base,
            environment_context=environment_context,
            strategy_hint=strategy_hint,
        )

    @staticmethod
    def _debate_review_prompt(
        config: RunConfig,
        round_no: int,
        discussion_context: str,
        reviewer_id: str,
        *,
        environment_context: str | None = None,
        strategy_hint: str | None = None,
    ) -> str:
        clipped = WorkflowEngine._clip_text(discussion_context, max_chars=3200)
        language_instruction = WorkflowEngine._conversation_language_instruction(config.conversation_language)
        plain_mode_instruction = WorkflowEngine._plain_mode_instruction(bool(config.plain_mode))
        audit_mode = WorkflowEngine._is_audit_discovery_task(config)
        depth_guidance = (
            "Task mode is audit/discovery: perform repository-wide checks as needed, then provide concrete findings with file paths."
            if audit_mode
            else "Review current context and provide concrete risk findings."
        )
        checklist_guidance = WorkflowEngine._review_checklist_guidance(config.evolution_level)
        base = (
            f"Task: {config.title}\n"
            f"Round: {round_no}\n"
            f"Reviewer: {reviewer_id}\n"
            f"{language_instruction}\n"
            f"{plain_mode_instruction}\n"
            "Debate mode step: review the current plan/context and provide concise, concrete concerns.\n"
            "Focus on correctness, regression risk, reliability, security, and test gaps.\n"
            f"{depth_guidance}\n"
            f"{checklist_guidance}\n"
            "If you run checks, include 1-3 evidence points with file paths.\n"
            "Do not include command logs, internal process narration, or tool/skill references.\n"
            "If context is insufficient, return one short line starting with: insufficient_context: ...\n"
            "Do not output VERDICT/NEXT_ACTION lines in this step.\n"
            "Provide plain text only: findings first, then suggested fixes.\n"
            f"Current context:\n{clipped}\n"
        )
        return WorkflowEngine._inject_prompt_extras(
            base=base,
            environment_context=environment_context,
            strategy_hint=strategy_hint,
        )

    @staticmethod
    def _debate_reply_prompt(
        config: RunConfig,
        round_no: int,
        discussion_context: str,
        reviewer_id: str,
        reviewer_feedback: str,
        *,
        environment_context: str | None = None,
        strategy_hint: str | None = None,
    ) -> str:
        clipped_context = WorkflowEngine._clip_text(discussion_context, max_chars=2600)
        clipped_feedback = WorkflowEngine._clip_text(reviewer_feedback, max_chars=1400)
        language_instruction = WorkflowEngine._conversation_language_instruction(config.conversation_language)
        plain_mode_instruction = WorkflowEngine._plain_mode_instruction(bool(config.plain_mode))
        base = (
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
        return WorkflowEngine._inject_prompt_extras(
            base=base,
            environment_context=environment_context,
            strategy_hint=strategy_hint,
        )

    @staticmethod
    def _is_actionable_debate_review_text(text: str) -> bool:
        payload = str(text or '').strip()
        if not payload:
            return False
        lowered = payload.lower()
        if lowered.startswith('[debate_review_error]'):
            return False
        if 'command_timeout provider=' in lowered:
            return False
        if 'provider_limit provider=' in lowered:
            return False
        if 'command_not_found provider=' in lowered:
            return False
        if 'command_failed provider=' in lowered:
            return False
        if 'command_not_configured provider=' in lowered:
            return False
        return True

    @staticmethod
    def _runtime_error_reason_from_text(text: str) -> str | None:
        lowered = str(text or '').strip().lower()
        if not lowered:
            return None
        if 'provider_limit provider=' in lowered:
            return 'provider_limit'
        if 'command_timeout provider=' in lowered:
            return 'command_timeout'
        if 'command_not_found provider=' in lowered:
            return 'command_not_found'
        if 'command_not_configured provider=' in lowered:
            return 'command_not_configured'
        if 'command_failed provider=' in lowered:
            return 'command_failed'
        return None

    @staticmethod
    def _runtime_error_reason_from_result(result: object) -> str | None:
        output = str(getattr(result, 'output', '') or '').strip()
        reason = WorkflowEngine._runtime_error_reason_from_text(output)
        if reason:
            return reason
        returncode_raw = getattr(result, 'returncode', 0)
        try:
            returncode = int(returncode_raw)
        except Exception:
            returncode = 0
        if returncode != 0:
            return 'participant_runtime_error'
        return None

    @staticmethod
    def _is_audit_discovery_task(config: RunConfig) -> bool:
        text = f"{str(config.title or '')}\n{str(config.description or '')}".lower()
        if not text.strip():
            return False
        keywords = (
            'audit',
            'review',
            'inspect',
            'scan',
            'check',
            'bug',
            'bugs',
            'vulnerability',
            'vulnerabilities',
            'security',
            'hardening',
            'improve',
            'improvement',
            'quality',
            'refine',
            '漏洞',
            '缺陷',
            '错误',
            '检查',
            '审查',
            '评审',
            '改进',
            '优化',
            '完善',
            '风险',
        )
        return any(k in text for k in keywords)

    @staticmethod
    def _review_prompt(
        config: RunConfig,
        round_no: int,
        implementation_output: str,
        *,
        environment_context: str | None = None,
        strategy_hint: str | None = None,
    ) -> str:
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
        control_schema_instruction = WorkflowEngine._control_output_schema_instruction()
        audit_mode = WorkflowEngine._is_audit_discovery_task(config)
        depth_guidance = (
            "Task mode is audit/discovery: allow deeper checks and provide concrete evidence with file paths."
            if audit_mode
            else "Keep review focused on implementation summary and stated scope."
        )
        checklist_guidance = WorkflowEngine._review_checklist_guidance(config.evolution_level)
        mode_guidance = ''
        if level >= 1:
            mode_guidance = (
                "For evolution proposals, block only if there is correctness/regression/security/data-loss risk.\n"
                "Do not block solely because an optional enhancement exists.\n"
            )
        base = (
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
            f"{depth_guidance}\n"
            f"{checklist_guidance}\n"
            "Keep output concise but complete enough to justify verdict.\n"
            "Do not include command logs, internal process narration, or tool/skill references.\n"
            'If evidence is insufficient, set "verdict":"UNKNOWN" quickly.\n'
            f"{mode_guidance}"
            "Output JSON only. No markdown fences. No extra prose before or after the JSON object.\n"
            f"{control_schema_instruction}\n"
            f"{plain_review_format}\n"
            "Reference at least one concrete repo-relative file path when possible.\n"
            "Do not ask follow-up questions. Keep response concise.\n"
            f"Implementation summary:\n{clipped}\n"
        )
        return WorkflowEngine._inject_prompt_extras(
            base=base,
            environment_context=environment_context,
            strategy_hint=strategy_hint,
        )

    @staticmethod
    def _review_timeout_seconds(participant_timeout_seconds: int) -> int:
        return max(1, int(participant_timeout_seconds))

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
            return (
                'Language: respond in Simplified Chinese; keep control field values in English '
                '(NO_BLOCKER/BLOCKER/UNKNOWN and pass/retry/stop).'
            )
        return 'Language: respond in English.'

    @staticmethod
    def _parse_deadline(value: str | None) -> datetime | None:
        text = (value or '').strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace(' ', 'T'))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc)
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
    def _normalize_participant_models(value: dict[str, str] | None) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, raw in (value or {}).items():
            participant = str(key or '').strip()
            model = str(raw or '').strip()
            if not participant or not model:
                continue
            out[participant] = model
            out.setdefault(participant.lower(), model)
        return out

    @staticmethod
    def _normalize_participant_model_params(value: dict[str, str] | None) -> dict[str, str]:
        out: dict[str, str] = {}
        for key, raw in (value or {}).items():
            participant = str(key or '').strip()
            params = str(raw or '').strip()
            if not participant or not params:
                continue
            out[participant] = params
            out.setdefault(participant.lower(), params)
        return out

    @staticmethod
    def _normalize_participant_agent_overrides(value: dict[str, bool] | None) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for key, raw in (value or {}).items():
            participant = str(key or '').strip()
            if not participant:
                continue
            if isinstance(raw, bool):
                enabled = raw
            else:
                text = str(raw or '').strip().lower()
                enabled = text in {'1', 'true', 'yes', 'on'}
            out[participant] = enabled
            out[participant.lower()] = enabled
        return out

    @staticmethod
    def _resolve_agent_toggle_for_participant(
        *,
        participant: Participant,
        global_enabled: bool,
        overrides: dict[str, bool],
    ) -> bool:
        participant_id = str(participant.participant_id or '').strip()
        if participant_id:
            if participant_id in overrides:
                return bool(overrides[participant_id])
            lowered = participant_id.lower()
            if lowered in overrides:
                return bool(overrides[lowered])
        return bool(global_enabled)

    @staticmethod
    def _resolve_model_for_participant(
        *,
        participant: Participant,
        provider_models: dict[str, str],
        participant_models: dict[str, str],
    ) -> str | None:
        participant_id = str(participant.participant_id or '').strip()
        if participant_id:
            exact = str(participant_models.get(participant_id) or '').strip()
            if exact:
                return exact
            lowered = str(participant_models.get(participant_id.lower()) or '').strip()
            if lowered:
                return lowered
        return str(provider_models.get(participant.provider) or '').strip() or None

    @staticmethod
    def _resolve_model_params_for_participant(
        *,
        participant: Participant,
        provider_model_params: dict[str, str],
        participant_model_params: dict[str, str],
    ) -> str | None:
        participant_id = str(participant.participant_id or '').strip()
        if participant_id:
            exact = str(participant_model_params.get(participant_id) or '').strip()
            if exact:
                return exact
            lowered = str(participant_model_params.get(participant_id.lower()) or '').strip()
            if lowered:
                return lowered
        return str(provider_model_params.get(participant.provider) or '').strip() or None

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
                'Fill JSON fields "issue", "impact", and "next" with concise rationale and critical risks.'
            )
        lang = str(language or '').strip().lower()
        if lang == 'zh':
            return (
                'Set JSON fields exactly:\n'
                '- issue: <一句话>\n'
                '- impact: <一句话>\n'
                '- next: <一句话>\n'
                'Each sentence should be simple and concrete; no internal workflow terms.'
            )
        return (
            'Set JSON fields exactly:\n'
            '- issue: <one sentence>\n'
            '- impact: <one sentence>\n'
            '- next: <one sentence>\n'
            'Keep wording simple and concrete; no internal workflow terms.'
        )

    @staticmethod
    def _review_checklist_guidance(level: int) -> str:
        normalized = max(0, min(2, int(level)))
        if normalized < 1:
            return "Checklist: focus on correctness, security, and regression evidence."
        return (
            "Required checklist (cover every item with evidence path or explicit 'n/a'): "
            "security; concurrency/state transitions; DB lock/retry handling; architecture size/responsibility "
            "(oversized files/modules); frontend maintainability (single-file UI bloat); cross-platform runtime/scripts."
        )

    @staticmethod
    def _control_output_schema_instruction() -> str:
        return (
            "Required control output schema (JSON only, one object; no markdown fences): "
            '{"verdict":"NO_BLOCKER|BLOCKER|UNKNOWN","next_action":"pass|retry|stop","issue":"...","impact":"...","next":"..."}'
            " Do not output legacy VERDICT/NEXT_ACTION lines unless compatibility mode is explicitly enabled."
        )
