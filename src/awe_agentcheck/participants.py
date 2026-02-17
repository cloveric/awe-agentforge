from __future__ import annotations

from dataclasses import dataclass

SUPPORTED_PROVIDERS = {'claude', 'codex', 'gemini'}


@dataclass(frozen=True)
class Participant:
    participant_id: str
    provider: str
    alias: str


def parse_participant_id(value: str) -> Participant:
    raw = (value or '').strip()
    if '#' not in raw:
        raise ValueError('participant id must be in provider#alias format')
    provider, alias = raw.split('#', 1)
    provider = provider.strip().lower()
    alias = alias.strip()
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f'unsupported provider: {provider}')
    if not alias:
        raise ValueError('participant alias cannot be empty')
    return Participant(participant_id=raw, provider=provider, alias=alias)
