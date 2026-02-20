from __future__ import annotations

from awe_agentcheck.adapters.base import GenericProviderAdapter, ProviderAdapter
from awe_agentcheck.adapters.claude import ClaudeAdapter
from awe_agentcheck.adapters.codex import CodexAdapter
from awe_agentcheck.adapters.gemini import GeminiAdapter


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


__all__ = ['ProviderFactory']
