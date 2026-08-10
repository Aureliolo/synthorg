"""The four-table rebuild, run over data rather than an empty database.

SQLite cannot drop a foreign key, so removing the pins that made a task
undeletable means rebuilding each table that carried one: create, copy,
drop, rename. The schema-drift gate builds from empty and compares two
schemas, so it proves the shape and can say nothing about the rows. A copy
that omits a column, mis-orders the SELECT, or silently drops rows would
pass it and lose the spend, metric and decision history the tombstones
exist to keep resolvable.

These tests seed one row in each rebuilt table, apply the revision, and
assert the row is still there, unchanged, and no longer pinning the task.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from synthorg.persistence import migrations

pytestmark = pytest.mark.unit

_REVISION = "20260809000000_approval_source_admits_plan_review.sql"

_STAMP = "2026-08-01T09:00:00+00:00"

_INSERT_PROJECT = (
    "INSERT INTO projects (id, name, created_at, updated_at) "
    "VALUES ('proj-1', 'Project', ?, ?)"
)

_INSERT_TASK = (
    "INSERT INTO tasks (id, title, description, type, project, created_by) "
    "VALUES ('task-1', 'Ship it', 'D', 'feature', 'proj-1', 'operator')"
)

_INSERT_APPROVAL = (
    "INSERT INTO approvals "
    "(id, action_type, title, description, requested_by, risk_level, source, "
    "status, created_at, task_id) "
    "VALUES ('appr-1', 'code:write', 'Write it', 'why', 'agent-1', 'medium', "
    "'review_gate', 'pending', ?, 'task-1')"
)

_INSERT_COST = (
    "INSERT INTO cost_records "
    "(agent_id, task_id, provider, model, input_tokens, output_tokens, cost, "
    "currency, timestamp) "
    "VALUES ('agent-1', 'task-1', 'test-provider', 'test-small-001', "
    "10, 20, 0.5, 'USD', ?)"
)

_INSERT_METRIC = (
    "INSERT INTO task_metrics "
    "(id, agent_id, task_id, task_type, completed_at, is_success, complexity) "
    "VALUES ('metric-1', 'agent-1', 'task-1', 'feature', ?, 1, 'medium')"
)

_INSERT_DECISION = (
    "INSERT INTO decision_records "
    "(id, task_id, executing_agent_id, reviewer_agent_id, decision, "
    "recorded_at, version) "
    "VALUES ('dec-1', 'task-1', 'agent-1', 'agent-2', 'approved', ?, 1)"
)


def _revisions_before(into: Path) -> Path:
    """Copy the SQLite revisions that precede the one under test into *into*.

    Strictly preceding, not "all but this one": revisions are applied in
    name order, so holding out only the revision under test would apply its
    successors first and then run it against a schema from its own future.

    Args:
        into: Directory to populate.

    Returns:
        The populated revisions directory.
    """
    into.mkdir(parents=True, exist_ok=True)
    for source in sorted(migrations.revisions_dir("sqlite").glob("*.sql")):
        if source.name < _REVISION:
            (into / source.name).write_bytes(source.read_bytes())
    return into


def _add_the_revision(into: Path) -> None:
    """Copy the revision under test into an existing revisions directory."""
    source = migrations.revisions_dir("sqlite") / _REVISION
    (into / _REVISION).write_bytes(source.read_bytes())


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a connection with references enforced, like the app's."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def _seed_one_of_each(conn: sqlite3.Connection) -> None:
    """Write a task and one row in every table that pinned it."""
    conn.execute(_INSERT_PROJECT, (_STAMP, _STAMP))
    conn.execute(_INSERT_TASK)
    for statement in (
        _INSERT_APPROVAL,
        _INSERT_COST,
        _INSERT_METRIC,
        _INSERT_DECISION,
    ):
        conn.execute(statement, (_STAMP,))
    conn.commit()


@pytest.fixture
async def migrated(tmp_path: Path) -> Path:
    """Seed the pre-revision schema with one row per table, then migrate.

    Returns:
        Path to the migrated database.
    """
    revisions = _revisions_before(tmp_path / "revisions")
    db_path = tmp_path / "seeded.db"
    url = migrations.to_sqlite_url(str(db_path))
    await migrations.migrate_apply(url, revisions_path=revisions)

    with _connect(db_path) as conn:
        _seed_one_of_each(conn)

    _add_the_revision(revisions)
    await migrations.migrate_apply(url, revisions_path=revisions)
    return db_path


class TestTheRebuildKeepsEveryRow:
    """A rebuild that loses a row loses the history the tombstone answers for."""

    @pytest.mark.parametrize(
        ("table", "column", "expected"),
        [
            ("approvals", "id", "appr-1"),
            ("task_metrics", "id", "metric-1"),
            ("decision_records", "id", "dec-1"),
        ],
        ids=["approvals", "task_metrics", "decision_records"],
    )
    def test_the_row_survives(
        self,
        migrated: Path,
        table: str,
        column: str,
        expected: str,
    ) -> None:
        with _connect(migrated) as conn:
            rows = conn.execute(
                f"SELECT {column} FROM {table}"  # noqa: S608 -- fixed literals
            ).fetchall()
        assert [r[0] for r in rows] == [expected]

    def test_the_cost_row_survives_with_its_amount(self, migrated: Path) -> None:
        """The row a task could not be deleted for is the one that must stay."""
        with _connect(migrated) as conn:
            row = conn.execute(
                "SELECT agent_id, task_id, cost, currency FROM cost_records"
            ).fetchone()
        assert row == ("agent-1", "task-1", 0.5, "USD")

    def test_no_reference_is_left_dangling(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


class TestThePinsAreGone:
    """The point of the rebuild: spending money stops blocking a delete."""

    def test_the_task_can_now_be_deleted(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            conn.execute("DELETE FROM tasks WHERE id = 'task-1'")
            conn.commit()
            remaining = conn.execute("SELECT id FROM tasks").fetchall()
        assert remaining == []

    def test_the_records_keep_naming_the_id(self, migrated: Path) -> None:
        """Which is why the tombstone has to answer for it afterwards."""
        with _connect(migrated) as conn:
            conn.execute("DELETE FROM tasks WHERE id = 'task-1'")
            conn.commit()
            named = conn.execute("SELECT task_id FROM cost_records").fetchone()
        assert named[0] == "task-1"
