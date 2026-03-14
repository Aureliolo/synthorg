"""Tests for HybridPromptRetrievalBackend."""

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import OrgFactCategory, SeniorityLevel
from synthorg.memory.org.access_control import WriteAccessConfig
from synthorg.memory.org.errors import (
    OrgMemoryAccessDeniedError,
    OrgMemoryConnectionError,
    OrgMemoryWriteError,
)
from synthorg.memory.org.hybrid_backend import HybridPromptRetrievalBackend
from synthorg.memory.org.models import (
    OrgFactAuthor,
    OrgFactWriteRequest,
    OrgMemoryQuery,
)
from synthorg.memory.org.store import SQLiteOrgFactStore

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr

pytestmark = pytest.mark.timeout(30)

_HUMAN = OrgFactAuthor(is_human=True)
_SENIOR = OrgFactAuthor(
    agent_id="agent-sr",
    seniority=SeniorityLevel.SENIOR,
    is_human=False,
)
_JUNIOR = OrgFactAuthor(
    agent_id="agent-jr",
    seniority=SeniorityLevel.JUNIOR,
    is_human=False,
)


async def _make_backend(
    policies: tuple[NotBlankStr, ...] = (),
) -> HybridPromptRetrievalBackend:
    store = SQLiteOrgFactStore(":memory:")
    backend = HybridPromptRetrievalBackend(
        core_policies=policies,
        store=store,
        access_config=WriteAccessConfig(),
    )
    await backend.connect()
    return backend


@pytest.mark.unit
class TestHybridBackendLifecycle:
    """Connection lifecycle tests."""

    async def test_connect_disconnect(self) -> None:
        backend = await _make_backend()
        assert backend.is_connected is True
        await backend.disconnect()
        assert backend.is_connected is False

    async def test_health_check(self) -> None:
        backend = await _make_backend()
        assert await backend.health_check() is True
        await backend.disconnect()
        assert await backend.health_check() is False

    async def test_backend_name(self) -> None:
        backend = await _make_backend()
        assert backend.backend_name == "hybrid_prompt_retrieval"
        await backend.disconnect()

    async def test_connect_failure_propagates(self) -> None:
        mock_store = AsyncMock()
        mock_store.connect = AsyncMock(
            side_effect=OrgMemoryConnectionError("store down"),
        )
        backend = HybridPromptRetrievalBackend(
            core_policies=(),
            store=mock_store,
            access_config=WriteAccessConfig(),
        )
        with pytest.raises(OrgMemoryConnectionError, match="store down"):
            await backend.connect()
        assert backend.is_connected is False


@pytest.mark.unit
class TestHybridBackendPolicies:
    """list_policies returns core policies as OrgFact objects."""

    async def test_list_policies_empty(self) -> None:
        backend = await _make_backend()
        policies = await backend.list_policies()
        assert policies == ()
        await backend.disconnect()

    async def test_list_policies_when_not_connected(self) -> None:
        store = SQLiteOrgFactStore(":memory:")
        backend = HybridPromptRetrievalBackend(
            core_policies=(),
            store=store,
            access_config=WriteAccessConfig(),
        )
        with pytest.raises(OrgMemoryConnectionError):
            await backend.list_policies()

    async def test_list_policies_returns_facts(self) -> None:
        backend = await _make_backend(
            policies=("No secrets in code", "All PRs need review"),
        )
        policies = await backend.list_policies()
        assert len(policies) == 2
        assert policies[0].content == "No secrets in code"
        assert policies[0].category == OrgFactCategory.CORE_POLICY
        assert policies[0].author.is_human is True
        assert policies[1].content == "All PRs need review"
        await backend.disconnect()


@pytest.mark.unit
class TestHybridBackendQuery:
    """Query delegation to underlying store."""

    async def test_query_empty(self) -> None:
        backend = await _make_backend()
        results = await backend.query(OrgMemoryQuery())
        assert results == ()
        await backend.disconnect()

    async def test_query_finds_written_facts(self) -> None:
        backend = await _make_backend()
        await backend.write(
            OrgFactWriteRequest(
                content="Use snake_case for variables",
                category=OrgFactCategory.CONVENTION,
            ),
            author=_SENIOR,
        )
        results = await backend.query(
            OrgMemoryQuery(context="snake_case"),
        )
        assert len(results) == 1
        assert "snake_case" in results[0].content
        await backend.disconnect()

    async def test_query_when_not_connected(self) -> None:
        backend = await _make_backend()
        await backend.disconnect()
        with pytest.raises(OrgMemoryConnectionError):
            await backend.query(OrgMemoryQuery())


