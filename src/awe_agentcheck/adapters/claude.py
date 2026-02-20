from __future__ import annotations

from awe_agentcheck.adapters.base import ProviderAdapter, has_agents_flag


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
        if claude_team_agents and supports_team_agents and not has_agents_flag(argv):
            argv.extend(['--agents', '{}'])
        return argv


__all__ = ['ClaudeAdapter']
