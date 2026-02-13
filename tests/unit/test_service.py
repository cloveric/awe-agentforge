from __future__ import annotations

from pathlib import Path

from awe_agentcheck.repository import InMemoryTaskRepository
from awe_agentcheck.service import CreateTaskInput, OrchestratorService
from awe_agentcheck.storage.artifacts import ArtifactStore
from awe_agentcheck.workflow import RunResult


class FakeWorkflowEngine:
    def __init__(self):
        self.calls = 0

    def run(self, config, *, on_event, should_cancel):
        self.calls += 1
        on_event({'type': 'discussion', 'round': 1, 'provider': config.author.provider, 'output': 'plan'})
        on_event({'type': 'implementation', 'round': 1, 'provider': config.author.provider, 'output': 'implemented'})
        on_event({'type': 'review', 'round': 1, 'participant': config.reviewers[0].participant_id, 'verdict': 'no_blocker', 'output': 'ok'})
        on_event({'type': 'gate_passed', 'round': 1, 'reason': 'passed'})
        return RunResult(status='passed', rounds=1, gate_reason='passed')


class FakeCanceledWorkflowEngine:
    def run(self, config, *, on_event, should_cancel):
        on_event({'type': 'task_started', 'round': 0})
        return RunResult(status='canceled', rounds=0, gate_reason='canceled')


class FakeFailingWorkflowEngine:
    def run(self, config, *, on_event, should_cancel):
        raise RuntimeError('boom')


class FakeForceFailedWorkflowEngine:
    def __init__(self):
        self.service = None

    def run(self, config, *, on_event, should_cancel):
        assert self.service is not None
        self.service.force_fail_task(config.task_id, reason='watchdog_timeout: test')
        return RunResult(status='passed', rounds=1, gate_reason='passed')


class FakeWorkflowEngineWithFileChange:
    def run(self, config, *, on_event, should_cancel):
        target = config.cwd / 'src' / 'hello.txt'
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('hello fusion\n', encoding='utf-8')
        on_event({'type': 'discussion', 'round': 1, 'provider': config.author.provider, 'output': 'plan'})
        on_event({'type': 'implementation', 'round': 1, 'provider': config.author.provider, 'output': 'changed file'})
        on_event({'type': 'gate_passed', 'round': 1, 'reason': 'passed'})
        return RunResult(status='passed', rounds=1, gate_reason='passed')


def build_service(tmp_path: Path, workflow_engine=None, *, max_concurrent_running_tasks: int = 1) -> OrchestratorService:
    return OrchestratorService(
        repository=InMemoryTaskRepository(),
        artifact_store=ArtifactStore(tmp_path / '.agents'),
        workflow_engine=workflow_engine or FakeWorkflowEngine(),
        max_concurrent_running_tasks=max_concurrent_running_tasks,
    )


def test_service_create_task_sets_queued_status(tmp_path: Path):
    svc = build_service(tmp_path)

    task = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            max_rounds=2,
        )
    )

    assert task.status.value == 'queued'
    assert task.max_rounds == 2
    assert task.workspace_path
    assert task.evolution_level == 0
    assert task.evolve_until is None
    assert task.auto_merge is True
    assert task.merge_target_path is None


def test_service_create_task_accepts_evolution_fields(tmp_path: Path):
    svc = build_service(tmp_path)
    task = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            evolution_level=2,
            evolve_until='2026-02-13 06:00',
        )
    )
    assert task.evolution_level == 2
    assert task.evolve_until == '2026-02-13T06:00:00'


def test_service_create_task_defaults_to_sandbox_workspace(tmp_path: Path):
    project = tmp_path / 'proj'
    project.mkdir()
    (project / 'README.md').write_text('hello\n', encoding='utf-8')

    svc = build_service(tmp_path)
    task = svc.create_task(
        CreateTaskInput(
            title='Sandbox default',
            description='sandbox',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
        )
    )

    assert task.sandbox_mode is True
    assert task.self_loop_mode == 0
    assert task.project_path == str(project)
    assert 'proj-lab' in Path(task.workspace_path).as_posix()
    assert task.sandbox_generated is True
    assert task.sandbox_cleanup_on_pass is True
    assert task.merge_target_path == str(project)
    assert (Path(task.workspace_path) / 'README.md').exists()


