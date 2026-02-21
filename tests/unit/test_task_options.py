from __future__ import annotations

import pytest

from awe_agentcheck import task_options


def test_normalize_evolve_until_handles_empty_and_iso():
    assert task_options.normalize_evolve_until(None) is None
    assert task_options.normalize_evolve_until('  ') is None
    assert task_options.normalize_evolve_until('2026-02-18 07:00:00.123456') == '2026-02-18T07:00:00'


def test_normalize_merge_target_path_and_language_aliases():
    assert task_options.normalize_merge_target_path('') is None
    assert task_options.normalize_merge_target_path('a/../b').endswith('b')
    assert task_options.normalize_conversation_language('english') == 'en'
    assert task_options.normalize_conversation_language('中文') == 'zh'
    assert task_options.normalize_conversation_language('no-such-language') == 'en'
    with pytest.raises(ValueError):
        task_options.normalize_conversation_language('invalid', strict=True)


def test_normalize_repair_mode_and_boolean_flags():
    assert task_options.normalize_repair_mode(None) == 'balanced'
    assert task_options.normalize_repair_mode('structural') == 'structural'
    assert task_options.normalize_repair_mode('oops') == 'balanced'
    with pytest.raises(ValueError):
        task_options.normalize_repair_mode('oops', strict=True)

    assert task_options.normalize_plain_mode('yes') is True
    assert task_options.normalize_plain_mode('off') is False
    assert task_options.normalize_bool_flag('', default=True) is True
    assert task_options.normalize_bool_flag('none', default=False) is False
    assert task_options.normalize_bool_flag('true', default=False) is True


def test_normalize_memory_mode_and_phase_timeout_seconds():
    assert task_options.normalize_memory_mode(None) == 'basic'
    assert task_options.normalize_memory_mode('0') == 'off'
    assert task_options.normalize_memory_mode('strict') == 'strict'
    assert task_options.normalize_memory_mode('invalid') == 'basic'
    with pytest.raises(ValueError):
        task_options.normalize_memory_mode('invalid', strict=True)

    parsed = task_options.normalize_phase_timeout_seconds(
        {
            'proposal': 120,
            'impl': 180,
            'verification': 90,
        }
    )
    assert parsed['proposal'] == 120
    assert parsed['implementation'] == 180
    assert parsed['command'] == 90

    # clamp to minimum
    clamped = task_options.normalize_phase_timeout_seconds({'review': 1})
    assert clamped['review'] == 10

    with pytest.raises(ValueError):
        task_options.normalize_phase_timeout_seconds({'unknown': 10})
    with pytest.raises(ValueError):
        task_options.normalize_phase_timeout_seconds({'proposal': 'bad'})

    assert task_options.normalize_phase_timeout_seconds({'unknown': 10}, strict=False) == {}


def test_normalize_provider_models_and_params():
    out = task_options.normalize_provider_models({'Codex': 'gpt-5.3-codex', 'claude': 'claude-opus-4-6'})
    assert out == {'codex': 'gpt-5.3-codex', 'claude': 'claude-opus-4-6'}

    params = task_options.normalize_provider_model_params({'Gemini': '--temperature 0.1'})
    assert params == {'gemini': '--temperature 0.1'}

    with pytest.raises(ValueError):
        task_options.normalize_provider_models('bad')  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        task_options.normalize_provider_models({'unknown': 'x'})
    with pytest.raises(ValueError):
        task_options.normalize_provider_model_params({'codex': ''})

    lenient_models = task_options.normalize_provider_models(
        {'Codex': 'gpt-5.3-codex', 'unknown': 'x', '': 'x'},
        strict=False,
    )
    assert lenient_models == {'codex': 'gpt-5.3-codex'}

    lenient_params = task_options.normalize_provider_model_params(
        {'claude': '--temperature 0.2', 'unknown': 'x', 'codex': ''},
        strict=False,
    )
    assert lenient_params == {'claude': '--temperature 0.2'}


