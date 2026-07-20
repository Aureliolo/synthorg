"""Cross-project memory isolation across every read and write strategy.

The guard for H1: a project-scoped agent must never recall another
project's memory, and a project-scoped capture must land in that
project's namespace, whichever injection strategy is in play. Each test
drives a real :class:`InMemoryBackend` (which enforces the namespace
filter) so it proves both halves at once: the strategy passes the right
scope, and the substrate honours it.
"""

import pytest

from synthorg.core.execution_identity import run_identity_scope
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.factory import build_in_memory_backend
from synthorg.memory.injection import InjectionStrategy
from synthorg.memory.models import (
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from synthorg.memory.namespace_scope import (
    PROJECT_NAMESPACE_PREFIX,
    read_namespaces,
)
from synthorg.memory.protocol import MemoryBackend
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy
from synthorg.memory.self_editing import SelfEditingMemoryStrategy
from synthorg.memory.tool_retriever import ToolBasedInjectionStrategy
from synthorg.memory.tools import ArchivalMemorySearchTool, SearchMemoryTool
from tests._shared import recall_request

pytestmark = pytest.mark.unit

_AGENT = NotBlankStr("agent-1")
_A = "proj-a"
_B = "proj-b"
_A_NS = NotBlankStr(f"{PROJECT_NAMESPACE_PREFIX}{_A}")
_B_NS = NotBlankStr(f"{PROJECT_NAMESPACE_PREFIX}{_B}")
_DEFAULT_NS = NotBlankStr("default")

# Every entry carries the same searchable token so the namespace is the
# only thing that can keep project B's memory out of project A's recall.
_SHARED = "quarterly revenue projection"


async def _connected_backend() -> MemoryBackend:
    """Build and connect an in-memory backend ready for reads and writes."""
    instance = build_in_memory_backend()
    await instance.connect()
    return instance


async def _seed(
    backend: MemoryBackend,
    *,
    category: MemoryCategory,
    tags: tuple[str, ...] = (),
) -> None:
    """Store one entry per namespace, all matching the shared query."""
    for namespace, marker in (
        (_A_NS, "alpha"),
        (_B_NS, "bravo"),
        (_DEFAULT_NS, "personal"),
    ):
        await backend.store(
            _AGENT,
            MemoryStoreRequest(
                category=category,
                content=NotBlankStr(f"{_SHARED} {marker}"),
                namespace=namespace,
                metadata=MemoryMetadata(tags=tags),
            ),
        )


def _joined(messages: object) -> str:
    """Concatenate a prepare_messages result into one lowercased string."""
    return " ".join(m.content for m in messages).lower()  # type: ignore[attr-defined]


class TestContextStrategyIsolation:
    """The pre-retrieval CONTEXT strategy filters to the project scope."""

    async def test_recall_excludes_other_projects(self) -> None:
        backend = await _connected_backend()
        await _seed(backend, category=MemoryCategory.SEMANTIC)
        strategy = ContextInjectionStrategy(
            backend=backend,
            config=MemoryRetrievalConfig(
                strategy=InjectionStrategy.CONTEXT,
                min_relevance=0.0,
            ),
        )

        messages = await strategy.prepare_messages(
            recall_request(
                agent_id=_AGENT,
                query=_SHARED,
                token_budget=4000,
                project_id=_A,
            )
        )

        text = _joined(messages)
        assert "alpha" in text
        assert "personal" in text
        assert "bravo" not in text


class TestSelfEditingCoreIsolation:
    """Core memory (always injected) is scoped to the project."""

    async def test_core_read_excludes_other_projects(self) -> None:
        backend = await _connected_backend()
        await _seed(backend, category=MemoryCategory.SEMANTIC, tags=("core",))
        strategy = SelfEditingMemoryStrategy(backend=backend)

        messages = await strategy.prepare_messages(
            recall_request(
                agent_id=_AGENT,
                query=_SHARED,
                token_budget=4000,
                project_id=_A,
            )
        )

        text = _joined(messages)
        assert "alpha" in text
        assert "personal" in text
        assert "bravo" not in text


class TestSelfEditingArchivalIsolation:
    """The archival-search tool scopes to the run's ambient project."""

    async def test_archival_search_excludes_other_projects(self) -> None:
        backend = await _connected_backend()
        await _seed(backend, category=MemoryCategory.SEMANTIC)
        tool = ArchivalMemorySearchTool(
            strategy=SelfEditingMemoryStrategy(backend=backend),
            agent_id=_AGENT,
        )

        with run_identity_scope(
            execution_id=NotBlankStr("exec-1"),
            task_id="task-1",
            project_id=_A,
        ):
            result = await tool.execute(arguments={"query": _SHARED})

        text = result.content.lower()
        assert "alpha" in text
        assert "personal" in text
        assert "bravo" not in text


class TestToolBasedIsolation:
    """The TOOL_BASED search tool scopes to the run's ambient project."""

    async def test_search_excludes_other_projects(self) -> None:
        backend = await _connected_backend()
        await _seed(backend, category=MemoryCategory.EPISODIC)
        tool = SearchMemoryTool(
            strategy=ToolBasedInjectionStrategy(
                backend=backend,
                config=MemoryRetrievalConfig(
                    strategy=InjectionStrategy.TOOL_BASED,
                    min_relevance=0.0,
                ),
            ),
            agent_id=_AGENT,
        )

        with run_identity_scope(
            execution_id=NotBlankStr("exec-1"),
            task_id="task-1",
            project_id=_A,
        ):
            result = await tool.execute(arguments={"query": _SHARED})

        text = result.content.lower()
        assert "alpha" in text
        assert "personal" in text
        assert "bravo" not in text


class TestSelfEditingWriteScoping:
    """A self-editing write lands in the run's project namespace."""

    async def test_recall_write_is_scoped_to_the_project(self) -> None:
        from synthorg.memory.tools import RecallMemoryWriteTool

        backend = await _connected_backend()
        tool = RecallMemoryWriteTool(
            strategy=SelfEditingMemoryStrategy(backend=backend),
            agent_id=_AGENT,
        )

        with run_identity_scope(
            execution_id=NotBlankStr("exec-1"),
            task_id="task-1",
            project_id=_A,
        ):
            await tool.execute(arguments={"content": f"{_SHARED} written in A"})

        # Visible inside project A's read scope...
        in_a = await backend.retrieve(
            _AGENT,
            MemoryQuery(text=_SHARED, namespaces=read_namespaces(_A)),
        )
        assert any("written in a" in e.content.lower() for e in in_a)

        # ...and absent from project B's.
        in_b = await backend.retrieve(
            _AGENT,
            MemoryQuery(text=_SHARED, namespaces=read_namespaces(_B)),
        )
        assert not in_b
