from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    database_url: str
    artifact_root: Path
    service_name: str
    otel_endpoint: str | None
    dry_run: bool
    claude_command: str
    codex_command: str
    gemini_command: str
    participant_timeout_seconds: int
    command_timeout_seconds: int
    participant_timeout_retries: int
    max_concurrent_running_tasks: int
    workflow_backend: str


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    raw = (os.getenv(name, '') or '').strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def load_settings() -> Settings:
    database_url = os.getenv(
        'AWE_DATABASE_URL',
        'postgresql+psycopg://postgres:postgres@localhost:5432/awe_agentcheck?connect_timeout=2',
    )
    artifact_root = Path(os.getenv('AWE_ARTIFACT_ROOT', '.agents')).resolve()
    service_name = os.getenv('AWE_SERVICE_NAME', 'awe-agentcheck')
    otel_endpoint = os.getenv('AWE_OTEL_EXPORTER_OTLP_ENDPOINT')
    dry_run = os.getenv('AWE_DRY_RUN', '').strip().lower() in {'1', 'true', 'yes', 'on'}
    claude_command = os.getenv(
        'AWE_CLAUDE_COMMAND',
        'claude -p --dangerously-skip-permissions --strict-mcp-config --effort low --model claude-opus-4-6',
    )
    codex_command = os.getenv(
        'AWE_CODEX_COMMAND',
        'codex exec --skip-git-repo-check --dangerously-bypass-approvals-and-sandbox -c model_reasoning_effort=xhigh',
    )
    gemini_command = os.getenv('AWE_GEMINI_COMMAND', 'gemini --yolo')
    # Default to a long participant timeout for deep audit/review tasks.
    participant_timeout_seconds = _env_int('AWE_PARTICIPANT_TIMEOUT_SECONDS', 3600, minimum=10)
    command_timeout_seconds = _env_int('AWE_COMMAND_TIMEOUT_SECONDS', 300, minimum=10)
    participant_timeout_retries = _env_int('AWE_PARTICIPANT_TIMEOUT_RETRIES', 1, minimum=0)
    max_concurrent_running_tasks = _env_int('AWE_MAX_CONCURRENT_RUNNING_TASKS', 1, minimum=0)
    workflow_backend = str(os.getenv('AWE_WORKFLOW_BACKEND', 'langgraph') or 'langgraph').strip().lower()
    if workflow_backend not in {'langgraph', 'classic'}:
        workflow_backend = 'langgraph'
    return Settings(
        database_url=database_url,
        artifact_root=artifact_root,
        service_name=service_name,
        otel_endpoint=otel_endpoint,
        dry_run=dry_run,
        claude_command=claude_command,
        codex_command=codex_command,
        gemini_command=gemini_command,
        participant_timeout_seconds=participant_timeout_seconds,
        command_timeout_seconds=command_timeout_seconds,
        participant_timeout_retries=participant_timeout_retries,
        max_concurrent_running_tasks=max_concurrent_running_tasks,
        workflow_backend=workflow_backend,
    )
