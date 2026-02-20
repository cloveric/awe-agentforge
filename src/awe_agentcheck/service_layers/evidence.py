from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Callable

from awe_agentcheck.domain.events import EventType
from awe_agentcheck.domain.models import TaskStatus


@dataclass(frozen=True)
class EvidenceDeps:
    validate_artifact_task_id: Callable[[str], str]
    validate_evidence_bundle: Callable[..., dict[str, object]]
    coerce_evidence_checks: Callable[[object], dict[str, object]]
    coerce_evidence_paths: Callable[[object], list[str]]


class EvidenceService:
    def __init__(self, *, repository, artifact_store, deps: EvidenceDeps):
        self.repository = repository
        self.artifact_store = artifact_store
        self._validate_artifact_task_id = deps.validate_artifact_task_id
        self._validate_evidence_bundle = deps.validate_evidence_bundle
        self._coerce_evidence_checks = deps.coerce_evidence_checks
        self._coerce_evidence_paths = deps.coerce_evidence_paths

    def collect_task_artifacts(self, *, task_id: str) -> list[dict]:
        key = self._validate_artifact_task_id(task_id)
        root = self.artifact_store.root / 'threads' / key
        if not root.exists() or not root.is_dir():
            return []
        out: list[dict] = []
        wanted = [
            ('state', root / 'state.json'),
            ('events', root / 'events.jsonl'),
            ('summary', root / 'summary.md'),
            ('final_report', root / 'final_report.md'),
            ('preflight_risk_gate', root / 'artifacts' / 'preflight_risk_gate.json'),
            ('auto_merge_summary', root / 'artifacts' / 'auto_merge_summary.json'),
            ('regression_case', root / 'artifacts' / 'regression_case.json'),
            ('pending_proposal', root / 'artifacts' / 'pending_proposal.json'),
            ('workspace_resume_guard', root / 'artifacts' / 'workspace_resume_guard.json'),
            ('precompletion_guard_failed', root / 'artifacts' / 'precompletion_guard_failed.json'),
        ]
        for name, path in wanted:
            if path.exists() and path.is_file():
                out.append({'name': name, 'path': str(path)})
        artifacts_root = root / 'artifacts'
        if artifacts_root.exists() and artifacts_root.is_dir():
            for candidate in sorted(artifacts_root.glob('evidence_bundle_round_*.json')):
                out.append({'name': candidate.stem, 'path': str(candidate)})
        return out

    def write_evidence_manifest(
        self,
        *,
        task_id: str,
        row: dict,
        workspace_root: Path,
        rounds_completed: int,
        status: str,
        reason: str,
        preflight_guard: dict | None,
        evidence_bundle: dict | None,
        head_snapshot: dict | None,
    ) -> dict[str, object]:
        bundle = dict(evidence_bundle or {})
        expected_round = max(1, int(rounds_completed))
        guard = self._validate_evidence_bundle(evidence_bundle=bundle, expected_round=expected_round)
        checks = self._coerce_evidence_checks(bundle.get('checks'))
        evidence_paths = self._coerce_evidence_paths(bundle.get('evidence_paths'))
        artifacts = self.collect_task_artifacts(task_id=task_id)
        payload: dict[str, object] = {
            'schema': 'evidence_manifest.v1',
            'task_id': task_id,
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'status': str(status or '').strip(),
            'reason': str(reason or '').strip() or 'unknown',
            'round': expected_round,
            'project_path': str(row.get('project_path') or row.get('workspace_path') or workspace_root),
            'workspace_path': str(workspace_root),
            'test_command': str(row.get('test_command') or '').strip(),
            'lint_command': str(row.get('lint_command') or '').strip(),
            'checks': checks,
            'evidence_paths': evidence_paths,
            'preflight': dict(preflight_guard or {}),
            'head_snapshot': dict(head_snapshot or {}),
            'artifact_refs': artifacts,
            'ok': bool(guard.get('ok', False)),
            'gate_reason': str(guard.get('reason') or 'precompletion_evidence_missing'),
        }
        try:
            manifest_path = self.artifact_store.write_artifact_json(
                task_id,
                name='evidence_manifest',
                payload=payload,
            )
        except Exception as exc:
            payload.update(
                {
                    'ok': False,
                    'reason': 'precompletion_evidence_missing',
                    'gate_reason': 'precompletion_evidence_missing',
                    'artifact_error': str(exc),
                }
            )
            return payload
        payload['artifact_path'] = str(manifest_path)
        if not bool(payload.get('ok', False)):
            payload['reason'] = str(payload.get('gate_reason') or 'precompletion_evidence_missing')
            return payload
        payload['reason'] = 'passed'
        return payload

    def emit_regression_case(
        self,
        *,
        task_id: str,
        row: dict,
        status: TaskStatus,
        reason: str,
    ) -> dict[str, object] | None:
        if status not in {TaskStatus.FAILED_GATE, TaskStatus.FAILED_SYSTEM}:
            return None
        reason_text = str(reason or '').strip()
        if not reason_text:
            return None

        project_root = Path(str(row.get('project_path') or row.get('workspace_path') or '')).resolve(strict=False)
        file_path = project_root / '.agents' / 'regressions' / 'failure_tasks.json'
        file_path.parent.mkdir(parents=True, exist_ok=True)

        rows: list[dict] = []
        if file_path.exists() and file_path.is_file():
            try:
                parsed = json.loads(file_path.read_text(encoding='utf-8'))
                if isinstance(parsed, list):
                    rows = [item for item in parsed if isinstance(item, dict)]
            except Exception:
                rows = []

        title_text = str(row.get('title') or '').strip()
        description_text = str(row.get('description') or '').strip()
        case_id_source = f'{title_text}\n{reason_text}'.lower()
        case_id = hashlib.sha256(case_id_source.encode('utf-8')).hexdigest()[:16]
        now = datetime.now(timezone.utc).isoformat()
        case_payload = {
            'id': f'failure-{case_id}',
            'title': f'Regression: {title_text[:96]}'.strip(),
            'description': (
                f'Failure reason: {reason_text}\n'
                f'Original task: {task_id}\n'
                f'Original description: {description_text}'
            ).strip(),
            'source_task_id': task_id,
            'source_status': status.value,
            'source_reason': reason_text,
            'updated_at': now,
        }
        merged = False
        for existing in rows:
            if str(existing.get('id') or '').strip() != case_payload['id']:
                continue
            existing.update(case_payload)
            merged = True
            break
        if not merged:
            rows.append(case_payload)

        try:
            file_path.write_text(json.dumps(rows, ensure_ascii=True, indent=2), encoding='utf-8')
        except Exception:
            return None

        event_payload = {
            'path': str(file_path),
            'case_id': case_payload['id'],
            'merged': merged,
            'reason': reason_text,
            'status': status.value,
        }
        self.repository.append_event(
            task_id,
            event_type=EventType.REGRESSION_CASE_RECORDED,
            payload=event_payload,
            round_number=None,
        )
        self.artifact_store.append_event(task_id, {'type': EventType.REGRESSION_CASE_RECORDED.value, **event_payload})
        self.artifact_store.write_artifact_json(task_id, name='regression_case', payload=event_payload)
        self.artifact_store.update_state(task_id, {'regression_case_last': event_payload})
        return event_payload

