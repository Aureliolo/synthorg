"""Dropping the roster's second copy of the capability rung, over real rows.

``AgentConfig`` is ``extra="forbid"``, so removing the field from the schema
without removing the key from the stored roster fails validation for every
agent at once and the whole company reads as empty behind one warning. The
schema-drift gate cannot see this: the ``settings`` table's shape never
changes, only its rows.

These tests seed a roster carrying the key, apply the revision, and assert the
rung survives where it belongs (inside ``model``, with the pair it describes)
and nowhere else.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import pytest

from synthorg.config.agent_schema import AgentConfig
from synthorg.persistence import migrations

pytestmark = pytest.mark.unit

_REVISION = "20260816000000_capability_ownership_and_latched_failures.sql"

_STAMP = "2026-08-01T09:00:00+00:00"

_INSERT_SETTING = (
    "INSERT INTO settings (namespace, key, value, updated_at) VALUES (?, ?, ?, ?)"
)

#: Written by ``json.dumps``, which is what the roster writer uses.
_ROSTER = json.dumps(
    [
        {
            "name": "Alice",
            "role": "CEO",
            "department": "executive",
            "capability": "expert",
            "model": {
                "provider": "test-provider",
                "model_id": "test-expert-001",
                "capability": "expert",
                "temperature": 0.7,
            },
        },
        {
            "name": "Bob",
            "role": "Developer",
            "department": "engineering",
            "capability": "expert",
            "model": {"provider": "test-provider", "model_id": "test-basic-001"},
        },
        {
            "name": "Carol",
            "role": "Designer",
            "department": "design",
            "model": {
                "provider": "test-provider",
                "model_id": "test-capable-001",
                "capability": "capable",
            },
        },
    ]
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
    """Seed a pre-drop roster, then apply the revision.

    Returns:
        Path to the migrated database.
    """
    revisions = _revisions_before(tmp_path / "revisions")
    db_path = tmp_path / "seeded.db"
    url = migrations.to_sqlite_url(str(db_path))
    await migrations.migrate_apply(url, revisions_path=revisions)

    with _connect(db_path) as conn:
        conn.execute(_INSERT_SETTING, ("company", "agents", _ROSTER, _STAMP))
        conn.execute(
            _INSERT_SETTING, ("company", "company_name", "Test Company", _STAMP)
        )
        conn.commit()

    source = migrations.revisions_dir("sqlite") / _REVISION
    (revisions / _REVISION).write_bytes(source.read_bytes())
    await migrations.migrate_apply(url, revisions_path=revisions)
    return db_path


def _roster(db_path: Path) -> list[dict[str, object]]:
    """Read the stored roster back.

    Returns:
        The agent list as stored.
    """
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE namespace = 'company' AND key = 'agents'"
        ).fetchone()
    assert row is not None
    agents: object = json.loads(str(row[0]))
    assert isinstance(agents, list)
    return cast("list[dict[str, object]]", agents)


class TestTheSecondCopyGoes:
    def test_no_agent_carries_a_top_level_rung(self, migrated: Path) -> None:
        assert [a for a in _roster(migrated) if "capability" in a] == []

    def test_every_agent_validates_against_the_schema(self, migrated: Path) -> None:
        # The failure this revision exists to prevent: one forbidden extra key
        # and the resolver answers a failed roster read with the code default.
        rebuilt = [AgentConfig.model_validate(a) for a in _roster(migrated)]
        assert [a.name for a in rebuilt] == ["Alice", "Bob", "Carol"]


class TestTheRungThatBelongsToThePairStays:
    def test_the_model_level_rung_survives(self, migrated: Path) -> None:
        rungs = [
            cast("dict[str, object]", a["model"]).get("capability")
            for a in _roster(migrated)
        ]
        assert rungs == ["expert", None, "capable"]

    def test_nothing_else_in_the_agent_is_disturbed(self, migrated: Path) -> None:
        alice = _roster(migrated)[0]
        assert alice["role"] == "CEO"
        assert alice["department"] == "executive"
        assert cast("dict[str, object]", alice["model"])["temperature"] == 0.7

    def test_an_unrelated_setting_is_left_alone(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            row = conn.execute(
                "SELECT value FROM settings "
                "WHERE namespace = 'company' AND key = 'company_name'"
            ).fetchone()
        assert row == ("Test Company",)
