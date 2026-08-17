"""The `tasks` rebuild, run over data rather than an empty database.

SQLite cannot alter a CHECK, so widening `tasks.blocked_reason` means
rebuilding the table: create, copy, drop, rename. The copy names all 31
columns twice, once in the INSERT list and once in the SELECT, both by hand.
The schema-drift gate builds from empty and compares two schemas, so it proves
the shape and can say nothing about the rows: a pair of columns transposed
between the two lists produces an identical schema and silently writes each
task's description into its title.

These tests seed a task carrying a distinct value in every column that could
be confused with a neighbour, apply the revision, and assert every value came
back where it started.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from synthorg.core.task_enums import BlockedReason
from synthorg.persistence import migrations

pytestmark = pytest.mark.unit

_REVISION = "20260818000000_blocked_reason_no_capable_agent.sql"

_STAMP = "2026-08-01T09:00:00+00:00"

_INSERT_PROJECT = (
    "INSERT INTO projects (id, name, created_at, updated_at) "
    "VALUES ('proj-1', 'Project', ?, ?)"
)

#: Every column the copy names, each with a value nothing else in the row
#: carries. Two transposed columns then show up as two wrong values rather than
#: as a pair that happens to match.
_TASK_COLUMNS: tuple[tuple[str, object], ...] = (
    ("id", "task-1"),
    ("title", "the-title"),
    ("description", "the-description"),
    ("type", "feature"),
    ("priority", "high"),
    ("project", "proj-1"),
    ("plan_id", "the-plan"),
    ("plan_item_id", "the-plan-item"),
    ("created_by", "the-creator"),
    ("requested_by_user_id", "the-requester"),
    ("assigned_to", "the-assignee"),
    ("status", "blocked"),
    ("estimated_complexity", "complex"),
    ("budget_limit", 12.5),
    ("deadline", "2026-09-01T00:00:00+00:00"),
    ("max_retries", 7),
    ("parent_task_id", "the-parent"),
    ("task_structure", "the-structure"),
    ("coordination_topology", "sequential"),
    ("reviewers", '["the-reviewer"]'),
    ("dependencies", '["the-dependency"]'),
    ("artifacts_expected", '["the-artifact"]'),
    ("acceptance_criteria", '["the-criterion"]'),
    ("delegation_chain", '["the-delegate"]'),
    ("hard_ceiling", 99.5),
    ("forecast_id", "the-forecast"),
    ("source", "the-source"),
    ("middleware_override", "the-middleware"),
    ("metadata", '{"the":"metadata"}'),
    ("hard_token_ceiling", 4242),
    ("blocked_reason", "reviewer_unstaffed"),
)


def _revisions_before(into: Path) -> Path:
    """Copy the SQLite revisions that precede the one under test into *into*.

    Strictly preceding, not "all but this one": revisions are applied in name
    order, so holding out only the revision under test would apply its
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


def _seed_a_fully_populated_task(conn: sqlite3.Connection) -> None:
    """Write one task with a distinct value in every copied column."""
    conn.execute(_INSERT_PROJECT, (_STAMP, _STAMP))
    names = ", ".join(name for name, _ in _TASK_COLUMNS)
    holders = ", ".join("?" for _ in _TASK_COLUMNS)
    conn.execute(
        f"INSERT INTO tasks ({names}) VALUES ({holders})",  # noqa: S608 -- fixed
        tuple(value for _, value in _TASK_COLUMNS),
    )
    conn.commit()


@pytest.fixture
async def migrated(tmp_path: Path) -> Path:
    """Seed the pre-revision schema with one full task, then migrate.

    Returns:
        Path to the migrated database.
    """
    revisions = _revisions_before(tmp_path / "revisions")
    db_path = tmp_path / "seeded.db"
    url = migrations.to_sqlite_url(str(db_path))
    await migrations.migrate_apply(url, revisions_path=revisions)

    with _connect(db_path) as conn:
        _seed_a_fully_populated_task(conn)

    _add_the_revision(revisions)
    await migrations.migrate_apply(url, revisions_path=revisions)
    return db_path


