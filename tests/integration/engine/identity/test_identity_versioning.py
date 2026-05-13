"""Integration: register agent -> update identity -> verify version history.

End-to-end test covering:
- AgentRegistryService snapshotting on register + update_identity
- VersioningService content-dedup (no-op on identical re-save)
- identity_versions roundtrip through SQLiteVersionRepository
- charter_version injected into DecisionRecord metadata
"""

import json
from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import aiosqlite
import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import DecisionOutcome
from synthorg.core.task import Task
from synthorg.engine.decisions import DecisionRecord
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from synthorg.persistence import migrations
from synthorg.persistence.decision_protocol import DecisionRepository
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.version_repo import SQLiteVersionRepository
from synthorg.versioning.service import VersioningService
from tests._shared.persistence import make_private_write_context

_MODEL = ModelConfig(provider="test-provider", model_id="test-medium-001")
_HIRE = date(2026, 1, 1)


def _make_identity(name: str = "agent-x") -> AgentIdentity:
    return AgentIdentity(
        name=name,
        role="test role for versioning",
        department="engineering",
        model=_MODEL,
        hiring_date=_HIRE,
    )


@pytest.fixture
async def db(tmp_path: Path) -> AsyncGenerator[aiosqlite.Connection]:
    """Temp-file SQLite connection with yoyo migrations applied."""
    db_path = tmp_path / "test.db"
    rev_path = migrations.copy_revisions(tmp_path / "revisions")
    await migrations.migrate_apply(
        migrations.to_sqlite_url(str(db_path)),
        revisions_path=rev_path,
    )
    conn = await aiosqlite.connect(str(db_path))
    try:
        conn.row_factory = aiosqlite.Row
        yield conn
    finally:
        await conn.close()


@pytest.fixture
async def version_repo(
    db: aiosqlite.Connection,
) -> SQLiteVersionRepository[AgentIdentity]:
    """Identity version repository backed by the migrated schema."""
    return SQLiteVersionRepository(
        db,
        table_name="agent_identity_versions",
        serialize_snapshot=lambda m: json.dumps(m.model_dump(mode="json")),
        deserialize_snapshot=lambda s: AgentIdentity.model_validate(json.loads(s)),
        write_context=make_private_write_context(),
    )


@pytest.fixture
def registry(
    version_repo: SQLiteVersionRepository[AgentIdentity],
) -> AgentRegistryService:
    """Registry wired with a live versioning service."""
    versioning: VersioningService[AgentIdentity] = VersioningService(version_repo)
    return AgentRegistryService(versioning=versioning)


class TestVersioningOnRegister:
    """register() creates an initial version snapshot."""

    @pytest.mark.integration
    async def test_register_creates_version_one(
        self,
        registry: AgentRegistryService,
        version_repo: SQLiteVersionRepository[AgentIdentity],
    ) -> None:
        identity = _make_identity()
        await registry.register(identity)
        agent_id = str(identity.id)
        latest = await version_repo.get_latest_version(agent_id)
        assert latest is not None
        assert latest.version == 1
        assert latest.entity_id == agent_id

    @pytest.mark.integration
    async def test_register_snapshot_matches_identity(
        self,
        registry: AgentRegistryService,
        version_repo: SQLiteVersionRepository[AgentIdentity],
    ) -> None:
        identity = _make_identity(name="named-agent")
        await registry.register(identity)
        latest = await version_repo.get_latest_version(str(identity.id))
        assert latest is not None
        assert latest.snapshot.name == "named-agent"


class TestVersioningOnUpdateIdentity:
    """update_identity() creates a new version when content changes."""

    @pytest.mark.integration
    async def test_update_increments_version(
        self,
        registry: AgentRegistryService,
        version_repo: SQLiteVersionRepository[AgentIdentity],
    ) -> None:
        identity = _make_identity()
        await registry.register(identity)
        new_model = ModelConfig(provider="test-provider", model_id="test-large-001")
        await registry.update_identity(str(identity.id), model=new_model)
        count = await version_repo.count_versions(str(identity.id))
        assert count == 2
        latest = await version_repo.get_latest_version(str(identity.id))
        assert latest is not None
        assert latest.version == 2
        assert latest.snapshot.model.model_id == "test-large-001"

    @pytest.mark.integration
    async def test_identical_update_skips_version(
        self,
        registry: AgentRegistryService,
        version_repo: SQLiteVersionRepository[AgentIdentity],
    ) -> None:
        identity = _make_identity()
        await registry.register(identity)
        # Updating with the same model -- no content change
        await registry.update_identity(str(identity.id), model=_MODEL)
        count = await version_repo.count_versions(str(identity.id))
        assert count == 1  # No new version created

    @pytest.mark.integration
    async def test_multiple_updates_produce_sequential_versions(
        self,
        registry: AgentRegistryService,
        version_repo: SQLiteVersionRepository[AgentIdentity],
    ) -> None:
        identity = _make_identity()
        await registry.register(identity)
        for model_id in ["test-small-001", "test-medium-001", "test-large-001"]:
            new_model = ModelConfig(provider="test-provider", model_id=model_id)
            await registry.update_identity(str(identity.id), model=new_model)
        count = await version_repo.count_versions(str(identity.id))
        assert count == 4  # v1 (register) + v2 + v3 + v4


