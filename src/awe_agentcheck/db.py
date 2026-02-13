from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
from typing import Iterator
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from awe_agentcheck.repository import decode_task_meta, encode_task_meta


class Base(DeclarativeBase):
    pass


class TaskEntity(Base):
    __tablename__ = 'tasks'

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text(), nullable=False)
    author_participant: Mapped[str] = mapped_column(String(255), nullable=False)
    reviewer_participants_json: Mapped[str] = mapped_column(Text(), nullable=False)
    workspace_path: Mapped[str] = mapped_column(Text(), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    last_gate_reason: Mapped[str | None] = mapped_column(String(128), nullable=True)
    max_rounds: Mapped[int] = mapped_column(Integer(), nullable=False)
    test_command: Mapped[str] = mapped_column(Text(), nullable=False)
    lint_command: Mapped[str] = mapped_column(Text(), nullable=False)
    rounds_completed: Mapped[int] = mapped_column(Integer(), nullable=False)
    cancel_requested: Mapped[bool] = mapped_column(Boolean(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    events: Mapped[list['TaskEventEntity']] = relationship('TaskEventEntity', back_populates='task', cascade='all,delete-orphan')


class TaskEventEntity(Base):
    __tablename__ = 'task_events'

    id: Mapped[int] = mapped_column(Integer(), primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey('tasks.task_id'), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer(), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    round_number: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    task: Mapped[TaskEntity] = relationship('TaskEntity', back_populates='events')


class Database:
    def __init__(self, url: str):
        self.engine = create_engine(url, future=True)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, class_=Session)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()


class SqlTaskRepository:
    def __init__(self, db: Database):
        self.db = db

    def create_task(
        self,
        *,
        title: str,
        description: str,
        author_participant: str,
        reviewer_participants: list[str],
        evolution_level: int,
        evolve_until: str | None,
        auto_merge: bool,
        merge_target_path: str | None,
        sandbox_mode: bool,
        sandbox_workspace_path: str | None,
        sandbox_generated: bool,
        sandbox_cleanup_on_pass: bool,
        project_path: str,
        self_loop_mode: int,
        workspace_path: str,
        max_rounds: int,
        test_command: str,
        lint_command: str,
    ) -> dict:
        now = datetime.now(timezone.utc)
        task = TaskEntity(
            task_id=f'task-{uuid4().hex[:12]}',
            title=title,
            description=description,
            author_participant=author_participant,
            reviewer_participants_json=encode_task_meta(
                reviewer_participants=reviewer_participants,
                evolution_level=evolution_level,
                evolve_until=evolve_until,
                auto_merge=auto_merge,
                merge_target_path=merge_target_path,
                sandbox_mode=sandbox_mode,
                sandbox_workspace_path=sandbox_workspace_path,
                sandbox_generated=sandbox_generated,
                sandbox_cleanup_on_pass=sandbox_cleanup_on_pass,
                project_path=project_path,
                self_loop_mode=self_loop_mode,
            ),
            workspace_path=workspace_path,
            status='queued',
            last_gate_reason=None,
            max_rounds=int(max_rounds),
            test_command=test_command,
            lint_command=lint_command,
            rounds_completed=0,
            cancel_requested=False,
            created_at=now,
            updated_at=now,
        )
        with self.db.session() as session:
            session.add(task)
        return self._task_to_dict(task)

    def list_tasks(self, *, limit: int = 100) -> list[dict]:
        with self.db.session() as session:
            rows = session.execute(select(TaskEntity).order_by(TaskEntity.created_at.desc()).limit(limit)).scalars().all()
            return [self._task_to_dict(r) for r in rows]

    def get_task(self, task_id: str) -> dict | None:
        with self.db.session() as session:
            row = session.get(TaskEntity, task_id)
            if row is None:
                return None
            return self._task_to_dict(row)

    def update_task_status(
        self,
        task_id: str,
        *,
        status: str,
        reason: str | None,
        rounds_completed: int | None = None,
    ) -> dict:
        with self.db.session() as session:
            row = session.get(TaskEntity, task_id)
            if row is None:
                raise KeyError(task_id)
            row.status = status
            row.last_gate_reason = reason
            if rounds_completed is not None:
                row.rounds_completed = int(rounds_completed)
            row.updated_at = datetime.now(timezone.utc)
            session.add(row)
            session.flush()
            return self._task_to_dict(row)

    def set_cancel_requested(self, task_id: str, *, requested: bool) -> dict:
        with self.db.session() as session:
            row = session.get(TaskEntity, task_id)
            if row is None:
                raise KeyError(task_id)
            row.cancel_requested = bool(requested)
            row.updated_at = datetime.now(timezone.utc)
            session.add(row)
            session.flush()
            return self._task_to_dict(row)

    def is_cancel_requested(self, task_id: str) -> bool:
        with self.db.session() as session:
            row = session.get(TaskEntity, task_id)
            if row is None:
                raise KeyError(task_id)
            return bool(row.cancel_requested)

    def append_event(
        self,
        task_id: str,
        *,
        event_type: str,
        payload: dict,
        round_number: int | None = None,
    ) -> dict:
        now = datetime.now(timezone.utc)
        with self.db.session() as session:
            task = session.get(TaskEntity, task_id)
            if task is None:
                raise KeyError(task_id)

            current_seq = (
                session.execute(select(TaskEventEntity).where(TaskEventEntity.task_id == task_id).order_by(TaskEventEntity.seq.desc()).limit(1))
                .scalars()
                .first()
            )
            next_seq = int(current_seq.seq) + 1 if current_seq else 1

            event = TaskEventEntity(
                task_id=task_id,
                seq=next_seq,
                event_type=event_type,
                round_number=round_number,
                payload_json=json.dumps(payload, ensure_ascii=True),
                created_at=now,
            )
            session.add(event)
            session.flush()
            return self._event_to_dict(event)

    def list_events(self, task_id: str) -> list[dict]:
        with self.db.session() as session:
            task = session.get(TaskEntity, task_id)
            if task is None:
                raise KeyError(task_id)
            rows = session.execute(
                select(TaskEventEntity)
                .where(TaskEventEntity.task_id == task_id)
                .order_by(TaskEventEntity.seq.asc())
            ).scalars().all()
            return [self._event_to_dict(r) for r in rows]

    @staticmethod
    def _task_to_dict(row: TaskEntity) -> dict:
        meta = decode_task_meta(row.reviewer_participants_json)
        try:
            self_loop_mode = int(meta.get('self_loop_mode', 1))
        except Exception:
            self_loop_mode = 1
        self_loop_mode = max(0, min(1, self_loop_mode))
        return {
            'task_id': row.task_id,
            'title': row.title,
            'description': row.description,
            'author_participant': row.author_participant,
            'reviewer_participants': meta['participants'],
            'evolution_level': meta['evolution_level'],
            'evolve_until': meta['evolve_until'],
            'auto_merge': bool(meta.get('auto_merge', True)),
            'merge_target_path': meta.get('merge_target_path'),
            'sandbox_mode': bool(meta.get('sandbox_mode', False)),
            'sandbox_workspace_path': meta.get('sandbox_workspace_path'),
            'sandbox_generated': bool(meta.get('sandbox_generated', False)),
            'sandbox_cleanup_on_pass': bool(meta.get('sandbox_cleanup_on_pass', False)),
            'project_path': str(meta.get('project_path') or row.workspace_path),
            'self_loop_mode': self_loop_mode,
            'workspace_path': row.workspace_path,
            'status': row.status,
            'last_gate_reason': row.last_gate_reason,
            'max_rounds': row.max_rounds,
            'test_command': row.test_command,
            'lint_command': row.lint_command,
            'rounds_completed': row.rounds_completed,
            'cancel_requested': row.cancel_requested,
            'created_at': row.created_at.isoformat(),
            'updated_at': row.updated_at.isoformat(),
        }

    @staticmethod
    def _event_to_dict(row: TaskEventEntity) -> dict:
        return {
            'id': row.id,
            'task_id': row.task_id,
            'seq': row.seq,
            'type': row.event_type,
            'round': row.round_number,
            'payload': json.loads(row.payload_json),
            'created_at': row.created_at.isoformat(),
        }
