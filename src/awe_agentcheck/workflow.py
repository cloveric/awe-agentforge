from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
import os
import re
import shlex
from string import Template
import subprocess
import time
from typing import Callable
from contextlib import nullcontext
from awe_agentcheck.adapters import ParticipantRunner
from awe_agentcheck.domain.events import EventType
from awe_agentcheck.domain.gate import evaluate_medium_gate
from awe_agentcheck.domain.models import ReviewVerdict
from awe_agentcheck.observability import get_logger, set_task_context
from awe_agentcheck.participants import Participant
from awe_agentcheck.workflow_architecture import (
    build_environment_context,
    run_architecture_audit,
)
from awe_agentcheck.workflow_prompting import (
    inject_prompt_extras,
    render_prompt_template,
)
from awe_agentcheck.workflow_runtime import (
    normalize_participant_agent_overrides as runtime_normalize_participant_agent_overrides,
    normalize_participant_model_params as runtime_normalize_participant_model_params,
    normalize_participant_models as runtime_normalize_participant_models,
    normalize_provider_model_params as runtime_normalize_provider_model_params,
    normalize_provider_models as runtime_normalize_provider_models,
    normalize_repair_mode as runtime_normalize_repair_mode,
    resolve_agent_toggle_for_participant as runtime_resolve_agent_toggle_for_participant,
    resolve_model_for_participant as runtime_resolve_model_for_participant,
    resolve_model_params_for_participant as runtime_resolve_model_params_for_participant,
)
from awe_agentcheck.task_options import (
    normalize_memory_mode as normalize_memory_mode_task,
    normalize_phase_timeout_seconds as normalize_phase_timeout_seconds_task,
)
from awe_agentcheck.workflow_text import clip_text, text_signature
try:
    from langgraph.graph import END, StateGraph
except ImportError:  # pragma: no cover - optional import fallback
    END = None
    StateGraph = None
