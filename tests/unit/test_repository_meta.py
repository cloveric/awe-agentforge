from __future__ import annotations

import json

from awe_agentcheck.repository import decode_task_meta


def test_decode_task_meta_parses_string_booleans() -> None:
    raw = json.dumps(
        {
            'participants': ['codex#review-A'],
            'plain_mode': 'false',
            'stream_mode': '0',
            'debate_mode': 'no',
        }
    )

    parsed = decode_task_meta(raw)

    assert parsed['plain_mode'] is False
    assert parsed['stream_mode'] is False
    assert parsed['debate_mode'] is False


def test_decode_task_meta_defaults_booleans_for_null_values() -> None:
    raw = json.dumps(
        {
            'participants': ['codex#review-A'],
            'plain_mode': None,
            'stream_mode': None,
            'debate_mode': None,
        }
    )

    parsed = decode_task_meta(raw)

    assert parsed['plain_mode'] is True
    assert parsed['stream_mode'] is True
    assert parsed['debate_mode'] is True


def test_decode_task_meta_parses_agent_override_maps() -> None:
    raw = json.dumps(
        {
            'participants': ['claude#review-A', 'codex#review-B'],
            'claude_team_agents_overrides': {
                'claude#review-A': 'true',
            },
            'codex_multi_agents_overrides': {
                'codex#review-B': '0',
            },
        }
    )

    parsed = decode_task_meta(raw)

    assert parsed['claude_team_agents_overrides']['claude#review-A'] is True
    assert parsed['codex_multi_agents_overrides']['codex#review-B'] is False