def test_normalize_participant_models_and_params():
    known = {'codex#author-A', 'claude#review-B'}
    models = task_options.normalize_participant_models(
        {'Codex#Author-A': 'gpt-5.3-codex', 'claude#review-B': 'claude-opus-4-6'},
        known_participants=known,
    )
    assert models['Codex#Author-A'] == 'gpt-5.3-codex'
    assert models['claude#review-B'] == 'claude-opus-4-6'

    params = task_options.normalize_participant_model_params(
        {'codex#author-A': '-c model_reasoning_effort=xhigh'},
        known_participants=known,
    )
    assert params['codex#author-A'].startswith('-c')

    with pytest.raises(ValueError):
        task_options.normalize_participant_models({'': 'x'}, known_participants=known)
    with pytest.raises(ValueError):
        task_options.normalize_participant_models({'gemini#review-C': 'x'}, known_participants=known)
    with pytest.raises(ValueError):
        task_options.normalize_participant_model_params({'claude#review-B': ''}, known_participants=known)

    lenient_models = task_options.normalize_participant_models(
        {'Codex#Author-A': 'gpt-5.3-codex', '': 'x', 'x': ''},
        strict=False,
        include_lower_alias=True,
    )
    assert lenient_models['Codex#Author-A'] == 'gpt-5.3-codex'
    assert lenient_models['codex#author-a'] == 'gpt-5.3-codex'

    lenient_params = task_options.normalize_participant_model_params(
        {'Codex#Author-A': '-c model_reasoning_effort=xhigh', '': '-c x'},
        strict=False,
        include_lower_alias=True,
    )
    assert lenient_params['codex#author-a'].endswith('xhigh')


def test_participant_agent_overrides_and_resolution():
    known = {'codex#author-A', 'claude#review-B'}
    overrides = task_options.normalize_participant_agent_overrides(
        {'codex#author-A': 'true'},
        known_participants=known,
        required_provider='codex',
        field='codex_multi_agents_overrides',
    )
    assert overrides == {'codex#author-A': True}

    with pytest.raises(ValueError):
        task_options.normalize_participant_agent_overrides(
            {'claude#review-B': True},
            known_participants=known,
            required_provider='codex',
            field='codex_multi_agents_overrides',
        )
    with pytest.raises(ValueError):
        task_options.normalize_participant_agent_overrides(
            {'': True},
            known_participants=known,
            required_provider='codex',
            field='codex_multi_agents_overrides',
        )
    with pytest.raises(ValueError):
        task_options.coerce_bool_override_value('maybe', field='x')

    lenient = task_options.normalize_participant_agent_overrides(
        {'Codex#Author-A': 'on', 'claude#review-B': 'off', '': True},
        strict=False,
        field='participant_agent_overrides',
        include_lower_alias=True,
    )
    assert lenient['Codex#Author-A'] is True
    assert lenient['claude#review-B'] is False
    assert lenient['codex#author-a'] is True

    runtime = task_options.normalize_participant_agent_overrides_runtime({'Codex#Author-A': 1, '': False})
    assert runtime['Codex#Author-A'] is True
    assert runtime['codex#author-a'] is True

    assert task_options.resolve_agent_toggle_for_participant(
        participant_id='Codex#Author-A',
        global_enabled=False,
        overrides=runtime,
    ) is True
    assert task_options.resolve_agent_toggle_for_participant(
        participant_id='missing',
        global_enabled=True,
        overrides=runtime,
    ) is True


def test_resolve_model_and_model_params_for_participant():
    model = task_options.resolve_model_for_participant(
        participant_id='Codex#Author-A',
        provider='codex',
        provider_models={'codex': 'gpt-5.3-codex'},
        participant_models={'codex#author-a': 'gpt-5.3-codex-spark'},
    )
    assert model == 'gpt-5.3-codex-spark'

    fallback = task_options.resolve_model_for_participant(
        participant_id='unknown',
        provider='codex',
        provider_models={'codex': 'gpt-5.3-codex'},
        participant_models={},
    )
    assert fallback == 'gpt-5.3-codex'

    params = task_options.resolve_model_params_for_participant(
        participant_id='codex#author-A',
        provider='codex',
        provider_model_params={'codex': '-c model_reasoning_effort=high'},
        participant_model_params={'codex#author-A': '-c model_reasoning_effort=xhigh'},
    )
    assert params == '-c model_reasoning_effort=xhigh'

    no_params = task_options.resolve_model_params_for_participant(
        participant_id='missing',
        provider='claude',
        provider_model_params={},
        participant_model_params={},
    )
    assert no_params is None


def test_extract_model_from_command_variants():
    assert task_options.extract_model_from_command('codex exec -m gpt-5.3-codex') == 'gpt-5.3-codex'
    assert task_options.extract_model_from_command('claude -p --model claude-sonnet-4-6') == 'claude-sonnet-4-6'
    assert task_options.extract_model_from_command('gemini --model=gemini-3-pro-preview') == 'gemini-3-pro-preview'
    assert task_options.extract_model_from_command('') is None
    # Broken quotes should fall back to split().
    assert task_options.extract_model_from_command('codex exec --model "gpt-5.3-codex') == '"gpt-5.3-codex'
    assert task_options.extract_model_from_command('codex exec --temperature 0.1') is None
