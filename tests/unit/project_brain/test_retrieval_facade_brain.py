"""Tests for the project-brain leg of :class:`ProjectAwareMemoryFacade`.

The facade fans out to a fourth leg for PROJECT_BRAIN memory and fences brain
content under ``<brain-state>`` (untrusted-content fence) before a resuming
agent sees it.
"""

from typing import override

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.constants import SYSTEM_DOCS_AGENT_ID
from synthorg.docs_engine.retrieval_facade import ProjectAwareMemoryFacade
from synthorg.engine.prompt_safety import TAG_BRAIN_STATE
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from synthorg.project_brain.constants import (
    BRAIN_MEMORY_NAMESPACE,
    BRAIN_PROJECT_TAG_PREFIX,
    SYSTEM_BRAIN_AGENT_ID,
)

pytestmark = pytest.mark.unit

_PROJECT = NotBlankStr("proj-1")
_AGENT = NotBlankStr("agent-x")


async def _store_brain_chunk(backend: InMemoryBackend, content: str) -> None:
    await backend.store(
        SYSTEM_BRAIN_AGENT_ID,
        MemoryStoreRequest(
            category=MemoryCategory.PROJECT_BRAIN,
            namespace=BRAIN_MEMORY_NAMESPACE,
            content=NotBlankStr(content),
            metadata=MemoryMetadata(
                source=NotBlankStr("project_brain.indexer"),
                tags=(NotBlankStr(f"{BRAIN_PROJECT_TAG_PREFIX}{_PROJECT}"),),
            ),
        ),
    )


async def test_facade_surfaces_brain_state_wrapped(
    memory_backend: InMemoryBackend,
) -> None:
    """A brain entry surfaces through the facade fenced under brain-state."""
    await _store_brain_chunk(
        memory_backend, "[decision/accepted] We accepted the payments risk"
    )
    facade = ProjectAwareMemoryFacade(backend=memory_backend, brain_enabled=True)

    results = await facade.retrieve(
        agent_id=_AGENT,
        project_id=_PROJECT,
        query=MemoryQuery(text=NotBlankStr("payments"), limit=10),
    )

    brain_hits = [r for r in results if f"<{TAG_BRAIN_STATE}>" in r.content]
    assert brain_hits, "brain entry should surface through the facade"
    assert f"</{TAG_BRAIN_STATE}>" in brain_hits[0].content
    assert "payments" in brain_hits[0].content


async def test_facade_without_brain_enabled_skips_leg(
    memory_backend: InMemoryBackend,
) -> None:
    """With brain_enabled False the brain content is not fanned out."""
    await _store_brain_chunk(memory_backend, "[decision/accepted] Hidden payments note")
    facade = ProjectAwareMemoryFacade(backend=memory_backend, brain_enabled=False)

    results = await facade.retrieve(
        agent_id=_AGENT,
        project_id=_PROJECT,
        query=MemoryQuery(text=NotBlankStr("payments"), limit=10),
    )
    assert all(f"<{TAG_BRAIN_STATE}>" not in r.content for r in results)


class _DocsLegFailingBackend(InMemoryBackend):
    """``InMemoryBackend`` whose docs-leg retrieve always raises.

    Lets a test prove the facade keeps the surviving legs' hits when one leg's
    backend call fails, rather than discarding the whole fan-out.
    """

    @override
    async def retrieve(
        self, agent_id: NotBlankStr, query: MemoryQuery
    ) -> tuple[MemoryEntry, ...]:
        if agent_id == SYSTEM_DOCS_AGENT_ID:
            msg = "docs leg backend failure"
            raise RuntimeError(msg)
        return await super().retrieve(agent_id, query)


async def test_facade_preserves_other_legs_when_one_fails() -> None:
    """One leg's backend failure must not discard the other legs' hits."""
    backend = _DocsLegFailingBackend()
    await backend.connect()
    try:
        await _store_brain_chunk(
            backend, "[decision/accepted] We accepted the payments risk"
        )
        facade = ProjectAwareMemoryFacade(backend=backend, brain_enabled=True)

        results = await facade.retrieve(
            agent_id=_AGENT,
            project_id=_PROJECT,
            query=MemoryQuery(text=NotBlankStr("payments"), limit=10),
        )

        brain_hits = [r for r in results if f"<{TAG_BRAIN_STATE}>" in r.content]
        assert brain_hits, "brain hit must survive a failing docs leg"
    finally:
        await backend.disconnect()
