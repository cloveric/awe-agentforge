from __future__ import annotations

from awe_agentcheck.api import create_app
from awe_agentcheck.adapters import ParticipantRunner
from awe_agentcheck.config import load_settings
from awe_agentcheck.db import Database, SqlTaskRepository
from awe_agentcheck.observability import configure_observability
from awe_agentcheck.repository import InMemoryTaskRepository
from awe_agentcheck.service import OrchestratorService
from awe_agentcheck.storage.artifacts import ArtifactStore
from awe_agentcheck.workflow import ShellCommandExecutor, WorkflowEngine


def build_app():
    settings = load_settings()
    configure_observability(
        service_name=settings.service_name,
        otlp_endpoint=settings.otel_endpoint,
    )
    artifacts = ArtifactStore(settings.artifact_root)

    try:
        db = Database(settings.database_url)
        db.create_schema()
        repo = SqlTaskRepository(db)
    except Exception:
        repo = InMemoryTaskRepository()

    runner = ParticipantRunner(
        command_overrides={
            'claude': settings.claude_command,
            'codex': settings.codex_command,
            'gemini': settings.gemini_command,
        },
        dry_run=settings.dry_run,
        timeout_retries=settings.participant_timeout_retries,
    )
    workflow = WorkflowEngine(
        runner=runner,
        command_executor=ShellCommandExecutor(),
        participant_timeout_seconds=settings.participant_timeout_seconds,
        command_timeout_seconds=settings.command_timeout_seconds,
    )
    service = OrchestratorService(
        repository=repo,
        artifact_store=artifacts,
        workflow_engine=workflow,
        max_concurrent_running_tasks=settings.max_concurrent_running_tasks,
    )
    return create_app(service=service)


app = build_app()
