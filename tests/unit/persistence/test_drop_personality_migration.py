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
from collections.abc import Callable, Iterator
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
_INSERT_CHECKPOINT = (
    "INSERT INTO checkpoints "
    "(id, execution_id, agent_id, task_id, turn_number, context_json, created_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_PARKED = (
    "INSERT INTO parked_contexts "
    "(id, execution_id, agent_id, task_id, approval_id, parked_at, context_json) "
    "VALUES (?, ?, ?, ?, ?, ?, ?)"
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

#: Settings rows whose value is not JSON at all. ``settings`` is one table for
#: every namespace, and the roster rewrite must reach its own row without
#: casting theirs: a Fernet blob and a bare enum name are what actually sits
#: beside it.
_NON_JSON_SETTINGS: tuple[tuple[str, str, str], ...] = (
    ("providers", "configs", "gAAAAABm-not-valid-json-ciphertext=="),
    ("security", "prompt_injection_action", "log_only"),
)

#: A roster nobody should have, and the one this revision has to survive. Every
#: element is a JSON type ``json_each`` hands back differently, which is what
#: the CASE in step 3 exists for; a single unhandled one rolls the upgrade back
#: and takes the well-formed agents beside it down too.
_HAND_EDITED_ROSTER: tuple[object, ...] = (
    {
        "name": "Ada Byron",
        "role": "Backend Developer",
        "department": "engineering",
        "model": _MODEL,
        "personality": _PERSONALITY,
    },
    #: An explicit JSON null, which json_extract cannot tell from a missing key.
    {
        "name": "Grace Bell",
        "role": "CEO",
        "department": "executive",
        "model": _MODEL,
        "personality": None,
    },
    "a bare string",
    True,
    False,
    3,
    [1, 2],
    None,
)


def _context(identity: dict[str, object]) -> str:
    """Serialise an ``AgentContext``-shaped row around *identity*.

    Returns:
        The ``context_json`` an execution would have persisted.
    """
    return json.dumps(
        {
            "execution_id": "exec-1",
            "identity": identity,
            "turn_count": 2,
            "max_turns": 40,
        }
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
    """Write the roster, archive, contexts and settings an install would hold."""
    conn.execute(
        _INSERT_SETTING,
        ("company", "agents", json.dumps(list(_ROSTER)), _STAMP),
    )
    for key in _RETIRED_SETTINGS:
        conn.execute(_INSERT_SETTING, ("engine", key, "true", _STAMP))
    for namespace, key, value in _NON_JSON_SETTINGS:
        conn.execute(_INSERT_SETTING, (namespace, key, value, _STAMP))
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
    ids = ("cp-with", "cp-without")
    for row_id, (_, identity) in zip(ids, _SNAPSHOTS, strict=True):
        conn.execute(
            _INSERT_CHECKPOINT,
            (row_id, "exec-1", "agent-1", "task-1", 2, _context(identity), _STAMP),
        )
    ids = ("pc-with", "pc-without")
    for row_id, (_, identity) in zip(ids, _SNAPSHOTS, strict=True):
        conn.execute(
            _INSERT_PARKED,
            (
                row_id,
                "exec-1",
                "agent-1",
                "task-1",
                "appr-1",
                _STAMP,
                _context(identity),
            ),
        )
    conn.commit()


def _seed_hand_edited(conn: sqlite3.Connection) -> None:
    """Write the shapes a hand-edited install holds, which must not abort."""
    conn.execute(
        _INSERT_SETTING,
        ("company", "agents", json.dumps(list(_HAND_EDITED_ROSTER)), _STAMP),
    )
    for namespace, key, value in _NON_JSON_SETTINGS:
        conn.execute(_INSERT_SETTING, (namespace, key, value, _STAMP))
    conn.execute(
        _INSERT_VERSION,
        ("scalar-snapshot", 1, "hash-scalar", '"personality"', "operator", _STAMP),
    )
    conn.execute(
        _INSERT_CHECKPOINT,
        ("cp-scalar", "exec-2", "agent-2", "task-2", 0, '"not an object"', _STAMP),
    )
    conn.execute(
        _INSERT_PARKED,
        (
            "pc-no-identity",
            "exec-2",
            "agent-2",
            "task-2",
            "appr-2",
            _STAMP,
            json.dumps({"execution_id": "exec-2"}),
        ),
    )
    conn.commit()


async def _seed_at_previous_revision(
    tmp_path: Path,
    name: str,
    seed: Callable[[sqlite3.Connection], None],
) -> Path:
    """Migrate a fresh database to just before the revision, then *seed* it.

    Returns:
        Path to the seeded database.
    """
    revisions = _revisions_before(tmp_path / f"revisions-{name}")
    db_path = tmp_path / f"{name}.db"
    await migrations.migrate_apply(
        migrations.to_sqlite_url(str(db_path)), revisions_path=revisions
    )
    with _connect(db_path) as conn:
        seed(conn)
    return db_path


async def _apply_revision(db_path: Path, tmp_path: Path) -> None:
    """Apply the revision under test, and only it, to *db_path*."""
    revisions = tmp_path / "revision-under-test"
    revisions.mkdir(parents=True, exist_ok=True)
    source = migrations.revisions_dir("sqlite") / _REVISION
    (revisions / _REVISION).write_bytes(source.read_bytes())
    await migrations.migrate_apply(
        migrations.to_sqlite_url(str(db_path)), revisions_path=revisions
    )


@pytest.fixture
async def seeded(tmp_path: Path) -> Path:
    """Migrate to just before the revision, then seed the pre-removal rows.

    Returns:
        Path to the seeded database.
    """
    return await _seed_at_previous_revision(tmp_path, "seeded", _seed)


@pytest.fixture
async def migrated(seeded: Path, tmp_path: Path) -> Path:
    """Apply the revision under test to the seeded database.

    Returns:
        Path to the migrated database.
    """
    await _apply_revision(seeded, tmp_path)
    return seeded


@pytest.fixture
async def migrated_hand_edited(tmp_path: Path) -> Path:
    """Apply the revision to a database holding hand-edited shapes.

    Returns:
        Path to the migrated database.
    """
    db_path = await _seed_at_previous_revision(
        tmp_path, "hand-edited", _seed_hand_edited
    )
    await _apply_revision(db_path, tmp_path)
    return db_path


def _roster(db_path: Path) -> list[object]:
    """Read the stored roster back, element types intact.

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
    return cast("list[object]", parsed)


def _roster_agents(db_path: Path) -> list[dict[str, object]]:
    """Read back only the roster elements that are objects.

    Returns:
        Every ``company.agents`` element an ``AgentConfig`` could be built from.
    """
    return [e for e in _roster(db_path) if isinstance(e, dict)]


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
        for element in _roster_agents(migrated):
            assert "personality" not in element
            assert "personality_preset" not in element

    def test_every_element_validates(self, migrated: Path) -> None:
        roster = _roster_agents(migrated)
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


def _identities(db_path: Path, table: str) -> dict[str, object]:
    """Read every stored context's ``identity`` member back.

    Returns:
        Row id mapped to the identity the stored context carries.
    """
    with _connect(db_path) as conn:
        rows = conn.execute(f"SELECT id, context_json FROM {table}").fetchall()  # noqa: S608
    out: dict[str, object] = {}
    for row_id, context_json in rows:
        parsed: object = json.loads(str(context_json))
        assert isinstance(parsed, dict)
        out[str(row_id)] = parsed.get("identity")
    return out


class TestInFlightContextsSurviveTheUpgrade:
    """Both tables embed an identity, and both outlive a restart by design.

    A parked context waits on a human with no deadline and a checkpoint is what
    crash recovery replays, so an upgrade lands between the write and the read.
    Neither reader degrades: they validate into ``AgentContext`` and raise, and
    the parked one preserves the row afterwards, so a resume that cannot parse
    fails identically for ever with the approval already decided.
    """

    @pytest.mark.parametrize("table", ["checkpoints", "parked_contexts"])
    def test_a_seeded_context_fails_the_identity_it_is_read_into(
        self, seeded: Path, table: str
    ) -> None:
        stored = _identities(seeded, table)
        with_key = next(v for k, v in stored.items() if k.endswith("-with"))
        with pytest.raises(ValueError, match="personality"):
            AgentIdentity.model_validate(with_key)

    @pytest.mark.parametrize("table", ["checkpoints", "parked_contexts"])
    def test_every_stored_context_validates_after(
        self, migrated: Path, table: str
    ) -> None:
        stored = _identities(migrated, table)
        assert len(stored) == len(_SNAPSHOTS)
        for identity in stored.values():
            assert AgentIdentity.model_validate(identity).name in {
                "Ada Byron",
                "Ida Ross",
            }

    @pytest.mark.parametrize("table", ["checkpoints", "parked_contexts"])
    def test_the_surrounding_context_fields_are_untouched(
        self, migrated: Path, table: str
    ) -> None:
        with _connect(migrated) as conn:
            row = conn.execute(
                f"SELECT context_json FROM {table} WHERE id LIKE '%-with'"  # noqa: S608
            ).fetchone()
        assert row is not None
        parsed: object = json.loads(str(row[0]))
        assert isinstance(parsed, dict)
        assert parsed["execution_id"] == "exec-1"
        assert parsed["turn_count"] == 2
        assert parsed["max_turns"] == 40


class TestAHandEditedInstallDoesNotAbortTheUpgrade:
    """One malformed row must not roll back the rewrite for every other row.

    ``settings`` holds every namespace in one table, so the roster rewrite runs
    beside a Fernet blob and a bare enum name; the archive and both context
    columns accept a scalar that the removal operators refuse. Each of these
    would take the whole revision down rather than skip a row.
    """

    def test_the_well_formed_agents_are_still_cleaned(
        self, migrated_hand_edited: Path
    ) -> None:
        agents = _roster_agents(migrated_hand_edited)
        assert len(agents) == 2
        for element in agents:
            assert "personality" not in element
            assert AgentConfig.model_validate(element).name in {
                "Ada Byron",
                "Grace Bell",
            }

    def test_an_explicit_json_null_is_removed_not_skipped(
        self, migrated_hand_edited: Path
    ) -> None:
        grace = next(
            e for e in _roster_agents(migrated_hand_edited) if e["name"] == "Grace Bell"
        )
        assert "personality" not in grace

    def test_every_non_object_element_survives_as_itself(
        self, migrated_hand_edited: Path
    ) -> None:
        roster = _roster(migrated_hand_edited)
        assert [e for e in roster if not isinstance(e, dict)] == [
            "a bare string",
            True,
            False,
            3,
            [1, 2],
            None,
        ]

    def test_the_non_json_settings_rows_are_untouched(
        self, migrated_hand_edited: Path
    ) -> None:
        with _connect(migrated_hand_edited) as conn:
            for namespace, key, value in _NON_JSON_SETTINGS:
                row = conn.execute(
                    "SELECT value FROM settings WHERE namespace = ? AND key = ?",
                    (namespace, key),
                ).fetchone()
                assert row is not None
                assert str(row[0]) == value

    def test_a_scalar_snapshot_survives_as_itself(
        self, migrated_hand_edited: Path
    ) -> None:
        with _connect(migrated_hand_edited) as conn:
            row = conn.execute(
                "SELECT snapshot FROM agent_identity_versions WHERE entity_id = ?",
                ("scalar-snapshot",),
            ).fetchone()
        assert row is not None
        assert str(row[0]) == '"personality"'

    def test_a_context_with_no_identity_survives_as_itself(
        self, migrated_hand_edited: Path
    ) -> None:
        assert _identities(migrated_hand_edited, "parked_contexts") == {
            "pc-no-identity": None
        }


class TestTheRewriteIsIdempotent:
    """Re-running the statements changes nothing, so a partial retry is safe."""

    async def test_a_second_pass_is_a_no_op(self, migrated: Path) -> None:
        tables = (
            "settings",
            "agent_identity_versions",
            "checkpoints",
            "parked_contexts",
        )
        with _connect(migrated) as conn:
            before = {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in tables}  # noqa: S608
            conn.executescript(
                (migrations.revisions_dir("sqlite") / _REVISION).read_text(
                    encoding="utf-8"
                )
            )
            after = {t: conn.execute(f"SELECT * FROM {t}").fetchall() for t in tables}  # noqa: S608
        assert before == after


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
