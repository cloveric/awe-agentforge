from __future__ import annotations

from datetime import datetime
from ipaddress import ip_address
import logging
import os
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from awe_agentcheck.domain.models import ReviewVerdict
from awe_agentcheck.domain.models import TaskStatus
from awe_agentcheck.repository import InMemoryTaskRepository, TaskRepository
from awe_agentcheck.service import CreateTaskInput, GateInput, InputValidationError, OrchestratorService
from awe_agentcheck.storage.artifacts import ArtifactStore

_log = logging.getLogger(__name__)


class CreateTaskRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1)
    author_participant: str = Field(min_length=1)
    reviewer_participants: list[str] = Field(min_length=1)
    evolution_level: int = Field(default=0, ge=0, le=2)
    evolve_until: str | None = Field(default=None, max_length=64)
    conversation_language: str = Field(default='en', min_length=2, max_length=16)
    provider_models: dict[str, str] = Field(default_factory=dict)
    provider_model_params: dict[str, str] = Field(default_factory=dict)
    claude_team_agents: bool = Field(default=False)
    repair_mode: str = Field(default='balanced', min_length=3, max_length=32)
    plain_mode: bool = Field(default=True)
    stream_mode: bool = Field(default=True)
    debate_mode: bool = Field(default=True)
    sandbox_mode: bool = Field(default=True)
    sandbox_workspace_path: str | None = Field(default=None, max_length=400)
    sandbox_cleanup_on_pass: bool = Field(default=True)
    self_loop_mode: int = Field(default=0, ge=0, le=1)
    auto_merge: bool = Field(default=True)
    merge_target_path: str | None = Field(default=None, max_length=400)
    workspace_path: str = Field(default='.', min_length=1)
    max_rounds: int = Field(default=3, ge=1, le=20)
    test_command: str = Field(default='py -m pytest -q', min_length=1)
    lint_command: str = Field(default='py -m ruff check .', min_length=1)
    auto_start: bool = Field(default=False)


class StartTaskRequest(BaseModel):
    background: bool = Field(default=False)


class AuthorDecisionRequest(BaseModel):
    approve: bool | None = Field(default=None)
    decision: Literal['approve', 'reject', 'revise'] | None = Field(default=None)
    note: str | None = Field(default=None, max_length=4000)
    auto_start: bool = Field(default=False)


class GateRequest(BaseModel):
    tests_ok: bool
    lint_ok: bool
    reviewer_verdicts: list[ReviewVerdict] = Field(min_length=1)


class ForceFailRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=4000)


class PromoteRoundRequest(BaseModel):
    round: int = Field(ge=1)
    merge_target_path: str | None = Field(default=None, max_length=400)


class PromoteRoundResponse(BaseModel):
    task_id: str
    round: int
    source_snapshot_path: str
    target_path: str
    changed_files: list[str]
    copied_files: list[str]
    deleted_files: list[str]
    snapshot_path: str
    changelog_path: str
    merged_at: str
    mode: str


class TaskResponse(BaseModel):
    task_id: str
    title: str
    description: str
    author_participant: str
    reviewer_participants: list[str]
    evolution_level: int
    evolve_until: str | None
    conversation_language: str
    provider_models: dict[str, str]
    provider_model_params: dict[str, str]
    claude_team_agents: bool
    repair_mode: str
    plain_mode: bool
    stream_mode: bool
    debate_mode: bool
    sandbox_mode: bool
    sandbox_workspace_path: str | None
    sandbox_generated: bool
    sandbox_cleanup_on_pass: bool
    self_loop_mode: int
    project_path: str
    auto_merge: bool
    merge_target_path: str | None
    workspace_path: str
    status: str
    last_gate_reason: str | None
    max_rounds: int
    test_command: str
    lint_command: str
    rounds_completed: int
    cancel_requested: bool


class EventResponse(BaseModel):
    seq: int
    task_id: str
    type: str
    round: int | None
    payload: dict
    created_at: str


class StatsResponse(BaseModel):
    total_tasks: int
    status_counts: dict[str, int]
    active_tasks: int
    reason_bucket_counts: dict[str, int]
    provider_error_counts: dict[str, int]
    recent_terminal_total: int
    pass_rate_50: float
    failed_gate_rate_50: float
    failed_system_rate_50: float
    mean_task_duration_seconds_50: float


