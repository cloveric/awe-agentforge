from __future__ import annotations

from abc import ABC
from dataclasses import dataclass
import json
import os
import re
import shlex

_VERDICT_RE = re.compile(r'^\s*VERDICT\s*:\s*(NO_BLOCKER|BLOCKER|UNKNOWN)\s*$', re.IGNORECASE)
_NEXT_RE = re.compile(r'^\s*NEXT_ACTION\s*:\s*(retry|pass|stop)\s*$', re.IGNORECASE)
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
    for match in fence_re.finditer(text):
        payload = str(match.group(1) or '').strip()
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
        match = _VERDICT_RE.match(line)
        if match:
            return match.group(1).lower().replace('no_blocker', 'no_blocker')
    return 'unknown'


def parse_next_action(output: str, *, allow_legacy: bool | None = None) -> str | None:
    json_payload = _parse_json_control_payload(output)
    json_action = _normalize_next_action_value(str(json_payload.get('next_action') or '').strip())
    if json_action is not None:
        return json_action
    if not _legacy_control_fallback_enabled(allow_legacy=allow_legacy):
        return None
    for line in (output or '').splitlines():
        match = _NEXT_RE.match(line)
        if match:
            return match.group(1).lower()
    return None


def split_extra_args(value: str | None) -> list[str]:
    text = str(value or '').strip()
    if not text:
        return []
    try:
        return [str(v) for v in shlex.split(text, posix=False) if str(v).strip()]
    except ValueError:
        return [v for v in text.split() if v]


def has_model_flag(argv: list[str]) -> bool:
    for token in argv:
        text = str(token).strip()
        if text in {'--model', '-m'}:
            return True
        if text.startswith('--model='):
            return True
    return False


def has_agents_flag(argv: list[str]) -> bool:
    for token in argv:
        text = str(token).strip()
        if text == '--agents' or text.startswith('--agents='):
            return True
    return False


def has_codex_multi_agent_config_token(value: str) -> bool:
    text = str(value or '').strip().strip('"').strip("'")
    return text.lower().startswith('features.multi_agent=')


def has_codex_multi_agent_flag(argv: list[str]) -> bool:
    idx = 0
    while idx < len(argv):
        token = str(argv[idx]).strip()
        if token in {'--enable', '--config'}:
            next_value = str(argv[idx + 1]).strip() if idx + 1 < len(argv) else ''
            if token == '--enable' and next_value == 'multi_agent':
                return True
            if token == '--config' and has_codex_multi_agent_config_token(next_value):
                return True
            idx += 2
            continue
        if token.startswith('--enable='):
            values = [value.strip().lower() for value in token.split('=', 1)[1].split(',')]
            if 'multi_agent' in values:
                return True
        if token.startswith('--config=') and has_codex_multi_agent_config_token(token.split('=', 1)[1]):
            return True
        idx += 1
    return False


def has_prompt_flag(argv: list[str]) -> bool:
    for token in argv:
        text = str(token).strip()
        if text == '--prompt' or text.startswith('--prompt='):
            return True
    return False


def normalize_gemini_approval_flags(argv: list[str]) -> list[str]:
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
        if model_text and not has_model_flag(argv):
            flag = str(self.provider_spec.get('model_flag') or '').strip()
            if flag:
                argv.extend([flag, model_text])

        extra = split_extra_args(model_params)
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


class GenericProviderAdapter(ProviderAdapter):
    pass


__all__ = [
    'AdapterResult',
    'DEFAULT_PROVIDER_REGISTRY',
    'DEFAULT_COMMANDS',
    'ProviderAdapter',
    'GenericProviderAdapter',
    'parse_verdict',
    'parse_next_action',
    'split_extra_args',
    'has_model_flag',
    'has_agents_flag',
    'has_codex_multi_agent_flag',
    'has_codex_multi_agent_config_token',
    'has_prompt_flag',
    'normalize_gemini_approval_flags',
]
