"""Dropping the meeting stack's tables and settings rows, over real rows.

Two claims in this revision are invisible to the schema-drift gate, which
compares declared shape rather than stored content:

* ``collaboration_metrics.meeting_contribution`` is RENAMED, not dropped, so
  every score an agent has already earned has to arrive on the other side
  under the new name. A drop-and-recreate would pass the drift gate and lose
  the column's history.
* The settings ``DELETE`` names a namespace beside every key list. Widened to
  a bare ``key IN (...)`` it would take a same-named key out of a namespace
  nothing in this revision touches, and the ``settings`` table's shape never
  changes either way.

These tests seed rows the revision has to act on plus rows it must leave
alone, apply it, and read both back.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from synthorg.persistence import migrations

pytestmark = pytest.mark.unit

_REVISION = "20260824000000_drop_meeting_ceremony_conflict_stack.sql"

_STAMP = "2026-08-01T09:00:00+00:00"

_INSERT_SETTING = (
    "INSERT INTO settings (namespace, key, value, updated_at) VALUES (?, ?, ?, ?)"
)

_INSERT_METRIC = (
    "INSERT INTO collaboration_metrics "
    "(id, agent_id, recorded_at, meeting_contribution, conflict_constructiveness) "
    "VALUES (?, ?, ?, ?, ?)"
)

#: Deleted keys, and for each a survivor that shares one half of its identity:
#: same key in another namespace, or same namespace with another key. A
#: ``DELETE`` that dropped either half of the pairing would take these too.
_DOOMED: tuple[tuple[str, str], ...] = (
    ("communication", "conflict_judge_model"),
    ("coordination", "ceremony_strategy"),
    ("strategy", "premortem_participants"),
    ("api", "max_meeting_context_keys"),
)
_SPARED: tuple[tuple[str, str], ...] = (
    ("engine", "conflict_judge_model"),
    ("engine", "ceremony_strategy"),
    ("communication", "premortem_participants"),
    ("communication", "bus_bridge_poll_timeout_seconds"),
    ("coordination", "company_departments_cas_retry_attempts"),
    ("api", "readiness_probe_timeout_seconds"),
)


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


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a connection with references enforced, like the app's."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
async def migrated(tmp_path: Path) -> Path:
    """Seed pre-drop rows, then apply the revision.

    Returns:
        Path to the migrated database.
    """
    revisions = _revisions_before(tmp_path / "revisions")
    db_path = tmp_path / "seeded.db"
    url = migrations.to_sqlite_url(str(db_path))
    await migrations.migrate_apply(url, revisions_path=revisions)

    with _connect(db_path) as conn:
        conn.execute(_INSERT_METRIC, ("m-1", "agent-1", _STAMP, 0.75, 0.5))
        conn.execute(_INSERT_METRIC, ("m-2", "agent-2", _STAMP, None, 0.25))
        for namespace, key in (*_DOOMED, *_SPARED):
            conn.execute(_INSERT_SETTING, (namespace, key, "seeded", _STAMP))
        conn.commit()

    source = migrations.revisions_dir("sqlite") / _REVISION
    (revisions / _REVISION).write_bytes(source.read_bytes())
    await migrations.migrate_apply(url, revisions_path=revisions)
    return db_path


def _settings(db_path: Path) -> set[tuple[str, str]]:
    """Read back which ``(namespace, key)`` pairs survived.

    Returns:
        The surviving pairs.
    """
    with _connect(db_path) as conn:
        rows = conn.execute("SELECT namespace, key FROM settings").fetchall()
    return {(str(ns), str(key)) for ns, key in rows}


class TestTheRenamedColumnCarriesItsValues:
    def test_the_scores_arrive_under_the_new_name(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            rows = conn.execute(
                "SELECT id, discussion_contribution FROM collaboration_metrics "
                "ORDER BY id"
            ).fetchall()
        assert rows == [("m-1", 0.75), ("m-2", None)]

    def test_the_old_name_is_gone(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(collaboration_metrics)"
                ).fetchall()
            }
        assert "discussion_contribution" in columns
        assert "meeting_contribution" not in columns

    def test_the_neighbouring_column_is_undisturbed(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            rows = conn.execute(
                "SELECT id, conflict_constructiveness FROM collaboration_metrics "
                "ORDER BY id"
            ).fetchall()
        assert rows == [("m-1", 0.5), ("m-2", 0.25)]


class TestTheTablesGo:
    @pytest.mark.parametrize(
        "table",
        ["conflict_escalations", "ceremony_scheduler_state", "meeting_cooldown"],
    )
    def test_table_dropped(self, migrated: Path, table: str) -> None:
        with _connect(migrated) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
        assert row is None


class TestTheSettingsDeleteIsScopedToItsNamespace:
    @pytest.mark.parametrize(("namespace", "key"), _DOOMED)
    def test_the_named_pair_goes(
        self, migrated: Path, namespace: str, key: str
    ) -> None:
        assert (namespace, key) not in _settings(migrated)

    @pytest.mark.parametrize(("namespace", "key"), _SPARED)
    def test_a_pair_sharing_one_half_stays(
        self, migrated: Path, namespace: str, key: str
    ) -> None:
        # Each of these shares either the namespace or the key with a doomed
        # pair; a DELETE matching on one half alone would take it too.
        assert (namespace, key) in _settings(migrated)
