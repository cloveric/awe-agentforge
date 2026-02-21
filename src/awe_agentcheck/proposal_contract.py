from __future__ import annotations

import json
import re

from awe_agentcheck.workflow_text import clip_text

_ISSUE_ID_RE = re.compile(r'^\s*ISSUE[-_ ]?([0-9]{1,4})\s*$', re.IGNORECASE)
_ISSUE_ID_SCAN_RE = re.compile(r'\bISSUE[-_ ]?([0-9]{1,4})\b', re.IGNORECASE)
_FENCED_JSON_RE = re.compile(r'```(?:json)?\s*(\{.*?\})\s*```', re.IGNORECASE | re.DOTALL)


def _normalize_issue_id(value: object, *, fallback_index: int) -> str:
    text = str(value or '').strip().upper().replace('_', '-').replace(' ', '-')
    if text:
        match = _ISSUE_ID_RE.match(text)
        if match:
            return f'ISSUE-{int(match.group(1)):03d}'
        scan = _ISSUE_ID_SCAN_RE.search(text)
        if scan:
            return f'ISSUE-{int(scan.group(1)):03d}'
    return f'ISSUE-{int(fallback_index):03d}'


def _coerce_text(value: object, *, max_chars: int) -> str:
    return clip_text(str(value or '').strip(), max_chars=max_chars)


def _coerce_string_list(value: object, *, max_items: int, max_chars: int) -> list[str]:
    if isinstance(value, str):
        raw_items = [value]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        text = _coerce_text(raw, max_chars=max_chars)
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
        if len(out) >= max_items:
            break
    return out


def _iter_json_candidates(output: str) -> list[str]:
    text = str(output or '').strip()
    if not text:
        return []
    candidates: list[str] = [text]
    for match in _FENCED_JSON_RE.finditer(text):
        payload = str(match.group(1) or '').strip()
        if payload:
            candidates.append(payload)
    for line in text.splitlines():
        line_text = str(line or '').strip()
        if line_text.startswith('{') and line_text.endswith('}'):
            candidates.append(line_text)
    out: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _parse_json_object(output: str) -> dict | None:
    for candidate in _iter_json_candidates(output):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def parse_reviewer_issues(*, output: str, verdict: str) -> list[dict]:
    verdict_text = str(verdict or '').strip().lower()
    lowered_output = str(output or '').strip().lower()
    payload = _parse_json_object(output) or {}
    raw_issues = payload.get('issues')
    out: list[dict] = []
    if isinstance(raw_issues, list):
        for idx, item in enumerate(raw_issues, start=1):
            if not isinstance(item, dict):
                continue
            issue_id = _normalize_issue_id(item.get('issue_id'), fallback_index=idx)
            summary = _coerce_text(item.get('summary') or item.get('issue') or item.get('title'), max_chars=220)
            if not summary:
                continue
            out.append(
                {
                    'issue_id': issue_id,
                    'summary': summary,
                    'severity': _coerce_text(item.get('severity') or verdict_text or 'unknown', max_chars=32).lower(),
                    'required_action': _coerce_text(item.get('required_action') or item.get('next'), max_chars=220),
                    'evidence_paths': _coerce_string_list(item.get('evidence_paths'), max_items=8, max_chars=180),
                    'required_response': bool(item.get('required_response', verdict_text in {'blocker', 'unknown'})),
                }
            )

    runtime_error_hint = (
        lowered_output.startswith('[proposal_precheck_review_error]')
        or lowered_output.startswith('[proposal_review_error]')
        or 'provider_limit provider=' in lowered_output
        or 'command_timeout provider=' in lowered_output
        or 'command_not_found provider=' in lowered_output
        or 'command_failed provider=' in lowered_output
        or 'command_not_configured provider=' in lowered_output
    )
    if not out and verdict_text in {'blocker', 'unknown'} and not runtime_error_hint:
        explicit_id = _ISSUE_ID_SCAN_RE.search(str(output or ''))
        if explicit_id:
            fallback_id = f'ISSUE-{int(explicit_id.group(1)):03d}'
            fallback_summary = _coerce_text(payload.get('issue') or output, max_chars=220)
            if fallback_summary:
                out.append(
                    {
                        'issue_id': fallback_id,
                        'summary': fallback_summary,
                        'severity': verdict_text,
                        'required_action': _coerce_text(payload.get('next') or payload.get('required_action'), max_chars=220),
                        'evidence_paths': _coerce_string_list(payload.get('evidence_paths'), max_items=8, max_chars=180),
                        'required_response': True,
                    }
                )

    normalized: list[dict] = []
    seen_ids: set[str] = set()
    for idx, item in enumerate(out, start=1):
        issue_id = _normalize_issue_id(item.get('issue_id'), fallback_index=idx)
        if issue_id in seen_ids:
            continue
        seen_ids.add(issue_id)
        normalized.append(
            {
                'issue_id': issue_id,
                'summary': _coerce_text(item.get('summary'), max_chars=220),
                'severity': _coerce_text(item.get('severity') or 'unknown', max_chars=32).lower(),
                'required_action': _coerce_text(item.get('required_action'), max_chars=220),
                'evidence_paths': _coerce_string_list(item.get('evidence_paths'), max_items=8, max_chars=180),
                'required_response': bool(item.get('required_response', False)),
            }
        )
    return normalized


