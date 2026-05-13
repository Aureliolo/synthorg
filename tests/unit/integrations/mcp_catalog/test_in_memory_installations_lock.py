"""Race coverage: ``InMemoryMcpInstallationRepository._store`` is lock-guarded.

The four async methods (`save`, `get`, `list_items`, `delete`) all
serialise through a lazy-init ``asyncio.Lock`` so a concurrent
``save`` / ``delete`` cannot interleave with a ``list_items``
iteration and trip ``RuntimeError: dictionary changed size during
iteration``.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.integrations.mcp_catalog.in_memory_installations import (
    InMemoryMcpInstallationRepository,
)
from synthorg.integrations.mcp_catalog.installations import McpInstallation

pytestmark = pytest.mark.unit


def _make_installation(index: int) -> McpInstallation:
    return McpInstallation(
        catalog_entry_id=NotBlankStr(f"entry-{index:04d}"),
        connection_name=NotBlankStr(f"conn-{index:04d}"),
        installed_at=datetime(2026, 5, 13, 0, 0, 0, tzinfo=UTC),
    )


class TestInMemoryInstallationsConcurrency:
    async def test_concurrent_save_and_delete_stays_consistent(self) -> None:
        repo = InMemoryMcpInstallationRepository()
        for index in range(20):
            await repo.save(_make_installation(index))

        async def saver() -> None:
            for index in range(20, 50):
                await repo.save(_make_installation(index))

        async def deleter() -> None:
            for index in range(10):
                await repo.delete(NotBlankStr(f"entry-{index:04d}"))

        async def lister() -> None:
            for _ in range(50):
                await repo.list_items(limit=200)

        async with asyncio.TaskGroup() as tg:
            tg.create_task(saver())
            tg.create_task(deleter())
            tg.create_task(lister())

        remaining = await repo.list_items(limit=200)
        # Original 0..9 deleted, 10..19 survive, 20..49 inserted.
        assert len(remaining) == 40

    async def test_lock_is_lazy_per_loop(self) -> None:
        """``__init__`` MUST NOT create the lock; loop-bound locks are unsafe."""
        repo = InMemoryMcpInstallationRepository()
        assert repo._lock is None
        await repo.save(_make_installation(99))
        assert repo._lock is not None