_log = get_logger('awe_agentcheck.workflow')
_PROMPT_TEMPLATE_DIR = Path(__file__).resolve().parent / 'prompt_templates'
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
        # Avoid leaking parent pytest/coverage instrumentation into task subprocesses.
        for key in (
            'COVERAGE_PROCESS_START',
            'COV_CORE_SOURCE',
            'COV_CORE_CONFIG',
            'COV_CORE_DATAFILE',
            'PYTEST_CURRENT_TEST',
            'PYTEST_ADDOPTS',
        ):
            env.pop(key, None)
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
    memory_mode: str = 'basic'
    memory_context: dict[str, str] | None = None
    phase_timeout_seconds: dict[str, int] | None = None
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
class WorkflowEngine:
    _prompt_template_cache: dict[str, Template] = {}
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
        emit_task_started: bool = True,
        round_offset: int = 0,
        initial_previous_gate_reason: str | None = None,
        initial_strategy_hint: str | None = None,
        initial_loop_tracker: dict | None = None,
        initial_prompt_cache_state: dict | None = None,
        force_single_round: bool = False,
    ) -> RunResult:
        emit = on_event or (lambda event: None)
        check_cancel = should_cancel or (lambda: False)
        if emit_task_started:
            emit({'type': EventType.TASK_STARTED.value, 'task_id': config.task_id})
            _log.info('workflow_started task_id=%s max_rounds=%d', config.task_id, config.max_rounds)
        set_task_context(task_id=config.task_id)
        tracer = self._get_tracer()
        previous_gate_reason: str | None = str(initial_previous_gate_reason or '').strip() or None
        deadline = self._parse_deadline(config.evolve_until)
        provider_models = runtime_normalize_provider_models(config.provider_models)
        provider_model_params = runtime_normalize_provider_model_params(config.provider_model_params)
        participant_models = runtime_normalize_participant_models(config.participant_models)
        participant_model_params = runtime_normalize_participant_model_params(config.participant_model_params)
        claude_team_agents_overrides = runtime_normalize_participant_agent_overrides(
            config.claude_team_agents_overrides
        )
        codex_multi_agents_overrides = runtime_normalize_participant_agent_overrides(
            config.codex_multi_agents_overrides
        )
        environment_context = build_environment_context(
            cwd=Path(config.cwd),
            test_command=config.test_command,
            lint_command=config.lint_command,
        )
        loop_tracker = initial_loop_tracker if isinstance(initial_loop_tracker, dict) else self._new_loop_tracker()
        prompt_cache_state = (
            initial_prompt_cache_state
            if isinstance(initial_prompt_cache_state, dict)
            else {
                'participant_model_signatures': {},
                'participant_tool_signatures': {},
                'participant_stage_prefix_signatures': {},
            }
        )
        strategy_hint: str | None = str(initial_strategy_hint or '').strip() or None
        stream_mode = bool(config.stream_mode)
        debate_mode = bool(config.debate_mode) and bool(config.reviewers)
        memory_mode = normalize_memory_mode_task(config.memory_mode, strict=False)
        phase_timeouts = self._resolve_phase_timeout_seconds(config.phase_timeout_seconds)
        discussion_timeout_seconds = int(phase_timeouts.get('discussion', self.participant_timeout_seconds))
        implementation_timeout_seconds = int(phase_timeouts.get('implementation', self.participant_timeout_seconds))
        review_timeout_seconds = int(phase_timeouts.get('review', self._review_timeout_seconds(self.participant_timeout_seconds)))
        command_timeout_seconds = int(phase_timeouts.get('command', self.command_timeout_seconds))
        proposal_memory_context = self._memory_context_for_stage(
            config=config,
            memory_mode=memory_mode,
            stage='proposal',
        )
        discussion_memory_context = self._memory_context_for_stage(
            config=config,
            memory_mode=memory_mode,
            stage='discussion',
        )
        implementation_memory_context = self._memory_context_for_stage(
            config=config,
            memory_mode=memory_mode,
            stage='implementation',
        )
        review_memory_context = self._memory_context_for_stage(
            config=config,
            memory_mode=memory_mode,
            stage='review',
        )
        deadline_mode = deadline is not None
        round_no = max(0, int(round_offset))
        while True:
            round_no += 1
            if debate_mode and check_cancel():
                emit({'type': EventType.CANCELED.value, 'round': round_no})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')
            if deadline is not None and datetime.now(timezone.utc) >= deadline:
                emit({'type': EventType.DEADLINE_REACHED.value, 'round': round_no, 'deadline': deadline.isoformat()})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='deadline_reached')
            set_task_context(task_id=config.task_id, round_no=round_no)
            _log.info('round_started round=%d', round_no)
            emit({'type': EventType.ROUND_STARTED.value, 'round': round_no})
            implementation_context = self._debate_seed_context(
                config,
                round_no,
                previous_gate_reason,
                environment_context=environment_context,
                strategy_hint=strategy_hint,
                memory_context=proposal_memory_context,
            )
            if debate_mode:
                debate_review_total = 0
                debate_review_usable = 0
                emit(
                    {
                        'type': EventType.DEBATE_STARTED.value,
                        'round': round_no,
                        'mode': 'reviewer_first',
                        'reviewer_count': len(config.reviewers),
                    }
                )
                for reviewer in config.reviewers:
                    if check_cancel():
                        emit({'type': EventType.CANCELED.value, 'round': round_no})
                        return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')
                    emit(
                        {
                            'type': EventType.DEBATE_REVIEW_STARTED.value,
                            'round': round_no,
                            'participant': reviewer.participant_id,
                            'provider': reviewer.provider,
                            'timeout_seconds': review_timeout_seconds,
                        }
                    )
                    try:
                        debate_review_prompt = self._debate_review_prompt(
                            config,
                            round_no,
                            implementation_context,
                            reviewer.participant_id,
                            environment_context=environment_context,
                            strategy_hint=strategy_hint,
                            memory_context=proposal_memory_context,
                        )
                        runtime_profile = self._participant_runtime_profile(
                            participant=reviewer,
                            config=config,
                            provider_models=provider_models,
                            provider_model_params=provider_model_params,
                            participant_models=participant_models,
                            participant_model_params=participant_model_params,
                            claude_team_agents_overrides=claude_team_agents_overrides,
                            codex_multi_agents_overrides=codex_multi_agents_overrides,
                        )
                        probe_event, break_events = self._record_prompt_cache_probe(
                            cache_state=prompt_cache_state,
                            round_no=round_no,
                            stage='debate_review',
                            participant=reviewer,
                            model=runtime_profile['model'],
                            model_params=runtime_profile['model_params'],
                            claude_team_agents=bool(runtime_profile['claude_team_agents']),
                            codex_multi_agents=bool(runtime_profile['codex_multi_agents']),
                            prompt=debate_review_prompt,
                        )
                        emit(probe_event)
                        for cache_break in break_events:
                            emit(cache_break)
                        debate_review = self.runner.run(
                            participant=reviewer,
                            prompt=debate_review_prompt,
                            cwd=config.cwd,
                            timeout_seconds=review_timeout_seconds,
                            model=runtime_profile['model'],
                            model_params=runtime_profile['model_params'],
                            claude_team_agents=bool(runtime_profile['claude_team_agents']),
                            codex_multi_agents=bool(runtime_profile['codex_multi_agents']),
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
                                    'type': EventType.DEBATE_REVIEW_ERROR.value,
                                    'round': round_no,
                                    'participant': reviewer.participant_id,
                                    'provider': reviewer.provider,
                                    'output': review_text,
                                }
                            )
                        else:
                            usable = self._is_actionable_debate_review_text(review_text)
                    except Exception as exc:
                        _log.exception('debate_review_exception round=%s participant=%s', round_no, reviewer.participant_id)
                        review_text = f'[debate_review_error] {str(exc or "review_failed").strip() or "review_failed"}'
                        usable = False
                        emit(
                            {
                                'type': EventType.DEBATE_REVIEW_ERROR.value,
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
                            'type': EventType.DEBATE_REVIEW.value,
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
                        'type': EventType.DEBATE_COMPLETED.value,
                        'round': round_no,
                        'reviewers_total': debate_review_total,
                        'reviewers_usable': debate_review_usable,
                    }
                )
                if debate_review_total > 0 and debate_review_usable == 0:
                    reason = 'debate_review_unavailable'
                    emit(
                        {
                            'type': EventType.GATE_FAILED.value,
                            'round': round_no,
                            'reason': reason,
                            'stage': 'debate_precheck',
                        }
                    )
                    return RunResult(status='failed_gate', rounds=round_no, gate_reason=reason)

            if check_cancel():
                emit({'type': EventType.CANCELED.value, 'round': round_no})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')

            with self._span(tracer, 'workflow.discussion', {'task.id': config.task_id, 'round': round_no}):
                emit(
                    {
                        'type': EventType.DISCUSSION_STARTED.value,
                        'round': round_no,
                        'participant': config.author.participant_id,
                        'provider': config.author.provider,
                        'timeout_seconds': discussion_timeout_seconds,
                    }
                )
                discussion_prompt = (
                    self._discussion_after_reviewer_prompt(
                        config,
                        round_no,
                        implementation_context,
                        environment_context=environment_context,
                        strategy_hint=strategy_hint,
                        memory_context=discussion_memory_context,
                    )
                    if debate_mode
                    else self._discussion_prompt(
                        config,
                        round_no,
                        previous_gate_reason,
                        environment_context=environment_context,
                        strategy_hint=strategy_hint,
                        memory_context=discussion_memory_context,
                    )
                )
                discussion_profile = self._participant_runtime_profile(
                    participant=config.author,
                    config=config,
                    provider_models=provider_models,
                    provider_model_params=provider_model_params,
                    participant_models=participant_models,
                    participant_model_params=participant_model_params,
                    claude_team_agents_overrides=claude_team_agents_overrides,
                    codex_multi_agents_overrides=codex_multi_agents_overrides,
                )
                discussion_probe_event, discussion_break_events = self._record_prompt_cache_probe(
                    cache_state=prompt_cache_state,
                    round_no=round_no,
                    stage='discussion',
                    participant=config.author,
                    model=discussion_profile['model'],
                    model_params=discussion_profile['model_params'],
                    claude_team_agents=bool(discussion_profile['claude_team_agents']),
                    codex_multi_agents=bool(discussion_profile['codex_multi_agents']),
                    prompt=discussion_prompt,
                )
                emit(discussion_probe_event)
                for cache_break in discussion_break_events:
                    emit(cache_break)
                discussion = self.runner.run(
                    participant=config.author,
                    prompt=discussion_prompt,
                    cwd=config.cwd,
                    timeout_seconds=discussion_timeout_seconds,
                    model=discussion_profile['model'],
                    model_params=discussion_profile['model_params'],
                    claude_team_agents=bool(discussion_profile['claude_team_agents']),
                    codex_multi_agents=bool(discussion_profile['codex_multi_agents']),
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
                    'type': EventType.DISCUSSION.value,
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
                        'type': EventType.GATE_FAILED.value,
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
                emit({'type': EventType.CANCELED.value, 'round': round_no})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')

            with self._span(tracer, 'workflow.implementation', {'task.id': config.task_id, 'round': round_no}):
                emit(
                    {
                        'type': EventType.IMPLEMENTATION_STARTED.value,
                        'round': round_no,
                        'participant': config.author.participant_id,
                        'provider': config.author.provider,
                        'timeout_seconds': implementation_timeout_seconds,
                    }
                )
                implementation_prompt = self._implementation_prompt(
                    config,
                    round_no,
                    implementation_context,
                    environment_context=environment_context,
                    strategy_hint=strategy_hint,
                    memory_context=implementation_memory_context,
                )
                implementation_profile = self._participant_runtime_profile(
                    participant=config.author,
                    config=config,
                    provider_models=provider_models,
                    provider_model_params=provider_model_params,
                    participant_models=participant_models,
                    participant_model_params=participant_model_params,
                    claude_team_agents_overrides=claude_team_agents_overrides,
                    codex_multi_agents_overrides=codex_multi_agents_overrides,
                )
                implementation_probe_event, implementation_break_events = self._record_prompt_cache_probe(
                    cache_state=prompt_cache_state,
                    round_no=round_no,
                    stage='implementation',
                    participant=config.author,
                    model=implementation_profile['model'],
                    model_params=implementation_profile['model_params'],
                    claude_team_agents=bool(implementation_profile['claude_team_agents']),
                    codex_multi_agents=bool(implementation_profile['codex_multi_agents']),
                    prompt=implementation_prompt,
                )
                emit(implementation_probe_event)
                for cache_break in implementation_break_events:
                    emit(cache_break)
                implementation = self.runner.run(
                    participant=config.author,
                    prompt=implementation_prompt,
                    cwd=config.cwd,
                    timeout_seconds=implementation_timeout_seconds,
                    model=implementation_profile['model'],
                    model_params=implementation_profile['model_params'],
                    claude_team_agents=bool(implementation_profile['claude_team_agents']),
                    codex_multi_agents=bool(implementation_profile['codex_multi_agents']),
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
                    'type': EventType.IMPLEMENTATION.value,
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
                        'type': EventType.GATE_FAILED.value,
                        'round': round_no,
                        'reason': implementation_runtime_reason,
                        'stage': 'implementation',
                    }
                )
                return RunResult(status='failed_gate', rounds=round_no, gate_reason=implementation_runtime_reason)

            if check_cancel():
                emit({'type': EventType.CANCELED.value, 'round': round_no})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')

            verdicts: list[ReviewVerdict] = []
            review_outputs: list[str] = []
            for reviewer in config.reviewers:
                with self._span(tracer, 'workflow.review', {'task.id': config.task_id, 'round': round_no, 'participant': reviewer.participant_id}):
                    emit(
                        {
                            'type': EventType.REVIEW_STARTED.value,
                            'round': round_no,
                            'participant': reviewer.participant_id,
                            'timeout_seconds': review_timeout_seconds,
                        }
                    )
                    try:
                        review_prompt = self._review_prompt(
                            config,
                            round_no,
                            implementation.output,
                            environment_context=environment_context,
                            strategy_hint=strategy_hint,
                            memory_context=review_memory_context,
                        )
                        review_profile = self._participant_runtime_profile(
                            participant=reviewer,
                            config=config,
                            provider_models=provider_models,
                            provider_model_params=provider_model_params,
                            participant_models=participant_models,
                            participant_model_params=participant_model_params,
                            claude_team_agents_overrides=claude_team_agents_overrides,
                            codex_multi_agents_overrides=codex_multi_agents_overrides,
                        )
                        review_probe_event, review_break_events = self._record_prompt_cache_probe(
                            cache_state=prompt_cache_state,
                            round_no=round_no,
                            stage='review',
                            participant=reviewer,
                            model=review_profile['model'],
                            model_params=review_profile['model_params'],
                            claude_team_agents=bool(review_profile['claude_team_agents']),
                            codex_multi_agents=bool(review_profile['codex_multi_agents']),
                            prompt=review_prompt,
                        )
                        emit(review_probe_event)
                        for cache_break in review_break_events:
                            emit(cache_break)
                        review = self.runner.run(
                            participant=reviewer,
                            prompt=review_prompt,
                            cwd=config.cwd,
                            timeout_seconds=review_timeout_seconds,
                            model=review_profile['model'],
                            model_params=review_profile['model_params'],
                            claude_team_agents=bool(review_profile['claude_team_agents']),
                            codex_multi_agents=bool(review_profile['codex_multi_agents']),
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
                                    'type': EventType.REVIEW_ERROR.value,
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
                                    'type': EventType.REVIEW.value,
                                    'round': round_no,
                                    'participant': reviewer.participant_id,
                                    'verdict': verdict.value,
                                    'output': f'[review_error] {runtime_reason}',
                                    'duration_seconds': review.duration_seconds,
                                }
                            )
                            continue
                    except Exception as exc:
                        _log.exception('review_exception round=%s participant=%s', round_no, reviewer.participant_id)
                        reason = str(exc or 'review_failed').strip() or 'review_failed'
                        emit(
                            {
                                'type': EventType.REVIEW_ERROR.value,
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
                                'type': EventType.REVIEW.value,
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
                        'type': EventType.REVIEW.value,
                        'round': round_no,
                        'participant': reviewer.participant_id,
                        'provider': reviewer.provider,
                        'verdict': verdict.value,
                        'output': review.output,
                        'duration_seconds': review.duration_seconds,
                    }
                )

            if check_cancel():
                emit({'type': EventType.CANCELED.value, 'round': round_no})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')

            with self._span(tracer, 'workflow.verify', {'task.id': config.task_id, 'round': round_no}):
                emit(
                    {
                        'type': EventType.VERIFICATION_STARTED.value,
                        'round': round_no,
                        'test_command': config.test_command,
                        'lint_command': config.lint_command,
                        'timeout_seconds': command_timeout_seconds,
                    }
                )
                test_result = self.command_executor.run(
                    config.test_command,
                    cwd=config.cwd,
                    timeout_seconds=command_timeout_seconds,
                )
                lint_result = self.command_executor.run(
                    config.lint_command,
                    cwd=config.cwd,
                    timeout_seconds=command_timeout_seconds,
                )
            emit(
                {
                    'type': EventType.VERIFICATION.value,
                    'round': round_no,
                    'tests_ok': test_result.ok,
                    'lint_ok': lint_result.ok,
                    'test_stdout': clip_text(test_result.stdout, max_chars=500),
                    'lint_stdout': clip_text(lint_result.stdout, max_chars=500),
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
                    'type': EventType.PRECOMPLETION_CHECKLIST.value,
                    'round': round_no,
                    'passed': checklist.passed,
                    'reason': checklist.reason,
                    'checks': dict(checklist.checks),
                    'evidence_paths': list(checklist.evidence_paths),
                }
            )

            if check_cancel():
                emit({'type': EventType.CANCELED.value, 'round': round_no})
                return RunResult(status='canceled', rounds=round_no - 1, gate_reason='canceled')

            if not checklist.passed:
                _log.warning('precompletion_failed round=%d reason=%s', round_no, checklist.reason)
                emit(
                    {
                        'type': EventType.GATE_FAILED.value,
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
                            'type': EventType.STRATEGY_SHIFTED.value,
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
                if force_single_round:
                    return RunResult(status='failed_gate', rounds=round_no, gate_reason=checklist.reason)
                if not deadline_mode and round_no >= config.max_rounds:
                    return RunResult(status='failed_gate', rounds=round_no, gate_reason=checklist.reason)
                continue

            architecture_audit = run_architecture_audit(
                cwd=Path(config.cwd),
                evolution_level=int(config.evolution_level),
            )
            emit(
                {
                    'type': EventType.ARCHITECTURE_AUDIT.value,
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
                        'type': EventType.GATE_FAILED.value,
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
                            'type': EventType.STRATEGY_SHIFTED.value,
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
                if force_single_round:
                    return RunResult(status='failed_gate', rounds=round_no, gate_reason=architecture_audit.reason)
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
                emit({'type': EventType.GATE_PASSED.value, 'round': round_no, 'reason': gate.reason})
                return RunResult(status='passed', rounds=round_no, gate_reason=gate.reason)

            _log.warning('gate_failed round=%d reason=%s', round_no, gate.reason)
            emit({'type': EventType.GATE_FAILED.value, 'round': round_no, 'reason': gate.reason})
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
                        'type': EventType.STRATEGY_SHIFTED.value,
                        'round': round_no,
                        'hint': strategy_hint,
                        'signals': dict(progress.get('signals') or {}),
                        'shift_count': int(progress.get('shift_count') or 0),
                    }
                )
            terminal_reason = str(progress.get('terminal_reason') or '').strip()
            if terminal_reason:
                return RunResult(status='failed_gate', rounds=round_no, gate_reason=terminal_reason)
            if force_single_round:
                return RunResult(status='failed_gate', rounds=round_no, gate_reason=gate.reason)
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
        workspace = Path(config.cwd)
        if (not workspace.exists()) or (not workspace.is_dir()):
            return {
                'preflight_ok': False,
                'preflight_error': f'langgraph_preflight_error: invalid cwd {workspace}',
                'config': config,
                'on_event': state.get('on_event'),
                'should_cancel': state.get('should_cancel'),
            }
        if not str(config.author.participant_id or '').strip():
            return {
                'preflight_ok': False,
                'preflight_error': 'langgraph_preflight_error: missing author participant_id',
                'config': config,
                'on_event': state.get('on_event'),
                'should_cancel': state.get('should_cancel'),
            }
        if not str(config.test_command or '').strip():
            return {
                'preflight_ok': False,
                'preflight_error': 'langgraph_preflight_error: missing test_command',
                'config': config,
                'on_event': state.get('on_event'),
                'should_cancel': state.get('should_cancel'),
            }
        if not str(config.lint_command or '').strip():
            return {
                'preflight_ok': False,
                'preflight_error': 'langgraph_preflight_error: missing lint_command',
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
        emit({'type': EventType.TASK_STARTED.value, 'task_id': config.task_id})
        set_task_context(task_id=config.task_id)
        _log.info('workflow_started task_id=%s max_rounds=%d backend=langgraph', config.task_id, config.max_rounds)
        return {
            'preflight_ok': True,
            'config': config,
            'on_event': state.get('on_event'),
            'should_cancel': state.get('should_cancel'),
            'deadline': self._parse_deadline(config.evolve_until),
            'round_no': 0,
            'previous_gate_reason': None,
            'strategy_hint': None,
            'loop_tracker': self._new_loop_tracker(),
            'prompt_cache_state': {
                'participant_model_signatures': {},
                'participant_tool_signatures': {},
                'participant_stage_prefix_signatures': {},
            },
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
        current_round = max(0, int(state.get('round_no') or 0))
        previous_gate_reason = str(state.get('previous_gate_reason') or '').strip() or None
        strategy_hint = str(state.get('strategy_hint') or '').strip() or None
        loop_tracker = state.get('loop_tracker')
        if not isinstance(loop_tracker, dict):
            loop_tracker = self._new_loop_tracker()
        prompt_cache_state = state.get('prompt_cache_state')
        if not isinstance(prompt_cache_state, dict):
            prompt_cache_state = {
                'participant_model_signatures': {},
                'participant_tool_signatures': {},
                'participant_stage_prefix_signatures': {},
            }

        def _emit_without_task_started(event: dict) -> None:
            if not isinstance(event, dict):
                return
            payload = dict(event)
            if str(payload.get('type') or '').strip().lower() == EventType.TASK_STARTED.value:
                return
            emit(payload)

        single_round_config = replace(config, max_rounds=1)
        one_round = self._run_classic(
            single_round_config,
            on_event=_emit_without_task_started,
            should_cancel=check_cancel,
            emit_task_started=False,
            round_offset=current_round,
            initial_previous_gate_reason=previous_gate_reason,
            initial_strategy_hint=strategy_hint,
            initial_loop_tracker=loop_tracker,
            initial_prompt_cache_state=prompt_cache_state,
            force_single_round=True,
        )
        result = RunResult(
            status=str(one_round.status or 'failed_system'),
            rounds=int(one_round.rounds or 0),
            gate_reason=str(one_round.gate_reason or ''),
        )
        deadline = state.get('deadline')
        should_finish = self._langgraph_should_finish_round(
            result=result,
            round_no=int(result.rounds or 0),
            max_rounds=int(config.max_rounds),
            deadline=deadline if isinstance(deadline, datetime) else None,
        )
        return {
            **state,
            'round_no': int(result.rounds or 0),
            'previous_gate_reason': (result.gate_reason if str(result.status) == 'failed_gate' else previous_gate_reason),
            'strategy_hint': strategy_hint,
            'loop_tracker': loop_tracker,
            'prompt_cache_state': prompt_cache_state,
            'result': (result if should_finish else None),
            'last_round_result': result,
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
        if deadline is not None:
            return False
        return int(round_no) >= max(1, int(max_rounds))

    @staticmethod
    def _langgraph_round_route(state: dict) -> str:
        result = state.get('result')
        if isinstance(result, RunResult):
            return 'finalize'
        last_round = state.get('last_round_result')
        if not isinstance(last_round, RunResult):
            return 'finalize'
        return 'round'

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
        except ImportError:
            return None
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
            except (AttributeError, TypeError, ValueError):
                _log.debug('span_attribute_ignored key=%s', key)
        class _Wrapper:
            def __enter__(self_inner):
                return ctx
            def __exit__(self_inner, exc_type, exc, tb):
                return span.__exit__(exc_type, exc, tb)
        return _Wrapper()

    @classmethod
    def _render_prompt_template(cls, template_name: str, **fields: object) -> str:
        return render_prompt_template(
            template_name=template_name,
            template_dir=_PROMPT_TEMPLATE_DIR,
            cache=cls._prompt_template_cache,
            fields=fields,
        )

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

    def _resolve_phase_timeout_seconds(self, value: dict[str, int] | None) -> dict[str, int]:
        configured = normalize_phase_timeout_seconds_task(value, strict=False)
        discussion_default = max(10, int(self.participant_timeout_seconds))
        defaults = {
            'proposal': discussion_default,
            'discussion': discussion_default,
            'implementation': discussion_default,
            'review': max(10, int(self._review_timeout_seconds(discussion_default))),
            'command': max(10, int(self.command_timeout_seconds)),
        }
        out: dict[str, int] = {}
        for phase, fallback in defaults.items():
            try:
                parsed = int(configured.get(phase, fallback))
            except (TypeError, ValueError):
                parsed = int(fallback)
            out[phase] = max(10, parsed)
        return out

    @staticmethod
    def _memory_context_for_stage(
        *,
        config: RunConfig,
        memory_mode: str,
        stage: str,
    ) -> str | None:
        mode = normalize_memory_mode_task(memory_mode, strict=False)
        if mode == 'off':
            return None
        mapping = config.memory_context if isinstance(config.memory_context, dict) else {}
        stage_key = str(stage or '').strip().lower()
        direct = str(mapping.get(stage_key) or '').strip()
        if direct:
            return direct
        fallback = str(mapping.get('all') or '').strip()
        return fallback or None

    def _participant_runtime_profile(
        self,
        *,
        participant: Participant,
        config: RunConfig,
        provider_models: dict[str, str],
        provider_model_params: dict[str, str],
        participant_models: dict[str, str],
        participant_model_params: dict[str, str],
        claude_team_agents_overrides: dict[str, bool],
        codex_multi_agents_overrides: dict[str, bool],
    ) -> dict[str, object]:
        return {
            'model': runtime_resolve_model_for_participant(
                participant=participant,
                provider_models=provider_models,
                participant_models=participant_models,
            ),
            'model_params': runtime_resolve_model_params_for_participant(
                participant=participant,
                provider_model_params=provider_model_params,
                participant_model_params=participant_model_params,
            ),
            'claude_team_agents': runtime_resolve_agent_toggle_for_participant(
                participant=participant,
                global_enabled=bool(config.claude_team_agents),
                overrides=claude_team_agents_overrides,
            ),
            'codex_multi_agents': runtime_resolve_agent_toggle_for_participant(
                participant=participant,
                global_enabled=bool(config.codex_multi_agents),
                overrides=codex_multi_agents_overrides,
            ),
        }

    @staticmethod
    def _record_prompt_cache_probe(
        *,
        cache_state: dict,
        round_no: int,
        stage: str,
        participant: Participant,
        model: str | None,
        model_params: str | None,
        claude_team_agents: bool,
        codex_multi_agents: bool,
        prompt: str,
    ) -> tuple[dict, list[dict]]:
        participant_model_signatures = cache_state.setdefault('participant_model_signatures', {})
        participant_tool_signatures = cache_state.setdefault('participant_tool_signatures', {})
        participant_stage_prefix_signatures = cache_state.setdefault('participant_stage_prefix_signatures', {})
        if not isinstance(participant_model_signatures, dict):
            participant_model_signatures = {}
            cache_state['participant_model_signatures'] = participant_model_signatures
        if not isinstance(participant_tool_signatures, dict):
            participant_tool_signatures = {}
            cache_state['participant_tool_signatures'] = participant_tool_signatures
        if not isinstance(participant_stage_prefix_signatures, dict):
            participant_stage_prefix_signatures = {}
            cache_state['participant_stage_prefix_signatures'] = participant_stage_prefix_signatures

        participant_key = str(participant.participant_id or '').strip().lower() or str(participant.alias or '').strip().lower()
        stage_key = f'{participant_key}|{str(stage or "").strip().lower() or "unknown"}'
        model_label = str(model or '').strip() or '__provider_default__'
        model_params_label = str(model_params or '').strip()
        prompt_text = str(prompt or '')
        marker_idx = prompt_text.find('\nContext:')
        if marker_idx < 0:
            marker_idx = prompt_text.find('Context:')
        static_prefix_text = prompt_text[:marker_idx] if marker_idx > 0 else prompt_text[:1800]
        prefix_sig = text_signature(static_prefix_text, max_chars=1800)
        prompt_sig = text_signature(prompt_text, max_chars=4000)
        model_sig = text_signature(
            f'provider={participant.provider}|model={model_label}|params={model_params_label}',
            max_chars=512,
        )
        toolset_sig = text_signature(
            f'claude_team_agents={1 if claude_team_agents else 0}|codex_multi_agents={1 if codex_multi_agents else 0}',
            max_chars=128,
        )

        previous_model_sig = str(participant_model_signatures.get(participant_key) or '')
        previous_toolset_sig = str(participant_tool_signatures.get(participant_key) or '')
        previous_prefix_sig = str(participant_stage_prefix_signatures.get(stage_key) or '')

        model_reuse_eligible = bool(previous_model_sig)
        toolset_reuse_eligible = bool(previous_toolset_sig)
        prefix_reuse_eligible = bool(previous_prefix_sig)

        model_reused = bool(model_reuse_eligible and previous_model_sig == model_sig)
        toolset_reused = bool(toolset_reuse_eligible and previous_toolset_sig == toolset_sig)
        prefix_reused = bool(prefix_reuse_eligible and previous_prefix_sig == prefix_sig)

        probe_event = {
            'type': EventType.PROMPT_CACHE_PROBE.value,
            'round': int(round_no),
            'stage': str(stage or '').strip().lower() or 'unknown',
            'participant': participant.participant_id,
            'provider': participant.provider,
            'model': model_label,
            'model_params': model_params_label,
            'prompt_chars': len(prompt_text),
            'prefix_signature': prefix_sig,
            'prompt_signature': prompt_sig,
            'toolset_signature': toolset_sig,
            'baseline': not prefix_reuse_eligible,
            'prefix_reuse_eligible': prefix_reuse_eligible,
            'prefix_reused': prefix_reused,
            'model_reuse_eligible': model_reuse_eligible,
            'model_reused': model_reused,
            'toolset_reuse_eligible': toolset_reuse_eligible,
            'toolset_reused': toolset_reused,
        }

        break_events: list[dict] = []
        if model_reuse_eligible and not model_reused:
            break_events.append(
                {
                    'type': EventType.PROMPT_CACHE_BREAK.value,
                    'round': int(round_no),
                    'stage': str(stage or '').strip().lower() or 'unknown',
                    'participant': participant.participant_id,
                    'provider': participant.provider,
                    'reason': 'model_changed',
                    'previous_signature': previous_model_sig,
                    'current_signature': model_sig,
                }
            )
        if toolset_reuse_eligible and not toolset_reused:
            break_events.append(
                {
                    'type': EventType.PROMPT_CACHE_BREAK.value,
                    'round': int(round_no),
                    'stage': str(stage or '').strip().lower() or 'unknown',
                    'participant': participant.participant_id,
                    'provider': participant.provider,
                    'reason': 'toolset_changed',
                    'previous_signature': previous_toolset_sig,
                    'current_signature': toolset_sig,
                }
            )
        if prefix_reuse_eligible and not prefix_reused:
            break_events.append(
                {
                    'type': EventType.PROMPT_CACHE_BREAK.value,
                    'round': int(round_no),
                    'stage': str(stage or '').strip().lower() or 'unknown',
                    'participant': participant.participant_id,
                    'provider': participant.provider,
                    'reason': 'prefix_changed',
                    'previous_signature': previous_prefix_sig,
                    'current_signature': prefix_sig,
                }
            )

        participant_model_signatures[participant_key] = model_sig
        participant_tool_signatures[participant_key] = toolset_sig
        participant_stage_prefix_signatures[stage_key] = prefix_sig
        return probe_event, break_events

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
        impl_sig = text_signature(implementation_output)
        review_sig = text_signature('\n'.join(str(v or '') for v in review_outputs))
        verify_sig = text_signature(f'tests_ok={bool(tests_ok)} lint_ok={bool(lint_ok)} reason={reason}')

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
            except (OSError, RuntimeError, ValueError):
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
                    'type': EventType.PARTICIPANT_STREAM.value,
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
        return clip_text(merged, max_chars=5000)

    @staticmethod
    def _discussion_prompt(
        config: RunConfig,
        round_no: int,
        previous_gate_reason: str | None = None,
        *,
        environment_context: str | None = None,
        strategy_hint: str | None = None,
        memory_context: str | None = None,
    ) -> str:
        level = max(0, min(3, int(config.evolution_level)))
        repair_mode = runtime_normalize_repair_mode(config.repair_mode)
        language_instruction = WorkflowEngine._conversation_language_instruction(config.conversation_language)
        repair_guidance = WorkflowEngine._repair_mode_guidance(repair_mode)
        plain_mode_instruction = WorkflowEngine._plain_mode_instruction(bool(config.plain_mode))
        mode_guidance = ''
        if level == 1:
            mode_guidance = (
                "Mode guidance: prioritize bug/risk fixes first, and optionally propose one small safe evolution.\n"
                "If proposing, include one line: EVOLUTION_PROPOSAL: <small enhancement>."
            )
        elif level == 2:
            mode_guidance = (
                "Mode guidance: prioritize bug/risk fixes first, then proactively propose 1-2 evolution directions.\n"
                "If proposing, include lines: EVOLUTION_PROPOSAL_1: ... and optional EVOLUTION_PROPOSAL_2: ...\n"
                "Ensure rollout stays incremental and testable."
            )
        elif level >= 3:
            mode_guidance = (
                "Mode guidance: frontier evolve mode. Prioritize blocker/risk fixes, then aggressively propose 2-4\n"
                "high-impact directions across feature ideas, framework/runtime upgrades, UI/UX improvements, and\n"
                "developer-experience upgrades.\n"
                "Use lines: EVOLUTION_PROPOSAL_1..N and include impact/risk/effort/verification path for each.\n"
                "Prefer incremental slices that keep tests/lint green."
            )
        previous_gate_context = ''
        if round_no > 1 and previous_gate_reason:
            previous_gate_context = (
                f"Previous gate failure reason: {previous_gate_reason}\n"
                "Address this explicitly."
            )
        base = WorkflowEngine._render_prompt_template(
            'discussion_prompt.txt',
            task_title=config.title,
            round_no=round_no,
            evolution_level=level,
            repair_mode=repair_mode,
            description=config.description,
            language_instruction=language_instruction,
            repair_guidance=repair_guidance,
            plain_mode_instruction=plain_mode_instruction,
            mode_guidance=mode_guidance,
            previous_gate_context=previous_gate_context,
        )
        return inject_prompt_extras(
            base=base,
            environment_context=environment_context,
            strategy_hint=strategy_hint,
            memory_context=memory_context,
        )

    @staticmethod
    def _debate_seed_context(
        config: RunConfig,
        round_no: int,
        previous_gate_reason: str | None = None,
        *,
        environment_context: str | None = None,
        strategy_hint: str | None = None,
        memory_context: str | None = None,
    ) -> str:
        level = max(0, min(3, int(config.evolution_level)))
        repair_mode = runtime_normalize_repair_mode(config.repair_mode)
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
        return inject_prompt_extras(
            base=base,
            environment_context=environment_context,
            strategy_hint=strategy_hint,
            memory_context=memory_context,
        )

    @staticmethod
    def _discussion_after_reviewer_prompt(
        config: RunConfig,
        round_no: int,
        reviewer_context: str,
        *,
        environment_context: str | None = None,
        strategy_hint: str | None = None,
        memory_context: str | None = None,
    ) -> str:
        clipped = clip_text(reviewer_context, max_chars=3200)
        level = max(0, min(3, int(config.evolution_level)))
        repair_mode = runtime_normalize_repair_mode(config.repair_mode)
        language_instruction = WorkflowEngine._conversation_language_instruction(config.conversation_language)
        repair_guidance = WorkflowEngine._repair_mode_guidance(repair_mode)
        plain_mode_instruction = WorkflowEngine._plain_mode_instruction(bool(config.plain_mode))
        base = WorkflowEngine._render_prompt_template(
            'discussion_after_reviewer_prompt.txt',
            task_title=config.title,
            round_no=round_no,
            evolution_level=level,
            repair_mode=repair_mode,
            language_instruction=language_instruction,
            repair_guidance=repair_guidance,
            plain_mode_instruction=plain_mode_instruction,
            reviewer_context=clipped,
        )
        return inject_prompt_extras(
            base=base,
            environment_context=environment_context,
            strategy_hint=strategy_hint,
            memory_context=memory_context,
        )

    @staticmethod
    def _implementation_prompt(
        config: RunConfig,
        round_no: int,
        discussion_output: str,
        *,
        environment_context: str | None = None,
        strategy_hint: str | None = None,
        memory_context: str | None = None,
    ) -> str:
        clipped = clip_text(discussion_output, max_chars=3000)
        level = max(0, min(3, int(config.evolution_level)))
        repair_mode = runtime_normalize_repair_mode(config.repair_mode)
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
        elif level >= 3:
            mode_guidance = (
                "Resolve blockers first, then implement one high-impact evolution slice from discussion "
                "(feature/framework/UI/idea). Keep migration risk explicit and maintain green tests/lint."
            )
        base = WorkflowEngine._render_prompt_template(
            'implementation_prompt.txt',
            task_title=config.title,
            round_no=round_no,
            evolution_level=level,
            repair_mode=repair_mode,
            language_instruction=language_instruction,
            repair_guidance=repair_guidance,
            plain_mode_instruction=plain_mode_instruction,
            plan=clipped,
            mode_guidance=mode_guidance,
        )
        return inject_prompt_extras(
            base=base,
            environment_context=environment_context,
            strategy_hint=strategy_hint,
            memory_context=memory_context,
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
        memory_context: str | None = None,
    ) -> str:
        clipped = clip_text(discussion_context, max_chars=3200)
        language_instruction = WorkflowEngine._conversation_language_instruction(config.conversation_language)
        plain_mode_instruction = WorkflowEngine._plain_mode_instruction(bool(config.plain_mode))
        audit_mode = WorkflowEngine._is_audit_discovery_task(config)
        depth_guidance = (
            "Task mode is audit/discovery: perform repository-wide checks as needed, then provide concrete findings with file paths."
            if audit_mode
            else "Review current context and provide concrete risk findings."
        )
        checklist_guidance = WorkflowEngine._review_checklist_guidance(config.evolution_level)
        base = WorkflowEngine._render_prompt_template(
            'debate_review_prompt.txt',
            task_title=config.title,
            round_no=round_no,
            reviewer_id=reviewer_id,
            language_instruction=language_instruction,
            plain_mode_instruction=plain_mode_instruction,
            depth_guidance=depth_guidance,
            checklist_guidance=checklist_guidance,
            discussion_context=clipped,
        )
        return inject_prompt_extras(
            base=base,
            environment_context=environment_context,
            strategy_hint=strategy_hint,
            memory_context=memory_context,
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
        memory_context: str | None = None,
    ) -> str:
        clipped_context = clip_text(discussion_context, max_chars=2600)
        clipped_feedback = clip_text(reviewer_feedback, max_chars=1400)
        language_instruction = WorkflowEngine._conversation_language_instruction(config.conversation_language)
        plain_mode_instruction = WorkflowEngine._plain_mode_instruction(bool(config.plain_mode))
        base = WorkflowEngine._render_prompt_template(
            'debate_reply_prompt.txt',
            task_title=config.title,
            round_no=round_no,
            language_instruction=language_instruction,
            plain_mode_instruction=plain_mode_instruction,
            reviewer_id=reviewer_id,
            discussion_context=clipped_context,
            reviewer_feedback=clipped_feedback,
        )
        return inject_prompt_extras(
            base=base,
            environment_context=environment_context,
            strategy_hint=strategy_hint,
            memory_context=memory_context,
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
        except (TypeError, ValueError):
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
        memory_context: str | None = None,
    ) -> str:
        clipped = clip_text(implementation_output, max_chars=3000)
        level = max(0, min(3, int(config.evolution_level)))
        repair_mode = runtime_normalize_repair_mode(config.repair_mode)
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
        base = WorkflowEngine._render_prompt_template(
            'review_prompt.txt',
            task_title=config.title,
            round_no=round_no,
            evolution_level=level,
            repair_mode=repair_mode,
            language_instruction=language_instruction,
            repair_guidance=repair_guidance,
            plain_mode_instruction=plain_mode_instruction,
            depth_guidance=depth_guidance,
            checklist_guidance=checklist_guidance,
            mode_guidance=mode_guidance,
            control_schema_instruction=control_schema_instruction,
            plain_review_format=plain_review_format,
            implementation_summary=clipped,
        )
        return inject_prompt_extras(
            base=base,
            environment_context=environment_context,
            strategy_hint=strategy_hint,
            memory_context=memory_context,
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
    def _repair_mode_guidance(mode: str) -> str:
        normalized = runtime_normalize_repair_mode(mode)
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
        normalized = max(0, min(3, int(level)))
        if normalized < 1:
            return "Checklist: focus on correctness, security, and regression evidence."
        if normalized >= 3:
            return (
                "Required checklist (cover every item with evidence path or explicit 'n/a'): "
                "security; concurrency/state transitions; DB lock/retry handling; architecture size/responsibility "
                "(oversized files/modules); frontend maintainability (single-file UI bloat); cross-platform runtime/scripts; "
                "feature opportunity map (2+ concrete ideas); framework/runtime upgrade candidates; UI/UX upgrade ideas; "
                "each opportunity must include impact/risk/effort and validation path."
            )
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
