from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import threading
from typing import Callable
from uuid import uuid4

from awe_agentcheck.observability import get_logger
from awe_agentcheck.task_options import normalize_memory_mode, normalize_phase_timeout_seconds

_log = get_logger('awe_agentcheck.service_layers.memory')

_TOKEN_RE = re.compile(r'[A-Za-z0-9_./-]+')
_DEFAULT_STAGE_SEQUENCE = ('proposal', 'discussion', 'implementation', 'review')
_DEFAULT_MAX_CONTENT_CHARS = 280
_SUPPORTED_MEMORY_TYPES = frozenset({'session', 'preference', 'semantic', 'failure'})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(value: datetime | None = None) -> str:
    target = value or _utc_now()
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    else:
        target = target.astimezone(timezone.utc)
    return target.isoformat()


def _normalize_project_key(value: str | None) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    return text.replace('\\', '/').rstrip('/').lower()


def _clip_text(value: object, *, max_chars: int = _DEFAULT_MAX_CONTENT_CHARS) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    if len(text) <= max_chars:
        return text
    return f'{text[: max_chars - 3].rstrip()}...'


def _tokenize(value: str) -> set[str]:
    text = str(value or '').lower()
    if not text:
        return set()
    return {token for token in _TOKEN_RE.findall(text) if len(token) >= 2}