def test_service_create_task_uses_unique_default_sandbox_per_task(tmp_path: Path):
    project = tmp_path / 'proj-unique'
    project.mkdir()
    (project / 'README.md').write_text('hello\n', encoding='utf-8')
    svc = build_service(tmp_path)

    t1 = svc.create_task(
        CreateTaskInput(
            title='T1',
            description='sandbox one',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
        )
    )
    t2 = svc.create_task(
        CreateTaskInput(
            title='T2',
            description='sandbox two',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
        )
    )

    assert t1.workspace_path != t2.workspace_path
    assert Path(t1.workspace_path).exists()
    assert Path(t2.workspace_path).exists()


def test_service_start_task_default_sandbox_is_cleaned_after_passed_auto_merge(tmp_path: Path):
    project = tmp_path / 'proj-cleanup'
    project.mkdir()
    (project / 'README.md').write_text('base\n', encoding='utf-8')
    svc = build_service(tmp_path, workflow_engine=FakeWorkflowEngineWithFileChange())
    task = svc.create_task(
        CreateTaskInput(
            title='Cleanup task',
            description='cleanup',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
            self_loop_mode=1,
        )
    )
    sandbox_path = Path(task.workspace_path)
    assert sandbox_path.exists()

    result = svc.start_task(task.task_id)
    assert result.status.value == 'passed'
    assert (project / 'src' / 'hello.txt').exists()
    assert not sandbox_path.exists()
    events = svc.list_events(task.task_id)
    assert any(e['type'] == 'sandbox_cleanup_completed' for e in events)


def test_service_start_task_custom_sandbox_is_not_auto_cleaned(tmp_path: Path):
    project = tmp_path / 'proj-custom-sandbox'
    custom = tmp_path / 'my-custom-lab'
    project.mkdir()
    custom.mkdir()
    (project / 'README.md').write_text('base\n', encoding='utf-8')
    svc = build_service(tmp_path, workflow_engine=FakeWorkflowEngineWithFileChange())
    task = svc.create_task(
        CreateTaskInput(
            title='Custom sandbox task',
            description='custom',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
            sandbox_workspace_path=str(custom),
            self_loop_mode=1,
        )
    )

    result = svc.start_task(task.task_id)
    assert result.status.value == 'passed'
    assert custom.exists()
    events = svc.list_events(task.task_id)
    assert not any(e['type'] == 'sandbox_cleanup_completed' for e in events)


def test_service_start_task_waits_for_author_confirmation_when_self_loop_manual(tmp_path: Path):
    project = tmp_path / 'manual-proj'
    project.mkdir()
    engine = FakeWorkflowEngine()
    svc = build_service(tmp_path, workflow_engine=engine)
    task = svc.create_task(
        CreateTaskInput(
            title='Manual approve',
            description='need approve',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
            sandbox_mode=False,
            self_loop_mode=0,
        )
    )

    waiting = svc.start_task(task.task_id)
    assert waiting.status.value == 'waiting_manual'
    assert waiting.last_gate_reason == 'author_confirmation_required'
    assert engine.calls == 0


def test_service_author_approve_requeues_and_can_run(tmp_path: Path):
    project = tmp_path / 'approve-proj'
    project.mkdir()
    engine = FakeWorkflowEngine()
    svc = build_service(tmp_path, workflow_engine=engine)
    task = svc.create_task(
        CreateTaskInput(
            title='Approve path',
            description='approve',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
            sandbox_mode=False,
            self_loop_mode=0,
        )
    )
    svc.start_task(task.task_id)

    queued = svc.submit_author_decision(task.task_id, approve=True, note='ship it')
    assert queued.status.value == 'queued'
    assert queued.last_gate_reason == 'author_approved'

    passed = svc.start_task(task.task_id)
    assert passed.status.value == 'passed'
    assert engine.calls == 1


