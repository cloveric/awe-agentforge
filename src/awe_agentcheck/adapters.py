from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
from queue import Empty, Queue
import json
import os
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
_MIN_ATTEMPT_TIMEOUT_SECONDS = 0.05
_CONTROL_SCHEMA_COMPAT_ENV = 'AWE_CONTROL_SCHEMA_COMPAT'


@dataclass(frozen=True)
class AdapterResult:
    output: str
    verdict: str
    next_action: str | None
    returncode: int
    duration_seconds: float


DEFAULT_PROVIDER_REGISTRY = {
    'claude': {
        'command': 'claude -p --dangerously-skip-permissions --strict-mcp-config --effort low --model claude-opus-4-6',
        'model_flag': '--model',
        'capabilities': {
            'claude_team_agents': True,
            'codex_multi_agents': False,
        },
    },
    'codex': {
        'command': 'codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=xhigh',
        'model_flag': '-m',
        'capabilities': {
            'claude_team_agents': False,
            'codex_multi_agents': True,
        },
    },
    'gemini': {
        'command': 'gemini --yolo',
        'model_flag': '-m',
        'capabilities': {
            'claude_team_agents': False,
            'codex_multi_agents': False,
        },
    },
}
DEFAULT_COMMANDS = {
    provider: str(spec.get('command') or '').strip()
    for provider, spec in DEFAULT_PROVIDER_REGISTRY.items()
}


def _normalize_verdict_value(value: str | None) -> str:
    text = str(value or '').strip().lower()
    aliases = {
        'no_blocker': 'no_blocker',
        'no-blocker': 'no_blocker',
        'ok': 'no_blocker',
        'pass': 'no_blocker',
        'passed': 'no_blocker',
        'blocker': 'blocker',
        'blocked': 'blocker',
        'fail': 'blocker',
        'failed': 'blocker',
        'unknown': 'unknown',
        'unsure': 'unknown',
        'uncertain': 'unknown',
    }
    return aliases.get(text, 'unknown')


def _normalize_next_action_value(value: str | None) -> str | None:
    text = str(value or '').strip().lower()
    if text in {'retry', 'pass', 'stop'}:
        return text
    return None


def _legacy_control_fallback_enabled(*, allow_legacy: bool | None = None) -> bool:
    if allow_legacy is not None:
        return bool(allow_legacy)
    text = str(os.getenv(_CONTROL_SCHEMA_COMPAT_ENV, '') or '').strip().lower()
    return text in {'1', 'true', 'yes', 'on'}


def _iter_json_candidates(output: str) -> list[str]:
    text = str(output or '').strip()
    if not text:
        return []
    candidates: list[str] = [text]
    fence_re = re.compile(r'```(?:json)?\s*(\{.*?\})\s*```', re.IGNORECASE | re.DOTALL)
    for m in fence_re.finditer(text):
        payload = str(m.group(1) or '').strip()
        if payload:
            candidates.append(payload)
    for line in text.splitlines():
        line_text = str(line or '').strip()
        if line_text.startswith('{') and line_text.endswith('}'):
            candidates.append(line_text)
    out: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _parse_json_control_payload(output: str) -> dict[str, str | None]:
    for candidate in _iter_json_candidates(output):
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if not isinstance(parsed, dict):
            continue
        verdict = _normalize_verdict_value(parsed.get('verdict'))
        next_action = _normalize_next_action_value(parsed.get('next_action'))
        has_control = ('verdict' in parsed) or ('next_action' in parsed)
        if has_control:
            return {
                'verdict': verdict,
                'next_action': next_action,
            }
    return {
        'verdict': None,
        'next_action': None,
    }


def parse_verdict(output: str, *, allow_legacy: bool | None = None) -> str:
    json_payload = _parse_json_control_payload(output)
    json_verdict = str(json_payload.get('verdict') or '').strip().lower()
    if json_verdict in {'no_blocker', 'blocker', 'unknown'}:
        return json_verdict
    if not _legacy_control_fallback_enabled(allow_legacy=allow_legacy):
        return 'unknown'
    for line in (output or '').splitlines():
        m = _VERDICT_RE.match(line)
        if m:
            return m.group(1).lower().replace('no_blocker', 'no_blocker')
    return 'unknown'


