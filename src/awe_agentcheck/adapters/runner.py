from __future__ import annotations

from queue import Empty, Queue
import os
from pathlib import Path
import random
import shutil
import subprocess
import time
from threading import Thread
from typing import Callable

from awe_agentcheck.adapters.base import (
    AdapterResult,
    DEFAULT_PROVIDER_REGISTRY,
    has_agents_flag,
    has_codex_multi_agent_config_token,
    has_codex_multi_agent_flag,
    has_model_flag,
    has_prompt_flag,
    normalize_gemini_approval_flags,
    parse_next_action,
    parse_verdict,
    split_extra_args,
)
from awe_agentcheck.adapters.codex import normalize_codex_exec_output
from awe_agentcheck.adapters.factory import ProviderFactory
from awe_agentcheck.participants import Participant

_LIMIT_PATTERNS = (
    'hit your limit',
    'usage limit',
    'rate limit',
    'ratelimitexceeded',
    'resource_exhausted',
    'model_capacity_exhausted',
    'no capacity available',
    'quota exceeded',
    'insufficient_quota',
)
_MIN_ATTEMPT_TIMEOUT_SECONDS = 0.05


class ParticipantRunner:
    def __init__(
        self,
        *,
        command_overrides: dict[str, str] | None = None,
        dry_run: bool = False,
        timeout_retries: int = 1,
    ):
        self.provider_registry = {
            provider: {
                'command': str(spec.get('command') or '').strip(),
                'model_flag': str(spec.get('model_flag') or '').strip(),
                'capabilities': dict(spec.get('capabilities') or {}),
            }
            for provider, spec in DEFAULT_PROVIDER_REGISTRY.items()
        }
        if command_overrides:
            for raw_provider, raw_command in command_overrides.items():
                provider = str(raw_provider or '').strip().lower()
                command = str(raw_command or '').strip()
                if not provider or not command:
                    continue
                existing = dict(self.provider_registry.get(provider) or {})
                if not existing:
                    existing = {
                        'command': '',
                        'model_flag': '-m',
                        'capabilities': {
                            'claude_team_agents': False,
                            'codex_multi_agents': False,
                        },
                    }
                existing['command'] = command
                self.provider_registry[provider] = existing
        self.commands = {
            provider: str(spec.get('command') or '').strip()
            for provider, spec in self.provider_registry.items()
            if str(spec.get('command') or '').strip()
        }
        self.provider_factory = ProviderFactory()
        self.dry_run = dry_run
        self.timeout_retries = max(0, int(timeout_retries))

    def run(
        self,
        *,
        participant: Participant,
        prompt: str,
        cwd: Path,
        timeout_seconds: int = 900,
        model: str | None = None,
        model_params: str | None = None,
        claude_team_agents: bool = False,
        codex_multi_agents: bool = False,
        on_stream: Callable[[str, str], None] | None = None,
    ) -> AdapterResult:
        if self.dry_run:
            simulated = (
                f'[dry-run participant={participant.participant_id}]\\n'
                '{"verdict":"NO_BLOCKER","next_action":"pass","issue":"n/a","impact":"n/a","next":"n/a"}\\n'
                'Evidence:\\n'
                '- src/awe_agentcheck/service.py\\n'
                '- src/awe_agentcheck/adapters/base.py\\n'
                '- tests/unit/test_service.py\\n'
                'Verification:\\n'
                '- py -m pytest -q tests/unit/test_service.py\\n'
                '- py -m ruff check src/awe_agentcheck'
            )
            return AdapterResult(
                output=simulated,
                verdict='no_blocker',
                next_action='pass',
                returncode=0,
                duration_seconds=0.01,
            )

        provider = str(participant.provider or '').strip().lower()
        command = self.commands.get(provider)
        if not command:
            return self._runtime_error_result(
                reason=f'command_not_configured provider={provider}',
                duration_seconds=0.0,
            )

        provider_spec = dict(self.provider_registry.get(provider) or {})
        adapter = self.provider_factory.create(provider=provider, provider_spec=provider_spec)

        argv = adapter.build_argv(
            command=command,
            model=model,
            model_params=model_params,
            claude_team_agents=claude_team_agents,
            codex_multi_agents=codex_multi_agents,
        )
        argv = self._resolve_executable(argv)
        effective_command = self._format_command(argv)
        attempts = self.timeout_retries + 1
        current_prompt = prompt
        started = time.monotonic()
        deadline = started + max(0.05, float(timeout_seconds))
        completed = None
        attempts_made = 0
        last_timeout: subprocess.TimeoutExpired | None = None

        for attempt in range(1, attempts + 1):
            remaining_budget = self._remaining_timeout_budget_seconds(deadline=deadline)
            if remaining_budget <= 0:
                break
            attempts_left = attempts - attempt + 1
            attempt_timeout = self._compute_attempt_timeout_seconds(
                remaining_budget=remaining_budget,
                attempts_left=attempts_left,
            )
            if attempt_timeout <= 0:
                break

            attempts_made += 1
            runtime_argv, runtime_input = adapter.prepare_runtime_invocation(argv=argv, prompt=current_prompt)
            try:
                if on_stream is None:
                    completed = subprocess.run(
                        runtime_argv,
                        input=runtime_input,
                        capture_output=True,
                        text=True,
                        encoding='utf-8',
                        errors='replace',
                        cwd=str(cwd),
                        timeout=attempt_timeout,
                        env=self._build_subprocess_env(cwd),
                    )
                else:
                    completed = self._run_streaming(
                        argv=runtime_argv,
                        runtime_input=runtime_input,
                        cwd=cwd,
                        timeout_seconds=attempt_timeout,
                        on_stream=on_stream,
                        env=self._build_subprocess_env(cwd),
                    )
                break
            except FileNotFoundError:
                return self._runtime_error_result(
                    reason=f'command_not_found provider={provider} command={effective_command}',
                    duration_seconds=(time.monotonic() - started),
                )
            except subprocess.TimeoutExpired as exc:
                last_timeout = exc
                if attempt >= attempts:
                    break
                current_prompt = self._clip_prompt_for_retry(current_prompt)
                if not self._sleep_before_timeout_retry(attempt=attempt, deadline=deadline):
                    break

        if completed is None:
            reason = (
                f'command_timeout provider={provider} command={effective_command} '
                f'timeout_seconds={timeout_seconds} attempts={attempts} attempts_made={attempts_made}'
            )
            _ = last_timeout
            return self._runtime_error_result(
                reason=reason,
                duration_seconds=(time.monotonic() - started),
            )

        elapsed = time.monotonic() - started
        output = (completed.stdout or '').strip()
        if completed.returncode != 0:
            stderr = (completed.stderr or '').strip()
            output = '\n'.join([part for part in [output, stderr] if part]).strip()

        if self._is_provider_limit_output(output):
            return self._runtime_error_result(
                reason=f'provider_limit provider={provider} command={effective_command}',
                duration_seconds=elapsed,
            )
        if completed.returncode != 0:
            return self._runtime_error_result(
                reason=(
                    f'command_failed provider={provider} command={effective_command} '
                    f'returncode={completed.returncode}'
                ),
                duration_seconds=elapsed,
            )

        verdict = parse_verdict(output)
        next_action = parse_next_action(output)
        normalized_output = adapter.normalize_output(output)

        return AdapterResult(
            output=normalized_output,
            verdict=verdict,
            next_action=next_action,
            returncode=completed.returncode,
            duration_seconds=elapsed,
        )

    @staticmethod
    def _runtime_error_result(*, reason: str, duration_seconds: float) -> AdapterResult:
        text = str(reason or '').strip() or 'adapter_runtime_error'
        return AdapterResult(
            output=text,
            verdict='unknown',
            next_action='stop',
            returncode=2,
            duration_seconds=max(0.0, float(duration_seconds)),
        )

    @staticmethod
    def _remaining_timeout_budget_seconds(*, deadline: float) -> float:
        return max(0.0, float(deadline) - time.monotonic())

    @staticmethod
    def _compute_attempt_timeout_seconds(*, remaining_budget: float, attempts_left: int) -> float:
        budget = max(0.0, float(remaining_budget))
        left = max(1, int(attempts_left))
        if budget <= 0:
            return 0.0
        requested = max(min(_MIN_ATTEMPT_TIMEOUT_SECONDS, budget), budget / left)
        return min(budget, requested)

    @staticmethod
    def _timeout_retry_backoff_seconds(*, attempt: int) -> float:
        bounded_attempt = max(1, int(attempt))
        base_delay = min(0.5, 0.15 * bounded_attempt)
        jitter = random.uniform(0.0, 0.1)
        return min(0.75, base_delay + jitter)

    @staticmethod
    def _sleep_before_timeout_retry(*, attempt: int, deadline: float) -> bool:
        remaining = ParticipantRunner._remaining_timeout_budget_seconds(deadline=deadline)
        if remaining <= 0:
            return False
        min_next_attempt = min(_MIN_ATTEMPT_TIMEOUT_SECONDS, remaining)
        pause_cap = max(0.0, remaining - min_next_attempt)
        if pause_cap > 0:
            pause = min(pause_cap, ParticipantRunner._timeout_retry_backoff_seconds(attempt=attempt))
            if pause > 0:
                time.sleep(pause)
        return ParticipantRunner._remaining_timeout_budget_seconds(deadline=deadline) > 0

    @staticmethod
    def _clip_prompt_for_retry(prompt: str) -> str:
        text = prompt or ''
        if len(text) <= 1200:
            return text
        kept = text[:1200]
        dropped = len(text) - len(kept)
        return kept + f'\n\n[retry prompt clipped: {dropped} chars removed]'

    @staticmethod
    def _is_provider_limit_output(output: str) -> bool:
        text = (output or '').strip().lower()
        if not text:
            return False
        return any(pattern in text for pattern in _LIMIT_PATTERNS)

    @staticmethod
    def _normalize_output_for_provider(*, provider: str, output: str) -> str:
        adapter = ProviderFactory.create(provider=provider, provider_spec=None)
        return adapter.normalize_output(output)

    @staticmethod
    def _normalize_codex_exec_output(output: str) -> str:
        return normalize_codex_exec_output(output)

    @staticmethod
    def _build_argv(
        *,
        command: str,
        provider: str,
        provider_spec: dict[str, object] | None,
        model: str | None,
        model_params: str | None,
        claude_team_agents: bool,
        codex_multi_agents: bool,
    ) -> list[str]:
        adapter = ProviderFactory.create(provider=provider, provider_spec=provider_spec)
        return adapter.build_argv(
            command=command,
            model=model,
            model_params=model_params,
            claude_team_agents=claude_team_agents,
            codex_multi_agents=codex_multi_agents,
        )

    @staticmethod
    def _prepare_runtime_invocation(*, argv: list[str], provider: str, prompt: str) -> tuple[list[str], str]:
        adapter = ProviderFactory.create(provider=provider, provider_spec=None)
        return adapter.prepare_runtime_invocation(argv=argv, prompt=prompt)

    @staticmethod
    def _split_extra_args(value: str | None) -> list[str]:
        return split_extra_args(value)

    @staticmethod
    def _has_model_flag(argv: list[str]) -> bool:
        return has_model_flag(argv)

    @staticmethod
    def _has_agents_flag(argv: list[str]) -> bool:
        return has_agents_flag(argv)

    @staticmethod
    def _has_codex_multi_agent_flag(argv: list[str]) -> bool:
        return has_codex_multi_agent_flag(argv)

    @staticmethod
    def _has_codex_multi_agent_config_token(value: str) -> bool:
        return has_codex_multi_agent_config_token(value)

    @staticmethod
    def _has_prompt_flag(argv: list[str]) -> bool:
        return has_prompt_flag(argv)

    @staticmethod
    def _normalize_gemini_approval_flags(argv: list[str]) -> list[str]:
        return normalize_gemini_approval_flags(argv)

    @staticmethod
    def _resolve_executable(argv: list[str]) -> list[str]:
        if not argv:
            return argv
        first = str(argv[0]).strip()
        if not first:
            return argv
        resolved = shutil.which(first)
        if not resolved:
            return argv
        patched = list(argv)
        patched[0] = resolved
        return patched

    @staticmethod
    def _format_command(argv: list[str]) -> str:
        return ' '.join(str(value) for value in argv)

    @staticmethod
    def _run_streaming(
        *,
        argv: list[str],
        runtime_input: str,
        cwd: Path,
        timeout_seconds: float,
        on_stream: Callable[[str, str], None],
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess:
        stdin_pipe = subprocess.PIPE if runtime_input else subprocess.DEVNULL
        process = subprocess.Popen(
            argv,
            stdin=stdin_pipe,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding='utf-8',
            errors='replace',
            cwd=str(cwd),
            bufsize=1,
            env=env,
        )

        if runtime_input and process.stdin is not None:
            try:
                process.stdin.write(runtime_input)
            finally:
                process.stdin.close()

        queue: Queue[tuple[str, str]] = Queue()
        stdout_chunks: list[str] = []
        stderr_chunks: list[str] = []

        def _pump(pipe, stream_name: str, sink: list[str]) -> None:
            if pipe is None:
                return
            try:
                while True:
                    chunk = pipe.readline()
                    if chunk == '':
                        break
                    sink.append(chunk)
                    queue.put((stream_name, chunk))
            finally:
                try:
                    pipe.close()
                except Exception:
                    pass

        workers = [
            Thread(target=_pump, args=(process.stdout, 'stdout', stdout_chunks), daemon=True),
            Thread(target=_pump, args=(process.stderr, 'stderr', stderr_chunks), daemon=True),
        ]
        for worker in workers:
            worker.start()

        deadline = time.monotonic() + max(0.05, float(timeout_seconds))
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                process.kill()
                try:
                    process.wait(timeout=2)
                except Exception:
                    pass
                raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout_seconds)

            timeout = min(0.1, max(0.01, remaining))
            try:
                stream_name, chunk = queue.get(timeout=timeout)
                on_stream(stream_name, chunk)
            except Empty:
                pass

            finished = process.poll() is not None
            drained = queue.empty() and all(not worker.is_alive() for worker in workers)
            if finished and drained:
                break

        for worker in workers:
            worker.join(timeout=0.2)

        return subprocess.CompletedProcess(
            args=argv,
            returncode=int(process.returncode or 0),
            stdout=''.join(stdout_chunks),
            stderr=''.join(stderr_chunks),
        )

    @staticmethod
    def _build_subprocess_env(cwd: Path) -> dict[str, str]:
        env = dict(os.environ)
        workspace_src = (Path(cwd) / 'src').resolve(strict=False)
        if not workspace_src.is_dir():
            return env

        current_raw = str(env.get('PYTHONPATH', '') or '').strip()
        current_items = [item for item in current_raw.split(os.pathsep) if str(item).strip()]

        ordered: list[str] = [str(workspace_src)]
        workspace_norm = str(workspace_src).replace('\\', '/').lower()
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


__all__ = ['ParticipantRunner']