class TestTheRebuildPreservesEveryColumn:
    """A transposed pair produces the same schema and the wrong rows."""

    def test_every_value_comes_back_in_its_own_column(self, migrated: Path) -> None:
        names = ", ".join(name for name, _ in _TASK_COLUMNS)
        with _connect(migrated) as conn:
            row = conn.execute(
                f"SELECT {names} FROM tasks"  # noqa: S608 -- fixed literals
            ).fetchone()
        assert row == tuple(value for _, value in _TASK_COLUMNS)

    def test_the_only_task_is_still_the_only_task(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            assert conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0] == 1

    def test_no_reference_is_left_dangling(self, migrated: Path) -> None:
        """The rebuild drops and recreates a table other rows point at."""
        with _connect(migrated) as conn:
            assert conn.execute("PRAGMA foreign_key_check").fetchall() == []

    def test_the_indexes_come_back(self, migrated: Path) -> None:
        """A dropped table takes its indexes with it, silently."""
        with _connect(migrated) as conn:
            names = {
                row[0]
                for row in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'index' AND tbl_name = 'tasks'"
                )
            }
        assert {
            "idx_tasks_status",
            "idx_tasks_assigned_to",
            "idx_tasks_project",
            "idx_tasks_plan_id",
            "idx_tasks_status_blocked_reason",
        } <= names


class TestTheInvariantTheRebuildRestsOn:
    """``DROP TABLE tasks`` only works with references unenforced.

    Five-plus SQLite revisions rebuild a table other rows point at, and each
    one depends on yoyo running with ``PRAGMA foreign_keys`` off: the drop
    performs an implicit delete, and ``plans.parent_task_id`` references
    ``tasks`` ON DELETE RESTRICT, which is immediate and which
    ``defer_foreign_keys`` does not rescue. Every revision states that in a
    comment; nothing asserted it, so a change to how migrations connect would
    break all of them at once and only on a database with rows in it.
    """

    @pytest.mark.unit
    async def test_the_migration_connection_leaves_references_unenforced(
        self, tmp_path: Path
    ) -> None:
        revisions = tmp_path / "revisions"
        revisions.mkdir()
        # A migration that reports the pragma it ran under, which is the one
        # thing a schema comparison afterwards cannot tell us.
        (revisions / "20260101000000_probe.sql").write_text(
            "CREATE TABLE fk_probe (enforced INTEGER NOT NULL);\n"
            "INSERT INTO fk_probe (enforced)\n"
            "SELECT * FROM pragma_foreign_keys();\n",
            encoding="utf-8",
        )
        db_path = tmp_path / "probe.db"
        await migrations.migrate_apply(
            migrations.to_sqlite_url(str(db_path)), revisions_path=revisions
        )

        with _connect(db_path) as conn:
            enforced = conn.execute("SELECT enforced FROM fk_probe").fetchone()
        assert enforced[0] == 0


class TestTheWidenedCheck:
    """What the rebuild is for: the value the loop writes is now storable."""

    @pytest.mark.parametrize("reason", list(BlockedReason))
    def test_every_member_is_accepted(
        self, migrated: Path, reason: BlockedReason
    ) -> None:
        with _connect(migrated) as conn:
            conn.execute(
                "UPDATE tasks SET blocked_reason = ? WHERE id = 'task-1'",
                (reason.value,),
            )
            conn.commit()
            stored = conn.execute(
                "SELECT blocked_reason FROM tasks WHERE id = 'task-1'"
            ).fetchone()
        assert stored[0] == reason.value

    def test_a_reason_nothing_declares_is_still_refused(self, migrated: Path) -> None:
        """Widening admits the new member, not anything at all."""
        with _connect(migrated) as conn, pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "UPDATE tasks SET blocked_reason = 'invented' WHERE id = 'task-1'"
            )
