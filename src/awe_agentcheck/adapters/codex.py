from __future__ import annotations

from awe_agentcheck.adapters.base import ProviderAdapter, has_codex_multi_agent_flag


def normalize_codex_exec_output(output: str) -> str:
    text = str(output or '').replace('\r\n', '\n').strip()
    if not text:
        return text

    marker = '\ncodex\n'
    if marker in text:
        tail = text.rsplit(marker, 1)[-1]
        if '\ntokens used' in tail:
            tail = tail.split('\ntokens used', 1)[0]
        cleaned = tail.strip()
        if cleaned:
            return cleaned

    banner = '\nOpenAI Codex v'
    if banner in text:
        head = text.split(banner, 1)[0].strip()
        if head:
            return head

    return text


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
        if codex_multi_agents and supports_multi_agents and not has_codex_multi_agent_flag(argv):
            argv.extend(['--enable', 'multi_agent'])
        return argv

    def normalize_output(self, output: str) -> str:
        return normalize_codex_exec_output(output)


__all__ = ['CodexAdapter', 'normalize_codex_exec_output']
