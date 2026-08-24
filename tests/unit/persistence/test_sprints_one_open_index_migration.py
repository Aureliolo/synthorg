"""The one-open-sprint-per-scope index, applied to a database with rows in it.

The schema-drift gate builds from empty and compares two schemas, so it
proves the index exists and can say nothing about what it refuses. What
matters here is behaviour on a live table: that a sprint already sitting in
a scope makes a second one impossible, that ``COALESCE(project, '')``
extends that to the org-wide scope both engines would otherwise leave
unguarded, and that completing a sprint frees its scope again.

The revision ships no repair pass by design (there is no deployed data to
repair), so the last case here is the counterpart: a database that DID
carry two open sprints for one scope refuses the migration loudly rather
than silently keeping one of them.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from synthorg.core.persistence_errors import MigrationError
from synthorg.persistence import migrations

pytestmark = pytest.mark.unit

_REVISION = "20260824140000_sprints_one_open_per_scope.sql"

_INSERT_SPRINT = (
    "INSERT INTO sprints (id, project, name, status, sprint_number, "
    "duration_days, start_date, end_date) VALUES (?, ?, ?, ?, ?, 14, ?, ?)"
)

_START = "2026-04-01T09:00:00+00:00"
_END = "2026-04-15T09:00:00+00:00"


def _revisions_before(into: Path) -> Path:
    """Copy the SQLite revisions preceding the one under test into *into*.

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
    """Yield a connection with references enforced, like the app's.

    Yields:
        The open connection.
    """
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def _insert_sprint(
    conn: sqlite3.Connection,
    sprint_id: str,
    *,
    project: str | None,
    status: str,
    number: int,
) -> None:
    """Write one sprint row directly, bypassing the repository."""
    conn.execute(
        _INSERT_SPRINT,
        (
            sprint_id,
            project,
            f"Sprint {number}",
            status,
            number,
            None if status == "planning" else _START,
            _END if status == "completed" else None,
        ),
    )
    conn.commit()


async def _migrate(db_path: Path, revisions: Path) -> None:
    """Apply every revision in *revisions* to the database at *db_path*."""
    await migrations.migrate_apply(
        migrations.to_sqlite_url(str(db_path)), revisions_path=revisions
    )


@pytest.fixture
async def migrated(tmp_path: Path) -> Path:
    """Apply every revision including the one under test.

    Returns:
        Path to the migrated database.
    """
    revisions = _revisions_before(tmp_path / "revisions")
    db_path = tmp_path / "sprints.db"
    await _migrate(db_path, revisions)
    _add_the_revision(revisions)
    await _migrate(db_path, revisions)
    return db_path


class TestTheIndexRefusesASecondOpenSprint:
    def test_a_project_may_only_have_one(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            _insert_sprint(conn, "s-1", project="proj-1", status="active", number=1)
            with pytest.raises(sqlite3.IntegrityError):
                _insert_sprint(
                    conn, "s-2", project="proj-1", status="planning", number=2
                )

    def test_the_org_wide_scope_is_guarded_too(self, migrated: Path) -> None:
        # Both engines treat NULLs as distinct in a unique index, so a bare
        # (project) index would admit this pair. COALESCE is what closes it.
        with _connect(migrated) as conn:
            _insert_sprint(conn, "ow-1", project=None, status="active", number=1)
            with pytest.raises(sqlite3.IntegrityError):
                _insert_sprint(conn, "ow-2", project=None, status="planning", number=2)

    def test_different_projects_are_different_scopes(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            _insert_sprint(conn, "a-1", project="proj-a", status="active", number=1)
            _insert_sprint(conn, "b-1", project="proj-b", status="active", number=1)
            assert conn.execute("SELECT COUNT(*) FROM sprints").fetchone()[0] == 2

    def test_completing_a_sprint_frees_its_scope(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            _insert_sprint(
                conn, "seq-1", project="proj-s", status="completed", number=1
            )
            _insert_sprint(conn, "seq-2", project="proj-s", status="active", number=2)
            assert conn.execute("SELECT COUNT(*) FROM sprints").fetchone()[0] == 2

    @pytest.mark.parametrize(
        "status", ["planning", "active", "in_review", "retrospective"]
    )
    def test_every_non_completed_status_occupies_the_scope(
        self, migrated: Path, status: str
    ) -> None:
        # The predicate is `status <> 'completed'`, matching the service's own
        # check exactly. A status it failed to cover would be a scope the
        # database left open while the service believed it closed.
        with _connect(migrated) as conn:
            _insert_sprint(conn, "held", project="proj-h", status=status, number=1)
            with pytest.raises(sqlite3.IntegrityError):
                _insert_sprint(
                    conn, "second", project="proj-h", status="planning", number=2
                )


class TestExistingDuplicatesRefuseTheMigration:
    async def test_two_open_sprints_stop_the_migration_loudly(
        self, tmp_path: Path
    ) -> None:
        """No repair pass, by decision: a duplicate pair is surfaced, not merged.

        Merging two divergent backlogs means deciding which delivery record
        is real, and completing the surplus would write undelivered work
        into the velocity history as delivered. So the migration refuses and
        an operator resolves it, rather than the migration choosing for them.

        The index is named in the failure, which is what makes the refusal
        actionable: an operator reading it knows which invariant stopped
        them rather than only that a migration failed.
        """
        revisions = _revisions_before(tmp_path / "revisions")
        db_path = tmp_path / "duplicates.db"
        await _migrate(db_path, revisions)
        with _connect(db_path) as conn:
            _insert_sprint(conn, "d-1", project="proj-d", status="active", number=1)
            _insert_sprint(conn, "d-2", project="proj-d", status="planning", number=2)

        _add_the_revision(revisions)
        with pytest.raises(MigrationError, match="idx_sprints_one_open_per_scope"):
            await _migrate(db_path, revisions)