def _safe_float(value: object, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _safe_int(value: object, *, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _parse_iso(value: object) -> datetime | None:
    text = str(value or '').strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class MemoryDeps:
    list_events: Callable[[str], list[dict]]
    read_artifact_json: Callable[[str, str], dict | None]


class MemoryService:
    def __init__(self, *, artifact_root: Path, deps: MemoryDeps):
        self.root = Path(artifact_root).resolve(strict=False) / 'memory'
        self.root.mkdir(parents=True, exist_ok=True)
        self._entries_path = self.root / 'entries.json'
        self._lock = threading.Lock()
        self._deps = deps

    def list_entries(
        self,
        *,
        project_path: str | None = None,
        memory_type: str | None = None,
        include_expired: bool = False,
        limit: int = 200,
    ) -> list[dict]:
        project_key = _normalize_project_key(project_path)
        kind = str(memory_type or '').strip().lower() or None
        entries = self._load_entries(clean_expired=not include_expired)
        out: list[dict] = []
        for entry in entries:
            if kind and str(entry.get('memory_type') or '') != kind:
                continue
            if project_key:
                scope = _normalize_project_key(entry.get('project_path'))
                if scope and scope != project_key:
                    continue
            out.append(self._entry_for_response(entry))
        out.sort(key=lambda item: str(item.get('updated_at') or ''), reverse=True)
        return out[: max(1, int(limit))]

    def query_entries(
        self,
        *,
        query: str,
        memory_mode: str,
        project_path: str | None = None,
        stage: str | None = None,
        limit: int = 8,
    ) -> list[dict]:
        mode = normalize_memory_mode(memory_mode)
        if mode == 'off':
            return []

        query_text = str(query or '').strip()
        query_tokens = _tokenize(query_text)
        project_key = _normalize_project_key(project_path)
        stage_key = str(stage or '').strip().lower()
        entries = self._load_entries(clean_expired=True)
        now = _utc_now()
        scored: list[tuple[float, dict]] = []

        min_confidence = 0.65 if mode == 'strict' else 0.30
        for entry in entries:
            confidence = _safe_float(entry.get('confidence'), default=0.0)
            if confidence < min_confidence:
                continue
            entry_project = _normalize_project_key(entry.get('project_path'))
            if project_key and entry_project and entry_project != project_key:
                continue

            text_blob = '\n'.join(
                [
                    str(entry.get('title') or ''),
                    str(entry.get('content') or ''),
                    ' '.join(str(v) for v in list(entry.get('tags') or [])),
                ]
            )
            text_tokens = _tokenize(text_blob)
            overlap = 0.0
            if query_tokens:
                overlap = len(query_tokens & text_tokens) / max(1, len(query_tokens))
            elif text_tokens:
                overlap = 0.15
            if mode == 'strict' and overlap <= 0.0:
                continue

            created = _parse_iso(entry.get('created_at'))
            age_days = 999.0
            if created is not None:
                age_days = max(0.0, (now - created).total_seconds() / 86_400.0)
            recency = max(0.0, 1.0 - (age_days / 120.0))

            preferred_stages = [
                str(v).strip().lower()
                for v in list(entry.get('preferred_stages') or [])
                if str(v).strip()
            ]
            stage_boost = 0.0
            if stage_key and stage_key in preferred_stages:
                stage_boost = 0.18

            project_boost = 0.0
            if project_key and entry_project and entry_project == project_key:
                project_boost = 0.15

            evidence_paths = [str(v).strip() for v in list(entry.get('evidence_paths') or []) if str(v).strip()]
            evidence_boost = 0.08 if evidence_paths else 0.0

            score = (
                overlap * 0.52
                + confidence * 0.20
                + recency * 0.12
                + stage_boost
                + project_boost
                + evidence_boost
            )
            if mode == 'strict' and score < 0.40:
                continue
            if score <= 0:
                continue
            scored.append((score, entry))

        scored.sort(key=lambda item: item[0], reverse=True)
        out: list[dict] = []
        for score, entry in scored[: max(1, int(limit))]:
            payload = self._entry_for_response(entry)
            payload['score'] = round(float(score), 4)
            out.append(payload)
        return out

    def build_stage_context(
        self,
        *,
        row: dict,
        query_text: str,
        memory_mode: str,
        stage_sequence: tuple[str, ...] | list[str] | None = None,
        limit_per_stage: int = 3,
    ) -> dict:
        mode = normalize_memory_mode(memory_mode)
        project_path = str(row.get('project_path') or row.get('workspace_path') or '').strip()
        stages = tuple(stage_sequence or _DEFAULT_STAGE_SEQUENCE)
        contexts: dict[str, str] = {}
        hits: dict[str, list[dict]] = {}
        if mode == 'off':
            return {'mode': mode, 'contexts': contexts, 'hits': hits}

        base_query = str(query_text or '').strip()
        for stage in stages:
            stage_key = str(stage or '').strip().lower()
            if not stage_key:
                continue
            stage_query = base_query
            if stage_key == 'review':
                stage_query = f'{base_query} blocker regression evidence'
            elif stage_key == 'implementation':
                stage_query = f'{base_query} fix patch verification'
            elif stage_key in {'proposal', 'discussion'}:
                stage_query = f'{base_query} scope risk plan'

            top = self.query_entries(
                query=stage_query,
                memory_mode=mode,
                project_path=project_path,
                stage=stage_key,
                limit=limit_per_stage,
            )
            if not top:
                continue
            hits[stage_key] = top
            lines = [f'Memory recall for {stage_key} ({mode}):']
            for item in top:
                evidence_paths = list(item.get('evidence_paths') or [])
                evidence = evidence_paths[0] if evidence_paths else 'n/a'
                source_task = str(item.get('source_task_id') or 'n/a')
                conf = _safe_float(item.get('confidence'), default=0.0)
                summary = _clip_text(item.get('content'), max_chars=210)
                lines.append(
                    (
                        f"- [{item.get('memory_type')}] {item.get('title')} "
                        f"(conf={conf:.2f}, task={source_task}, evidence={evidence})"
                    )
                )
                if summary:
                    lines.append(f"  takeaway: {summary}")
            contexts[stage_key] = '\n'.join(lines).strip()
        return {'mode': mode, 'contexts': contexts, 'hits': hits}

    def persist_task_preferences(self, *, row: dict) -> dict | None:
        project_path = str(row.get('project_path') or row.get('workspace_path') or '').strip()
        if not project_path:
            return None
        project_key = _normalize_project_key(project_path)
        if not project_key:
            return None

        defaults = {
            'memory_mode': normalize_memory_mode(row.get('memory_mode', 'basic')),
            'repair_mode': str(row.get('repair_mode') or 'balanced').strip().lower() or 'balanced',
            'evolution_level': max(0, min(3, _safe_int(row.get('evolution_level'), default=0))),
            'self_loop_mode': max(0, min(1, _safe_int(row.get('self_loop_mode'), default=0))),
            'debate_mode': bool(row.get('debate_mode', True)),
            'auto_merge': bool(row.get('auto_merge', True)),
            'max_rounds': max(1, _safe_int(row.get('max_rounds'), default=1)),
            'phase_timeout_seconds': normalize_phase_timeout_seconds(row.get('phase_timeout_seconds'), strict=False),
        }
        summary = (
            f"memory={defaults['memory_mode']}, repair={defaults['repair_mode']}, "
            f"evolution={defaults['evolution_level']}, self_loop={defaults['self_loop_mode']}, "
            f"debate={1 if defaults['debate_mode'] else 0}, auto_merge={1 if defaults['auto_merge'] else 0}, "
            f"rounds={defaults['max_rounds']}"
        )

        with self._lock:
            entries = self._load_entries(clean_expired=True, lock_held=True)
            now_text = _utc_iso()
            existing = None
            for entry in entries:
                if str(entry.get('memory_type') or '') != 'preference':
                    continue
                if str(entry.get('scope') or '') != 'project':
                    continue
                if _normalize_project_key(entry.get('project_path')) != project_key:
                    continue
                if str(entry.get('title') or '').strip().lower() != 'project preference snapshot':
                    continue
                existing = entry
                break
            if existing is None:
                existing = {
                    'memory_id': f'mem-{uuid4().hex[:12]}',
                    'memory_type': 'preference',
                    'scope': 'project',
                    'project_path': project_path,
                    'title': 'Project preference snapshot',
                    'content': summary,
                    'tags': ['preference', 'policy'],
                    'evidence_paths': [],
                    'source_task_id': str(row.get('task_id') or '').strip() or None,
                    'confidence': 0.55,
                    'preferred_stages': ['proposal', 'discussion', 'implementation', 'review'],
                    'metadata': {'defaults': defaults},
                    'created_at': now_text,
                    'updated_at': now_text,
                    'expires_at': None,
                    'pinned': False,
                }
                entries.append(existing)
            else:
                existing['content'] = summary
                existing['metadata'] = {'defaults': defaults}
                existing['updated_at'] = now_text
                source_task_id = str(row.get('task_id') or '').strip()
                if source_task_id:
                    existing['source_task_id'] = source_task_id
            self._save_entries(entries, lock_held=True)
            return self._entry_for_response(existing)

    def persist_task_outcome(
        self,
        *,
        task_id: str,
        row: dict,
        status: str,
        reason: str | None,
    ) -> list[dict]:
        task_key = str(task_id or '').strip()
        if not task_key:
            return []
        status_text = str(status or '').strip().lower() or 'unknown'
        reason_text = str(reason or '').strip() or 'n/a'
        project_path = str(row.get('project_path') or row.get('workspace_path') or '').strip()
        title = str(row.get('title') or '').strip() or task_key
        description = str(row.get('description') or '').strip()

        events = self._safe_list_events(task_key)
        evidence_manifest = self._deps.read_artifact_json(task_key, 'evidence_manifest')
        auto_merge_summary = self._deps.read_artifact_json(task_key, 'auto_merge_summary')

        highlights = self._extract_highlights_from_events(events, limit=6)
        evidence_paths = self._extract_evidence_paths(evidence_manifest)
        if not evidence_paths:
            evidence_paths = self._extract_evidence_paths_from_events(events, limit=8)

        now = _utc_now()
        now_text = _utc_iso(now)
        entries: list[dict] = []

        session_ttl = now + timedelta(hours=72)
        session_entry = {
            'memory_id': f'mem-{uuid4().hex[:12]}',
            'memory_type': 'session',
            'scope': 'project',
            'project_path': project_path or None,
            'title': f'{title} [{status_text}]',
            'content': _clip_text(
                (
                    f'reason={reason_text}; rounds={_safe_int(row.get("rounds_completed"), default=0)}; '
                    f'notes={"; ".join(highlights) if highlights else "n/a"}'
                ),
                max_chars=420,
            ),
            'tags': ['session', status_text],
            'evidence_paths': evidence_paths[:6],
            'source_task_id': task_key,
            'confidence': 0.58 if status_text == 'passed' else 0.52,
            'preferred_stages': ['proposal', 'discussion'],
            'metadata': {
                'reason': reason_text,
                'status': status_text,
            },
            'created_at': now_text,
            'updated_at': now_text,
            'expires_at': _utc_iso(session_ttl),
            'pinned': False,
        }
        entries.append(session_entry)

        if status_text == 'passed':
            semantic_confidence = 0.9 if evidence_paths else 0.72
            semantic_entry = {
                'memory_id': f'mem-{uuid4().hex[:12]}',
                'memory_type': 'semantic',
                'scope': 'project',
                'project_path': project_path or None,
                'title': f'Proven pattern: {title}',
                'content': _clip_text(
                    (
                        f'Passed with reason={reason_text}. '
                        f'Validated highlights: {"; ".join(highlights) if highlights else "n/a"}. '
                        f'Description: {description}'
                    ),
                    max_chars=680,
                ),
                'tags': ['passed', 'semantic', 'verified'],
                'evidence_paths': evidence_paths[:10],
                'source_task_id': task_key,
                'confidence': semantic_confidence,
                'preferred_stages': ['proposal', 'implementation', 'review'],
                'metadata': {
                    'reason': reason_text,
                    'status': status_text,
                    'auto_merge': bool(auto_merge_summary),
                    'changed_files': len(list(auto_merge_summary.get('changed_files') or []))
                    if isinstance(auto_merge_summary, dict)
                    else 0,
                },
                'created_at': now_text,
                'updated_at': now_text,
                'expires_at': None,
                'pinned': False,
            }
            entries.append(semantic_entry)
        elif status_text in {'failed_gate', 'failed_system', 'canceled'}:
            failure_ttl = now + timedelta(days=45)
            failure_entry = {
                'memory_id': f'mem-{uuid4().hex[:12]}',
                'memory_type': 'failure',
                'scope': 'project',
                'project_path': project_path or None,
                'title': f'Failure pattern: {reason_text}',
                'content': _clip_text(
                    (
                        f'Task failed with status={status_text}, reason={reason_text}. '
                        f'Observed signals: {"; ".join(highlights) if highlights else "n/a"}'
                    ),
                    max_chars=620,
                ),
                'tags': ['failure', status_text],
                'evidence_paths': evidence_paths[:8],
                'source_task_id': task_key,
                'confidence': 0.75,
                'preferred_stages': ['proposal', 'review'],
                'metadata': {'reason': reason_text, 'status': status_text},
                'created_at': now_text,
                'updated_at': now_text,
                'expires_at': _utc_iso(failure_ttl),
                'pinned': False,
            }
            entries.append(failure_entry)

        with self._lock:
            existing = self._load_entries(clean_expired=True, lock_held=True)
            existing.extend(entries)
            self._save_entries(existing, lock_held=True)
        return [self._entry_for_response(item) for item in entries]

    def set_pinned(self, *, memory_id: str, pinned: bool) -> dict | None:
        target = str(memory_id or '').strip()
        if not target:
            return None
        with self._lock:
            entries = self._load_entries(clean_expired=False, lock_held=True)
            selected = None
            for entry in entries:
                if str(entry.get('memory_id') or '') != target:
                    continue
                entry['pinned'] = bool(pinned)
                entry['updated_at'] = _utc_iso()
                selected = entry
                break
            if selected is None:
                return None
            self._save_entries(entries, lock_held=True)
            return self._entry_for_response(selected)

    def clear_entries(
        self,
        *,
        project_path: str | None = None,
        memory_type: str | None = None,
        include_pinned: bool = False,
    ) -> dict[str, int]:
        project_key = _normalize_project_key(project_path)
        kind = str(memory_type or '').strip().lower() or None
        deleted = 0
        with self._lock:
            entries = self._load_entries(clean_expired=False, lock_held=True)
            kept: list[dict] = []
            for entry in entries:
                if kind and str(entry.get('memory_type') or '') != kind:
                    kept.append(entry)
                    continue
                if project_key:
                    entry_project = _normalize_project_key(entry.get('project_path'))
                    if entry_project and entry_project != project_key:
                        kept.append(entry)
                        continue
                if (not include_pinned) and bool(entry.get('pinned', False)):
                    kept.append(entry)
                    continue
                deleted += 1
            self._save_entries(kept, lock_held=True)
        return {'deleted': int(deleted), 'remaining': max(0, len(kept))}

    def _safe_list_events(self, task_id: str) -> list[dict]:
        try:
            return list(self._deps.list_events(task_id))
        except Exception:
            _log.exception('memory_list_events_failed task_id=%s', task_id)
            return []

    @staticmethod
    def _extract_highlights_from_events(events: list[dict], *, limit: int) -> list[str]:
        out: list[str] = []
        for event in reversed(list(events or [])):
            etype = str(event.get('type') or '').strip().lower()
            if etype not in {
                'review',
                'proposal_review',
                'proposal_precheck_review',
                'gate_failed',
                'gate_passed',
                'system_failure',
                'precompletion_checklist',
            }:
                continue
            payload = event.get('payload') if isinstance(event.get('payload'), dict) else {}
            raw = str(payload.get('output') or payload.get('reason') or '').strip()
            if not raw:
                continue
            line = _clip_text(raw.replace('\n', ' '), max_chars=180)
            if line and line not in out:
                out.append(line)
            if len(out) >= max(1, int(limit)):
                break
        return list(reversed(out))

    @staticmethod
    def _extract_evidence_paths(evidence_manifest: dict | None) -> list[str]:
        if not isinstance(evidence_manifest, dict):
            return []
        refs = list(evidence_manifest.get('artifact_refs') or [])
        out: list[str] = []
        seen: set[str] = set()
        for item in refs:
            if not isinstance(item, dict):
                continue
            path = str(item.get('path') or '').strip()
            if not path:
                continue
            key = path.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(path)
        evidence_bundle = evidence_manifest.get('evidence_bundle')
        if isinstance(evidence_bundle, dict):
            for raw in list(evidence_bundle.get('evidence_paths') or []):
                path = str(raw or '').strip()
                if not path:
                    continue
                key = path.lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(path)
        return out

    @staticmethod
    def _extract_evidence_paths_from_events(events: list[dict], *, limit: int) -> list[str]:
        pattern = re.compile(r'(?:[A-Za-z]:[\\/])?[A-Za-z0-9._\\/-]+\.[A-Za-z0-9]{1,8}')
        out: list[str] = []
        seen: set[str] = set()
        for event in reversed(list(events or [])):
            payload = event.get('payload') if isinstance(event.get('payload'), dict) else {}
            text = str(payload.get('output') or payload.get('reason') or '')
            for raw in pattern.findall(text):
                path = str(raw or '').strip().strip('.,;:()[]{}<>"\'')
                if not path:
                    continue
                key = path.replace('\\', '/').lower()
                if key in seen:
                    continue
                seen.add(key)
                out.append(path.replace('\\', '/'))
                if len(out) >= max(1, int(limit)):
                    return list(reversed(out))
        return list(reversed(out))

    @staticmethod
    def _entry_for_response(entry: dict) -> dict:
        payload = {
            'memory_id': str(entry.get('memory_id') or ''),
            'memory_type': str(entry.get('memory_type') or 'session'),
            'scope': str(entry.get('scope') or 'project'),
            'project_path': str(entry.get('project_path') or '').strip() or None,
            'title': str(entry.get('title') or '').strip(),
            'content': str(entry.get('content') or '').strip(),
            'tags': [str(v).strip() for v in list(entry.get('tags') or []) if str(v).strip()],
            'evidence_paths': [str(v).strip() for v in list(entry.get('evidence_paths') or []) if str(v).strip()],
            'source_task_id': str(entry.get('source_task_id') or '').strip() or None,
            'confidence': round(_safe_float(entry.get('confidence'), default=0.0), 3),
            'preferred_stages': [
                str(v).strip().lower()
                for v in list(entry.get('preferred_stages') or [])
                if str(v).strip()
            ],
            'metadata': dict(entry.get('metadata') or {}),
            'created_at': str(entry.get('created_at') or _utc_iso()),
            'updated_at': str(entry.get('updated_at') or str(entry.get('created_at') or _utc_iso())),
            'expires_at': str(entry.get('expires_at') or '').strip() or None,
            'pinned': bool(entry.get('pinned', False)),
        }
        return payload

    def _load_entries(self, *, clean_expired: bool, lock_held: bool = False) -> list[dict]:
        if not lock_held:
            with self._lock:
                return self._load_entries(clean_expired=clean_expired, lock_held=True)

        if not self._entries_path.exists():
            return []
        try:
            raw = self._entries_path.read_text(encoding='utf-8').strip()
        except OSError:
            _log.exception('memory_read_failed path=%s', str(self._entries_path))
            return []
        if not raw:
            return []
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            _log.warning('memory_json_decode_failed path=%s', str(self._entries_path))
            return []
        if not isinstance(payload, list):
            return []

        out: list[dict] = []
        dirty = False
        now = _utc_now()
        for item in payload:
            if not isinstance(item, dict):
                dirty = True
                continue
            entry = self._entry_for_response(item)
            if clean_expired and self._is_expired(entry, now=now) and (not bool(entry.get('pinned', False))):
                dirty = True
                continue
            if str(entry.get('memory_type') or '') not in _SUPPORTED_MEMORY_TYPES:
                dirty = True
                continue
            out.append(entry)
        if dirty:
            self._save_entries(out, lock_held=True)
        return out

    def _save_entries(self, entries: list[dict], *, lock_held: bool = False) -> None:
        if not lock_held:
            with self._lock:
                self._save_entries(entries, lock_held=True)
                return
        safe_entries = [self._entry_for_response(item if isinstance(item, dict) else {}) for item in entries]
        tmp = self._entries_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(safe_entries, ensure_ascii=True, indent=2), encoding='utf-8')
        tmp.replace(self._entries_path)

    @staticmethod
    def _is_expired(entry: dict, *, now: datetime) -> bool:
        exp = _parse_iso(entry.get('expires_at'))
        if exp is None:
            return False
        return exp <= now
