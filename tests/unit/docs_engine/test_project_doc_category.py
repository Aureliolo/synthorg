"""Smoke tests for the ``MemoryCategory.PROJECT_DOC`` variant.

Confirms the new enum member round-trips through the inmemory backend's
store/retrieve path with the same machinery as every other category
(no special-case code paths needed). The end-to-end facade behaviour is
exercised by the dual-purpose integration tests; this file only verifies
that adding PROJECT_DOC to the enum did not require backend changes
beyond the constant's introduction.
"""

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.constants import (
    DOCS_MEMORY_NAMESPACE,
    DOCS_PROJECT_TAG_PREFIX,
    DOCS_SLUG_TAG_PREFIX,
    SYSTEM_DOCS_AGENT_ID,
)
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.memory.models import MemoryMetadata, MemoryQuery, MemoryStoreRequest

pytestmark = pytest.mark.unit


def _project_tag(project_id: str) -> NotBlankStr:
    return NotBlankStr(f"{DOCS_PROJECT_TAG_PREFIX}{project_id}")


def _slug_tag(slug: str) -> NotBlankStr:
    return NotBlankStr(f"{DOCS_SLUG_TAG_PREFIX}{slug}")


async def test_project_doc_round_trips_through_inmemory_backend() -> None:
    backend = InMemoryBackend()
    await backend.connect()
    try:
        request = MemoryStoreRequest(
            category=MemoryCategory.PROJECT_DOC,
            namespace=DOCS_MEMORY_NAMESPACE,
            content=NotBlankStr("Checkout funnel improved by 5%."),
            metadata=MemoryMetadata(
                source=NotBlankStr("docs_engine.indexer"),
                tags=(_project_tag("proj-1"), _slug_tag("q2-status")),
            ),
        )
        memory_id = await backend.store(SYSTEM_DOCS_AGENT_ID, request)
        assert memory_id

        results = await backend.retrieve(
            SYSTEM_DOCS_AGENT_ID,
            MemoryQuery(
                text=NotBlankStr("checkout"),
                categories=frozenset({MemoryCategory.PROJECT_DOC}),
                namespaces=frozenset({DOCS_MEMORY_NAMESPACE}),
                tags=(_project_tag("proj-1"),),
            ),
        )
        assert len(results) == 1
        assert results[0].category is MemoryCategory.PROJECT_DOC
        assert results[0].namespace == DOCS_MEMORY_NAMESPACE
        assert _project_tag("proj-1") in results[0].metadata.tags
        assert _slug_tag("q2-status") in results[0].metadata.tags
    finally:
        await backend.disconnect()


async def test_project_doc_tags_isolate_projects() -> None:
    """The project tag filters out other projects' docs at retrieval."""
    backend = InMemoryBackend()
    await backend.connect()
    try:
        for project_id in ("proj-1", "proj-2"):
            await backend.store(
                SYSTEM_DOCS_AGENT_ID,
                MemoryStoreRequest(
                    category=MemoryCategory.PROJECT_DOC,
                    namespace=DOCS_MEMORY_NAMESPACE,
                    content=NotBlankStr(f"content for {project_id}"),
                    metadata=MemoryMetadata(
                        tags=(_project_tag(project_id), _slug_tag("note")),
                    ),
                ),
            )

        only_one = await backend.retrieve(
            SYSTEM_DOCS_AGENT_ID,
            MemoryQuery(
                text=NotBlankStr("content"),
                categories=frozenset({MemoryCategory.PROJECT_DOC}),
                namespaces=frozenset({DOCS_MEMORY_NAMESPACE}),
                tags=(_project_tag("proj-1"),),
            ),
        )
        assert len(only_one) == 1
        assert "content for proj-1" in only_one[0].content
    finally:
        await backend.disconnect()
