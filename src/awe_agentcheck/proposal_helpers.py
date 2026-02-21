from __future__ import annotations

import hashlib
import re

from awe_agentcheck.domain.models import ReviewVerdict
from awe_agentcheck.workflow import RunConfig, WorkflowEngine
from awe_agentcheck.workflow_prompting import inject_prompt_extras
from awe_agentcheck.workflow_text import clip_text

PROPOSAL_STALL_RETRY_LIMIT = 10
PROPOSAL_REPEAT_ROUNDS_LIMIT = 4


def proposal_review_prompt(
    config: RunConfig,
    discussion_output: str,
    *,
    stage: str = 'proposal_review',
    environment_context: str | None = None,
    memory_context: str | None = None,
) -> str:
    clipped = clip_text(discussion_output, max_chars=2500)
    language_instruction = WorkflowEngine._conversation_language_instruction(config.conversation_language)
    plain_instruction = WorkflowEngine._plain_mode_instruction(bool(config.plain_mode))
    plain_review_format = WorkflowEngine._plain_review_format_instruction(
        enabled=bool(config.plain_mode),
        language=config.conversation_language,
    )
    control_schema_instruction = WorkflowEngine._control_output_schema_instruction()
    checklist_guidance = WorkflowEngine._review_checklist_guidance(config.evolution_level)
    stage_text = str(stage or 'proposal_review').strip().lower()
    audit_intent = is_audit_intent(config)
    stage_guidance = (
        "Stage: precheck. Build a concrete review scope."
        " For audit/discovery tasks, run repository checks as needed and cite concrete evidence."
        " Then summarize findings for author discussion."
        if stage_text == 'proposal_precheck_review'
        else "Stage: proposal review. Evaluate the updated proposal and unresolved risks."
    )
    scope_guidance = (
        "If user request is broad, do not block only for broad wording."
        " Convert it into concrete review scope, checks, and priorities, then continue."
    )
    depth_guidance = (
        "Task mode is audit/discovery: run repository checks as needed and cite evidence."
        if audit_intent
        else "Keep checks focused on current feature scope and known risk paths."
    )
    base = WorkflowEngine._render_prompt_template(
        'proposal_review_prompt.txt',
        task_title=config.title,
        language_instruction=language_instruction,
        plain_instruction=plain_instruction,
        stage_guidance=stage_guidance,
        scope_guidance=scope_guidance,
        depth_guidance=depth_guidance,
        checklist_guidance=checklist_guidance,
        control_schema_instruction=control_schema_instruction,
        plain_review_format=plain_review_format,
        plan_text=clipped,
    )
    return inject_prompt_extras(
        base=base,
        environment_context=environment_context,
        strategy_hint=None,
        memory_context=memory_context,
    )


def review_timeout_seconds(participant_timeout_seconds: int) -> int:
    return max(1, int(participant_timeout_seconds))


def proposal_author_prompt(
    config: RunConfig,
    merged_context: str,
    review_payload: list[dict],
    *,
    environment_context: str | None = None,
    memory_context: str | None = None,
) -> str:
    clipped = clip_text(merged_context, max_chars=3200)
    language_instruction = WorkflowEngine._conversation_language_instruction(config.conversation_language)
    plain_instruction = WorkflowEngine._plain_mode_instruction(bool(config.plain_mode))
    level = max(0, min(3, int(config.evolution_level)))
    no_blocker = sum(1 for item in review_payload if str(item.get('verdict')) == ReviewVerdict.NO_BLOCKER.value)
    blocker = sum(1 for item in review_payload if str(item.get('verdict')) == ReviewVerdict.BLOCKER.value)
    unknown = sum(1 for item in review_payload if str(item.get('verdict')) == ReviewVerdict.UNKNOWN.value)
    if level >= 3:
        author_scope_guidance = (
            "Primary plan must still map to reviewer findings and user intent. "
            "You may append 1-3 optional proactive evolution candidates if they are low-risk and testable."
        )
        evolution_author_guidance = (
            "For each optional candidate, include impact/risk/effort and a concrete verification path."
        )
    else:
        author_scope_guidance = (
            "Do not invent unrelated changes. "
            "Only propose changes that map to reviewer findings and user intent."
        )
        evolution_author_guidance = ""
    audit_author_guidance = (
        "This is audit/discovery intent. Convert reviewer findings into a concrete execution plan: "
        "scope(files/modules), checks/tests, expected outputs, and stop conditions."
        if is_audit_intent(config)
        else "Keep proposal concrete and implementation-ready."
    )
    base = WorkflowEngine._render_prompt_template(
        'proposal_author_prompt.txt',
        task_title=config.title,
        language_instruction=language_instruction,
        plain_instruction=plain_instruction,
        no_blocker=no_blocker,
        blocker=blocker,
        unknown=unknown,
        author_scope_guidance=author_scope_guidance,
        evolution_author_guidance=evolution_author_guidance,
        audit_author_guidance=audit_author_guidance,
        context_text=clipped,
    )
    return inject_prompt_extras(
        base=base,
        environment_context=environment_context,
        strategy_hint=None,
        memory_context=memory_context,
    )