class WorkspaceTreeNodeResponse(BaseModel):
    path: str
    name: str
    kind: str
    depth: int
    size_bytes: int | None


class WorkspaceTreeResponse(BaseModel):
    workspace_path: str
    generated_at: str
    max_depth: int
    max_entries: int
    total_entries: int
    truncated: bool
    nodes: list[WorkspaceTreeNodeResponse]


class ProviderModelsResponse(BaseModel):
    providers: dict[str, list[str]]


class PolicyTemplateDefaultsResponse(BaseModel):
    sandbox_mode: int
    self_loop_mode: int
    auto_merge: int
    max_rounds: int
    debate_mode: int
    plain_mode: int
    stream_mode: int
    repair_mode: str


class PolicyTemplateItemResponse(BaseModel):
    id: str
    label: str
    description: str
    defaults: PolicyTemplateDefaultsResponse


class PolicyTemplateProfileResponse(BaseModel):
    workspace_path: str
    exists: bool
    repo_size: str
    risk_level: str
    file_count: int
    risk_markers: int
    scan_truncated: bool | None = None


class PolicyTemplatesResponse(BaseModel):
    recommended_template: str
    profile: PolicyTemplateProfileResponse
    templates: list[PolicyTemplateItemResponse]


class AnalyticsFailureBucketResponse(BaseModel):
    bucket: str
    count: int
    share: float


class AnalyticsFailureTrendRowResponse(BaseModel):
    day: str
    total: int
    buckets: dict[str, int]


class AnalyticsReviewerGlobalResponse(BaseModel):
    reviews: int
    no_blocker_rate: float
    blocker_rate: float
    unknown_rate: float
    adverse_rate: float


class AnalyticsReviewerDriftResponse(BaseModel):
    participant: str
    reviews: int
    no_blocker_rate: float
    blocker_rate: float
    unknown_rate: float
    adverse_rate: float
    drift_score: float


class AnalyticsResponse(BaseModel):
    generated_at: str
    window_tasks: int
    window_failed_gate: int
    failure_taxonomy: list[AnalyticsFailureBucketResponse]
    failure_taxonomy_trend: list[AnalyticsFailureTrendRowResponse]
    reviewer_global: AnalyticsReviewerGlobalResponse
    reviewer_drift: list[AnalyticsReviewerDriftResponse]


class GitHubSummaryArtifactResponse(BaseModel):
    name: str
    path: str


class GitHubSummaryGitStateResponse(BaseModel):
    is_git_repo: bool
    branch: str | None = None
    worktree_clean: bool | None = None
    remote_origin: str | None = None
    guard_allowed: bool
    guard_reason: str
    enabled: bool | None = None
    target_path: str | None = None
    allowed_branches: list[str] | None = None
    require_clean: bool | None = None


class GitHubSummaryResponse(BaseModel):
    task_id: str
    project_path: str
    status: str
    git: GitHubSummaryGitStateResponse
    summary_markdown: str
    artifacts: list[GitHubSummaryArtifactResponse]


class ProjectHistoryDisputeResponse(BaseModel):
    participant: str
    verdict: str
    note: str


class ProjectHistoryRevisionResponse(BaseModel):
    auto_merge: bool
    mode: str | None = None
    changed_files: int = 0
    copied_files: int = 0
    deleted_files: int = 0
    snapshot_path: str | None = None
    changelog_path: str | None = None
    merged_at: str | None = None


class ProjectHistoryItemResponse(BaseModel):
    task_id: str
    title: str
    project_path: str
    status: str
    last_gate_reason: str | None
    created_at: str | None
    updated_at: str | None
    core_findings: list[str]
    revisions: ProjectHistoryRevisionResponse
    disputes: list[ProjectHistoryDisputeResponse]
    next_steps: list[str]


class ProjectHistoryResponse(BaseModel):
    project_path: str | None
    total: int
    items: list[ProjectHistoryItemResponse]


class ProjectHistoryClearRequest(BaseModel):
    project_path: str | None = Field(default=None, max_length=400)
    include_non_terminal: bool = Field(default=False)


class ProjectHistoryClearResponse(BaseModel):
    project_path: str | None
    deleted_tasks: int
    deleted_artifacts: int
    skipped_non_terminal: int


class ValidationErrorResponse(BaseModel):
    code: str
    message: str
    field: str | None = None


class AppState:
    def __init__(self, service: OrchestratorService):
        self.service = service


