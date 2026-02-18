from awe_agentcheck.domain.gate import evaluate_medium_gate
from awe_agentcheck.domain.models import ReviewVerdict


def test_medium_gate_passes_when_all_checks_and_reviews_clear():
    outcome = evaluate_medium_gate(
        tests_ok=True,
        lint_ok=True,
        reviewer_verdicts=[ReviewVerdict.NO_BLOCKER, ReviewVerdict.NO_BLOCKER],
    )
    assert outcome.passed is True
    assert outcome.reason == 'passed'


def test_medium_gate_fails_when_any_blocker_exists():
    outcome = evaluate_medium_gate(
        tests_ok=True,
        lint_ok=True,
        reviewer_verdicts=[ReviewVerdict.NO_BLOCKER, ReviewVerdict.BLOCKER],
    )
    assert outcome.passed is False
    assert outcome.reason == 'review_blocker'


def test_medium_gate_fails_when_no_reviewer_verdicts():
    outcome = evaluate_medium_gate(
        tests_ok=True,
        lint_ok=True,
        reviewer_verdicts=[],
    )
    assert outcome.passed is False
    assert outcome.reason == 'review_missing'


def test_medium_gate_fails_when_all_reviewer_verdicts_unknown():
    outcome = evaluate_medium_gate(
        tests_ok=True,
        lint_ok=True,
        reviewer_verdicts=[ReviewVerdict.UNKNOWN, ReviewVerdict.UNKNOWN],
    )
    assert outcome.passed is False
    assert outcome.reason == 'review_unknown'


def test_medium_gate_prioritizes_test_failure_over_blocker_verdict():
    outcome = evaluate_medium_gate(
        tests_ok=False,
        lint_ok=True,
        reviewer_verdicts=[ReviewVerdict.BLOCKER],
    )
    assert outcome.passed is False
    assert outcome.reason == 'tests_failed'