def test_service_author_reject_cancels(tmp_path: Path):
    project = tmp_path / 'reject-proj'
    project.mkdir()
    svc = build_service(tmp_path, workflow_engine=FakeWorkflowEngine())
    task = svc.create_task(
        CreateTaskInput(
            title='Reject path',
            description='reject',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(project),
            sandbox_mode=False,
            self_loop_mode=0,
        )
    )
    svc.start_task(task.task_id)

    canceled = svc.submit_author_decision(task.task_id, approve=False, note='not now')
    assert canceled.status.value == 'canceled'
    assert canceled.last_gate_reason == 'author_rejected'


def test_service_start_task_auto_merge_copies_changes_and_writes_changelog_snapshot(tmp_path: Path):
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    (source / 'README.md').write_text('base\n', encoding='utf-8')

    svc = build_service(tmp_path, workflow_engine=FakeWorkflowEngineWithFileChange())
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Fusion task',
            description='auto merge',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(source),
            auto_merge=True,
            merge_target_path=str(target),
        )
    )

    result = svc.start_task(created.task_id)
    assert result.status.value == 'passed'

    merged_file = target / 'src' / 'hello.txt'
    assert merged_file.exists()
    assert merged_file.read_text(encoding='utf-8') == 'hello fusion\n'

    changelog = target / 'CHANGELOG.auto.md'
    assert changelog.exists()
    assert created.task_id in changelog.read_text(encoding='utf-8')

    snapshots = list((tmp_path / '.agents' / 'snapshots').glob(f'{created.task_id}-*.zip'))
    assert snapshots

    events = svc.list_events(created.task_id)
    assert any(e['type'] == 'auto_merge_completed' for e in events)


def test_service_start_task_can_disable_auto_merge(tmp_path: Path):
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    source.mkdir()
    target.mkdir()
    (source / 'README.md').write_text('base\n', encoding='utf-8')

    svc = build_service(tmp_path, workflow_engine=FakeWorkflowEngineWithFileChange())
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='No fusion task',
            description='auto merge off',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
            workspace_path=str(source),
            auto_merge=False,
            merge_target_path=str(target),
        )
    )

    result = svc.start_task(created.task_id)
    assert result.status.value == 'passed'
    assert not (target / 'src' / 'hello.txt').exists()
    assert not (target / 'CHANGELOG.auto.md').exists()


def test_service_start_task_runs_workflow_and_records_events(tmp_path: Path):
    svc = build_service(tmp_path)
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )

    result = svc.start_task(created.task_id)
    events = svc.list_events(created.task_id)

    assert result.status.value == 'passed'
    assert result.rounds_completed == 1
    assert len(events) >= 3


def test_service_cancel_request_marks_flag(tmp_path: Path):
    svc = build_service(tmp_path)
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )

    canceled = svc.request_cancel(created.task_id)
    assert canceled.cancel_requested is True


def test_service_start_task_on_terminal_status_is_idempotent(tmp_path: Path):
    engine = FakeWorkflowEngine()
    svc = build_service(tmp_path, workflow_engine=engine)
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )

    first = svc.start_task(created.task_id)
    second = svc.start_task(created.task_id)

    assert first.status.value == 'passed'
    assert second.status.value == 'passed'
    assert engine.calls == 1


def test_service_start_task_clears_cancel_flag_in_returned_view(tmp_path: Path):
    svc = build_service(tmp_path, workflow_engine=FakeCanceledWorkflowEngine())
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    svc.request_cancel(created.task_id)

    result = svc.start_task(created.task_id)
    assert result.status.value == 'canceled'
    assert result.cancel_requested is False


