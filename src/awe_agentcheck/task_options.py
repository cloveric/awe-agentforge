from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shlex

from awe_agentcheck.participants import get_supported_providers, parse_participant_id

SUPPORTED_CONVERSATION_LANGUAGES = frozenset({'en', 'zh'})
SUPPORTED_REPAIR_MODES = frozenset({'minimal', 'balanced', 'structural'})
SUPPORTED_MEMORY_MODES = frozenset({'off', 'basic', 'strict'})


def supported_providers() -> set[str]:
    return get_supported_providers()


def normalize_evolve_until(value: str | None) -> str | None:
    text = str(value or '').strip()
    if not text:
        return None
    candidate = text.replace(' ', 'T')
    parsed = datetime.fromisoformat(candidate)
    return parsed.replace(microsecond=0).isoformat()


def normalize_merge_target_path(value: str | None) -> str | None:
    text = str(value or '').strip()
    if not text:
        return None
    return str(Path(text))


def normalize_conversation_language(value: str | None, *, strict: bool = False) -> str:
    text = str(value or '').strip().lower()
    if not text:
        return 'en'
    aliases = {
        'en': 'en',
        'english': 'en',
        'eng': 'en',
        'zh': 'zh',
        'zh-cn': 'zh',
        'cn': 'zh',
        'chinese': 'zh',
        '中文': 'zh',
    }
    normalized = aliases.get(text, text)
    if normalized not in SUPPORTED_CONVERSATION_LANGUAGES:
        if strict:
            raise ValueError(f'invalid conversation_language: {text}')
        return 'en'
    return normalized


def normalize_repair_mode(value, *, strict: bool = False) -> str:
    text = str(value or '').strip().lower()
    if not text:
        return 'balanced'
    if text not in SUPPORTED_REPAIR_MODES:
        if strict:
            raise ValueError(f'invalid repair_mode: {text}')
        return 'balanced'
    return text


def normalize_memory_mode(value, *, strict: bool = False) -> str:
    text = str(value or '').strip().lower()
    aliases = {
        '': 'basic',
        'off': 'off',
        '0': 'off',
        'none': 'off',
        'basic': 'basic',
        '1': 'basic',
        'default': 'basic',
        'strict': 'strict',
        '2': 'strict',
        'hard': 'strict',
    }
    normalized = aliases.get(text, text)
    if normalized not in SUPPORTED_MEMORY_MODES:
        if strict:
            raise ValueError(f'invalid memory_mode: {text}')
        return 'basic'
    return normalized


def normalize_phase_timeout_seconds(
    value: dict[str, int] | None,
    *,
    strict: bool = True,
    minimum: int = 10,
    maximum: int = 60_000,
) -> dict[str, int]:
    if not value:
        return {}
    if not isinstance(value, dict):
        if strict:
            raise ValueError('phase_timeout_seconds must be an object')
        return {}

    aliases = {
        'proposal': 'proposal',
        'precheck': 'proposal',
        'discussion': 'discussion',
        'author': 'discussion',
        'implementation': 'implementation',
        'impl': 'implementation',
        'review': 'review',
        'verification': 'command',
        'command': 'command',
        'lint_test': 'command',
    }

    out: dict[str, int] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or '').strip().lower()
        mapped = aliases.get(key)
        if not mapped:
            if strict:
                raise ValueError(f'invalid phase_timeout_seconds key: {raw_key}')
            continue
        try:
            parsed = int(raw_value)
        except (TypeError, ValueError):
            if strict:
                raise ValueError(f'invalid phase_timeout_seconds[{mapped}]: {raw_value}')
            continue
        out[mapped] = max(minimum, min(maximum, parsed))
    return out


def normalize_plain_mode(value) -> bool:
    text = str(value).strip().lower()
    if text in {'0', 'false', 'no', 'off'}:
        return False
    if text in {'1', 'true', 'yes', 'on'}:
        return True
    return bool(value)


def normalize_bool_flag(value, *, default: bool) -> bool:
    text = str(value).strip().lower()
    if text in {'0', 'false', 'no', 'off'}:
        return False
    if text in {'1', 'true', 'yes', 'on'}:
        return True
    if text in {'', 'none'}:
        return bool(default)
    return bool(value)


