"""Smoke tests for the ``MemoryCategory.PROJECT_BRAIN`` variant.

Confirms the new enum member round-trips through the in-memory backend's
store/retrieve path with the same machinery as every other category (no
special-case code paths), and that the per-project tag isolates one project's
brain entries from another's at retrieval.
"""

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.memory.models import MemoryMetadata, MemoryQuery, MemoryStoreRequest
from synthorg.project_brain.constants import (
    BRAIN_ENTRY_TAG_PREFIX,
    BRAIN_MEMORY_NAMESPACE,
    BRAIN_PROJECT_TAG_PREFIX,
    SYSTEM_BRAIN_AGENT_ID,
)

pytestmark = pytest.mark.unit


def _project_tag(project_id: str) -> NotBlankStr:
    return NotBlankStr(f"{BRAIN_PROJECT_TAG_PREFIX}{project_id}")


def _entry_tag(entry_id: str) -> NotBlankStr:
    return NotBlankStr(f"{BRAIN_ENTRY_TAG_PREFIX}{entry_id}")


async def test_project_brain_round_trips_through_inmemory_backend() -> None:
    backend = InMemoryBackend()
    await backend.connect()
    try:
        request = MemoryStoreRequest(
            category=MemoryCategory.PROJECT_BRAIN,
            namespace=BRAIN_MEMORY_NAMESPACE,
            content=NotBlankStr("Decided to use append-only storage."),
            metadata=MemoryMetadata(
                source=NotBlankStr("project_brain.indexer"),
                tags=(_project_tag("proj-1"), _entry_tag("entry-1")),
            ),
        )
        memory_id = await backend.store(SYSTEM_BRAIN_AGENT_ID, request)
        assert memory_id

        results = await backend.retrieve(
            SYSTEM_BRAIN_AGENT_ID,
            MemoryQuery(
                text=NotBlankStr("append-only"),
                categories=frozenset({MemoryCategory.PROJECT_BRAIN}),
                namespaces=frozenset({BRAIN_MEMORY_NAMESPACE}),
                tags=(_project_tag("proj-1"),),
            ),
        )
        assert len(results) == 1
        assert results[0].category is MemoryCategory.PROJECT_BRAIN
        assert results[0].namespace == BRAIN_MEMORY_NAMESPACE
        assert _entry_tag("entry-1") in results[0].metadata.tags
    finally:
        await backend.disconnect()


async def test_project_brain_tags_isolate_projects() -> None:
    """The project tag filters out other projects' brain entries."""
    backend = InMemoryBackend()
    await backend.connect()
    try:
        for project_id in ("proj-1", "proj-2"):
            await backend.store(
                SYSTEM_BRAIN_AGENT_ID,
                MemoryStoreRequest(
                    category=MemoryCategory.PROJECT_BRAIN,
                    namespace=BRAIN_MEMORY_NAMESPACE,
                    content=NotBlankStr(f"brain entry for {project_id}"),
                    metadata=MemoryMetadata(
                        tags=(_project_tag(project_id), _entry_tag("e")),
                    ),
                ),
            )

        only_one = await backend.retrieve(
            SYSTEM_BRAIN_AGENT_ID,
            MemoryQuery(
                text=NotBlankStr("brain entry"),
                categories=frozenset({MemoryCategory.PROJECT_BRAIN}),
                namespaces=frozenset({BRAIN_MEMORY_NAMESPACE}),
                tags=(_project_tag("proj-1"),),
            ),
        )
        assert len(only_one) == 1
        assert "brain entry for proj-1" in only_one[0].content
    finally:
        await backend.disconnect()