def _to_task_response(task) -> TaskResponse:
    return TaskResponse(
        task_id=task.task_id,
        title=task.title,
        description=task.description,
        author_participant=task.author_participant,
        reviewer_participants=task.reviewer_participants,
        evolution_level=task.evolution_level,
        evolve_until=task.evolve_until,
        conversation_language=task.conversation_language,
        provider_models=task.provider_models,
        provider_model_params=task.provider_model_params,
        claude_team_agents=task.claude_team_agents,
        repair_mode=task.repair_mode,
        plain_mode=task.plain_mode,
        stream_mode=task.stream_mode,
        debate_mode=task.debate_mode,
        sandbox_mode=task.sandbox_mode,
        sandbox_workspace_path=task.sandbox_workspace_path,
        sandbox_generated=task.sandbox_generated,
        sandbox_cleanup_on_pass=task.sandbox_cleanup_on_pass,
        self_loop_mode=task.self_loop_mode,
        project_path=task.project_path,
        auto_merge=task.auto_merge,
        merge_target_path=task.merge_target_path,
        workspace_path=task.workspace_path,
        status=task.status.value,
        last_gate_reason=task.last_gate_reason,
        max_rounds=task.max_rounds,
        test_command=task.test_command,
        lint_command=task.lint_command,
        rounds_completed=task.rounds_completed,
        cancel_requested=task.cancel_requested,
    )