def _normalize_provider_mapping(
    value: dict[str, str] | None,
    *,
    field: str,
    strict: bool,
) -> dict[str, str]:
    if not value:
        return {}
    if not isinstance(value, dict):
        if strict:
            raise ValueError(f'{field} must be an object')
        return {}

    out: dict[str, str] = {}
    allowed = supported_providers()
    for raw_provider, raw_value in value.items():
        provider = str(raw_provider or '').strip().lower()
        item = str(raw_value or '').strip()
        if not provider:
            if strict:
                raise ValueError(f'{field} key cannot be empty')
            continue
        if provider not in allowed:
            if strict:
                raise ValueError(f'invalid {field} key: {provider}')
            continue
        if not item:
            if strict:
                raise ValueError(f'{field}[{provider}] cannot be empty')
            continue
        out[provider] = item
    return out


def normalize_provider_models(value: dict[str, str] | None, *, strict: bool = True) -> dict[str, str]:
    return _normalize_provider_mapping(value, field='provider_models', strict=strict)


def normalize_provider_model_params(value: dict[str, str] | None, *, strict: bool = True) -> dict[str, str]:
    return _normalize_provider_mapping(value, field='provider_model_params', strict=strict)


def normalize_participant_models(
    value: dict[str, str] | None,
    *,
    known_participants: set[str] | None = None,
    strict: bool = True,
    include_lower_alias: bool = False,
) -> dict[str, str]:
    if not value:
        return {}
    if not isinstance(value, dict):
        if strict:
            raise ValueError('participant_models must be an object')
        return {}

    known = {str(v or '').strip() for v in (known_participants or set()) if str(v or '').strip()}
    known_lower = {v.lower() for v in known}
    out: dict[str, str] = {}
    for raw_participant, raw_model in value.items():
        participant = str(raw_participant or '').strip()
        model = str(raw_model or '').strip()
        if not participant:
            if strict:
                raise ValueError('participant_models key cannot be empty')
            continue
        if known_lower and participant.lower() not in known_lower:
            if strict:
                raise ValueError(f'participant_models key is not in task participants: {participant}')
            continue
        if not model:
            if strict:
                raise ValueError(f'participant_models[{participant}] cannot be empty')
            continue
        out[participant] = model
        if include_lower_alias:
            out.setdefault(participant.lower(), model)
    return out


def normalize_participant_model_params(
    value: dict[str, str] | None,
    *,
    known_participants: set[str] | None = None,
    strict: bool = True,
    include_lower_alias: bool = False,
) -> dict[str, str]:
    if not value:
        return {}
    if not isinstance(value, dict):
        if strict:
            raise ValueError('participant_model_params must be an object')
        return {}

    known = {str(v or '').strip() for v in (known_participants or set()) if str(v or '').strip()}
    known_lower = {v.lower() for v in known}
    out: dict[str, str] = {}
    for raw_participant, raw_params in value.items():
        participant = str(raw_participant or '').strip()
        params = str(raw_params or '').strip()
        if not participant:
            if strict:
                raise ValueError('participant_model_params key cannot be empty')
            continue
        if known_lower and participant.lower() not in known_lower:
            if strict:
                raise ValueError(f'participant_model_params key is not in task participants: {participant}')
            continue
        if not params:
            if strict:
                raise ValueError(f'participant_model_params[{participant}] cannot be empty')
            continue
        out[participant] = params
        if include_lower_alias:
            out.setdefault(participant.lower(), params)
    return out


def coerce_bool_override_value(value, *, field: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or '').strip().lower()
    if text in {'1', 'true', 'yes', 'on'}:
        return True
    if text in {'0', 'false', 'no', 'off'}:
        return False
    raise ValueError(f'{field} must be boolean')


