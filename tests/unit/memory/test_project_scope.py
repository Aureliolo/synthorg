"""Project-scoped recall must include a project's memory and no other's.

The issue names the project layer as the one that stayed reactive: the
project id was concatenated into the embedded query, where an opaque
identifier is noise, and selected nothing. Recall now scopes by
namespace instead, so an agent working inside a project proactively
recalls that project's memory alongside its own, and never a sibling
project's.
"""

import pytest

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.backends.inmemory import InMemoryBackend
from synthorg.memory.models import MemoryMetadata, MemoryStoreRequest
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy
from tests._shared import recall_request

pytestmark = pytest.mark.unit

_AGENT = NotBlankStr("agent-1")
# All three share the query term so recall selects on namespace scope
# rather than on relevance differences.
_PERSONAL = "The payment retry policy caps at three attempts."
_PROJECT_A = "Checkout payment uses the versioned gateway."
_PROJECT_B = "Billing payment migrates off the legacy invoice table."


async def _store(backend: InMemoryBackend, content: str, *, namespace: str) -> None:
    """Store one memory under a namespace."""
    await backend.store(
        _AGENT,
        MemoryStoreRequest(
            category=MemoryCategory.SEMANTIC,
            namespace=NotBlankStr(namespace),
            content=NotBlankStr(content),
            metadata=MemoryMetadata(),
        ),
    )


async def _recalled(backend: InMemoryBackend, project_id: str) -> str:
    """Return the injected memory text for a project-scoped recall."""
    strategy = ContextInjectionStrategy(
        backend=backend,
        config=MemoryRetrievalConfig(min_relevance=0.0),
    )
    messages = await strategy.prepare_messages(
        recall_request(
            query="payment",
            token_budget=2000,
            project_id=project_id,
        )
    )
    return "\n".join(m.content or "" for m in messages)


class TestProjectScopedRecall:
    async def test_project_recall_includes_personal_and_own_project(self) -> None:
        backend = InMemoryBackend()
        await backend.connect()
        await _store(backend, _PERSONAL, namespace="default")
        await _store(backend, _PROJECT_A, namespace="project:checkout")

        content = await _recalled(backend, "checkout")

        assert _PERSONAL in content
        assert _PROJECT_A in content

    async def test_another_projects_memory_never_bleeds_in(self) -> None:
        backend = InMemoryBackend()
        await backend.connect()
        await _store(backend, _PROJECT_A, namespace="project:checkout")
        await _store(backend, _PROJECT_B, namespace="project:billing")

        content = await _recalled(backend, "checkout")

        assert _PROJECT_A in content
        assert _PROJECT_B not in content
