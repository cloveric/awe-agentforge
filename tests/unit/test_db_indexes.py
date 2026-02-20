from __future__ import annotations

from pathlib import Path

from sqlalchemy import text

from awe_agentcheck.db import Database


def test_database_schema_creates_expected_composite_indexes(tmp_path: Path):
    db_path = tmp_path / 'awe-indexes.db'
    db = Database(f'sqlite:///{db_path.as_posix()}')
    db.create_schema()

    with db.engine.connect() as conn:
        rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='index'")
        ).fetchall()

    names = {str(row[0]) for row in rows}
    assert 'ix_tasks_status_created_at' in names
    assert 'ix_tasks_status_updated_at' in names
    assert 'ix_task_events_task_id_created_at' in names
    assert 'ix_task_events_task_id_event_type_created_at' in names