def extract_required_issue_ids(review_payload: list[dict]) -> list[str]:
    ids: set[str] = set()
    for item in list(review_payload or []):
        verdict = str(item.get('verdict') or '').strip().lower()
        issues = item.get('issues')
        if not isinstance(issues, list):
            issues = parse_reviewer_issues(output=str(item.get('output') or ''), verdict=verdict)
        for idx, issue in enumerate(issues, start=1):
            if not isinstance(issue, dict):
                continue
            issue_id = _normalize_issue_id(issue.get('issue_id'), fallback_index=idx)
            require = bool(issue.get('required_response', verdict in {'blocker', 'unknown'}))
            if require:
                ids.add(issue_id)
    return sorted(ids)


def validate_reviewer_issue_contract(review_payload: list[dict]) -> dict:
    missing_issue_participants: list[str] = []
    required_issue_ids: list[str] = []
    for item in list(review_payload or []):
        verdict = str(item.get('verdict') or '').strip().lower()
        participant = str(item.get('participant') or '').strip()
        issues = item.get('issues')
        if not isinstance(issues, list):
            issues = parse_reviewer_issues(output=str(item.get('output') or ''), verdict=verdict)
        if verdict in {'blocker', 'unknown'} and not issues:
            missing_issue_participants.append(participant or 'unknown')
        for issue in issues:
            if not isinstance(issue, dict):
                continue
            issue_id = _normalize_issue_id(issue.get('issue_id'), fallback_index=1)
            require = bool(issue.get('required_response', verdict in {'blocker', 'unknown'}))
            if require:
                required_issue_ids.append(issue_id)
    dedup_ids = sorted({v for v in required_issue_ids if v})
    missing = sorted({v for v in missing_issue_participants if v})
    return {
        'ok': len(missing) == 0,
        'required_issue_ids': dedup_ids,
        'missing_issue_participants': missing,
    }


def parse_author_issue_responses(output: str) -> dict[str, dict]:
    payload = _parse_json_object(output) or {}
    raw = payload.get('issue_responses')
    items: list[dict] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                items.append(item)
    if not items:
        # Fallback plain-text parse: "ISSUE-001: accept|reject|defer ..."
        for line in str(output or '').splitlines():
            line_text = str(line or '').strip()
            if not line_text:
                continue
            match = _ISSUE_ID_SCAN_RE.search(line_text)
            if not match:
                continue
            issue_id = f'ISSUE-{int(match.group(1)):03d}'
            lowered = line_text.lower()
            if ' reject' in lowered or lowered.endswith('reject'):
                status = 'reject'
            elif ' defer' in lowered or lowered.endswith('defer'):
                status = 'defer'
            elif ' accept' in lowered or lowered.endswith('accept'):
                status = 'accept'
            else:
                status = 'accept'
            items.append({'issue_id': issue_id, 'status': status})

    responses: dict[str, dict] = {}
    for idx, item in enumerate(items, start=1):
        issue_id = _normalize_issue_id(item.get('issue_id'), fallback_index=idx)
        status = _coerce_text(item.get('status') or 'accept', max_chars=24).lower()
        if status not in {'accept', 'reject', 'defer'}:
            status = 'accept'
        responses[issue_id] = {
            'issue_id': issue_id,
            'status': status,
            'reason': _coerce_text(item.get('reason'), max_chars=280),
            'alternative_plan': _coerce_text(item.get('alternative_plan'), max_chars=280),
            'validation_commands': _coerce_string_list(item.get('validation_commands'), max_items=6, max_chars=180),
            'evidence_paths': _coerce_string_list(item.get('evidence_paths'), max_items=10, max_chars=180),
        }
    return responses


