from __future__ import annotations

from datetime import datetime
import re
from typing import Callable

from awe_agentcheck.domain.events import EventType, REVIEW_EVENT_TYPES
from awe_agentcheck.domain.models import TaskStatus
from awe_agentcheck.observability import get_logger


_TERMINAL_STATUSES = {
    TaskStatus.PASSED.value,
    TaskStatus.FAILED_GATE.value,
    TaskStatus.FAILED_SYSTEM.value,
    TaskStatus.CANCELED.value,
}
_log = get_logger('awe_agentcheck.service_layers.analytics')


class AnalyticsService:
    def __init__(
        self,
        *,
        repository,
        stats_factory: Callable[..., object],
        reason_bucket_fn: Callable[[str | None], str | None],
        provider_pattern: re.Pattern,
        parse_iso_datetime_fn: Callable[[object], datetime | None],
        format_task_day_fn: Callable[[object], str],
        merged_event_payload_fn: Callable[[dict], dict],
    ):
        self.repository = repository
        self._stats_factory = stats_factory
        self._reason_bucket = reason_bucket_fn
        self._provider_pattern = provider_pattern
        self._parse_iso_datetime = parse_iso_datetime_fn
        self._format_task_day = format_task_day_fn
        self._merged_event_payload = merged_event_payload_fn

    def get_stats(self):
        rows = self.repository.list_tasks(limit=10_000)
        counts: dict[str, int] = {}
        reason_bucket_counts: dict[str, int] = {}
        provider_error_counts: dict[str, int] = {}
        for row in rows:
            status = str(row.get('status', 'unknown'))
            counts[status] = counts.get(status, 0) + 1

            reason = row.get('last_gate_reason')
            bucket = self._reason_bucket(reason)
            if bucket:
                reason_bucket_counts[bucket] = reason_bucket_counts.get(bucket, 0) + 1

            provider_match = self._provider_pattern.search(str(reason or ''))
            if provider_match:
                provider = provider_match.group(1).strip().lower()
                provider_error_counts[provider] = provider_error_counts.get(provider, 0) + 1

        active = counts.get(TaskStatus.RUNNING.value, 0) + counts.get(TaskStatus.QUEUED.value, 0)
        recent_rows = rows[:50]
        recent_terminal = [r for r in recent_rows if str(r.get('status', '')) in _TERMINAL_STATUSES]
        recent_terminal_total = len(recent_terminal)
        if recent_terminal_total > 0:
            pass_rate_50 = sum(1 for r in recent_terminal if str(r.get('status')) == TaskStatus.PASSED.value) / recent_terminal_total
            failed_gate_rate_50 = sum(1 for r in recent_terminal if str(r.get('status')) == TaskStatus.FAILED_GATE.value) / recent_terminal_total
            failed_system_rate_50 = sum(1 for r in recent_terminal if str(r.get('status')) == TaskStatus.FAILED_SYSTEM.value) / recent_terminal_total
        else:
            pass_rate_50 = 0.0
            failed_gate_rate_50 = 0.0
            failed_system_rate_50 = 0.0

        durations: list[float] = []
        for row in recent_terminal:
            created = self._parse_iso_datetime(row.get('created_at'))
            updated = self._parse_iso_datetime(row.get('updated_at'))
            if created is None or updated is None:
                continue
            delta = (updated - created).total_seconds()
            if delta >= 0:
                durations.append(delta)
        mean_task_duration_seconds_50 = (sum(durations) / len(durations)) if durations else 0.0

        def _to_bool(value) -> bool:
            if isinstance(value, bool):
                return value
            text = str(value or '').strip().lower()
            return text in {'1', 'true', 'yes', 'on'}

        prefix_reuse_eligible = 0
        prefix_reuse_hits = 0
        prompt_cache_break_count_50 = 0
        prompt_cache_break_model_50 = 0
        prompt_cache_break_toolset_50 = 0
        prompt_cache_break_prefix_50 = 0
        for row in recent_rows:
            task_id = str(row.get('task_id') or '').strip()
            if not task_id:
                continue
            try:
                events = self.repository.list_events(task_id)
            except KeyError:
                events = []
            except Exception:
                _log.exception('list_events failed while building stats task_id=%s', task_id)
                events = []
            for event in events:
                etype = str(event.get('type') or '').strip().lower()
                if etype == EventType.PROMPT_CACHE_PROBE.value:
                    payload = self._merged_event_payload(event)
                    if _to_bool(payload.get('prefix_reuse_eligible')):
                        prefix_reuse_eligible += 1
                        if _to_bool(payload.get('prefix_reused')):
                            prefix_reuse_hits += 1
                elif etype == EventType.PROMPT_CACHE_BREAK.value:
                    prompt_cache_break_count_50 += 1
                    payload = self._merged_event_payload(event)
                    reason = str(payload.get('reason') or '').strip().lower()
                    if reason == 'model_changed':
                        prompt_cache_break_model_50 += 1
                    elif reason == 'toolset_changed':
                        prompt_cache_break_toolset_50 += 1
                    elif reason == 'prefix_changed':
                        prompt_cache_break_prefix_50 += 1
        prompt_prefix_reuse_rate_50 = (
            prefix_reuse_hits / prefix_reuse_eligible
            if prefix_reuse_eligible > 0
            else 0.0
        )

        return self._stats_factory(
            total_tasks=len(rows),
            status_counts=counts,
            active_tasks=active,
            reason_bucket_counts=reason_bucket_counts,
            provider_error_counts=provider_error_counts,
            recent_terminal_total=recent_terminal_total,
            pass_rate_50=pass_rate_50,
            failed_gate_rate_50=failed_gate_rate_50,
            failed_system_rate_50=failed_system_rate_50,
            mean_task_duration_seconds_50=mean_task_duration_seconds_50,
            prompt_prefix_reuse_rate_50=prompt_prefix_reuse_rate_50,
            prompt_cache_break_count_50=prompt_cache_break_count_50,
            prompt_cache_break_model_50=prompt_cache_break_model_50,
            prompt_cache_break_toolset_50=prompt_cache_break_toolset_50,
            prompt_cache_break_prefix_50=prompt_cache_break_prefix_50,
        )

    def get_analytics(self, *, limit: int = 300) -> dict:
        rows = self.repository.list_tasks(limit=max(1, min(2000, int(limit))))
        failures = [row for row in rows if str(row.get('status') or '') == TaskStatus.FAILED_GATE.value]
        total_failures = len(failures)

        failure_taxonomy: dict[str, int] = {}
        trend_by_day: dict[str, dict[str, int]] = {}
        for row in failures:
            reason = str(row.get('last_gate_reason') or '')
            bucket = self._reason_bucket(reason) or 'other'
            failure_taxonomy[bucket] = failure_taxonomy.get(bucket, 0) + 1

            day = self._format_task_day(row.get('updated_at') or row.get('created_at'))
            day_bucket = trend_by_day.setdefault(day, {})
            day_bucket[bucket] = day_bucket.get(bucket, 0) + 1

        taxonomy_rows: list[dict] = []
        for bucket, count in sorted(failure_taxonomy.items(), key=lambda item: (-item[1], item[0])):
            share = (count / total_failures) if total_failures else 0.0
            taxonomy_rows.append({'bucket': bucket, 'count': count, 'share': round(share, 4)})

        trend_rows: list[dict] = []
        for day in sorted(trend_by_day.keys()):
            day_counts = trend_by_day[day]
            trend_rows.append(
                {
                    'day': day,
                    'total': int(sum(day_counts.values())),
                    'buckets': dict(sorted(day_counts.items(), key=lambda item: (-item[1], item[0]))),
                }
            )

        reviewer_counts: dict[str, dict[str, int]] = {}
        global_counts = {'no_blocker': 0, 'blocker': 0, 'unknown': 0}
        for row in rows:
            task_id = str(row.get('task_id') or '').strip()
            if not task_id:
                continue
            try:
                events = self.repository.list_events(task_id)
            except KeyError:
                events = []
            except Exception:
                _log.exception('list_events failed while building analytics task_id=%s', task_id)
                events = []
            for event in events:
                etype = str(event.get('type') or '').strip().lower()
                if etype not in REVIEW_EVENT_TYPES:
                    continue
                payload = self._merged_event_payload(event)
                participant = str(payload.get('participant') or '').strip()
                if not participant:
                    continue
                verdict = str(payload.get('verdict') or '').strip().lower()
                if verdict not in {'no_blocker', 'blocker', 'unknown'}:
                    verdict = 'unknown'

                bucket = reviewer_counts.setdefault(participant, {'no_blocker': 0, 'blocker': 0, 'unknown': 0})
                bucket[verdict] = int(bucket.get(verdict, 0)) + 1
                global_counts[verdict] = int(global_counts.get(verdict, 0)) + 1

        global_total = int(sum(global_counts.values()))
        global_adverse_rate = (
            (global_counts.get('blocker', 0) + global_counts.get('unknown', 0)) / global_total
            if global_total
            else 0.0
        )

        reviewer_rows: list[dict] = []
        for participant, counts in reviewer_counts.items():
            total = int(sum(int(v) for v in counts.values()))
            if total <= 0:
                continue
            blocker_rate = counts.get('blocker', 0) / total
            unknown_rate = counts.get('unknown', 0) / total
            no_blocker_rate = counts.get('no_blocker', 0) / total
            adverse_rate = blocker_rate + unknown_rate
            reviewer_rows.append(
                {
                    'participant': participant,
                    'reviews': total,
                    'no_blocker_rate': round(no_blocker_rate, 4),
                    'blocker_rate': round(blocker_rate, 4),
                    'unknown_rate': round(unknown_rate, 4),
                    'adverse_rate': round(adverse_rate, 4),
                    'drift_score': round(abs(adverse_rate - global_adverse_rate), 4),
                }
            )

        reviewer_rows.sort(key=lambda item: (-float(item.get('drift_score', 0.0)), -int(item.get('reviews', 0)), item.get('participant', '')))

        return {
            'generated_at': datetime.now().isoformat(),
            'window_tasks': len(rows),
            'window_failed_gate': total_failures,
            'failure_taxonomy': taxonomy_rows,
            'failure_taxonomy_trend': trend_rows,
            'reviewer_global': {
                'reviews': global_total,
                'no_blocker_rate': round((global_counts.get('no_blocker', 0) / global_total) if global_total else 0.0, 4),
                'blocker_rate': round((global_counts.get('blocker', 0) / global_total) if global_total else 0.0, 4),
                'unknown_rate': round((global_counts.get('unknown', 0) / global_total) if global_total else 0.0, 4),
                'adverse_rate': round(global_adverse_rate, 4),
            },
            'reviewer_drift': reviewer_rows,
        }