def parse_next_action(output: str, *, allow_legacy: bool | None = None) -> str | None:
    json_payload = _parse_json_control_payload(output)
    json_action = _normalize_next_action_value(str(json_payload.get('next_action') or '').strip())
    if json_action is not None:
        return json_action
    if not _legacy_control_fallback_enabled(allow_legacy=allow_legacy):
        return None
    for line in (output or '').splitlines():
        m = _NEXT_RE.match(line)
        if m:
            return m.group(1).lower()
    return None


def _split_extra_args(value: str | None) -> list[str]:
    text = str(value or '').strip()
    if not text:
        return []
    try:
        return [str(v) for v in shlex.split(text, posix=False) if str(v).strip()]
    except ValueError:
        return [v for v in text.split() if v]


def _has_model_flag(argv: list[str]) -> bool:
    for token in argv:
        text = str(token).strip()
        if text in {'--model', '-m'}:
            return True
        if text.startswith('--model='):
            return True
    return False


def _has_agents_flag(argv: list[str]) -> bool:
    for token in argv:
        text = str(token).strip()
        if text == '--agents' or text.startswith('--agents='):
            return True
    return False


def _has_codex_multi_agent_config_token(value: str) -> bool:
    text = str(value or '').strip().strip('"').strip("'")
    lowered = text.lower()
    if not lowered.startswith('features.multi_agent='):
        return False
    return True


def _has_codex_multi_agent_flag(argv: list[str]) -> bool:
    for idx, token in enumerate(argv):
        text = str(token).strip()
        lowered = text.lower()
        if lowered == '--enable=multi_agent':
            return True
        if lowered == '--enable':
            if idx + 1 < len(argv):
                value = str(argv[idx + 1]).strip().lower()
                if value == 'multi_agent':
                    return True
            continue
        if lowered == '--config':
            if idx + 1 < len(argv):
                if _has_codex_multi_agent_config_token(str(argv[idx + 1])):
                    return True
            continue
        if lowered.startswith('--config='):
            value = text.split('=', 1)[1] if '=' in text else ''
            if _has_codex_multi_agent_config_token(value):
                return True
            continue
        if lowered == '-c':
            if idx + 1 < len(argv):
                if _has_codex_multi_agent_config_token(str(argv[idx + 1])):
                    return True
            continue
    return False


def _has_prompt_flag(argv: list[str]) -> bool:
    for token in argv:
        text = str(token).strip()
        if text in {'-p', '--prompt'}:
            return True
        if text.startswith('--prompt='):
            return True
    return False


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


class ProviderAdapter(ABC):
    def __init__(self, *, provider: str, provider_spec: dict[str, object] | None = None):
        self.provider = str(provider or '').strip().lower()
        self.provider_spec = dict(provider_spec or {})

    def build_argv(
        self,
        *,
        command: str,
        model: str | None,
        model_params: str | None,
        claude_team_agents: bool,
        codex_multi_agents: bool,
    ) -> list[str]:
        argv = shlex.split(command, posix=False)

        model_text = str(model or '').strip()
        if model_text and not _has_model_flag(argv):
            flag = str(self.provider_spec.get('model_flag') or '').strip()
            if flag:
                argv.extend([flag, model_text])

        extra = _split_extra_args(model_params)
        if extra:
            argv.extend(extra)

        return self._build_provider_argv(
            argv=argv,
            claude_team_agents=claude_team_agents,
            codex_multi_agents=codex_multi_agents,
        )

    def _build_provider_argv(
        self,
        *,
        argv: list[str],
        claude_team_agents: bool,
        codex_multi_agents: bool,
    ) -> list[str]:
        _ = claude_team_agents
        _ = codex_multi_agents
        return argv

    def prepare_runtime_invocation(self, *, argv: list[str], prompt: str) -> tuple[list[str], str]:
        return list(argv), prompt

    def normalize_output(self, output: str) -> str:
        return str(output or '').strip()