def validate_author_issue_responses(*, required_issue_ids: list[str], responses: dict[str, dict]) -> dict:
    required = sorted({_normalize_issue_id(v, fallback_index=idx + 1) for idx, v in enumerate(required_issue_ids or [])})
    normalized: dict[str, dict] = {}
    for idx, (key, item) in enumerate(dict(responses or {}).items(), start=1):
        issue_id = _normalize_issue_id(key or item.get('issue_id'), fallback_index=idx)
        normalized[issue_id] = dict(item or {})
        normalized[issue_id]['issue_id'] = issue_id

    missing = [issue_id for issue_id in required if issue_id not in normalized]
    invalid_reject: list[str] = []
    unresolved: list[str] = []
    for issue_id in required:
        item = normalized.get(issue_id, {})
        status = str(item.get('status') or '').strip().lower()
        if status in {'reject', 'defer'}:
            unresolved.append(issue_id)
        if status == 'reject':
            has_reason = bool(str(item.get('reason') or '').strip())
            has_alt = bool(str(item.get('alternative_plan') or '').strip())
            has_commands = len(_coerce_string_list(item.get('validation_commands'), max_items=6, max_chars=180)) > 0
            has_evidence = len(_coerce_string_list(item.get('evidence_paths'), max_items=10, max_chars=180)) > 0
            if not (has_reason and has_alt and has_commands and has_evidence):
                invalid_reject.append(issue_id)

    return {
        'ok': len(missing) == 0 and len(invalid_reject) == 0,
        'discussion_complete': len(missing) == 0 and len(unresolved) == 0 and len(invalid_reject) == 0,
        'required_issue_ids': required,
        'missing_issue_ids': missing,
        'unresolved_issue_ids': sorted(set(unresolved)),
        'invalid_reject_issue_ids': sorted(set(invalid_reject)),
        'responses': normalized,
    }


def parse_review_issue_checks(*, output: str, required_issue_ids: list[str]) -> dict:
    required = sorted({_normalize_issue_id(v, fallback_index=idx + 1) for idx, v in enumerate(required_issue_ids or [])})
    if not required:
        return {
            'ok': True,
            'required_issue_ids': [],
            'covered_issue_ids': [],
            'missing_issue_ids': [],
            'unresolved_issue_ids': [],
            'issue_checks': [],
        }
    payload = _parse_json_object(output) or {}
    raw_checks = payload.get('issue_checks')
    if not isinstance(raw_checks, list):
        raw_checks = []
    checks: list[dict] = []
    covered: set[str] = set()
    unresolved: set[str] = set()
    for idx, item in enumerate(raw_checks, start=1):
        if not isinstance(item, dict):
            continue
        issue_id = _normalize_issue_id(item.get('issue_id'), fallback_index=idx)
        status = _coerce_text(item.get('status') or 'unknown', max_chars=24).lower()
        if status not in {'resolved', 'unresolved', 'unknown'}:
            status = 'unknown'
        checks.append(
            {
                'issue_id': issue_id,
                'status': status,
                'evidence_paths': _coerce_string_list(item.get('evidence_paths'), max_items=10, max_chars=180),
                'note': _coerce_text(item.get('note') or item.get('summary'), max_chars=220),
            }
        )
        if issue_id in required:
            covered.add(issue_id)
            if status != 'resolved':
                unresolved.add(issue_id)
    missing = sorted([v for v in required if v not in covered])
    unresolved_ids = sorted(unresolved)
    return {
        'ok': len(missing) == 0 and len(unresolved_ids) == 0,
        'required_issue_ids': required,
        'covered_issue_ids': sorted(covered),
        'missing_issue_ids': missing,
        'unresolved_issue_ids': unresolved_ids,
        'issue_checks': checks,
    }
