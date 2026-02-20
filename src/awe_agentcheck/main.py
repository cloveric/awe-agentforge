from __future__ import annotations

import logging

from awe_agentcheck.api import create_app
from awe_agentcheck.adapters import ParticipantRunner
from awe_agentcheck.config import load_settings
from awe_agentcheck.db import Database, SqlTaskRepository
from awe_agentcheck.observability import configure_observability
from awe_agentcheck.participants import set_extra_providers
from awe_agentcheck.repository import InMemoryTaskRepository
from awe_agentcheck.service import OrchestratorService
from awe_agentcheck.storage.artifacts import ArtifactStore
from awe_agentcheck.workflow import ShellCommandExecutor, WorkflowEngine

_log = logging.getLogger(__name__)


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
        _log.exception('database bootstrap failed; falling back to in-memory repository')
        repo = InMemoryTaskRepository()

    runner = ParticipantRunner(
        command_overrides={
            'claude': settings.claude_command,
            'codex': settings.codex_command,
            'gemini': settings.gemini_command,
            **settings.extra_provider_commands,
        },
        dry_run=settings.dry_run,
        timeout_retries=settings.participant_timeout_retries,
    )
    commands = getattr(runner, 'commands', None)
    if isinstance(commands, dict):
        set_extra_providers(set(commands.keys()))
    else:
        set_extra_providers({'claude', 'codex', 'gemini', *settings.extra_provider_commands.keys()})
    workflow = WorkflowEngine(
        runner=runner,
        command_executor=ShellCommandExecutor(),
        participant_timeout_seconds=settings.participant_timeout_seconds,
        command_timeout_seconds=settings.command_timeout_seconds,
        workflow_backend=settings.workflow_backend,
    )
    service = OrchestratorService(
        repository=repo,
        artifact_store=artifacts,
        workflow_engine=workflow,
        max_concurrent_running_tasks=settings.max_concurrent_running_tasks,
    )
    return create_app(service=service)


app = build_app()