def create_app(
    *,
    repository: TaskRepository | None = None,
    service: OrchestratorService | None = None,
    artifact_root: Path | None = None,
    workspace_tree_safe_root: Path | None = None,
    allow_remote_api: bool | None = None,
    api_access_token: str | None = None,
    api_access_token_header: str = 'x-awe-api-token',
) -> FastAPI:
    if service is None:
        repo = repository or InMemoryTaskRepository()
        artifacts = ArtifactStore(artifact_root or (Path.cwd() / '.agents'))
        service = OrchestratorService(repository=repo, artifact_store=artifacts)

    app = FastAPI(title='awe-agentcheck api', version='0.5.0')
    app.state.container = AppState(service=service)

    safe_root = (workspace_tree_safe_root or Path.cwd()).resolve()
    resolved_allow_remote_api = allow_remote_api
    if resolved_allow_remote_api is None:
        resolved_allow_remote_api = str(os.getenv('AWE_API_ALLOW_REMOTE', '')).strip().lower() in {'1', 'true', 'yes', 'on'}
    resolved_api_access_token = api_access_token
    if resolved_api_access_token is None:
        resolved_api_access_token = str(os.getenv('AWE_API_TOKEN', '')).strip() or None
    resolved_api_access_token_header = str(
        os.getenv('AWE_API_TOKEN_HEADER', api_access_token_header) or api_access_token_header
    ).strip().lower()

    def _field_from_loc(loc: tuple | list | None) -> str | None:
        if not loc:
            return None
        source_prefixes = {'body', 'query', 'path', 'header', 'cookie'}
        parts = list(loc)
        if parts and str(parts[0]) in source_prefixes:
            parts = parts[1:]
        if not parts:
            return None

        field = ''
        for part in parts:
            if isinstance(part, int):
                field += f'[{part}]'
                continue

            text = str(part)
            if field:
                field += f'.{text}'
            else:
                field = text

        return field or None

    def _validation_error_payload(*, message: str, field: str | None = None, code: str = 'validation_error') -> dict:
        payload: dict[str, str] = {
            'code': code,
            'message': message,
        }
        if field:
            payload['field'] = field
        return payload

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation_error(request: Request, exc: RequestValidationError):  # noqa: ARG001
        details = exc.errors()
        if details:
            first = details[0]
            message = str(first.get('msg') or 'invalid request body')
            field = _field_from_loc(first.get('loc'))
        else:
            message = 'invalid request body'
            field = None
        return JSONResponse(
            status_code=400,
            content=_validation_error_payload(message=message, field=field),
        )

    @app.exception_handler(InputValidationError)
    async def handle_input_validation_error(request: Request, exc: InputValidationError):  # noqa: ARG001
        return JSONResponse(
            status_code=400,
            content=_validation_error_payload(
                message=str(exc),
                field=exc.field,
                code=exc.code,
            ),
        )

    def get_service() -> OrchestratorService:
        return app.state.container.service

    def _is_loopback_host(host: str | None) -> bool:
        text = str(host or '').strip().lower()
        if not text:
            return False
        if text in {'localhost', 'testclient'}:
            return True
        if text.startswith('::ffff:'):
            text = text[7:]
        try:
            return ip_address(text).is_loopback
        except ValueError:
            return False

    @app.middleware('http')
    async def enforce_api_access_controls(request: Request, call_next):
        if request.url.path.startswith('/api/'):
            client_host = request.client.host if request.client is not None else ''
            if not resolved_allow_remote_api and not _is_loopback_host(client_host):
                return JSONResponse(
                    status_code=403,
                    content=_validation_error_payload(code='forbidden', message='api access denied'),
                )
            if resolved_api_access_token:
                token = request.headers.get(resolved_api_access_token_header)
                if token != resolved_api_access_token:
                    return JSONResponse(
                        status_code=401,
                        content=_validation_error_payload(code='unauthorized', message='invalid api token'),
                    )
        return await call_next(request)

    def _start_task_worker(task_id: str) -> None:
        service = get_service()
        try:
            service.start_task(task_id)
        except Exception as exc:
            reason_text = str(exc).strip() or exc.__class__.__name__
            _log.exception('background worker failed task_id=%s reason=%s', task_id, reason_text)
            try:
                service.mark_failed_system(task_id, reason=f'background_error: {reason_text}')
            except Exception:
                _log.exception('background worker failed to mark task as failed task_id=%s', task_id)

    @app.get('/healthz')
    def healthz() -> dict[str, str]:
        return {'status': 'ok'}

    @app.get('/')
    def index():
        web_path = Path.cwd() / 'web' / 'index.html'
        if web_path.exists():
            return FileResponse(web_path)
        return JSONResponse({'name': 'awe-agentcheck', 'status': 'ok'})

    @app.post('/api/tasks', response_model=TaskResponse, status_code=201)
    def create_task(
        payload: CreateTaskRequest,
        background_tasks: BackgroundTasks,
        service: OrchestratorService = Depends(get_service),
    ) -> TaskResponse:
        task = service.create_task(
            CreateTaskInput(
                title=payload.title,
                description=payload.description,
                author_participant=payload.author_participant,
                reviewer_participants=payload.reviewer_participants,
                evolution_level=payload.evolution_level,
                evolve_until=payload.evolve_until,
                conversation_language=payload.conversation_language,
                provider_models=payload.provider_models,
                provider_model_params=payload.provider_model_params,
                claude_team_agents=payload.claude_team_agents,
                repair_mode=payload.repair_mode,
                plain_mode=payload.plain_mode,
                stream_mode=payload.stream_mode,
                debate_mode=payload.debate_mode,
                sandbox_mode=payload.sandbox_mode,
                sandbox_workspace_path=payload.sandbox_workspace_path,
                sandbox_cleanup_on_pass=payload.sandbox_cleanup_on_pass,
                self_loop_mode=payload.self_loop_mode,
                auto_merge=payload.auto_merge,
                merge_target_path=payload.merge_target_path,
                workspace_path=payload.workspace_path,
                max_rounds=payload.max_rounds,
                test_command=payload.test_command,
                lint_command=payload.lint_command,
            )
        )

        if payload.auto_start:
            background_tasks.add_task(_start_task_worker, task.task_id)

        return _to_task_response(task)

    @app.get('/api/tasks', response_model=list[TaskResponse])
    def list_tasks(
        service: OrchestratorService = Depends(get_service),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[TaskResponse]:
        rows = service.list_tasks(limit=limit)
        return [_to_task_response(r) for r in rows]

    @app.get('/api/stats', response_model=StatsResponse)
    def get_stats(service: OrchestratorService = Depends(get_service)) -> StatsResponse:
        stats = service.get_stats()
        return StatsResponse(
            total_tasks=stats.total_tasks,
            status_counts=stats.status_counts,
            active_tasks=stats.active_tasks,
            reason_bucket_counts=stats.reason_bucket_counts,
            provider_error_counts=stats.provider_error_counts,
            recent_terminal_total=stats.recent_terminal_total,
            pass_rate_50=stats.pass_rate_50,
            failed_gate_rate_50=stats.failed_gate_rate_50,
            failed_system_rate_50=stats.failed_system_rate_50,
            mean_task_duration_seconds_50=stats.mean_task_duration_seconds_50,
        )

    @app.get('/api/provider-models', response_model=ProviderModelsResponse)
    def get_provider_models(service: OrchestratorService = Depends(get_service)) -> ProviderModelsResponse:
        return ProviderModelsResponse(providers=service.get_provider_models_catalog())

    @app.get('/api/policy-templates', response_model=PolicyTemplatesResponse)
    def get_policy_templates(
        service: OrchestratorService = Depends(get_service),
        workspace_path: str = Query(default='.', min_length=1),
    ) -> PolicyTemplatesResponse:
        payload = service.get_policy_templates(workspace_path=workspace_path)
        return PolicyTemplatesResponse(**payload)

    @app.get('/api/analytics', response_model=AnalyticsResponse)
    def get_analytics(
        service: OrchestratorService = Depends(get_service),
        limit: int = Query(default=300, ge=1, le=2000),
    ) -> AnalyticsResponse:
        payload = service.get_analytics(limit=limit)
        return AnalyticsResponse(**payload)

    @app.get('/api/tasks/{task_id}/github-summary', response_model=GitHubSummaryResponse)
    def get_github_summary(task_id: str, service: OrchestratorService = Depends(get_service)) -> GitHubSummaryResponse:
        try:
            payload = service.build_github_pr_summary(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='task not found') from exc
        return GitHubSummaryResponse(**payload)

    @app.get('/api/project-history', response_model=ProjectHistoryResponse)
    def get_project_history(
        service: OrchestratorService = Depends(get_service),
        project_path: str | None = Query(default=None),
        limit: int = Query(default=200, ge=1, le=1000),
    ) -> ProjectHistoryResponse:
        items = service.list_project_history(project_path=project_path, limit=limit)
        return ProjectHistoryResponse(
            project_path=(str(project_path).strip() if project_path else None),
            total=len(items),
            items=[ProjectHistoryItemResponse(**item) for item in items],
        )

    @app.post('/api/project-history/clear', response_model=ProjectHistoryClearResponse)
    def clear_project_history(
        payload: ProjectHistoryClearRequest,
        service: OrchestratorService = Depends(get_service),
    ) -> ProjectHistoryClearResponse:
        result = service.clear_project_history(
            project_path=payload.project_path,
            include_non_terminal=payload.include_non_terminal,
        )
        return ProjectHistoryClearResponse(**result)

    @app.get('/api/workspace-tree', response_model=WorkspaceTreeResponse)
    def get_workspace_tree(
        workspace_path: str = Query(default='.', min_length=1),
        max_depth: int = Query(default=4, ge=1, le=8),
        max_entries: int = Query(default=500, ge=50, le=5000),
    ) -> WorkspaceTreeResponse:
        raw_root = Path(workspace_path)
        if '..' in raw_root.parts:
            raise HTTPException(status_code=400, detail='workspace_path is invalid')

        try:
            root = raw_root.resolve()
        except OSError as exc:
            raise HTTPException(status_code=400, detail='workspace_path is invalid') from exc

        try:
            root.relative_to(safe_root)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail='workspace_path is outside allowed root') from exc

        if not root.exists() or not root.is_dir():
            raise HTTPException(status_code=400, detail='workspace_path must be an existing directory')

        nodes: list[WorkspaceTreeNodeResponse] = []
        truncated = False
        total_entries = 0

        stack: list[tuple[Path, int]] = [(root, 0)]
        while stack:
            current, depth = stack.pop()
            if depth >= max_depth:
                continue
            try:
                children = list(current.iterdir())
            except OSError:
                continue
            children.sort(key=lambda p: (not p.is_dir(), p.name.lower()))

            for child in children:
                total_entries += 1
                if len(nodes) >= max_entries:
                    truncated = True
                    break
                rel = child.relative_to(root).as_posix()
                kind = 'dir' if child.is_dir() else 'file'
                size: int | None = None
                if kind == 'file':
                    try:
                        size = int(child.stat().st_size)
                    except OSError:
                        size = None
                nodes.append(
                    WorkspaceTreeNodeResponse(
                        path=rel,
                        name=child.name,
                        kind=kind,
                        depth=depth + 1,
                        size_bytes=size,
                    )
                )
            if truncated:
                break

            for child in reversed(children):
                try:
                    is_dir = child.is_dir()
                except OSError:
                    is_dir = False
                if is_dir:
                    stack.append((child, depth + 1))

        return WorkspaceTreeResponse(
            workspace_path=str(root),
            generated_at=datetime.now().isoformat(),
            max_depth=max_depth,
            max_entries=max_entries,
            total_entries=total_entries,
            truncated=truncated,
            nodes=nodes,
        )

    @app.get('/api/tasks/{task_id}', response_model=TaskResponse)
    def get_task(task_id: str, service: OrchestratorService = Depends(get_service)) -> TaskResponse:
        task = service.get_task(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail='task not found')
        return _to_task_response(task)

    @app.post('/api/tasks/{task_id}/start', response_model=TaskResponse)
    def start_task(
        task_id: str,
        payload: StartTaskRequest,
        background_tasks: BackgroundTasks,
        service: OrchestratorService = Depends(get_service),
    ) -> TaskResponse:
        if service.get_task(task_id) is None:
            raise HTTPException(status_code=404, detail='task not found')

        if payload.background:
            background_tasks.add_task(_start_task_worker, task_id)
            task = service.get_task(task_id)
            assert task is not None
            return _to_task_response(task)

        try:
            task = service.start_task(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='task not found') from exc
        return _to_task_response(task)

    @app.post('/api/tasks/{task_id}/author-decision', response_model=TaskResponse)
    def author_decision(
        task_id: str,
        payload: AuthorDecisionRequest,
        background_tasks: BackgroundTasks,
        service: OrchestratorService = Depends(get_service),
    ) -> TaskResponse:
        decision = str(payload.decision or '').strip().lower()
        if not decision:
            decision = 'approve' if bool(payload.approve) else 'reject'
        try:
            task = service.submit_author_decision(
                task_id,
                approve=payload.approve,
                decision=decision,
                note=payload.note,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='task not found') from exc
        except InputValidationError as exc:
            raise HTTPException(
                status_code=400,
                detail={'code': exc.code, 'field': exc.field, 'message': str(exc)},
            ) from exc

        if decision in {'approve', 'revise'} and payload.auto_start and task.status == TaskStatus.QUEUED:
            background_tasks.add_task(_start_task_worker, task.task_id)
        return _to_task_response(task)

    @app.post('/api/tasks/{task_id}/cancel', response_model=TaskResponse)
    def cancel_task(task_id: str, service: OrchestratorService = Depends(get_service)) -> TaskResponse:
        try:
            task = service.request_cancel(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='task not found') from exc
        return _to_task_response(task)

    @app.post('/api/tasks/{task_id}/force-fail', response_model=TaskResponse)
    def force_fail_task(
        task_id: str,
        payload: ForceFailRequest,
        service: OrchestratorService = Depends(get_service),
    ) -> TaskResponse:
        try:
            task = service.force_fail_task(task_id, reason=payload.reason)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='task not found') from exc
        return _to_task_response(task)

    @app.post('/api/tasks/{task_id}/promote-round', response_model=PromoteRoundResponse)
    def promote_round(
        task_id: str,
        payload: PromoteRoundRequest,
        service: OrchestratorService = Depends(get_service),
    ) -> PromoteRoundResponse:
        try:
            result = service.promote_selected_round(
                task_id,
                round_number=int(payload.round),
                merge_target_path=payload.merge_target_path,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='task not found') from exc
        return PromoteRoundResponse(**result)

    @app.get('/api/tasks/{task_id}/events', response_model=list[EventResponse])
    def list_events(task_id: str, service: OrchestratorService = Depends(get_service)) -> list[EventResponse]:
        try:
            rows = service.list_events(task_id)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='task not found') from exc
        return [
            EventResponse(
                seq=int(row['seq']),
                task_id=str(row['task_id']),
                type=str(row['type']),
                round=row.get('round'),
                payload=dict(row.get('payload', {})),
                created_at=str(row['created_at']),
            )
            for row in rows
        ]

    @app.post('/api/tasks/{task_id}/gate', response_model=TaskResponse)
    def evaluate_gate(task_id: str, payload: GateRequest, service: OrchestratorService = Depends(get_service)) -> TaskResponse:
        try:
            task = service.evaluate_gate(
                task_id,
                GateInput(
                    tests_ok=payload.tests_ok,
                    lint_ok=payload.lint_ok,
                    reviewer_verdicts=payload.reviewer_verdicts,
                ),
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='task not found') from exc
        return _to_task_response(task)

    return app
