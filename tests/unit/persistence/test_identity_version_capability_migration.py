"""The capability rename, carried into the identity archive.

``20260811000000_capability_vocabulary`` rewrote the roster in ``settings`` and
the rungs in ``model_pin_validations``. It did not touch
``agent_identity_versions``, which stores whole ``AgentIdentity`` objects under
the same ``extra="forbid"``, so every snapshot ever taken became unreadable and
every read degraded to a warning naming ``schema_drift``.

The drift gate cannot see this: it builds both schemas from empty and compares
shapes, and this table's shape never changed. Only the rows did. So these tests
seed pre-rename snapshots, apply the revision, and assert the snapshot both
speaks the new vocabulary and validates as the model the reader constructs.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast

import pytest

from synthorg.core.agent import AgentIdentity
from synthorg.persistence import migrations

pytestmark = pytest.mark.unit

_REVISION = "20260816000000_capability_ownership_and_latched_failures.sql"

_STAMP = "2026-08-01T09:00:00+00:00"

_INSERT_VERSION = (
    "INSERT INTO agent_identity_versions "
    "(entity_id, version, content_hash, snapshot, saved_by, saved_at) "
    "VALUES (?, ?, ?, ?, ?, ?)"
)


def _snapshot(name: str, model: dict[str, object]) -> str:
    """Build the stored form of one identity snapshot.

    Returns:
        The snapshot as the repository writes it.
    """
    return json.dumps(
        {
            "name": name,
            "role": "Developer",
            "department": "engineering",
            "hiring_date": "2026-01-01",
            "model": {"provider": "test-provider", "model_id": "test-basic-001"}
            | model,
        }
    )


#: One row per shape the live archive turned out to hold: each old rung, the
#: blank rung a third of the rows carried, and a row already migrated by the
#: writer rather than by SQL.
_SEED: tuple[tuple[str, dict[str, object]], ...] = (
    ("expert-rung", {"model_tier": "large", "fallback_model": None}),
    ("capable-rung", {"model_tier": "medium", "fallback_model": None}),
    ("basic-rung", {"model_tier": "small", "fallback_model": None}),
    ("local-rung", {"model_tier": "local-small", "fallback_model": None}),
    ("blank-rung", {"model_tier": "", "fallback_model": None}),
    ("named-fallback", {"model_tier": "large", "fallback_model": "test-basic-001"}),
    ("already-new", {"capability": "expert"}),
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


def _seed_pre_rename(conn: sqlite3.Connection) -> None:
    """Write the snapshots an installation would hold before the rename."""
    for index, (name, model) in enumerate(_SEED, start=1):
        conn.execute(
            _INSERT_VERSION,
            (name, index, f"hash-{index}", _snapshot(name, model), "operator", _STAMP),
        )
    conn.commit()


@pytest.fixture
async def seeded(tmp_path: Path) -> Path:
    """Migrate to just before the revision, then seed the archive.

    Returns:
        Path to the seeded database.
    """
    revisions = _revisions_before(tmp_path / "revisions")
    db_path = tmp_path / "seeded.db"
    await migrations.migrate_apply(
        migrations.to_sqlite_url(str(db_path)), revisions_path=revisions
    )
    with _connect(db_path) as conn:
        _seed_pre_rename(conn)
    return db_path


@pytest.fixture
async def migrated(seeded: Path, tmp_path: Path) -> Path:
    """Apply the revision under test to the seeded archive.

    Returns:
        Path to the migrated database.
    """
    revisions = tmp_path / "revisions"
    source = migrations.revisions_dir("sqlite") / _REVISION
    (revisions / _REVISION).write_bytes(source.read_bytes())
    await migrations.migrate_apply(
        migrations.to_sqlite_url(str(seeded)), revisions_path=revisions
    )
    return seeded


def _snapshot_of(db_path: Path, entity_id: str) -> dict[str, object]:
    """Read one stored snapshot back.

    Returns:
        The snapshot as stored.
    """
    with _connect(db_path) as conn:
        row = conn.execute(
            "SELECT snapshot FROM agent_identity_versions WHERE entity_id = ?",
            (entity_id,),
        ).fetchone()
    assert row is not None
    snapshot: object = json.loads(str(row[0]))
    assert isinstance(snapshot, dict)
    return cast("dict[str, object]", snapshot)


def _model_of(db_path: Path, entity_id: str) -> dict[str, object]:
    """Read one snapshot's model block.

    Returns:
        The ``model`` mapping as stored.
    """
    model = _snapshot_of(db_path, entity_id)["model"]
    assert isinstance(model, dict)
    return cast("dict[str, object]", model)


class TestTheArchiveWasUnreadable:
    """The defect itself: what the reader does with a pre-rename snapshot."""

    def test_a_seeded_snapshot_fails_the_model_it_is_read_back_into(
        self, seeded: Path
    ) -> None:
        with pytest.raises(ValueError, match="model_tier"):
            AgentIdentity.model_validate(_snapshot_of(seeded, "expert-rung"))


class TestEachRungLandsOnItsSuccessor:
    """A rung left behind is a snapshot the reader still cannot construct."""

    @pytest.mark.parametrize(
        ("entity_id", "expected"),
        [
            ("expert-rung", "expert"),
            ("capable-rung", "capable"),
            ("basic-rung", "basic"),
            ("local-rung", "basic"),
        ],
        ids=["large", "medium", "small", "local-small"],
    )
    def test_the_old_rung_is_read_back_as_the_new_one(
        self, migrated: Path, entity_id: str, expected: str
    ) -> None:
        assert _model_of(migrated, entity_id)["capability"] == expected

    def test_a_rung_nobody_wrote_stays_absent(self, migrated: Path) -> None:
        """Blank is not evidence for a rung, so it becomes no rung at all."""
        model = _model_of(migrated, "blank-rung")
        assert "capability" not in model
        identity = AgentIdentity.model_validate(_snapshot_of(migrated, "blank-rung"))
        assert identity.model.capability is None

    def test_a_snapshot_already_speaking_the_new_vocabulary_is_untouched(
        self, migrated: Path
    ) -> None:
        assert _model_of(migrated, "already-new") == {
            "provider": "test-provider",
            "model_id": "test-basic-001",
            "capability": "expert",
        }


class TestTheDeletedFieldGoes:
    """``fallback_model`` was removed outright, so it moves nowhere."""

    @pytest.mark.parametrize(
        "entity_id",
        ["expert-rung", "blank-rung", "named-fallback"],
        ids=["null-with-rung", "null-without-rung", "named"],
    )
    def test_the_key_is_gone(self, migrated: Path, entity_id: str) -> None:
        # A JSON null is the interesting case: an extraction-based existence
        # test reads it as SQL NULL and would leave the key in place.
        assert "fallback_model" not in _model_of(migrated, entity_id)


class TestTheArchiveIsReadableAgain:
    """The claim the run actually needed: every snapshot constructs."""

    def test_every_seeded_snapshot_validates(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            rows = conn.execute(
                "SELECT entity_id, snapshot FROM agent_identity_versions"
            ).fetchall()
        assert len(rows) == len(_SEED)
        for entity_id, snapshot in rows:
            identity = AgentIdentity.model_validate(json.loads(str(snapshot)))
            assert identity.name == entity_id
