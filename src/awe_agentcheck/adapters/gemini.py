from __future__ import annotations

from awe_agentcheck.adapters.base import ProviderAdapter, has_prompt_flag, normalize_gemini_approval_flags


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
        return normalize_gemini_approval_flags(argv)

    def prepare_runtime_invocation(self, *, argv: list[str], prompt: str) -> tuple[list[str], str]:
        runtime_argv = list(argv)
        runtime_input = prompt
        if has_prompt_flag(runtime_argv):
            return runtime_argv, runtime_input
        runtime_argv.extend(['--prompt', prompt])
        return runtime_argv, ''


__all__ = ['GeminiAdapter']
