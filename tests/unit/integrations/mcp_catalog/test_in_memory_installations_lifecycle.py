"""Lifecycle / cleanup coverage for the in-memory MCP installations repo.

Pins the new ``clear()`` and ``size()`` surface so future regressions
cannot reintroduce the empty-cleanup stub.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.integrations.mcp_catalog.in_memory_installations import (
    InMemoryMcpInstallationRepository,
)
from synthorg.integrations.mcp_catalog.installations import McpInstallation

pytestmark = pytest.mark.unit


def _installation(catalog_entry_id: str = "ce-1") -> McpInstallation:
    return McpInstallation(
        catalog_entry_id=NotBlankStr(catalog_entry_id),
        connection_name=NotBlankStr("default"),
        installed_at=datetime(2026, 5, 15, tzinfo=UTC),
    )


async def test_clear_returns_count_and_empties_store() -> None:
    repo = InMemoryMcpInstallationRepository()
    await repo.save(_installation("ce-1"))
    await repo.save(_installation("ce-2"))
    assert await repo.size() == 2
    removed = await repo.clear()
    assert removed == 2
    assert await repo.size() == 0


async def test_clear_returns_zero_when_empty() -> None:
    repo = InMemoryMcpInstallationRepository()
    removed = await repo.clear()
    assert removed == 0
