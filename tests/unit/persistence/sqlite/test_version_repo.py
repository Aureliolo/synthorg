"""Tests for the generic SQLiteVersionRepository.

Uses a minimal ``_Stub`` model and a test-specific SQL table so the
generic machinery can be validated without depending on real entity
schemas.
"""

import json
from datetime import UTC, datetime

import aiosqlite
import pytest
from pydantic import BaseModel, ConfigDict

from synthorg.persistence.sqlite.version_repo import SQLiteVersionRepository
from synthorg.versioning.hashing import compute_content_hash
from synthorg.versioning.models import VersionSnapshot
from tests._shared.persistence import make_private_write_context

_NOW = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS test_versions (
    entity_id TEXT NOT NULL CHECK(length(entity_id) > 0),
    version INTEGER NOT NULL CHECK(version >= 1),
    content_hash TEXT NOT NULL CHECK(length(content_hash) > 0),
    snapshot TEXT NOT NULL CHECK(length(snapshot) > 0),
    saved_by TEXT NOT NULL CHECK(length(saved_by) > 0),
    saved_at TEXT NOT NULL CHECK(
        saved_at LIKE '%+00:00' OR saved_at LIKE '%Z'
    ),
    PRIMARY KEY (entity_id, version)
)
"""


class _Stub(BaseModel):
    """Minimal model used as the generic T in these tests."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    value: int


def _serialize(m: _Stub) -> str:
    return json.dumps(m.model_dump(mode="json"))


def _deserialize(s: str) -> _Stub:
    return _Stub.model_validate(json.loads(s))


def _make_version(
    entity_id: str = "ent-001",
    version: int = 1,
    model: _Stub | None = None,
    saved_at: datetime = _NOW,
) -> VersionSnapshot[_Stub]:
    m = model or _Stub(name="test", value=1)
    return VersionSnapshot(
        entity_id=entity_id,
        version=version,
        content_hash=compute_content_hash(m),
        snapshot=m,
        saved_by="user",
        saved_at=saved_at,
    )


@pytest.fixture
async def repo(
    migrated_db: aiosqlite.Connection,
) -> SQLiteVersionRepository[_Stub]:
    """Generic repo backed by a test-specific table."""
    await migrated_db.execute(_CREATE_TABLE)
    await migrated_db.commit()
    return SQLiteVersionRepository(
        migrated_db,
        table_name="test_versions",
        serialize_snapshot=_serialize,
        deserialize_snapshot=_deserialize,
        write_context=make_private_write_context(),
    )