class ClaudeAdapter(ProviderAdapter):
    def _build_provider_argv(
        self,
        *,
        argv: list[str],
        claude_team_agents: bool,
        codex_multi_agents: bool,
    ) -> list[str]:
        _ = codex_multi_agents
        capabilities = dict(self.provider_spec.get('capabilities') or {})
        supports_team_agents = bool(capabilities.get('claude_team_agents', False))
        if claude_team_agents and supports_team_agents and not _has_agents_flag(argv):
            argv.extend(['--agents', '{}'])
        return argv


class CodexAdapter(ProviderAdapter):
    def _build_provider_argv(
        self,
        *,
        argv: list[str],
        claude_team_agents: bool,
        codex_multi_agents: bool,
    ) -> list[str]:
        _ = claude_team_agents
        capabilities = dict(self.provider_spec.get('capabilities') or {})
        supports_multi_agents = bool(capabilities.get('codex_multi_agents', False))
        if codex_multi_agents and supports_multi_agents and not _has_codex_multi_agent_flag(argv):
            argv.extend(['--enable', 'multi_agent'])
        return argv

    def normalize_output(self, output: str) -> str:
        return ParticipantRunner._normalize_codex_exec_output(str(output or '').strip())


class GeminiAdapter(ProviderAdapter):
    def _build_provider_argv(
        self,
        *,
        argv: list[str],
        claude_team_agents: bool,
        codex_multi_agents: bool,
    ) -> list[str]:
        _ = claude_team_agents
        _ = codex_multi_agents
        return _normalize_gemini_approval_flags(argv)

    def prepare_runtime_invocation(self, *, argv: list[str], prompt: str) -> tuple[list[str], str]:
        runtime_argv = list(argv)
        runtime_input = prompt
        if _has_prompt_flag(runtime_argv):
            return runtime_argv, runtime_input
        # Gemini CLI is significantly more stable in non-interactive mode.
        runtime_argv.extend(['--prompt', prompt])
        return runtime_argv, ''


class GenericProviderAdapter(ProviderAdapter):
    pass


class ProviderFactory:
    _ADAPTERS: dict[str, type[ProviderAdapter]] = {
        'claude': ClaudeAdapter,
        'codex': CodexAdapter,
        'gemini': GeminiAdapter,
    }

    @classmethod
    def create(cls, *, provider: str, provider_spec: dict[str, object] | None = None) -> ProviderAdapter:
        key = str(provider or '').strip().lower()
        adapter_cls = cls._ADAPTERS.get(key, GenericProviderAdapter)
        return adapter_cls(provider=key, provider_spec=provider_spec)


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
                '- src/awe_agentcheck/adapters.py\\n'
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
            except FileNotFoundError as exc:
                _ = exc
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
        assert completed is not None
        elapsed = time.monotonic() - started

        output = (completed.stdout or '').strip()
        if completed.returncode != 0:
            stderr = (completed.stderr or '').strip()
            output = '\n'.join([p for p in [output, stderr] if p]).strip()

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
        adapter = ProviderFactory.create(provider=provider, provider_spec=None)
        return adapter.normalize_output(output)

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
        return _split_extra_args(value)

    @staticmethod
    def _has_model_flag(argv: list[str]) -> bool:
        return _has_model_flag(argv)

    @staticmethod
    def _has_agents_flag(argv: list[str]) -> bool:
        return _has_agents_flag(argv)

    @staticmethod
    def _has_codex_multi_agent_flag(argv: list[str]) -> bool:
        return _has_codex_multi_agent_flag(argv)

    @staticmethod
    def _has_codex_multi_agent_config_token(value: str) -> bool:
        return _has_codex_multi_agent_config_token(value)

    @staticmethod
    def _has_prompt_flag(argv: list[str]) -> bool:
        return _has_prompt_flag(argv)

    @staticmethod
    def _normalize_gemini_approval_flags(argv: list[str]) -> list[str]:
        return _normalize_gemini_approval_flags(argv)

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
            # Drop stale main-workspace src entries to avoid cross-workspace
            # import contamination when running in sandbox tasks.
            if resolved_norm.endswith('/awe-agentcheck/src'):
                continue
            ordered.append(text)

        env['PYTHONPATH'] = os.pathsep.join(ordered)
        return env
