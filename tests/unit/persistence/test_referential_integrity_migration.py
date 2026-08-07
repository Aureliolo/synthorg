"""The referential-integrity revision, run over data rather than an empty DB.

The schema-drift gate builds from empty, so it compares two schemas and
never sees what a migration does to rows. That is the hole a previous
version of this revision fell through: it deleted orphan plans and left
their evaluation reports pointing at the dropped ids, because yoyo runs
SQLite with ``foreign_keys`` at its OFF default and the declared cascade
never fired. The database then failed ``PRAGMA foreign_key_check`` on
rows the migration itself had created.

These tests seed the exact shape first and assert on the result.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from synthorg.persistence import migrations

pytestmark = pytest.mark.unit

_REVISION = "20260807000000_referential_integrity_and_project_timestamps.sql"

_STAMP = "2026-01-01T00:00:00+00:00"
_EPOCH = "1970-01-01T00:00:00+00:00"

_INSERT_PROJECT = "INSERT INTO projects (id, name) VALUES ('proj-1', 'Project')"

_INSERT_TASK = (
    "INSERT INTO tasks (id, title, description, type, project, created_by) "
    "VALUES (?, 'T', 'D', 'feature', 'proj-1', 'operator')"
)

_INSERT_PLAN = (
    "INSERT INTO plans "
    "(id, project, objective_id, objective_title, parent_task_id, items, "
    "status, created_at, updated_at) "
    "VALUES (?, 'proj-1', 'obj-1', 'Objective', ?, '[]', 'planning', ?, ?)"
)

_INSERT_REPORT = (
    "INSERT INTO initiative_evaluation_report "
    "(record_id, plan_id, project_id, attempt, verdict_summary, verdicts, "
    "objective_met, evaluated_at) "
    "VALUES (?, ?, 'proj-1', 1, 'summary', '[]', 0, ?)"
)

_INSERT_COMMENT = (
    "INSERT INTO plan_item_comments "
    "(id, plan_id, item_id, author, body, created_at) "
    "VALUES (?, ?, 'item-1', 'operator', 'a remark', ?)"
)


def _revisions_before(into: Path) -> Path:
    """Copy every SQLite revision except the one under test into *into*.

    Args:
        into: Directory to populate.

    Returns:
        The populated revisions directory.
    """
    into.mkdir(parents=True, exist_ok=True)
    for source in sorted(migrations.revisions_dir("sqlite").glob("*.sql")):
        if source.name != _REVISION:
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


def _seed_two_plans(conn: sqlite3.Connection) -> None:
    """Write one orphaned plan and one properly parented plan.

    Both carry an evaluation report and a comment, so the migration has
    to remove exactly one set and keep the other.
    """
    conn.execute(_INSERT_PROJECT)
    conn.execute(_INSERT_TASK, ("t-1",))
    for plan_id, parent in (("plan-orphan", "t-ghost"), ("plan-live", "t-1")):
        conn.execute(_INSERT_PLAN, (plan_id, parent, _STAMP, _STAMP))
        conn.execute(_INSERT_REPORT, (f"report-{plan_id}", plan_id, _STAMP))
        conn.execute(_INSERT_COMMENT, (f"comment-{plan_id}", plan_id, _STAMP))
    conn.commit()


def _ids(conn: sqlite3.Connection, sql: str) -> set[str]:
    return {row[0] for row in conn.execute(sql).fetchall()}


@pytest.fixture
async def migrated(tmp_path: Path) -> Path:
    """Seed the pre-revision schema with orphans, then apply the revision.

    Returns:
        Path to the migrated database.
    """
    revisions = _revisions_before(tmp_path / "revisions")
    db_path = tmp_path / "seeded.db"
    url = migrations.to_sqlite_url(str(db_path))
    await migrations.migrate_apply(url, revisions_path=revisions)

    with _connect(db_path) as conn:
        _seed_two_plans(conn)

    _add_the_revision(revisions)
    await migrations.migrate_apply(url, revisions_path=revisions)
    return db_path


class TestTheOrphanAndItsDependentsGoTogether:
    def test_no_reference_is_left_dangling(self, migrated: Path) -> None:
        """The check the previous revision failed, on the rows it created."""
        with _connect(migrated) as conn:
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    def test_the_orphaned_plan_is_removed(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            assert _ids(conn, "SELECT id FROM plans") == {"plan-live"}

    def test_its_evaluation_report_goes_with_it(self, migrated: Path) -> None:
        """Postgres cascades here; SQLite runs FK-off, so it must be explicit."""
        with _connect(migrated) as conn:
            surviving = _ids(conn, "SELECT record_id FROM initiative_evaluation_report")
        assert surviving == {"report-plan-live"}

    def test_its_comments_go_with_it(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            surviving = _ids(conn, "SELECT id FROM plan_item_comments")
        assert surviving == {"comment-plan-live"}


class TestTheReferencesAreLive:
    def test_deleting_a_task_that_owns_a_plan_is_refused(self, migrated: Path) -> None:
        with _connect(migrated) as conn, pytest.raises(sqlite3.IntegrityError):
            conn.execute("DELETE FROM tasks WHERE id = 't-1'")

    def test_deleting_a_plan_takes_its_comments(self, migrated: Path) -> None:
        """A comment is a remark ON a plan; it means nothing without one."""
        with _connect(migrated) as conn:
            conn.execute("DELETE FROM initiative_evaluation_report")
            conn.execute("DELETE FROM plans WHERE id = 'plan-live'")
            conn.commit()
            surviving = _ids(conn, "SELECT id FROM plan_item_comments")
        assert surviving == set()

    def test_a_plan_naming_no_task_cannot_be_inserted(self, migrated: Path) -> None:
        with _connect(migrated) as conn, pytest.raises(sqlite3.IntegrityError):
            conn.execute(_INSERT_PLAN, ("plan-new", "t-ghost", _STAMP, _STAMP))


class TestTheProjectBackfill:
    def test_a_backfilled_project_has_never_been_edited(self, migrated: Path) -> None:
        """``updated_at`` cannot honestly be later than the date we inferred."""
        with _connect(migrated) as conn:
            row = conn.execute(
                "SELECT created_at, updated_at FROM projects WHERE id = 'proj-1'"
            ).fetchone()
        assert row[1] == row[0]

    async def test_an_owning_plan_dates_the_project(self, tmp_path: Path) -> None:
        """The first plan is drafted for the project, so it approximates its start."""
        revisions = _revisions_before(tmp_path / "revisions")
        db_path = tmp_path / "dated.db"
        url = migrations.to_sqlite_url(str(db_path))
        await migrations.migrate_apply(url, revisions_path=revisions)
        with _connect(db_path) as conn:
            conn.execute(_INSERT_PROJECT)
            conn.execute(_INSERT_TASK, ("t-1",))
            conn.execute(_INSERT_PLAN, ("plan-live", "t-1", _STAMP, _STAMP))
            conn.commit()

        _add_the_revision(revisions)
        await migrations.migrate_apply(url, revisions_path=revisions)

        with _connect(db_path) as conn:
            created = conn.execute(
                "SELECT created_at FROM projects WHERE id = 'proj-1'"
            ).fetchone()[0]
        assert created == _STAMP

    async def test_a_project_with_nothing_to_date_it_lands_at_the_epoch(
        self, tmp_path: Path
    ) -> None:
        revisions = _revisions_before(tmp_path / "revisions")
        db_path = tmp_path / "undated.db"
        url = migrations.to_sqlite_url(str(db_path))
        await migrations.migrate_apply(url, revisions_path=revisions)
        with _connect(db_path) as conn:
            conn.execute(_INSERT_PROJECT)
            conn.commit()

        _add_the_revision(revisions)
        await migrations.migrate_apply(url, revisions_path=revisions)

        with _connect(db_path) as conn:
            created = conn.execute(
                "SELECT created_at FROM projects WHERE id = 'proj-1'"
            ).fetchone()[0]
        assert created == _EPOCH
