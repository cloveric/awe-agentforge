from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Queue
import random
import re
import shlex
import shutil
import subprocess
import time
from threading import Thread
from pathlib import Path
from typing import Callable

from awe_agentcheck.participants import Participant

_VERDICT_RE = re.compile(r'^\s*VERDICT\s*:\s*(NO_BLOCKER|BLOCKER|UNKNOWN)\s*$', re.IGNORECASE)
_NEXT_RE = re.compile(r'^\s*NEXT_ACTION\s*:\s*(retry|pass|stop)\s*$', re.IGNORECASE)
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
_MODEL_FLAG_BY_PROVIDER = {
    'claude': '--model',
    'codex': '-m',
    'gemini': '-m',
}
_MIN_ATTEMPT_TIMEOUT_SECONDS = 0.05


@dataclass(frozen=True)
class AdapterResult:
    output: str
    verdict: str
    next_action: str | None
    returncode: int
    duration_seconds: float


DEFAULT_COMMANDS = {
    'claude': 'claude -p --dangerously-skip-permissions --strict-mcp-config --effort low --model claude-opus-4-6',
    'codex': 'codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=xhigh',
    'gemini': 'gemini --yolo',
}


def parse_verdict(output: str) -> str:
    for line in (output or '').splitlines():
        m = _VERDICT_RE.match(line)
        if m:
            return m.group(1).lower().replace('no_blocker', 'no_blocker')
    return 'unknown'


def parse_next_action(output: str) -> str | None:
    for line in (output or '').splitlines():
        m = _NEXT_RE.match(line)
        if m:
            return m.group(1).lower()
    return None


