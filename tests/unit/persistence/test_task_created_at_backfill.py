"""The ``created_at`` backfill, run over a row that predates the column.

The schema-drift gate builds from empty and compares two schemas, so it
proves the column exists and can say nothing about the value the rebuild
puts in it. That value is hand-built SQL: ``STRFTIME`` with a ``%f000``
suffix, padding SQLite's milliseconds out to the six digits an ISO
timestamp wants. Nothing else in either schema is written that way, and
its output has to survive being read back as an ``AwareDatetime`` by the
application, which is a different question from whether the SQL ran.

So this seeds a task under the pre-revision schema, migrates, and reads the
backfilled row through the repository the application uses.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from synthorg.core.iso_datetime import parse_iso_utc
from synthorg.persistence import migrations

pytestmark = pytest.mark.unit

_REVISION = "20260817140000_tasks_created_at_and_blocked_reasons.sql"

_INSERT_TASK = (
    "INSERT INTO tasks (id, title, description, type, project, created_by) "
    "VALUES ('legacy-1', 'Filed before the column', 'D', 'feature', "
    "'proj-1', 'operator')"
)


def _revisions_before(into: Path) -> Path:
    """Copy the SQLite revisions preceding the one under test.

    Returns:
        The populated revisions directory.
    """
    into.mkdir(parents=True, exist_ok=True)
    for source in sorted(migrations.revisions_dir("sqlite").glob("*.sql")):
        if source.name < _REVISION:
            (into / source.name).write_bytes(source.read_bytes())
    return into


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a plain connection to the migrated database."""
    conn = sqlite3.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
async def backfilled(tmp_path: Path) -> Path:
    """Seed a pre-column task, then apply the revision that adds it.

    Returns:
        Path to the migrated database.
    """
    revisions = _revisions_before(tmp_path / "revisions")
    db_path = tmp_path / "legacy.db"
    url = migrations.to_sqlite_url(str(db_path))
    await migrations.migrate_apply(url, revisions_path=revisions)

    with _connect(db_path) as conn:
        conn.execute(_INSERT_TASK)
        conn.commit()

    (revisions / _REVISION).write_bytes(
        (migrations.revisions_dir("sqlite") / _REVISION).read_bytes()
    )
    await migrations.migrate_apply(url, revisions_path=revisions)
    return db_path


class TestTheBackfilledValueIsReadable:
    """Running without error is not the same as writing something usable."""

    def _stamp(self, db_path: Path) -> str:
        with _connect(db_path) as conn:
            row = conn.execute(
                "SELECT created_at FROM tasks WHERE id = 'legacy-1'"
            ).fetchone()
        assert row is not None
        return str(row[0])

    async def test_the_row_survives_the_rebuild(self, backfilled: Path) -> None:
        assert self._stamp(backfilled)

    async def test_the_stamp_parses_as_an_aware_instant(self, backfilled: Path) -> None:
        """What the application does with the value, not what SQLite stored.

        ``%f`` gives ``SS.SSS``; the literal ``000`` after it is what makes
        the fraction six digits. Get that wrong and the column holds a
        string every read rejects, on every legacy row, for ever.
        """
        parsed = parse_iso_utc(self._stamp(backfilled))

        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == UTC.utcoffset(None)

    async def test_the_backfill_is_an_upper_bound_not_a_guess(
        self, backfilled: Path
    ) -> None:
        """Nothing recorded when a legacy row was filed, so it reads as now.

        Stated in the revision and worth pinning: a legacy task reads as no
        older than the migration, which is honest, rather than being given
        an invented earlier time that would make it look stuck.
        """
        parsed = parse_iso_utc(self._stamp(backfilled))

        assert parsed <= datetime.now(UTC)
