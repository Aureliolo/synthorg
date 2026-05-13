"""Unit test: AgentIdentity round-trip through SQLiteVersionRepository.

Validates that the serialize/deserialize callables used in the backend
correctly preserve an ``AgentIdentity`` through a DB round-trip.
"""

import json
from datetime import UTC, date, datetime

import aiosqlite
import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.persistence.sqlite.version_repo import SQLiteVersionRepository
from synthorg.versioning.hashing import compute_content_hash
from synthorg.versioning.models import VersionSnapshot
from tests._shared.persistence import make_private_write_context

_NOW = datetime(2026, 4, 7, 12, 0, tzinfo=UTC)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_identity_versions (
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


def _serialize(m: AgentIdentity) -> str:
    return json.dumps(m.model_dump(mode="json"))


def _deserialize(s: str) -> AgentIdentity:
    return AgentIdentity.model_validate(json.loads(s))


@pytest.fixture
async def repo(
    migrated_db: aiosqlite.Connection,
) -> SQLiteVersionRepository[AgentIdentity]:
    """Version repo backed by the agent_identity_versions table."""
    await migrated_db.execute(_SCHEMA)
    await migrated_db.commit()
    return SQLiteVersionRepository(
        migrated_db,
        table_name="agent_identity_versions",
        serialize_snapshot=_serialize,
        deserialize_snapshot=_deserialize,
        write_context=make_private_write_context(),
    )


_MODEL = ModelConfig(provider="test-provider", model_id="test-medium-001")
_HIRE = date(2026, 1, 1)


def _make_identity(name: str = "test-agent") -> AgentIdentity:
    """Create a minimal valid AgentIdentity."""
    return AgentIdentity(
        name=name,
        role="test role description",
        department="engineering",
        model=_MODEL,
        hiring_date=_HIRE,
    )


def _make_version(
    identity: AgentIdentity | None = None,
    entity_id: str = "agt-001",
    version: int = 1,
) -> VersionSnapshot[AgentIdentity]:
    m = identity or _make_identity()
    return VersionSnapshot(
        entity_id=entity_id,
        version=version,
        content_hash=compute_content_hash(m),
        snapshot=m,
        saved_by="user",
        saved_at=_NOW,
    )


class TestAgentIdentityRoundTrip:
    """AgentIdentity survives a serialize -> save -> load -> deserialize cycle."""

    @pytest.mark.unit
    async def test_roundtrip_preserves_name(
        self, repo: SQLiteVersionRepository[AgentIdentity]
    ) -> None:
        identity = _make_identity(name="alpha-agent")
        v = _make_version(identity=identity)
        await repo.save_version(v)
        result = await repo.get_version("agt-001", 1)
        assert result is not None
        assert result.snapshot.name == "alpha-agent"

    @pytest.mark.unit
    async def test_roundtrip_preserves_role(
        self, repo: SQLiteVersionRepository[AgentIdentity]
    ) -> None:
        identity = _make_identity()
        v = _make_version(identity=identity)
        await repo.save_version(v)
        result = await repo.get_version("agt-001", 1)
        assert result is not None
        assert result.snapshot.role == identity.role

    @pytest.mark.unit
    async def test_roundtrip_preserves_content_hash(
        self, repo: SQLiteVersionRepository[AgentIdentity]
    ) -> None:
        identity = _make_identity()
        expected_hash = compute_content_hash(identity)
        v = _make_version(identity=identity)
        await repo.save_version(v)
        result = await repo.get_version("agt-001", 1)
        assert result is not None
        assert result.content_hash == expected_hash

    @pytest.mark.unit
    async def test_get_by_content_hash_with_real_identity(
        self, repo: SQLiteVersionRepository[AgentIdentity]
    ) -> None:
        identity = _make_identity(name="find-me")
        v = _make_version(identity=identity)
        await repo.save_version(v)
        found = await repo.get_by_content_hash(
            "agt-001", compute_content_hash(identity)
        )
        assert found is not None
        assert found.snapshot.name == "find-me"

    @pytest.mark.unit
    async def test_idempotent_save_with_real_identity(
        self, repo: SQLiteVersionRepository[AgentIdentity]
    ) -> None:
        identity = _make_identity()
        v = _make_version(identity=identity)
        first = await repo.save_version(v)
        second = await repo.save_version(v)
        assert first is True
        assert second is False
        assert await repo.count_versions("agt-001") == 1

    @pytest.mark.unit
    async def test_multiple_versions_same_agent(
        self, repo: SQLiteVersionRepository[AgentIdentity]
    ) -> None:
        for i, name in enumerate(["v1-agent", "v2-agent", "v3-agent"], start=1):
            identity = _make_identity(name=name)
            v = _make_version(identity=identity, version=i)
            await repo.save_version(v)
        latest = await repo.get_latest_version("agt-001")
        assert latest is not None
        assert latest.version == 3
        assert latest.snapshot.name == "v3-agent"
