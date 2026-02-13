from __future__ import annotations

from pathlib import Path
from datetime import datetime

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from awe_agentcheck.domain.models import ReviewVerdict
from awe_agentcheck.domain.models import TaskStatus
from awe_agentcheck.repository import InMemoryTaskRepository, TaskRepository
from awe_agentcheck.service import CreateTaskInput, GateInput, OrchestratorService
from awe_agentcheck.storage.artifacts import ArtifactStore


class CreateTaskRequest(BaseModel):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(min_length=1)
    author_participant: str = Field(min_length=1)
    reviewer_participants: list[str] = Field(min_length=1)
    evolution_level: int = Field(default=0, ge=0, le=2)
    evolve_until: str | None = Field(default=None, max_length=64)
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
    approve: bool
    note: str | None = Field(default=None, max_length=4000)
    auto_start: bool = Field(default=False)


class GateRequest(BaseModel):
    tests_ok: bool
    lint_ok: bool
    reviewer_verdicts: list[ReviewVerdict] = Field(min_length=1)


class ForceFailRequest(BaseModel):
    reason: str = Field(min_length=1, max_length=4000)


class TaskResponse(BaseModel):
    task_id: str
    title: str
    description: str
    author_participant: str
    reviewer_participants: list[str]
    evolution_level: int
    evolve_until: str | None
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
) -> FastAPI:
    if service is None:
        repo = repository or InMemoryTaskRepository()
        artifacts = ArtifactStore(artifact_root or (Path.cwd() / '.agents'))
        service = OrchestratorService(repository=repo, artifact_store=artifacts)

    app = FastAPI(title='awe-agentcheck api', version='0.5.0')
    app.state.container = AppState(service=service)

    def get_service() -> OrchestratorService:
        return app.state.container.service

    def _start_task_worker(task_id: str) -> None:
        try:
            get_service().start_task(task_id)
        except Exception as exc:
            get_service().mark_failed_system(task_id, reason=f'background_error: {exc}')

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
        try:
            task = service.create_task(
                CreateTaskInput(
                    title=payload.title,
                    description=payload.description,
                    author_participant=payload.author_participant,
                    reviewer_participants=payload.reviewer_participants,
                    evolution_level=payload.evolution_level,
                    evolve_until=payload.evolve_until,
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
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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

    @app.get('/api/workspace-tree', response_model=WorkspaceTreeResponse)
    def get_workspace_tree(
        workspace_path: str = Query(default='.', min_length=1),
        max_depth: int = Query(default=4, ge=1, le=8),
        max_entries: int = Query(default=500, ge=50, le=5000),
    ) -> WorkspaceTreeResponse:
        root = Path(workspace_path)
        if not root.exists() or not root.is_dir():
            raise HTTPException(status_code=400, detail=f'workspace_path must be an existing directory: {workspace_path}')

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
        try:
            task = service.submit_author_decision(
                task_id,
                approve=bool(payload.approve),
                note=payload.note,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail='task not found') from exc

        if payload.approve and payload.auto_start and task.status == TaskStatus.QUEUED:
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
