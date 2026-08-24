"""Personality leaves the models, so it has to leave the rows too.

``AgentConfig`` and ``AgentIdentity`` are both ``extra="forbid"``. A stored
roster element or identity snapshot still carrying ``personality`` therefore
stops validating the moment the field is gone, and the failure is not local:
``company.agents`` is one settings row holding the whole roster, so one stale
key empties every agent at once.

The drift gate cannot see this. It builds both schemas from empty and compares
shapes, and neither table's shape changes here; only the rows do. So these
tests seed the pre-removal shapes, apply the revision, and assert both that the
key is gone and that what is left constructs as the model the reader builds.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import pytest

from synthorg.config.agent_schema import AgentConfig
from synthorg.core.agent import AgentIdentity
from synthorg.persistence import migrations

pytestmark = pytest.mark.unit

_REVISION = "20260824150000_drop_personality_surface.sql"

_STAMP = "2026-08-01T09:00:00+00:00"

_INSERT_SETTING = (
    "INSERT INTO settings (namespace, key, value, updated_at) VALUES (?, ?, ?, ?)"
)
_INSERT_VERSION = (
    "INSERT INTO agent_identity_versions "
    "(entity_id, version, content_hash, snapshot, saved_by, saved_at) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)

_MODEL: dict[str, object] = {
    "provider": "test-provider",
    "model_id": "test-capable-001",
}

#: The personality block a preset expanded to, in full: the five scored floats,
#: the six behavioural enums and the three free-text fields.
_PERSONALITY: dict[str, object] = {
    "traits": ["analytical", "pragmatic"],
    "communication_style": "concise and technical",
    "risk_tolerance": "low",
    "creativity": "medium",
    "description": "A methodical analyst.",
    "openness": 0.6,
    "conscientiousness": 0.9,
    "extraversion": 0.3,
    "agreeableness": 0.5,
    "stress_response": 0.7,
    "decision_making": "analytical",
    "collaboration": "pair",
    "verbosity": "balanced",
    "conflict_approach": "compromise",
}

#: One roster element per shape a live installation turned out to hold: a
#: preset-expanded agent, one carrying only the preset name, and one that never
#: had either.
_ROSTER: tuple[dict[str, object], ...] = (
    {
        "name": "Ada Byron",
        "role": "Backend Developer",
        "department": "engineering",
        "model": _MODEL,
        "personality": _PERSONALITY,
        "personality_preset": "methodical_analyst",
    },
    {
        "name": "Grace Bell",
        "role": "CEO",
        "department": "executive",
        "model": _MODEL,
        "personality_preset": "visionary_leader",
    },
    {
        "name": "Ida Ross",
        "role": "Completion Reviewer",
        "department": "quality_assurance",
        "model": _MODEL,
    },
)

_SNAPSHOTS: tuple[tuple[str, dict[str, object]], ...] = (
    (
        "with-personality",
        {
            "name": "Ada Byron",
            "role": "Backend Developer",
            "department": "engineering",
            "hiring_date": "2026-01-01",
            "model": _MODEL,
            "personality": _PERSONALITY,
        },
    ),
    (
        "without-personality",
        {
            "name": "Ida Ross",
            "role": "Completion Reviewer",
            "department": "quality_assurance",
            "hiring_date": "2026-01-01",
            "model": _MODEL,
        },
    ),
)

_RETIRED_SETTINGS: tuple[str, ...] = (
    "personality_trimming_enabled",
    "personality_max_tokens_override",
    "personality_trimming_notify",
)


def _revisions_before(into: Path) -> Path:
    """Copy the SQLite revisions preceding the one under test into *into*.

    Strictly preceding, not "all but this one": revisions apply in name order,
    so holding out only the revision under test would run it against a schema
    from its own future.

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


