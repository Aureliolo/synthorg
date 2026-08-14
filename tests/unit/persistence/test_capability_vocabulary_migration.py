"""The capability rename, run over data rather than an empty database.

The schema-drift gate builds from empty and compares two schemas, so it proves
the shape of ``model_pin_validations`` and can say nothing about the rows, and
nothing at all about the ``settings`` table, whose shape does not change here.
Everything this revision exists to protect is invisible to it: an operator's
capability overrides, spend profile, matcher thresholds and forecast priors,
and the agent roster itself.

The roster is the one that bites hardest. ``AgentConfig`` and ``ModelConfig``
both forbid extra keys, so a row still carrying ``tier`` / ``model_tier`` fails
validation for every agent at once, and the resolver answers a failed roster
read with the code default: the whole company reads as empty, with a single
WARNING as the only trace.

These tests seed pre-rename rows, apply the revision, and assert each one
arrives under the name and the vocabulary the new code reads.
"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from synthorg.persistence import migrations

pytestmark = pytest.mark.unit

_REVISION = "20260811000000_capability_vocabulary.sql"

_STAMP = "2026-08-01T09:00:00+00:00"

_INSERT_SETTING = (
    "INSERT INTO settings (namespace, key, value, updated_at) VALUES (?, ?, ?, ?)"
)

_INSERT_PIN = (
    "INSERT INTO model_pin_validations (prompt_class_id, validated_at, tier) "
    "VALUES (?, ?, ?)"
)

#: One agent per old rung, spelled the way ``json.dumps`` writes it (a space
#: after the colon) since that is what the roster writer uses.
_ROSTER = json.dumps(
    [
        {
            "name": "Alice",
            "role": "CEO",
            "tier": "large",
            "model": {
                "provider": "test-provider",
                "model_id": "test-expert-001",
                "model_tier": "large",
            },
        },
        {
            "name": "Bob",
            "role": "Developer",
            "tier": "local-small",
            "model": {
                "provider": "test-provider",
                "model_id": "test-basic-001",
                "model_tier": "local-small",
            },
        },
    ]
)

#: The override blob is written by ``model_dump_json``, which emits compact
#: separators. Both spellings have to survive, so the two blobs differ.
_OVERRIDES = (
    '{"schema_version":1,"overrides":[{"provider":"test-provider",'
    '"model_id":"operator-model","tier":"medium","provenance":"operator",'
    '"reason":"measured"}]}'
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


def _seed_pre_rename(conn: sqlite3.Connection) -> None:
    """Write the rows an installation would hold before the rename."""
    for prompt_class, old in (
        ("cls-expert", "large"),
        ("cls-capable", "medium"),
        ("cls-basic", "small"),
        ("cls-local", "local-small"),
    ):
        conn.execute(_INSERT_PIN, (prompt_class, _STAMP, old))

    for namespace, key, value in (
        ("providers", "tier_assignment_overrides", _OVERRIDES),
        ("providers", "tier_classifier_model", "test-provider/test-expert-001"),
        ("providers", "tier_classifier_enabled", "true"),
        ("budget", "model_tier_overrides", '{"operator-model": "large"}'),
        ("budget", "forecast_static_prior_per_turn_large", "0.20"),
        ("budget", "forecast_static_prior_per_turn_medium", "0.06"),
        ("budget", "forecast_static_prior_per_turn_small", "0.01"),
        ("budget", "forecast_static_prior_per_turn_local_small", "0.0"),
        ("company", "model_tier_profile", "premium"),
        ("company", "agents", _ROSTER),
        ("engine", "matcher_tier_large_min_context", "300000"),
        ("engine", "matcher_tier_medium_min_context", "48000"),
        ("engine", "matcher_min_cloud_tier", "3"),
    ):
        conn.execute(_INSERT_SETTING, (namespace, key, value, _STAMP))
    conn.commit()


@pytest.fixture
async def migrated(tmp_path: Path) -> Path:
    """Seed the pre-rename schema and data, then migrate.

    Returns:
        Path to the migrated database.
    """
    revisions = _revisions_before(tmp_path / "revisions")
    db_path = tmp_path / "seeded.db"
    url = migrations.to_sqlite_url(str(db_path))
    await migrations.migrate_apply(url, revisions_path=revisions)

    with _connect(db_path) as conn:
        _seed_pre_rename(conn)

    _add_the_revision(revisions)
    await migrations.migrate_apply(url, revisions_path=revisions)
    return db_path


def _setting(conn: sqlite3.Connection, namespace: str, key: str) -> str | None:
    """Read one setting value, or ``None`` when no row carries that key.

    Returns:
        The stored value, or ``None``.
    """
    row = conn.execute(
        "SELECT value FROM settings WHERE namespace = ? AND key = ?",
        (namespace, key),
    ).fetchone()
    return None if row is None else str(row[0])


class TestThePinColumnCarriesItsValues:
    """The rebuild has to bring the rows, not just the new CHECK."""

    @pytest.mark.parametrize(
        ("prompt_class", "expected"),
        [
            ("cls-expert", "expert"),
            ("cls-capable", "capable"),
            ("cls-basic", "basic"),
            ("cls-local", "basic"),
        ],
        ids=["large", "medium", "small", "local-small"],
    )
    def test_each_old_rung_lands_on_its_new_one(
        self,
        migrated: Path,
        prompt_class: str,
        expected: str,
    ) -> None:
        with _connect(migrated) as conn:
            row = conn.execute(
                "SELECT capability FROM model_pin_validations "
                "WHERE prompt_class_id = ?",
                (prompt_class,),
            ).fetchone()
        assert row == (expected,)

    def test_the_locality_half_of_local_small_is_not_smuggled_in(
        self, migrated: Path
    ) -> None:
        """``local-small`` asserted two axes; only the capability half maps."""
        with _connect(migrated) as conn:
            rows = conn.execute("SELECT DISTINCT capability FROM model_pin_validations")
            found = {r[0] for r in rows.fetchall()}
        assert found == {"expert", "capable", "basic"}


class TestEverySettingKeyMoves:
    """A key left behind reads as "the operator never configured this"."""

    @pytest.mark.parametrize(
        ("namespace", "old_key", "new_key"),
        [
            ("providers", "tier_classifier_model", "capability_classifier_model"),
            ("providers", "tier_classifier_enabled", "capability_classifier_enabled"),
            ("providers", "tier_assignment_overrides", "capability_overrides"),
            ("budget", "model_tier_overrides", "model_capability_overrides"),
            (
                "budget",
                "forecast_static_prior_per_turn_large",
                "forecast_static_prior_per_turn_expert",
            ),
            (
                "budget",
                "forecast_static_prior_per_turn_medium",
                "forecast_static_prior_per_turn_capable",
            ),
            (
                "budget",
                "forecast_static_prior_per_turn_small",
                "forecast_static_prior_per_turn_basic",
            ),
            (
                "budget",
                "forecast_static_prior_per_turn_local_small",
                "forecast_static_prior_per_turn_local",
            ),
            ("company", "model_tier_profile", "model_spend_profile"),
            ("engine", "matcher_tier_large_min_context", "matcher_expert_min_context"),
            (
                "engine",
                "matcher_tier_medium_min_context",
                "matcher_capable_min_context",
            ),
            ("engine", "matcher_min_cloud_tier", "matcher_min_cloud_cost_tier"),
        ],
    )
    def test_the_row_is_readable_under_the_new_key(
        self,
        migrated: Path,
        namespace: str,
        old_key: str,
        new_key: str,
    ) -> None:
        with _connect(migrated) as conn:
            assert _setting(conn, namespace, old_key) is None
            assert _setting(conn, namespace, new_key) is not None

    def test_a_value_that_is_not_vocabulary_is_left_alone(self, migrated: Path) -> None:
        """Renaming the key must not rewrite what the operator chose."""
        with _connect(migrated) as conn:
            assert _setting(conn, "company", "model_spend_profile") == "premium"
            assert _setting(conn, "engine", "matcher_min_cloud_cost_tier") == "3"
            assert (
                _setting(conn, "budget", "forecast_static_prior_per_turn_expert")
                == "0.20"
            )


class TestTheStoredVocabularyMoves:
    """Renaming the key is half of it; the value speaks the ladder too."""

    def test_the_compact_override_blob_is_rewritten(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            raw = _setting(conn, "providers", "capability_overrides")
        assert raw is not None
        blob = json.loads(raw)
        override = blob["overrides"][0]
        assert override["capability"] == "capable"
        assert "tier" not in override
        # The model id is untouched: only the rung is vocabulary.
        assert override["model_id"] == "operator-model"

    def test_the_budget_override_map_is_rewritten(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            raw = _setting(conn, "budget", "model_capability_overrides")
        assert raw is not None
        assert json.loads(raw) == {"operator-model": "expert"}

    def test_the_roster_loses_both_old_keys(self, migrated: Path) -> None:
        with _connect(migrated) as conn:
            raw = _setting(conn, "company", "agents")
        assert raw is not None
        agents = json.loads(raw)
        assert [a["capability"] for a in agents] == ["expert", "basic"]
        assert [a["model"]["capability"] for a in agents] == ["expert", "basic"]
        assert not any("tier" in a for a in agents)
        assert not any("model_tier" in a["model"] for a in agents)

    def test_the_roster_keeps_everything_that_is_not_a_rung(
        self, migrated: Path
    ) -> None:
        """A model id containing a rung word must not be rewritten with it."""
        with _connect(migrated) as conn:
            raw = _setting(conn, "company", "agents")
        assert raw is not None
        agents = json.loads(raw)
        assert [a["name"] for a in agents] == ["Alice", "Bob"]
        assert [a["model"]["model_id"] for a in agents] == [
            "test-expert-001",
            "test-basic-001",
        ]
        assert {a["model"]["provider"] for a in agents} == {"test-provider"}