def test_service_start_task_marks_failed_system_on_workflow_exception(tmp_path: Path):
    svc = build_service(tmp_path, workflow_engine=FakeFailingWorkflowEngine())
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )

    result = svc.start_task(created.task_id)
    assert result.status.value == 'failed_system'
    assert 'workflow_error' in (result.last_gate_reason or '')


def test_service_mark_failed_system_updates_status(tmp_path: Path):
    svc = build_service(tmp_path)
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )

    failed = svc.mark_failed_system(created.task_id, reason='boom')
    assert failed.status.value == 'failed_system'
    assert failed.last_gate_reason == 'boom'


def test_service_force_fail_task_sets_status_and_cancel_requested(tmp_path: Path):
    svc = build_service(tmp_path)
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )

    failed = svc.force_fail_task(created.task_id, reason='watchdog_timeout: task exceeded 1800s')
    assert failed.status.value == 'failed_system'
    assert failed.cancel_requested is True
    assert 'watchdog_timeout' in (failed.last_gate_reason or '')
    events = svc.list_events(created.task_id)
    assert any(e['type'] == 'force_failed' for e in events)


def test_service_start_task_does_not_override_external_force_fail(tmp_path: Path):
    engine = FakeForceFailedWorkflowEngine()
    svc = build_service(tmp_path, workflow_engine=engine)
    engine.service = svc
    created = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='Build parser',
            description='Implement parser for feed',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )

    result = svc.start_task(created.task_id)
    assert result.status.value == 'failed_system'
    assert 'watchdog_timeout' in (result.last_gate_reason or '')


def test_service_create_task_rejects_missing_workspace(tmp_path: Path):
    svc = build_service(tmp_path)
    missing = tmp_path / 'does-not-exist'

    try:
        svc.create_task(
            CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
                title='Build parser',
                description='Implement parser for feed',
                author_participant='claude#author-A',
                reviewer_participants=['codex#review-B'],
                workspace_path=str(missing),
            )
        )
    except ValueError as exc:
        assert 'workspace_path' in str(exc)
    else:
        raise AssertionError('expected ValueError')


def test_service_create_task_rejects_invalid_evolve_until(tmp_path: Path):
    svc = build_service(tmp_path)
    try:
        svc.create_task(
            CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
                title='Build parser',
                description='Implement parser for feed',
                author_participant='claude#author-A',
                reviewer_participants=['codex#review-B'],
                evolve_until='bad-value',
            )
        )
    except ValueError as exc:
        assert 'evolve_until' in str(exc)
    else:
        raise AssertionError('expected ValueError')


def test_service_create_task_rejects_invalid_merge_target_when_auto_merge_enabled(tmp_path: Path):
    svc = build_service(tmp_path)
    missing = tmp_path / 'missing-merge-target'
    try:
        svc.create_task(
            CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
                title='Build parser',
                description='Implement parser for feed',
                author_participant='claude#author-A',
                reviewer_participants=['codex#review-B'],
                auto_merge=True,
                merge_target_path=str(missing),
            )
        )
    except ValueError as exc:
        assert 'merge_target_path' in str(exc)
    else:
        raise AssertionError('expected ValueError')


def test_service_stats_include_reason_and_provider_error_breakdown(tmp_path: Path):
    svc = build_service(tmp_path)

    t1 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T1',
            description='d1',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    svc.mark_failed_system(
        t1.task_id,
        reason='workflow_error: command_timeout provider=codex command=codex exec timeout_seconds=240',
    )

    t2 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T2',
            description='d2',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    svc.mark_failed_system(
        t2.task_id,
        reason='workflow_error: command_not_found provider=claude command=claude -p',
    )

    stats = svc.get_stats()
    assert stats.reason_bucket_counts.get('command_timeout') == 1
    assert stats.reason_bucket_counts.get('command_not_found') == 1
    assert stats.provider_error_counts.get('codex') == 1
    assert stats.provider_error_counts.get('claude') == 1


