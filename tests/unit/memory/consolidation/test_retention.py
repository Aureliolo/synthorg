"""Tests for RetentionEnforcer."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.memory.consolidation.config import RetentionConfig
from synthorg.memory.consolidation.models import RetentionRule
from synthorg.memory.consolidation.retention import RetentionEnforcer
from synthorg.memory.models import MemoryEntry, MemoryMetadata, MemoryQuery

pytestmark = pytest.mark.timeout(30)

_NOW = datetime.now(UTC)
_AGENT_ID = "test-agent"


def _make_entry(entry_id: str, category: MemoryCategory) -> MemoryEntry:
    return MemoryEntry(
        id=entry_id,
        agent_id=_AGENT_ID,
        category=category,
        content=f"Content {entry_id}",
        metadata=MemoryMetadata(),
        created_at=_NOW - timedelta(days=60),
    )


@pytest.mark.unit
class TestRetentionEnforcer:
    """RetentionEnforcer cleanup behaviour."""

    async def test_no_rules_no_deletions(self) -> None:
        backend = AsyncMock()
        config = RetentionConfig()
        enforcer = RetentionEnforcer(config=config, backend=backend)
        deleted = await enforcer.cleanup_expired(_AGENT_ID, now=_NOW)
        assert deleted == 0
        backend.retrieve.assert_not_called()

    async def test_cleanup_per_category(self) -> None:
        expired_entry = _make_entry("m1", MemoryCategory.WORKING)
        backend = AsyncMock()
        backend.retrieve = AsyncMock(return_value=(expired_entry,))
        backend.delete = AsyncMock(return_value=True)

        config = RetentionConfig(
            rules=(
                RetentionRule(
                    category=MemoryCategory.WORKING,
                    retention_days=30,
                ),
            ),
        )
        enforcer = RetentionEnforcer(config=config, backend=backend)
        deleted = await enforcer.cleanup_expired(_AGENT_ID, now=_NOW)
        assert deleted == 1

    async def test_default_retention_applies_to_all(self) -> None:
        backend = AsyncMock()
        backend.retrieve = AsyncMock(return_value=())
        backend.delete = AsyncMock(return_value=True)

        config = RetentionConfig(default_retention_days=30)
        enforcer = RetentionEnforcer(config=config, backend=backend)
        await enforcer.cleanup_expired(_AGENT_ID, now=_NOW)
        assert backend.retrieve.call_count == len(MemoryCategory)

    async def test_no_expired_entries(self) -> None:
        backend = AsyncMock()
        backend.retrieve = AsyncMock(return_value=())

        config = RetentionConfig(
            rules=(
                RetentionRule(
                    category=MemoryCategory.EPISODIC,
                    retention_days=7,
                ),
            ),
        )
        enforcer = RetentionEnforcer(config=config, backend=backend)
        deleted = await enforcer.cleanup_expired(_AGENT_ID, now=_NOW)
        assert deleted == 0

    async def test_mixed_categories(self) -> None:
        working_entry = _make_entry("w1", MemoryCategory.WORKING)
        # e1 is created but not expired — no assignment needed

        backend = AsyncMock()
        backend.retrieve = AsyncMock(
            side_effect=lambda *a, **kw: (
                (working_entry,)
                if MemoryCategory.WORKING in kw.get("query", a[-1]).categories
                else ()
            ),
        )
        backend.delete = AsyncMock(return_value=True)

        config = RetentionConfig(
            rules=(
                RetentionRule(category=MemoryCategory.WORKING, retention_days=30),
                RetentionRule(category=MemoryCategory.EPISODIC, retention_days=90),
            ),
        )
        enforcer = RetentionEnforcer(config=config, backend=backend)
        deleted = await enforcer.cleanup_expired(_AGENT_ID, now=_NOW)
        assert deleted == 1

    async def test_continues_on_per_category_failure(self) -> None:
        """Item 11: failure in one category does not block the rest."""
        _make_entry("w1", MemoryCategory.WORKING)  # used indirectly in mock
        episodic_entry = _make_entry("e1", MemoryCategory.EPISODIC)

        call_count = 0

        async def _mock_retrieve(
            agent_id: str,
            query: MemoryQuery,
        ) -> tuple[MemoryEntry, ...]:
            nonlocal call_count
            call_count += 1
            cats = query.categories or frozenset()
            if MemoryCategory.WORKING in cats:
                msg = "working store unavailable"
                raise RuntimeError(msg)
            if MemoryCategory.EPISODIC in cats:
                return (episodic_entry,)
            return ()

        backend = AsyncMock()
        backend.retrieve = AsyncMock(side_effect=_mock_retrieve)
        backend.delete = AsyncMock(return_value=True)

        config = RetentionConfig(
            rules=(
                RetentionRule(
                    category=MemoryCategory.WORKING,
                    retention_days=30,
                ),
                RetentionRule(
                    category=MemoryCategory.EPISODIC,
                    retention_days=30,
                ),
            ),
        )
        enforcer = RetentionEnforcer(config=config, backend=backend)
        deleted = await enforcer.cleanup_expired(_AGENT_ID, now=_NOW)
        # Working failed, but episodic should still succeed
        assert deleted == 1
        backend.delete.assert_called_once()

    async def test_delete_returns_false_not_counted(self) -> None:
        expired_entry = _make_entry("m1", MemoryCategory.WORKING)
        backend = AsyncMock()
        backend.retrieve = AsyncMock(return_value=(expired_entry,))
        backend.delete = AsyncMock(return_value=False)

        config = RetentionConfig(
            rules=(
                RetentionRule(
                    category=MemoryCategory.WORKING,
                    retention_days=30,
                ),
            ),
        )
        enforcer = RetentionEnforcer(config=config, backend=backend)
        deleted = await enforcer.cleanup_expired(_AGENT_ID, now=_NOW)
        assert deleted == 0