def normalize_participant_agent_overrides(
    value: dict[str, bool] | None,
    *,
    known_participants: set[str] | None = None,
    required_provider: str | None = None,
    field: str,
    strict: bool = True,
    include_lower_alias: bool = False,
) -> dict[str, bool]:
    if not value:
        return {}
    if not isinstance(value, dict):
        if strict:
            raise ValueError(f'{field} must be an object')
        return {}

    known = {str(v or '').strip() for v in (known_participants or set()) if str(v or '').strip()}
    known_map = {v.lower(): v for v in known}
    provider_required = str(required_provider or '').strip().lower()
    out: dict[str, bool] = {}
    for raw_participant, raw_enabled in value.items():
        participant = str(raw_participant or '').strip()
        if not participant:
            if strict:
                raise ValueError(f'{field} key cannot be empty')
            continue
        canonical = known_map.get(participant.lower()) if known_map else participant
        if known_map and not canonical:
            if strict:
                raise ValueError(f'{field} key is not in task participants: {participant}')
            continue
        canonical = canonical or participant
        if strict and provider_required:
            parsed = parse_participant_id(canonical)
            if parsed.provider != provider_required:
                raise ValueError(f'{field}[{canonical}] must target provider={provider_required}')
            enabled = coerce_bool_override_value(
                raw_enabled,
                field=f'{field}[{canonical}]',
            )
        elif strict:
            enabled = coerce_bool_override_value(
                raw_enabled,
                field=f'{field}[{canonical}]',
            )
        else:
            if isinstance(raw_enabled, bool):
                enabled = raw_enabled
            else:
                text = str(raw_enabled or '').strip().lower()
                enabled = text in {'1', 'true', 'yes', 'on'}
        out[canonical] = enabled
        if include_lower_alias:
            out[canonical.lower()] = enabled
    return out


def normalize_participant_agent_overrides_runtime(value: dict[str, bool] | None) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for raw_participant, raw_enabled in (value or {}).items():
        participant = str(raw_participant or '').strip()
        if not participant:
            continue
        lowered = participant.lower()
        enabled = bool(raw_enabled)
        out[participant] = enabled
        out[lowered] = enabled
    return out


def resolve_agent_toggle_for_participant(
    *,
    participant_id: str,
    global_enabled: bool,
    overrides: dict[str, bool],
) -> bool:
    participant = str(participant_id or '').strip()
    if participant:
        if participant in overrides:
            return bool(overrides[participant])
        lowered = participant.lower()
        if lowered in overrides:
            return bool(overrides[lowered])
    return bool(global_enabled)


def resolve_model_for_participant(
    *,
    participant_id: str,
    provider: str,
    provider_models: dict[str, str] | None,
    participant_models: dict[str, str] | None,
) -> str | None:
    participant_text = str(participant_id or '').strip()
    participant_map = dict(participant_models or {})
    if participant_text:
        exact = str(participant_map.get(participant_text) or '').strip()
        if exact:
            return exact
        lowered = str(participant_map.get(participant_text.lower()) or '').strip()
        if lowered:
            return lowered
    provider_map = dict(provider_models or {})
    return str(provider_map.get(str(provider or '').strip().lower()) or '').strip() or None


def resolve_model_params_for_participant(
    *,
    participant_id: str,
    provider: str,
    provider_model_params: dict[str, str] | None,
    participant_model_params: dict[str, str] | None,
) -> str | None:
    participant_text = str(participant_id or '').strip()
    participant_map = dict(participant_model_params or {})
    if participant_text:
        exact = str(participant_map.get(participant_text) or '').strip()
        if exact:
            return exact
        lowered = str(participant_map.get(participant_text.lower()) or '').strip()
        if lowered:
            return lowered
    provider_map = dict(provider_model_params or {})
    return str(provider_map.get(str(provider or '').strip().lower()) or '').strip() or None


def extract_model_from_command(command: str) -> str | None:
    text = str(command or '').strip()
    if not text:
        return None
    try:
        argv = shlex.split(text, posix=False)
    except ValueError:
        argv = text.split()
    i = 0
    while i < len(argv):
        token = str(argv[i]).strip()
        if token in {'-m', '--model'} and i + 1 < len(argv):
            value = str(argv[i + 1]).strip()
            return value or None
        if token.startswith('--model='):
            value = token.split('=', 1)[1].strip()
            return value or None
        i += 1
    return None
