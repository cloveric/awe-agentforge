from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import time
from typing import Iterator
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    delete,
    func,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from awe_agentcheck.repository import decode_task_meta, encode_task_meta


def _iso_utc(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


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
    __table_args__ = (
        UniqueConstraint('task_id', 'seq', name='uq_task_events_task_id_seq'),
    )

    id: Mapped[int] = mapped_column(Integer(), primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(String(64), ForeignKey('tasks.task_id'), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer(), nullable=False)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    round_number: Mapped[int | None] = mapped_column(Integer(), nullable=True)
    payload_json: Mapped[str] = mapped_column(Text(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    task: Mapped[TaskEntity] = relationship('TaskEntity', back_populates='events')


class TaskEventCounterEntity(Base):
    __tablename__ = 'task_event_counters'

    task_id: Mapped[str] = mapped_column(String(64), ForeignKey('tasks.task_id'), primary_key=True)
    next_seq: Mapped[int] = mapped_column(Integer(), nullable=False)


class Database:
    def __init__(self, url: str):
        engine_kwargs: dict[str, object] = {
            'future': True,
        }
        if str(url or '').strip().lower().startswith('sqlite'):
            # Improve sqlite writer stability under concurrent API/task traffic.
            engine_kwargs['connect_args'] = {'check_same_thread': False, 'timeout': 30}
        self.engine = create_engine(url, **engine_kwargs)
        self.session_factory = sessionmaker(bind=self.engine, expire_on_commit=False, class_=Session)
        if self.engine.dialect.name == 'sqlite':
            self._configure_sqlite_pragmas()

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

    def _configure_sqlite_pragmas(self) -> None:
        with self.engine.connect() as conn:
            conn.exec_driver_sql('PRAGMA journal_mode=WAL')
            conn.exec_driver_sql('PRAGMA synchronous=NORMAL')
            conn.exec_driver_sql('PRAGMA foreign_keys=ON')
            conn.exec_driver_sql('PRAGMA busy_timeout=30000')


class SqlTaskRepository:
    def __init__(self, db: Database):
        self.db = db

    def _sqlite_lock_retry_attempts(self) -> int:
        return 8 if self.db.engine.dialect.name == 'sqlite' else 1

    @staticmethod
    def _is_sqlite_lock_error(exc: Exception) -> bool:
        text = str(exc or '').lower()
        return 'database is locked' in text or 'database table is locked' in text

    @staticmethod
    def _sqlite_lock_backoff_seconds(attempt: int) -> float:
        # Small exponential backoff capped to keep API responsive.
        return min(0.2, 0.02 * (2 ** max(0, int(attempt) - 1)))

    def create_task(
        self,
        *,
        title: str,
        description: str,
        author_participant: str,
        reviewer_participants: list[str],
        evolution_level: int,
        evolve_until: str | None,
        conversation_language: str,
        provider_models: dict[str, str],
        provider_model_params: dict[str, str],
        participant_models: dict[str, str] | None = None,
        participant_model_params: dict[str, str] | None = None,
        claude_team_agents: bool,
        codex_multi_agents: bool,
        claude_team_agents_overrides: dict[str, bool] | None = None,
        codex_multi_agents_overrides: dict[str, bool] | None = None,
        repair_mode: str,
        plain_mode: bool,
        stream_mode: bool,
        debate_mode: bool,
        auto_merge: bool,
        merge_target_path: str | None,
        sandbox_mode: bool,
        sandbox_workspace_path: str | None,
        sandbox_generated: bool,
        sandbox_cleanup_on_pass: bool,
        project_path: str,
        self_loop_mode: int,
        workspace_path: str,
        workspace_fingerprint: dict[str, object] | None = None,
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
                conversation_language=conversation_language,
                provider_models=provider_models,
                provider_model_params=provider_model_params,
                participant_models=participant_models,
                participant_model_params=participant_model_params,
                claude_team_agents=claude_team_agents,
                codex_multi_agents=codex_multi_agents,
                claude_team_agents_overrides=claude_team_agents_overrides,
                codex_multi_agents_overrides=codex_multi_agents_overrides,
                repair_mode=repair_mode,
                plain_mode=plain_mode,
                stream_mode=stream_mode,
                debate_mode=debate_mode,
                auto_merge=auto_merge,
                merge_target_path=merge_target_path,
                sandbox_mode=sandbox_mode,
                sandbox_workspace_path=sandbox_workspace_path,
                sandbox_generated=sandbox_generated,
                sandbox_cleanup_on_pass=sandbox_cleanup_on_pass,
                project_path=project_path,
                self_loop_mode=self_loop_mode,
                workspace_fingerprint=workspace_fingerprint,
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
        attempts = self._sqlite_lock_retry_attempts()
        for attempt in range(1, attempts + 1):
            try:
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
            except OperationalError as exc:
                if (not self._is_sqlite_lock_error(exc)) or attempt >= attempts:
                    raise
                time.sleep(self._sqlite_lock_backoff_seconds(attempt))
        raise RuntimeError('update_task_status_retry_exhausted')

    def update_task_status_if(
        self,
        task_id: str,
        *,
        expected_status: str,
        status: str,
        reason: str | None,
        rounds_completed: int | None = None,
        set_cancel_requested: bool | None = None,
    ) -> dict | None:
        attempts = self._sqlite_lock_retry_attempts()
        for attempt in range(1, attempts + 1):
            try:
                now = datetime.now(timezone.utc)
                with self.db.session() as session:
                    values: dict[str, object] = {
                        'status': status,
                        'last_gate_reason': reason,
                        'updated_at': now,
                    }
                    if rounds_completed is not None:
                        values['rounds_completed'] = int(rounds_completed)
                    if set_cancel_requested is not None:
                        values['cancel_requested'] = bool(set_cancel_requested)

                    result = session.execute(
                        update(TaskEntity)
                        .where(
                            TaskEntity.task_id == task_id,
                            TaskEntity.status == expected_status,
                        )
                        .values(**values)
                    )
                    session.flush()
                    if int(result.rowcount or 0) == 0:
                        existing = session.get(TaskEntity, task_id)
                        if existing is None:
                            raise KeyError(task_id)
                        return None

                    row = session.get(TaskEntity, task_id)
                    if row is None:
                        raise KeyError(task_id)
                    return self._task_to_dict(row)
            except OperationalError as exc:
                if (not self._is_sqlite_lock_error(exc)) or attempt >= attempts:
                    raise
                time.sleep(self._sqlite_lock_backoff_seconds(attempt))
        raise RuntimeError('update_task_status_if_retry_exhausted')

    def set_cancel_requested(self, task_id: str, *, requested: bool) -> dict:
        attempts = self._sqlite_lock_retry_attempts()
        for attempt in range(1, attempts + 1):
            try:
                with self.db.session() as session:
                    row = session.get(TaskEntity, task_id)
                    if row is None:
                        raise KeyError(task_id)
                    row.cancel_requested = bool(requested)
                    row.updated_at = datetime.now(timezone.utc)
                    session.add(row)
                    session.flush()
                    return self._task_to_dict(row)
            except OperationalError as exc:
                if (not self._is_sqlite_lock_error(exc)) or attempt >= attempts:
                    raise
                time.sleep(self._sqlite_lock_backoff_seconds(attempt))
        raise RuntimeError('set_cancel_requested_retry_exhausted')

    def is_cancel_requested(self, task_id: str) -> bool:
        attempts = self._sqlite_lock_retry_attempts()
        for attempt in range(1, attempts + 1):
            try:
                with self.db.session() as session:
                    row = session.get(TaskEntity, task_id)
                    if row is None:
                        raise KeyError(task_id)
                    return bool(row.cancel_requested)
            except OperationalError as exc:
                if (not self._is_sqlite_lock_error(exc)) or attempt >= attempts:
                    raise
                time.sleep(self._sqlite_lock_backoff_seconds(attempt))
        raise RuntimeError('is_cancel_requested_retry_exhausted')

    def append_event(
        self,
        task_id: str,
        *,
        event_type: str,
        payload: dict,
        round_number: int | None = None,
    ) -> dict:
        now = datetime.now(timezone.utc)
        max_attempts = max(3, self._sqlite_lock_retry_attempts())
        for attempt in range(max_attempts):
            try:
                with self.db.session() as session:
                    task = session.get(TaskEntity, task_id)
                    if task is None:
                        raise KeyError(task_id)

                    next_seq = self._reserve_next_event_seq(session, task_id)
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
            except IntegrityError:
                if attempt + 1 >= max_attempts:
                    raise
            except OperationalError as exc:
                if (not self._is_sqlite_lock_error(exc)) or attempt + 1 >= max_attempts:
                    raise
                time.sleep(self._sqlite_lock_backoff_seconds(attempt + 1))
        raise RuntimeError('append_event_retry_exhausted')

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

    def delete_tasks(self, task_ids: list[str]) -> int:
        unique_ids: list[str] = []
        seen: set[str] = set()
        for raw in task_ids:
            task_id = str(raw or '').strip()
            if not task_id or task_id in seen:
                continue
            seen.add(task_id)
            unique_ids.append(task_id)
        if not unique_ids:
            return 0

        deleted = 0
        with self.db.session() as session:
            session.execute(
                delete(TaskEventCounterEntity).where(TaskEventCounterEntity.task_id.in_(unique_ids))
            )
            rows = session.execute(
                select(TaskEntity).where(TaskEntity.task_id.in_(unique_ids))
            ).scalars().all()
            for row in rows:
                session.delete(row)
                deleted += 1
            session.flush()
        return deleted

    @staticmethod
    def _reserve_next_event_seq(session: Session, task_id: str) -> int:
        initial_next_seq = (
            select((func.coalesce(func.max(TaskEventEntity.seq), 0) + 2))
            .where(TaskEventEntity.task_id == task_id)
            .scalar_subquery()
        )
        bind = session.get_bind()
        dialect_name = bind.dialect.name if bind is not None else ''

        if dialect_name == 'sqlite':
            stmt = (
                sqlite_insert(TaskEventCounterEntity)
                .values(task_id=task_id, next_seq=initial_next_seq)
                .on_conflict_do_update(
                    index_elements=[TaskEventCounterEntity.task_id],
                    set_={'next_seq': TaskEventCounterEntity.next_seq + 1},
                )
                .returning(TaskEventCounterEntity.next_seq)
            )
            reserved_next_seq = int(session.execute(stmt).scalar_one())
            return reserved_next_seq - 1

        if dialect_name == 'postgresql':
            stmt = (
                pg_insert(TaskEventCounterEntity)
                .values(task_id=task_id, next_seq=initial_next_seq)
                .on_conflict_do_update(
                    index_elements=[TaskEventCounterEntity.task_id],
                    set_={'next_seq': TaskEventCounterEntity.next_seq + 1},
                )
                .returning(TaskEventCounterEntity.next_seq)
            )
            reserved_next_seq = int(session.execute(stmt).scalar_one())
            return reserved_next_seq - 1

        # Fallback for other SQLAlchemy dialects.
        counter = session.get(TaskEventCounterEntity, task_id, with_for_update=True)
        if counter is None:
            max_seq = int(
                session.execute(
                    select(func.coalesce(func.max(TaskEventEntity.seq), 0))
                    .where(TaskEventEntity.task_id == task_id)
                ).scalar_one()
            )
            assigned_seq = max_seq + 1
            session.add(TaskEventCounterEntity(task_id=task_id, next_seq=assigned_seq + 1))
            session.flush()
            return assigned_seq

        assigned_seq = int(counter.next_seq)
        counter.next_seq = assigned_seq + 1
        session.add(counter)
        session.flush()
        return assigned_seq

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
            'conversation_language': str(meta.get('conversation_language') or 'en'),
            'provider_models': dict(meta.get('provider_models', {})),
            'provider_model_params': dict(meta.get('provider_model_params', {})),
            'participant_models': dict(meta.get('participant_models', {})),
            'participant_model_params': dict(meta.get('participant_model_params', {})),
            'claude_team_agents': bool(meta.get('claude_team_agents', False)),
            'codex_multi_agents': bool(meta.get('codex_multi_agents', False)),
            'claude_team_agents_overrides': {str(k): bool(v) for k, v in dict(meta.get('claude_team_agents_overrides', {})).items()},
            'codex_multi_agents_overrides': {str(k): bool(v) for k, v in dict(meta.get('codex_multi_agents_overrides', {})).items()},
            'repair_mode': str(meta.get('repair_mode') or 'balanced'),
            'plain_mode': bool(meta.get('plain_mode', True)),
            'stream_mode': bool(meta.get('stream_mode', True)),
            'debate_mode': bool(meta.get('debate_mode', True)),
            'auto_merge': bool(meta.get('auto_merge', True)),
            'merge_target_path': meta.get('merge_target_path'),
            'sandbox_mode': bool(meta.get('sandbox_mode', False)),
            'sandbox_workspace_path': meta.get('sandbox_workspace_path'),
            'sandbox_generated': bool(meta.get('sandbox_generated', False)),
            'sandbox_cleanup_on_pass': bool(meta.get('sandbox_cleanup_on_pass', False)),
            'project_path': str(meta.get('project_path') or row.workspace_path),
            'self_loop_mode': self_loop_mode,
            'workspace_fingerprint': dict(meta.get('workspace_fingerprint', {})),
            'workspace_path': row.workspace_path,
            'status': row.status,
            'last_gate_reason': row.last_gate_reason,
            'max_rounds': row.max_rounds,
            'test_command': row.test_command,
            'lint_command': row.lint_command,
            'rounds_completed': row.rounds_completed,
            'cancel_requested': row.cancel_requested,
            'created_at': _iso_utc(row.created_at),
            'updated_at': _iso_utc(row.updated_at),
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
            'created_at': _iso_utc(row.created_at),
        }