class TestCharterVersionInDecisionRecord:
    """charter_version is injected into DecisionRecord metadata when available."""

    @pytest.mark.integration
    async def test_charter_version_in_metadata(
        self,
        version_repo: SQLiteVersionRepository[AgentIdentity],
    ) -> None:
        """Verify charter_version dict structure when identity version exists."""
        from synthorg.engine.review_gate import ReviewGateService

        identity = _make_identity(name="executing-agent")
        agent_id = str(identity.id)

        # Pre-populate a version snapshot as if registry had run
        versioning: VersioningService[AgentIdentity] = VersioningService(version_repo)
        await versioning.snapshot_if_changed(agent_id, identity, "system")

        # Build a mock persistence backend that returns our real identity_versions
        # repo for charter lookup, and a mock decision_records repo for capture
        captured_metadata: dict[str, object] | None = None

        async def _capture(**kwargs: object) -> object:
            nonlocal captured_metadata
            captured_metadata = kwargs.get("metadata")  # type: ignore[assignment]
            record = MagicMock(spec=DecisionRecord)
            record.decision = DecisionOutcome.APPROVED
            record.version = 1
            return record

        mock_decision_repo = AsyncMock(spec=DecisionRepository)
        mock_decision_repo.append_with_next_version.side_effect = _capture

        mock_persistence = MagicMock(spec=PersistenceBackend)
        mock_persistence.identity_versions = version_repo
        mock_persistence.decision_records = mock_decision_repo

        # Build a minimal Task mock
        mock_task = MagicMock(spec=Task)
        mock_task.id = str(uuid4())
        mock_task.assigned_to = agent_id
        mock_task.acceptance_criteria = []
        mock_task.title = "test-task"

        svc = ReviewGateService(
            task_engine=MagicMock(spec=TaskEngine),
            persistence=mock_persistence,
        )
        await svc._record_decision(
            task=mock_task,
            decided_by="reviewer-001",
            approved=True,
            reason=None,
            approval_id=None,
        )

        assert captured_metadata is not None
        assert "charter_version" in captured_metadata
        cv = captured_metadata["charter_version"]
        assert isinstance(cv, dict)
        assert cv["agent_id"] == agent_id
        assert cv["version"] == 1
        assert isinstance(cv["content_hash"], str)

    @pytest.mark.integration
    async def test_charter_version_none_when_no_versions(
        self,
        version_repo: SQLiteVersionRepository[AgentIdentity],
    ) -> None:
        """metadata is None (not a dict) when no identity version exists."""
        from synthorg.engine.review_gate import ReviewGateService

        captured_metadata: dict[str, object] | None = None

        async def _capture(**kwargs: object) -> object:
            nonlocal captured_metadata
            captured_metadata = kwargs.get("metadata")  # type: ignore[assignment]
            record = MagicMock(spec=DecisionRecord)
            record.decision = DecisionOutcome.APPROVED
            record.version = 1
            return record

        mock_decision_repo = AsyncMock(spec=DecisionRepository)
        mock_decision_repo.append_with_next_version.side_effect = _capture

        mock_persistence = MagicMock(spec=PersistenceBackend)
        mock_persistence.identity_versions = version_repo
        mock_persistence.decision_records = mock_decision_repo

        mock_task = MagicMock(spec=Task)
        mock_task.id = str(uuid4())
        mock_task.assigned_to = "unknown-agent-id"
        mock_task.acceptance_criteria = []

        svc = ReviewGateService(
            task_engine=MagicMock(spec=TaskEngine),
            persistence=mock_persistence,
        )
        await svc._record_decision(
            task=mock_task,
            decided_by="reviewer-001",
            approved=True,
            reason=None,
            approval_id=None,
        )

        assert captured_metadata is None

    @pytest.mark.integration
    async def test_charter_version_lookup_error_sets_failure_flag(
        self,
        version_repo: SQLiteVersionRepository[AgentIdentity],
    ) -> None:
        """QueryError during charter lookup results in failure-flag metadata."""
        from synthorg.core.persistence_errors import QueryError
        from synthorg.engine.review_gate import ReviewGateService

        captured_metadata: dict[str, object] | None = None

        async def _capture(**kwargs: object) -> object:
            nonlocal captured_metadata
            captured_metadata = kwargs.get("metadata")  # type: ignore[assignment]
            record = MagicMock(spec=DecisionRecord)
            record.decision = DecisionOutcome.APPROVED
            record.version = 1
            return record

        mock_decision_repo = AsyncMock(spec=DecisionRepository)
        mock_decision_repo.append_with_next_version.side_effect = _capture

        # identity_versions raises QueryError on lookup
        failing_version_repo = AsyncMock(spec=SQLiteVersionRepository)
        failing_version_repo.get_latest_version.side_effect = QueryError(
            "DB failure simulated"
        )

        mock_persistence = MagicMock(spec=PersistenceBackend)
        mock_persistence.identity_versions = failing_version_repo
        mock_persistence.decision_records = mock_decision_repo

        mock_task = MagicMock(spec=Task)
        mock_task.id = str(uuid4())
        mock_task.assigned_to = "any-agent-id"
        mock_task.acceptance_criteria = []

        svc = ReviewGateService(
            task_engine=MagicMock(spec=TaskEngine),
            persistence=mock_persistence,
        )
        await svc._record_decision(
            task=mock_task,
            decided_by="reviewer-001",
            approved=True,
            reason=None,
            approval_id=None,
        )

        assert captured_metadata == {"charter_version_lookup_failed": True}
