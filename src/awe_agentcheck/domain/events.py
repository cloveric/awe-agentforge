from __future__ import annotations

from enum import Enum


class EventType(str, Enum):
    ARCHITECTURE_AUDIT = 'architecture_audit'
    AUTO_MERGE_COMPLETED = 'auto_merge_completed'
    AUTHOR_CONFIRMATION_REQUIRED = 'author_confirmation_required'
    AUTHOR_DECISION = 'author_decision'
    AUTHOR_FEEDBACK_REQUESTED = 'author_feedback_requested'
    CANCEL_REQUESTED = 'cancel_requested'
    CANCELED = 'canceled'
    DEADLINE_REACHED = 'deadline_reached'
    DEBATE_COMPLETED = 'debate_completed'
    DEBATE_REPLY = 'debate_reply'
    DEBATE_REPLY_STARTED = 'debate_reply_started'
    DEBATE_REVIEW = 'debate_review'
    DEBATE_REVIEW_ERROR = 'debate_review_error'
    DEBATE_REVIEW_STARTED = 'debate_review_started'
    DEBATE_STARTED = 'debate_started'
    DISCUSSION = 'discussion'
    DISCUSSION_STARTED = 'discussion_started'
    EVIDENCE_BUNDLE_READY = 'evidence_bundle_ready'
    EVIDENCE_MANIFEST_FAILED = 'evidence_manifest_failed'
    EVIDENCE_MANIFEST_READY = 'evidence_manifest_ready'
    FORCE_FAILED = 'force_failed'
    GATE_FAILED = 'gate_failed'
    GATE_PASSED = 'gate_passed'
    HEAD_SHA_CAPTURED = 'head_sha_captured'
    HEAD_SHA_MISMATCH = 'head_sha_mismatch'
    HEAD_SHA_MISSING = 'head_sha_missing'
    IMPLEMENTATION = 'implementation'
    IMPLEMENTATION_STARTED = 'implementation_started'
    MANUAL_ROUND_PROMOTED = 'manual_round_promoted'
    MEMORY_HIT = 'memory_hit'
    MEMORY_PERSISTED = 'memory_persisted'
    PARTICIPANT_STREAM = 'participant_stream'
    PRECOMPLETION_CHECKLIST = 'precompletion_checklist'
    PRECOMPLETION_GUARD_FAILED = 'precompletion_guard_failed'
    PREFLIGHT_RISK_GATE = 'preflight_risk_gate'
    PREFLIGHT_RISK_GATE_FAILED = 'preflight_risk_gate_failed'
    PROMOTION_GUARD_BLOCKED = 'promotion_guard_blocked'
    PROMOTION_GUARD_CHECKED = 'promotion_guard_checked'
    PROMPT_CACHE_BREAK = 'prompt_cache_break'
    PROMPT_CACHE_PROBE = 'prompt_cache_probe'
    PROPOSAL_CONSENSUS_REACHED = 'proposal_consensus_reached'
    PROPOSAL_CONSENSUS_RETRY = 'proposal_consensus_retry'
    PROPOSAL_CONSENSUS_STALLED = 'proposal_consensus_stalled'
    PROPOSAL_CANCELED = 'proposal_canceled'
    PROPOSAL_DEADLINE_REACHED = 'proposal_deadline_reached'
    PROPOSAL_DISCUSSION_ERROR = 'proposal_discussion_error'
    PROPOSAL_DISCUSSION_STARTED = 'proposal_discussion_started'
    PROPOSAL_PRECHECK_REVIEW = 'proposal_precheck_review'
    PROPOSAL_PRECHECK_REVIEW_STARTED = 'proposal_precheck_review_started'
    PROPOSAL_PRECHECK_UNAVAILABLE = 'proposal_precheck_unavailable'
    PROPOSAL_REVIEW = 'proposal_review'
    PROPOSAL_REVIEW_PARTIAL = 'proposal_review_partial'
    PROPOSAL_REVIEW_STARTED = 'proposal_review_started'
    PROPOSAL_REVIEW_UNAVAILABLE = 'proposal_review_unavailable'
    REGRESSION_CASE_RECORDED = 'regression_case_recorded'
    REVIEW = 'review'
    REVIEW_ERROR = 'review_error'
    REVIEW_STARTED = 'review_started'
    ROUND_ARTIFACT_ERROR = 'round_artifact_error'
    ROUND_ARTIFACT_READY = 'round_artifact_ready'
    ROUND_STARTED = 'round_started'
    SANDBOX_CLEANUP_COMPLETED = 'sandbox_cleanup_completed'
    START_DEDUPED = 'start_deduped'
    START_DEFERRED = 'start_deferred'
    STRATEGY_SHIFTED = 'strategy_shifted'
    SYSTEM_FAILURE = 'system_failure'
    TASK_STARTED = 'task_started'
    TASK_RUNNING = 'task_running'
    VERIFICATION = 'verification'
    VERIFICATION_STARTED = 'verification_started'
    WORKSPACE_RESUME_GUARD_BLOCKED = 'workspace_resume_guard_blocked'


def normalize_event_type(value: str | EventType) -> str:
    if isinstance(value, EventType):
        return value.value
    text = str(value or '').strip().lower()
    if not text:
        raise ValueError('event_type is required')
    return text


REVIEW_EVENT_TYPES = frozenset(
    {
        EventType.REVIEW.value,
        EventType.PROPOSAL_REVIEW.value,
        EventType.PROPOSAL_PRECHECK_REVIEW.value,
        EventType.DEBATE_REVIEW.value,
    }
)
