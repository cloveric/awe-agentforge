from __future__ import annotations

DEEP_DISCOVERY_POLICY_TEMPLATE = 'deep-discovery-first'
DEFAULT_POLICY_TEMPLATE = DEEP_DISCOVERY_POLICY_TEMPLATE

DEFAULT_RISK_POLICY_CONTRACT: dict[str, object] = {
    'version': '1',
    'riskTierRules': {
        'high': ['src/awe_agentcheck/api.py', 'src/awe_agentcheck/service.py'],
        'low': ['**'],
    },
    'mergePolicy': {
        'high': {
            'requiredChecks': ['risk-policy-gate', 'harness-smoke', 'head-sha-gate', 'evidence-manifest'],
        },
        'low': {
            'requiredChecks': ['risk-policy-gate', 'head-sha-gate'],
        },
    },
}

POLICY_TEMPLATE_CATALOG: dict[str, dict] = {
    'deep-discovery-first': {
        'id': 'deep-discovery-first',
        'label': 'Deep Discovery First',
        'description': 'Default audit-first profile with deeper repository-wide discovery and autonomous execution.',
        'defaults': {
            'sandbox_mode': 1,
            'self_loop_mode': 1,
            'auto_merge': 1,
            'max_rounds': 3,
            'debate_mode': 1,
            'plain_mode': 1,
            'stream_mode': 1,
            'repair_mode': 'balanced',
            'memory_mode': 'strict',
            'phase_timeout_seconds': {},
            'evolution_level': 2,
        },
    },
    'balanced-default': {
        'id': 'balanced-default',
        'label': 'Balanced Default',
        'description': 'General-purpose profile for most repositories.',
        'defaults': {
            'sandbox_mode': 1,
            'self_loop_mode': 0,
            'auto_merge': 1,
            'max_rounds': 1,
            'debate_mode': 1,
            'plain_mode': 1,
            'stream_mode': 1,
            'repair_mode': 'balanced',
            'memory_mode': 'basic',
            'phase_timeout_seconds': {},
            'evolution_level': 0,
        },
    },
    'safe-review': {
        'id': 'safe-review',
        'label': 'Safe Review',
        'description': 'Conservative profile for high-risk or large repositories.',
        'defaults': {
            'sandbox_mode': 1,
            'self_loop_mode': 0,
            'auto_merge': 0,
            'max_rounds': 2,
            'debate_mode': 1,
            'plain_mode': 1,
            'stream_mode': 1,
            'repair_mode': 'minimal',
            'memory_mode': 'strict',
            'phase_timeout_seconds': {},
            'evolution_level': 0,
        },
    },
    'rapid-fix': {
        'id': 'rapid-fix',
        'label': 'Rapid Fix',
        'description': 'Fast patch profile for small/low-risk repositories.',
        'defaults': {
            'sandbox_mode': 1,
            'self_loop_mode': 1,
            'auto_merge': 1,
            'max_rounds': 1,
            'debate_mode': 1,
            'plain_mode': 1,
            'stream_mode': 1,
            'repair_mode': 'minimal',
            'memory_mode': 'basic',
            'phase_timeout_seconds': {},
            'evolution_level': 0,
        },
    },
    'deep-evolve': {
        'id': 'deep-evolve',
        'label': 'Deep Evolve',
        'description': 'Multi-round structural evolution with stronger scrutiny.',
        'defaults': {
            'sandbox_mode': 1,
            'self_loop_mode': 1,
            'auto_merge': 0,
            'max_rounds': 3,
            'debate_mode': 1,
            'plain_mode': 1,
            'stream_mode': 1,
            'repair_mode': 'structural',
            'memory_mode': 'basic',
            'phase_timeout_seconds': {},
            'evolution_level': 2,
        },
    },
    'frontier-evolve': {
        'id': 'frontier-evolve',
        'label': 'Frontier Evolve',
        'description': 'Aggressive proactive evolution: feature ideas, framework upgrades, and UI improvements.',
        'defaults': {
            'sandbox_mode': 1,
            'self_loop_mode': 1,
            'auto_merge': 1,
            'max_rounds': 4,
            'debate_mode': 1,
            'plain_mode': 1,
            'stream_mode': 1,
            'repair_mode': 'structural',
            'memory_mode': 'strict',
            'phase_timeout_seconds': {},
            'evolution_level': 3,
        },
    },
}