@contextmanager
def _connect(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Yield a connection with references enforced, like the app's."""
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def _seed(conn: sqlite3.Connection) -> None:
    """Write the roster, archive and settings an installation would hold."""
    conn.execute(
        _INSERT_SETTING,
        ("company", "agents", json.dumps(list(_ROSTER)), _STAMP),
    )
    for key in _RETIRED_SETTINGS:
        conn.execute(_INSERT_SETTING, ("engine", key, "true", _STAMP))
    for index, (entity_id, snapshot) in enumerate(_SNAPSHOTS, start=1):
        conn.execute(
            _INSERT_VERSION,
            (
                entity_id,
                index,
                f"hash-{index}",
                json.dumps(snapshot),
                "operator",
                _STAMP,
            ),
        )
    conn.commit()


@pytest.fixture
async def seeded(tmp_path: Path) -> Path:
    """Migrate to just before the revision, then seed the pre-removal rows.

    Returns:
        Path to the seeded database.
    """
    revisions = _revisions_before(tmp_path / "revisions")
    db_path = tmp_path / "seeded.db"
    await migrations.migrate_apply(
        migrations.to_sqlite_url(str(db_path)), revisions_path=revisions
    )
    with _connect(db_path) as conn:
        _seed(conn)
    return db_path


@pytest.fixture
async def migrated(seeded: Path, tmp_path: Path) -> Path:
    """Apply the revision under test to the seeded database.

    Returns:
        Path to the migrated database.
    """
    revisions = tmp_path / "revision-under-test"
    revisions.mkdir(parents=True, exist_ok=True)
    source = migrations.revisions_dir("sqlite") / _REVISION
    (revisions / _REVISION).write_bytes(source.read_bytes())
    await migrations.migrate_apply(
        migrations.to_sqlite_url(str(seeded)), revisions_path=revisions
    )
    return seeded


def _roster(db_path: Path) -> list[dict[str, object]]:
    """Read the stored roster back.

    Returns:
        The ``company.agents`` array as stored.
    """
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE namespace = 'company' AND key = 'agents'"
        ).fetchone()
    assert row is not None
    parsed: object = json.loads(str(row[0]))
    assert isinstance(parsed, list)
    return cast("list[dict[str, object]]", parsed)


def _snapshot(db_path: Path, entity_id: str) -> dict[str, object]:
    """Read one stored identity snapshot back.

    Returns:
        The snapshot as stored.
    """
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT snapshot FROM agent_identity_versions WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
    assert row is not None
    parsed: object = json.loads(str(row[0]))
    assert isinstance(parsed, dict)
    return cast("dict[str, object]", parsed)


class TestTheStoredRowsWereUnreadable:
    """The defect itself: what the reader does with a pre-removal row."""

    def test_a_seeded_roster_element_fails_the_config_it_is_read_into(
        self, seeded: Path
    ) -> None:
        with pytest.raises(ValueError, match="personality"):
            AgentConfig.model_validate(_roster(seeded)[0])

    def test_a_seeded_snapshot_fails_the_identity_it_is_read_into(
        self, seeded: Path
    ) -> None:
        with pytest.raises(ValueError, match="personality"):
            AgentIdentity.model_validate(_snapshot(seeded, "with-personality"))


class TestTheRosterIsReadableAgain:
    """One stale key empties every agent, so every element is checked."""

    def test_no_element_carries_either_key(self, migrated: Path) -> None:
        for element in _roster(migrated):
            assert "personality" not in element
            assert "personality_preset" not in element

    def test_every_element_validates(self, migrated: Path) -> None:
        roster = _roster(migrated)
        assert len(roster) == len(_ROSTER)
        assert [AgentConfig.model_validate(e).name for e in roster] == [
            "Ada Byron",
            "Grace Bell",
            "Ida Ross",
        ]

    def test_the_surviving_fields_are_untouched(self, migrated: Path) -> None:
        config = AgentConfig.model_validate(_roster(migrated)[0])
        assert config.role == "Backend Developer"
        assert config.department == "engineering"
        assert config.model == _MODEL


class TestTheArchiveIsReadableAgain:
    def test_every_snapshot_validates(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            rows = conn.execute(
                "SELECT entity_id, snapshot FROM agent_identity_versions"
            ).fetchall()
        assert len(rows) == len(_SNAPSHOTS)
        for entity_id, snapshot in rows:
            identity = AgentIdentity.model_validate(json.loads(str(snapshot)))
            assert str(identity.department) in {"engineering", "quality_assurance"}
            assert entity_id in {"with-personality", "without-personality"}

    def test_a_snapshot_that_never_had_the_key_is_untouched(
        self, migrated: Path
    ) -> None:
        assert _snapshot(migrated, "without-personality") == {
            "name": "Ida Ross",
            "role": "Completion Reviewer",
            "department": "quality_assurance",
            "hiring_date": "2026-01-01",
            "model": _MODEL,
        }


class TestTheSurfaceIsGone:
    def test_the_custom_presets_table_is_dropped(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            found = conn.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type = 'table' AND name = 'custom_presets'"
            ).fetchall()
        assert found == []

    def test_the_retired_settings_rows_are_deleted(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            rows = conn.execute(
                "SELECT key FROM settings WHERE namespace = 'engine'"
            ).fetchall()
        assert {str(key) for (key,) in rows}.isdisjoint(_RETIRED_SETTINGS)