def is_audit_intent(config: RunConfig) -> bool:
    text = f"{str(config.title or '')}\n{str(config.description or '')}".lower()
    if not text.strip():
        return False
    keywords = (
        'audit',
        'review',
        'inspect',
        'scan',
        'check',
        'bug',
        'bugs',
        'vulnerability',
        'vulnerabilities',
        'security',
        'hardening',
        'improve',
        'improvement',
        'quality',
        'refine',
        '漏洞',
        '缺陷',
        '错误',
        '检查',
        '审查',
        '评审',
        '改进',
        '优化',
        '完善',
        '风险',
    )
    return any(k in text for k in keywords)


def looks_like_scope_ambiguity(review_text: str) -> bool:
    text = str(review_text or '').lower()
    if not text:
        return False
    hints = (
        'too vague',
        'vague',
        'unclear scope',
        'not specific',
        'no specific bug',
        '缺少具体',
        '太模糊',
        '不明确',
        '没有具体',
        '先说明具体',
        '无法判断改动',
    )
    return any(h in text for h in hints)


def looks_like_hard_risk(review_text: str) -> bool:
    text = str(review_text or '').lower()
    if not text:
        return False
    hints = (
        'data loss',
        'destructive',
        'unsafe',
        'critical',
        'high risk',
        'regression',
        'security risk',
        'sql injection',
        'rce',
        '权限',
        '数据丢失',
        '高风险',
        '严重',
        '回归',
        '安全风险',
    )
    return any(h in text for h in hints)


def normalize_proposal_reviewer_result(
    *,
    config: RunConfig,
    stage: str,
    verdict: ReviewVerdict,
    review_text: str,
) -> tuple[ReviewVerdict, str]:
    stage_text = str(stage or '').strip().lower()
    if stage_text not in {'proposal_precheck_review', 'proposal_review'}:
        return verdict, review_text
    if verdict not in {ReviewVerdict.UNKNOWN, ReviewVerdict.BLOCKER}:
        return verdict, review_text
    if not looks_like_scope_ambiguity(review_text):
        return verdict, review_text
    if looks_like_hard_risk(review_text):
        return verdict, review_text

    guidance = (
        "[system_note] Scope ambiguity is non-blocking: reviewer must convert broad user intent into "
        "concrete scope (files/modules), checks, and priorities, then continue."
    )
    merged = str(review_text or '').strip()
    if guidance not in merged:
        merged = f"{merged}\n\n{guidance}".strip()
    return ReviewVerdict.NO_BLOCKER, merged


def append_proposal_feedback_context(base_text: str, *, reviewer_id: str, review_text: str) -> str:
    seed = str(base_text or '').strip()
    note = str(review_text or '').strip()
    if not note:
        return seed
    merged = f"{seed}\n\n[reviewer:{reviewer_id}]\n{note}".strip()
    return clip_text(merged, max_chars=4500)


def proposal_verdict_counts(review_payload: list[dict]) -> tuple[int, int, int]:
    no_blocker = sum(1 for item in review_payload if str(item.get('verdict')) == ReviewVerdict.NO_BLOCKER.value)
    blocker = sum(1 for item in review_payload if str(item.get('verdict')) == ReviewVerdict.BLOCKER.value)
    unknown = sum(1 for item in review_payload if str(item.get('verdict')) == ReviewVerdict.UNKNOWN.value)
    return no_blocker, blocker, unknown


def proposal_consensus_reached(review_payload: list[dict], *, expected_reviewers: int) -> bool:
    if expected_reviewers <= 0:
        return True
    if len(review_payload) < expected_reviewers:
        return False
    return all(str(item.get('verdict')) == ReviewVerdict.NO_BLOCKER.value for item in review_payload[:expected_reviewers])


def proposal_review_usable_count(review_payload: list[dict]) -> int:
    usable = 0
    for item in review_payload:
        if is_actionable_proposal_review_text(str(item.get('output') or '')):
            usable += 1
    return usable


def proposal_round_signature(review_payload: list[dict], *, proposal_text: str) -> str:
    parts: list[str] = []
    for item in review_payload:
        participant = str(item.get('participant') or '').strip().lower()
        verdict = str(item.get('verdict') or '').strip().lower()
        text = str(item.get('output') or '').strip().lower()
        text = re.sub(r'\s+', ' ', text)
        if len(text) > 300:
            text = text[:300]
        parts.append(f'{participant}|{verdict}|{text}')

    proposal = re.sub(r'\s+', ' ', str(proposal_text or '').strip().lower())
    if len(proposal) > 200:
        proposal = proposal[:200]
    payload = '\n'.join(sorted(parts) + [f'proposal|{proposal}'])
    if not payload.strip():
        return ''
    return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


def is_actionable_proposal_review_text(text: str) -> bool:
    payload = str(text or '').strip()
    if not payload:
        return False
    lowered = payload.lower()
    if lowered.startswith('[proposal_precheck_review_error]'):
        return False
    if lowered.startswith('[proposal_review_error]'):
        return False
    if 'command_timeout provider=' in lowered:
        return False
    if 'provider_limit provider=' in lowered:
        return False
    if 'command_not_found provider=' in lowered:
        return False
    if 'command_failed provider=' in lowered:
        return False
    if 'command_not_configured provider=' in lowered:
        return False
    return True