class ParticipantRunner:
    def __init__(
        self,
        *,
        command_overrides: dict[str, str] | None = None,
        dry_run: bool = False,
        timeout_retries: int = 1,
    ):
        self.commands = dict(DEFAULT_COMMANDS)
        if command_overrides:
            self.commands.update(command_overrides)
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
        on_stream: Callable[[str, str], None] | None = None,
    ) -> AdapterResult:
        if self.dry_run:
            simulated = (
                f'[dry-run participant={participant.participant_id}]\\n'
                'VERDICT: NO_BLOCKER\\n'
                'NEXT_ACTION: pass\\n'
                'Simulated output for orchestration smoke testing.'
            )
            return AdapterResult(
                output=simulated,
                verdict='no_blocker',
                next_action='pass',
                returncode=0,
                duration_seconds=0.01,
            )

        command = self.commands.get(participant.provider)
        if not command:
            raise ValueError(f'no command configured for provider: {participant.provider}')

        argv = self._build_argv(
            command=command,
            provider=participant.provider,
            model=model,
            model_params=model_params,
            claude_team_agents=claude_team_agents,
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
            runtime_argv, runtime_input = self._prepare_runtime_invocation(
                argv=argv,
                provider=participant.provider,
                prompt=current_prompt,
            )
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
                    )
                else:
                    completed = self._run_streaming(
                        argv=runtime_argv,
                        runtime_input=runtime_input,
                        cwd=cwd,
                        timeout_seconds=attempt_timeout,
                        on_stream=on_stream,
                    )
                break
            except FileNotFoundError as exc:
                raise RuntimeError(
                    f'command_not_found provider={participant.provider} command={effective_command}'
                ) from exc
            except subprocess.TimeoutExpired as exc:
                last_timeout = exc
                if attempt >= attempts:
                    break
                current_prompt = self._clip_prompt_for_retry(current_prompt)
                if not self._sleep_before_timeout_retry(attempt=attempt, deadline=deadline):
                    break
        if completed is None:
            reason = (
                f'command_timeout provider={participant.provider} command={effective_command} '
                f'timeout_seconds={timeout_seconds} attempts={attempts} attempts_made={attempts_made}'
            )
            if last_timeout is not None:
                raise RuntimeError(reason) from last_timeout
            raise RuntimeError(reason)
        assert completed is not None
        elapsed = time.monotonic() - started

        output = (completed.stdout or '').strip()
        if completed.returncode != 0:
            stderr = (completed.stderr or '').strip()
            output = '\n'.join([p for p in [output, stderr] if p]).strip()

        if self._is_provider_limit_output(output):
            raise RuntimeError(f'provider_limit provider={participant.provider} command={effective_command}')

        verdict = parse_verdict(output)
        next_action = parse_next_action(output)
        normalized_output = self._normalize_output_for_provider(provider=participant.provider, output=output)

        return AdapterResult(
            output=normalized_output,
            verdict=verdict,
            next_action=next_action,
            returncode=completed.returncode,
            duration_seconds=elapsed,
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
        # Keep a small slice for the next retry attempt instead of spending
        # the full remaining budget in backoff.
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
        text = str(output or '').strip()
        provider_text = str(provider or '').strip().lower()
        if provider_text != 'codex':
            return text
        return ParticipantRunner._normalize_codex_exec_output(text)

    @staticmethod
    def _normalize_codex_exec_output(output: str) -> str:
        text = str(output or '').replace('\r\n', '\n').strip()
        if not text:
            return text

        # Prefer assistant final message section when Codex emits full transcript.
        marker = '\ncodex\n'
        if marker in text:
            tail = text.rsplit(marker, 1)[-1]
            if '\ntokens used' in tail:
                tail = tail.split('\ntokens used', 1)[0]
            cleaned = tail.strip()
            if cleaned:
                return cleaned

        # Fallback: keep content before CLI metadata banner.
        banner = '\nOpenAI Codex v'
        if banner in text:
            head = text.split(banner, 1)[0].strip()
            if head:
                return head

        return text

    @staticmethod
    def _build_argv(
        *,
        command: str,
        provider: str,
        model: str | None,
        model_params: str | None,
        claude_team_agents: bool,
    ) -> list[str]:
        argv = shlex.split(command, posix=False)

        model_text = str(model or '').strip()
        if model_text and not ParticipantRunner._has_model_flag(argv):
            flag = _MODEL_FLAG_BY_PROVIDER.get(str(provider or '').strip().lower())
            if flag:
                argv.extend([flag, model_text])

        extra = ParticipantRunner._split_extra_args(model_params)
        if extra:
            argv.extend(extra)

        provider_text = str(provider or '').strip().lower()
        if provider_text == 'gemini':
            argv = ParticipantRunner._normalize_gemini_approval_flags(argv)

        if provider_text == 'claude':
            if claude_team_agents and not ParticipantRunner._has_agents_flag(argv):
                argv.extend(['--agents', '{}'])

        return argv

    @staticmethod
    def _prepare_runtime_invocation(*, argv: list[str], provider: str, prompt: str) -> tuple[list[str], str]:
        provider_text = str(provider or '').strip().lower()
        runtime_argv = list(argv)
        runtime_input = prompt
        if provider_text != 'gemini':
            return runtime_argv, runtime_input
        if ParticipantRunner._has_prompt_flag(runtime_argv):
            return runtime_argv, runtime_input
        # Gemini CLI is significantly more stable in non-interactive mode.
        runtime_argv.extend(['--prompt', prompt])
        runtime_input = ''
        return runtime_argv, runtime_input

    @staticmethod
    def _split_extra_args(value: str | None) -> list[str]:
        text = str(value or '').strip()
        if not text:
            return []
        try:
            return [str(v) for v in shlex.split(text, posix=False) if str(v).strip()]
        except ValueError:
            return [v for v in text.split() if v]

    @staticmethod
    def _has_model_flag(argv: list[str]) -> bool:
        for token in argv:
            text = str(token).strip()
            if text in {'--model', '-m'}:
                return True
            if text.startswith('--model='):
                return True
        return False

    @staticmethod
    def _has_agents_flag(argv: list[str]) -> bool:
        for token in argv:
            text = str(token).strip()
            if text == '--agents' or text.startswith('--agents='):
                return True
        return False

    @staticmethod
    def _has_prompt_flag(argv: list[str]) -> bool:
        for token in argv:
            text = str(token).strip()
            if text in {'-p', '--prompt'}:
                return True
            if text.startswith('--prompt='):
                return True
        return False

    @staticmethod
    def _normalize_gemini_approval_flags(argv: list[str]) -> list[str]:
        has_yolo = False
        has_approval_mode = False
        for token in argv:
            text = str(token).strip()
            if text in {'-y', '--yolo'}:
                has_yolo = True
            elif text == '--approval-mode' or text.startswith('--approval-mode='):
                has_approval_mode = True
        if not (has_yolo and has_approval_mode):
            return argv

        # Gemini CLI treats --yolo and --approval-mode as mutually exclusive.
        out: list[str] = []
        for token in argv:
            text = str(token).strip()
            if text in {'-y', '--yolo'}:
                continue
            out.append(str(token))
        return out

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
        return ' '.join(str(v) for v in argv)

    @staticmethod
    def _run_streaming(
        *,
        argv: list[str],
        runtime_input: str,
        cwd: Path,
        timeout_seconds: float,
        on_stream: Callable[[str, str], None],
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
