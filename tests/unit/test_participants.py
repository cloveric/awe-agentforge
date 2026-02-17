from awe_agentcheck.participants import parse_participant_id


def test_parse_participant_id_with_role_suffix():
    p = parse_participant_id('claude#author-A')
    assert p.provider == 'claude'
    assert p.alias == 'author-A'


def test_parse_participant_id_supports_gemini_provider():
    p = parse_participant_id('gemini#review-X')
    assert p.provider == 'gemini'
    assert p.alias == 'review-X'


def test_parse_participant_id_requires_hash_separator():
    try:
        parse_participant_id('claude-author')
    except ValueError as exc:
        assert 'provider#alias' in str(exc)
    else:
        raise AssertionError('expected ValueError')