def test_service_stats_do_not_bucket_passed_reason(tmp_path: Path):
    svc = build_service(tmp_path)
    t1 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T1',
            description='d1',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    svc.repository.update_task_status(t1.task_id, status='passed', reason='passed', rounds_completed=1)

    stats = svc.get_stats()
    assert stats.reason_bucket_counts.get('other') is None


def test_service_stats_bucket_review_unknown(tmp_path: Path):
    svc = build_service(tmp_path)
    t1 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T1',
            description='d1',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    svc.repository.update_task_status(t1.task_id, status='failed_gate', reason='review_unknown', rounds_completed=1)
    stats = svc.get_stats()
    assert stats.reason_bucket_counts.get('review_unknown') == 1


def test_service_stats_bucket_provider_limit(tmp_path: Path):
    svc = build_service(tmp_path)
    t1 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T1',
            description='d1',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    svc.mark_failed_system(
        t1.task_id,
        reason='workflow_error: provider_limit provider=claude command=claude -p',
    )
    stats = svc.get_stats()
    assert stats.reason_bucket_counts.get('provider_limit') == 1


def test_service_stats_bucket_watchdog_timeout(tmp_path: Path):
    svc = build_service(tmp_path)
    t1 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T1',
            description='d1',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    svc.mark_failed_system(
        t1.task_id,
        reason='watchdog_timeout: task exceeded 1800s without terminal status',
    )
    stats = svc.get_stats()
    assert stats.reason_bucket_counts.get('watchdog_timeout') == 1


def test_service_start_task_is_deferred_when_running_limit_reached(tmp_path: Path):
    svc = build_service(tmp_path, max_concurrent_running_tasks=1)
    t1 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T1',
            description='d1',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    t2 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T2',
            description='d2',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )

    svc.repository.update_task_status(
        t1.task_id,
        status='running',
        reason=None,
        rounds_completed=0,
    )

    deferred = svc.start_task(t2.task_id)
    assert deferred.status.value == 'queued'
    assert deferred.last_gate_reason == 'concurrency_limit'
    events = svc.list_events(t2.task_id)
    assert any(e['type'] == 'start_deferred' for e in events)
    stats = svc.get_stats()
    assert stats.reason_bucket_counts.get('concurrency_limit') == 1


def test_service_stats_include_recent_rates_and_duration(tmp_path: Path):
    svc = build_service(tmp_path)
    t1 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T1',
            description='d1',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    t2 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T2',
            description='d2',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    t3 = svc.create_task(
        CreateTaskInput(
            sandbox_mode=False,
            self_loop_mode=1,
            title='T3',
            description='d3',
            author_participant='claude#author-A',
            reviewer_participants=['codex#review-B'],
        )
    )
    svc.repository.update_task_status(t1.task_id, status='passed', reason='passed', rounds_completed=1)
    svc.repository.update_task_status(t2.task_id, status='failed_gate', reason='review_blocker', rounds_completed=1)
    svc.repository.update_task_status(t3.task_id, status='failed_system', reason='workflow_error: command_timeout provider=codex', rounds_completed=0)

    svc.repository.items[t1.task_id]['created_at'] = '2026-02-12T00:00:00+00:00'
    svc.repository.items[t1.task_id]['updated_at'] = '2026-02-12T00:01:00+00:00'
    svc.repository.items[t2.task_id]['created_at'] = '2026-02-12T00:00:00+00:00'
    svc.repository.items[t2.task_id]['updated_at'] = '2026-02-12T00:03:00+00:00'
    svc.repository.items[t3.task_id]['created_at'] = '2026-02-12T00:00:00+00:00'
    svc.repository.items[t3.task_id]['updated_at'] = '2026-02-12T00:02:00+00:00'

    stats = svc.get_stats()
    assert stats.recent_terminal_total == 3
    assert stats.pass_rate_50 == 1 / 3
    assert stats.failed_gate_rate_50 == 1 / 3
    assert stats.failed_system_rate_50 == 1 / 3
    assert stats.mean_task_duration_seconds_50 == 120.0