class TestTableNameValidation:
    """Constructor rejects invalid table names."""

    @pytest.mark.unit
    async def test_invalid_table_name_raises(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        with pytest.raises(ValueError, match="Invalid table name"):
            SQLiteVersionRepository(
                migrated_db,
                table_name="bad name!",
                serialize_snapshot=_serialize,
                deserialize_snapshot=_deserialize,
                write_context=make_private_write_context(),
            )

    @pytest.mark.unit
    async def test_uppercase_table_name_raises(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        with pytest.raises(ValueError, match="Invalid table name"):
            SQLiteVersionRepository(
                migrated_db,
                table_name="TestVersions",
                serialize_snapshot=_serialize,
                deserialize_snapshot=_deserialize,
                write_context=make_private_write_context(),
            )

    @pytest.mark.unit
    async def test_valid_underscored_table_name_accepted(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        await migrated_db.execute(
            "CREATE TABLE IF NOT EXISTS agent_identity_versions "
            "(entity_id TEXT, version INTEGER, content_hash TEXT, "
            "snapshot TEXT, saved_by TEXT, saved_at TEXT, "
            "PRIMARY KEY (entity_id, version))"
        )
        # No error
        SQLiteVersionRepository(
            migrated_db,
            table_name="agent_identity_versions",
            serialize_snapshot=_serialize,
            deserialize_snapshot=_deserialize,
            write_context=make_private_write_context(),
        )


class TestSaveAndGetVersion:
    """save_version + get_version roundtrip."""

    @pytest.mark.unit
    async def test_roundtrip(self, repo: SQLiteVersionRepository[_Stub]) -> None:
        v = _make_version(version=1, model=_Stub(name="alice", value=7))
        await repo.save_version(v)
        result = await repo.get_version("ent-001", 1)
        assert result is not None
        assert result.entity_id == "ent-001"
        assert result.version == 1
        assert result.snapshot.name == "alice"
        assert result.snapshot.value == 7

    @pytest.mark.unit
    async def test_get_not_found_returns_none(
        self, repo: SQLiteVersionRepository[_Stub]
    ) -> None:
        result = await repo.get_version("ent-001", 99)
        assert result is None

    @pytest.mark.unit
    async def test_idempotent_save(self, repo: SQLiteVersionRepository[_Stub]) -> None:
        v = _make_version()
        await repo.save_version(v)
        await repo.save_version(v)  # Must not raise
        assert await repo.count_versions("ent-001") == 1

    @pytest.mark.unit
    async def test_timestamps_preserved_with_tz(
        self, repo: SQLiteVersionRepository[_Stub]
    ) -> None:
        v = _make_version(saved_at=_NOW)
        await repo.save_version(v)
        result = await repo.get_version("ent-001", 1)
        assert result is not None
        assert result.saved_at == _NOW
        assert result.saved_at.tzinfo is not None


class TestGetLatestVersion:
    """get_latest_version behavior."""

    @pytest.mark.unit
    async def test_returns_none_when_empty(
        self, repo: SQLiteVersionRepository[_Stub]
    ) -> None:
        assert await repo.get_latest_version("ent-001") is None

    @pytest.mark.unit
    async def test_returns_highest_version(
        self, repo: SQLiteVersionRepository[_Stub]
    ) -> None:
        await repo.save_version(_make_version(version=1))
        await repo.save_version(
            _make_version(version=2, model=_Stub(name="b", value=2))
        )
        await repo.save_version(
            _make_version(version=3, model=_Stub(name="c", value=3))
        )
        result = await repo.get_latest_version("ent-001")
        assert result is not None
        assert result.version == 3


class TestGetByContentHash:
    """get_by_content_hash lookup."""

    @pytest.mark.unit
    async def test_found_by_hash(self, repo: SQLiteVersionRepository[_Stub]) -> None:
        m = _Stub(name="findme", value=99)
        v = _make_version(model=m)
        await repo.save_version(v)
        result = await repo.get_by_content_hash("ent-001", compute_content_hash(m))
        assert result is not None
        assert result.snapshot.name == "findme"

    @pytest.mark.unit
    async def test_not_found_returns_none(
        self, repo: SQLiteVersionRepository[_Stub]
    ) -> None:
        result = await repo.get_by_content_hash("ent-001", "a" * 64)
        assert result is None


class TestListVersions:
    """list_versions behavior."""

    @pytest.mark.unit
    async def test_empty_list(self, repo: SQLiteVersionRepository[_Stub]) -> None:
        result = await repo.list_versions("ent-001")
        assert result == ()

    @pytest.mark.unit
    async def test_ordered_by_version_desc(
        self, repo: SQLiteVersionRepository[_Stub]
    ) -> None:
        for i in range(1, 4):
            await repo.save_version(
                _make_version(version=i, model=_Stub(name=f"v{i}", value=i))
            )
        result = await repo.list_versions("ent-001")
        assert [v.version for v in result] == [3, 2, 1]

    @pytest.mark.unit
    async def test_pagination(self, repo: SQLiteVersionRepository[_Stub]) -> None:
        for i in range(1, 6):
            await repo.save_version(
                _make_version(version=i, model=_Stub(name=f"v{i}", value=i))
            )
        page1 = await repo.list_versions("ent-001", limit=2, offset=0)
        page2 = await repo.list_versions("ent-001", limit=2, offset=2)
        assert len(page1) == 2
        assert page1[0].version == 5
        assert len(page2) == 2
        assert page2[0].version == 3

    @pytest.mark.unit
    async def test_different_entities_isolated(
        self, repo: SQLiteVersionRepository[_Stub]
    ) -> None:
        await repo.save_version(_make_version(entity_id="ent-A"))
        await repo.save_version(_make_version(entity_id="ent-B"))
        assert len(await repo.list_versions("ent-A")) == 1
        assert len(await repo.list_versions("ent-B")) == 1


class TestCountVersions:
    """count_versions behavior."""

    @pytest.mark.unit
    async def test_count_empty(self, repo: SQLiteVersionRepository[_Stub]) -> None:
        assert await repo.count_versions("ent-001") == 0

    @pytest.mark.unit
    async def test_count_multiple(self, repo: SQLiteVersionRepository[_Stub]) -> None:
        await repo.save_version(_make_version(version=1))
        await repo.save_version(
            _make_version(version=2, model=_Stub(name="b", value=2))
        )
        assert await repo.count_versions("ent-001") == 2


class TestDeleteVersions:
    """delete_versions_for_entity behavior."""

    @pytest.mark.unit
    async def test_delete_all(self, repo: SQLiteVersionRepository[_Stub]) -> None:
        await repo.save_version(_make_version(version=1))
        await repo.save_version(
            _make_version(version=2, model=_Stub(name="b", value=2))
        )
        count = await repo.delete_versions_for_entity("ent-001")
        assert count == 2
        assert await repo.count_versions("ent-001") == 0

    @pytest.mark.unit
    async def test_delete_nonexistent_returns_zero(
        self, repo: SQLiteVersionRepository[_Stub]
    ) -> None:
        count = await repo.delete_versions_for_entity("ent-999")
        assert count == 0

    @pytest.mark.unit
    async def test_delete_leaves_other_entities(
        self, repo: SQLiteVersionRepository[_Stub]
    ) -> None:
        await repo.save_version(_make_version(entity_id="ent-A"))
        await repo.save_version(_make_version(entity_id="ent-B"))
        await repo.delete_versions_for_entity("ent-A")
        assert await repo.count_versions("ent-A") == 0
        assert await repo.count_versions("ent-B") == 1


class TestSaveVersionBoolReturn:
    """save_version returns True on insert, False on duplicate."""

    @pytest.mark.unit
    async def test_first_save_returns_true(
        self, repo: SQLiteVersionRepository[_Stub]
    ) -> None:
        v = _make_version()
        inserted = await repo.save_version(v)
        assert inserted is True

    @pytest.mark.unit
    async def test_duplicate_save_returns_false(
        self, repo: SQLiteVersionRepository[_Stub]
    ) -> None:
        v = _make_version()
        await repo.save_version(v)
        inserted_again = await repo.save_version(v)
        assert inserted_again is False


class TestTableNameValidationEdgeCases:
    """Additional table name validation: empty string and leading digit."""

    @pytest.mark.unit
    async def test_empty_table_name_raises(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        with pytest.raises(ValueError, match="Invalid table name"):
            SQLiteVersionRepository(
                migrated_db,
                table_name="",
                serialize_snapshot=_serialize,
                deserialize_snapshot=_deserialize,
                write_context=make_private_write_context(),
            )

    @pytest.mark.unit
    async def test_leading_digit_table_name_raises(
        self, migrated_db: aiosqlite.Connection
    ) -> None:
        with pytest.raises(ValueError, match="Invalid table name"):
            SQLiteVersionRepository(
                migrated_db,
                table_name="1_versions",
                serialize_snapshot=_serialize,
                deserialize_snapshot=_deserialize,
                write_context=make_private_write_context(),
            )


class TestDeserializeRowErrors:
    """_deserialize_row raises QueryError with descriptive messages."""

    @pytest.mark.unit
    async def test_corrupt_json_raises_query_error(
        self, repo: SQLiteVersionRepository[_Stub]
    ) -> None:
        from synthorg.core.persistence_errors import QueryError

        # Insert a row with corrupt JSON directly
        await repo._db.execute(
            "INSERT INTO test_versions "
            "(entity_id, version, content_hash, snapshot, saved_by, saved_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("ent-corrupt", 1, "a" * 64, "not-valid-json{{{", "user", _NOW.isoformat()),
        )
        await repo._db.commit()
        with pytest.raises(QueryError, match="Corrupt JSON"):
            await repo.get_version("ent-corrupt", 1)

    @pytest.mark.unit
    async def test_schema_drift_raises_query_error(
        self, repo: SQLiteVersionRepository[_Stub]
    ) -> None:
        from synthorg.core.persistence_errors import QueryError

        # Insert a row whose JSON is valid but doesn't match _Stub schema
        bad_snapshot = json.dumps({"unexpected_field": "oops"})
        await repo._db.execute(
            "INSERT INTO test_versions "
            "(entity_id, version, content_hash, snapshot, saved_by, saved_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("ent-drift", 1, "b" * 64, bad_snapshot, "user", _NOW.isoformat()),
        )
        await repo._db.commit()
        with pytest.raises(QueryError, match="Schema mismatch"):
            await repo.get_version("ent-drift", 1)

    @pytest.mark.unit
    async def test_unexpected_callback_error_raises_query_error(
        self, repo: SQLiteVersionRepository[_Stub]
    ) -> None:
        """Deserialize callback raising TypeError is wrapped as QueryError."""
        from synthorg.core.persistence_errors import QueryError

        # Insert a valid row, then swap the deserializer to one that fails
        await repo._db.execute(
            "INSERT INTO test_versions "
            "(entity_id, version, content_hash, snapshot, saved_by, saved_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "ent-callback",
                1,
                "c" * 64,
                json.dumps({"name": "test", "value": 1}),
                "user",
                _NOW.isoformat(),
            ),
        )
        await repo._db.commit()

        def _bad_deserialize(_s: str) -> _Stub:
            msg = "simulated callback failure"
            raise TypeError(msg)

        repo._deserialize = _bad_deserialize
        with pytest.raises(QueryError, match="Failed to deserialize"):
            await repo.get_version("ent-callback", 1)

    @pytest.mark.unit
    async def test_serializer_error_raises_query_error(
        self, repo: SQLiteVersionRepository[_Stub]
    ) -> None:
        """Serialize callback raising TypeError is wrapped as QueryError."""
        from synthorg.core.persistence_errors import QueryError
        from synthorg.versioning.models import VersionSnapshot

        def _bad_serialize(_m: _Stub) -> str:
            msg = "simulated serializer failure"
            raise TypeError(msg)

        repo._serialize = _bad_serialize
        version = VersionSnapshot(
            entity_id="ent-ser",
            version=1,
            content_hash="d" * 64,
            snapshot=_Stub(name="test", value=1),
            saved_by="user",
            saved_at=_NOW,
        )
        with pytest.raises(QueryError, match="Failed to serialize"):
            await repo.save_version(version)