@pytest.mark.unit
class TestHybridBackendWrite:
    """Write with access control enforcement."""

    async def test_write_adr_as_senior(self) -> None:
        backend = await _make_backend()
        fact_id = await backend.write(
            OrgFactWriteRequest(
                content="Use event sourcing for audit",
                category=OrgFactCategory.ADR,
            ),
            author=_SENIOR,
        )
        assert fact_id
        await backend.disconnect()

    async def test_write_core_policy_as_agent_denied(self) -> None:
        backend = await _make_backend()
        with pytest.raises(OrgMemoryAccessDeniedError):
            await backend.write(
                OrgFactWriteRequest(
                    content="New policy",
                    category=OrgFactCategory.CORE_POLICY,
                ),
                author=_SENIOR,
            )
        await backend.disconnect()

    async def test_write_adr_as_junior_denied(self) -> None:
        backend = await _make_backend()
        with pytest.raises(OrgMemoryAccessDeniedError):
            await backend.write(
                OrgFactWriteRequest(
                    content="Junior ADR",
                    category=OrgFactCategory.ADR,
                ),
                author=_JUNIOR,
            )
        await backend.disconnect()

    async def test_write_core_policy_as_human(self) -> None:
        backend = await _make_backend()
        fact_id = await backend.write(
            OrgFactWriteRequest(
                content="Human policy",
                category=OrgFactCategory.CORE_POLICY,
            ),
            author=_HUMAN,
        )
        assert fact_id
        await backend.disconnect()

    async def test_version_increments(self) -> None:
        backend = await _make_backend()
        await backend.write(
            OrgFactWriteRequest(
                content="Convention v1",
                category=OrgFactCategory.CONVENTION,
            ),
            author=_SENIOR,
        )
        await backend.write(
            OrgFactWriteRequest(
                content="Convention v2",
                category=OrgFactCategory.CONVENTION,
            ),
            author=_SENIOR,
        )
        results = await backend.query(
            OrgMemoryQuery(
                categories=frozenset({OrgFactCategory.CONVENTION}),
                limit=10,
            ),
        )
        versions = sorted(r.version for r in results)
        assert versions == [1, 2]
        await backend.disconnect()

    async def test_write_when_not_connected(self) -> None:
        backend = await _make_backend()
        await backend.disconnect()
        with pytest.raises(OrgMemoryConnectionError):
            await backend.write(
                OrgFactWriteRequest(
                    content="test",
                    category=OrgFactCategory.ADR,
                ),
                author=_SENIOR,
            )

    async def test_write_non_org_memory_write_error_wraps(self) -> None:
        mock_store = AsyncMock()
        mock_store.connect = AsyncMock()
        mock_store.is_connected = True
        mock_store.list_by_category = AsyncMock(return_value=())
        mock_store.save = AsyncMock(
            side_effect=RuntimeError("unexpected failure"),
        )

        backend = HybridPromptRetrievalBackend(
            core_policies=(),
            store=mock_store,
            access_config=WriteAccessConfig(),
        )
        await backend.connect()

        with pytest.raises(OrgMemoryWriteError, match="unexpected failure"):
            await backend.write(
                OrgFactWriteRequest(
                    content="test fact",
                    category=OrgFactCategory.CONVENTION,
                ),
                author=_SENIOR,
            )

    async def test_write_org_memory_write_error_reraises(self) -> None:
        original_error = OrgMemoryWriteError("store write failed")
        mock_store = AsyncMock()
        mock_store.connect = AsyncMock()
        mock_store.is_connected = True
        mock_store.list_by_category = AsyncMock(return_value=())
        mock_store.save = AsyncMock(side_effect=original_error)

        backend = HybridPromptRetrievalBackend(
            core_policies=(),
            store=mock_store,
            access_config=WriteAccessConfig(),
        )
        await backend.connect()

        with pytest.raises(OrgMemoryWriteError) as exc_info:
            await backend.write(
                OrgFactWriteRequest(
                    content="test fact",
                    category=OrgFactCategory.CONVENTION,
                ),
                author=_SENIOR,
            )
        assert exc_info.value is original_error
