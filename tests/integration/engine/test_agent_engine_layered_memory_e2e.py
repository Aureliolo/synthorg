# module-kind: tests
"""End-to-end proof that all three memory layers reach a working prompt.

The injection wiring exists (an ``AgentEngine`` consults its
``memory_injection_strategy`` during ``run()``), but nothing proved that the
*three layers* the layered-memory substrate distinguishes actually arrive
together in a live run:

- **agent** memory (the agent's own store, the shared ``default`` namespace),
- **project** memory (an entry scoped to ``project:<id>``), and
- **org** memory (a company-wide fact fused in through ``OrgSharedKnowledgeStore``).

This drives the real :class:`ContextInjectionStrategy` over the real
:class:`InMemoryBackend` (agent + project, namespace-filtered) and a real
:class:`HybridPromptRetrievalBackend` on a migrated SQLite store (org), through
the engine's own ``run()`` dispatch. The LLM stand-in only records the prompt it
is handed; the assertion is that a distinct marker from each layer is present,
so a regression that drops any one layer fails loudly.
"""

import uuid
from collections.abc import AsyncGenerator
from datetime import date
from pathlib import Path
from typing import Final

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.completion_enums import FinishReason
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.memory.enums import OrgFactCategory
from synthorg.memory.models import MemoryMetadata, MemoryStoreRequest
from synthorg.memory.namespace_scope import write_namespace
from synthorg.memory.org.config import OrgMemoryConfig
from synthorg.memory.org.factory import build_org_memory_backend
from synthorg.memory.org.hybrid_backend import HybridPromptRetrievalBackend
from synthorg.memory.org.models import OrgFactAuthor, OrgFactWriteRequest
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy
from synthorg.memory.shared_store import OrgSharedKnowledgeStore
from synthorg.persistence import migrations
from synthorg.persistence.config import SQLiteConfig
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolDefinition,
)
from tests._shared import as_uuid, sid

pytestmark = pytest.mark.integration

_AGENT_UUID = as_uuid("layered-memory-agent")
_PROJECT_ID: Final[str] = sid("checkout-initiative")

# The task title doubles as the retrieval query; every seeded layer shares the
# word "checkout" with it so the term-overlap backend surfaces all three.
_TASK_TITLE: Final[str] = "checkout resilience"

# Marker tokens are deliberately letter-only: the memory store redacts
# secret-shaped content at write time, and a hex-like token would be masked
# (hiding the very marker the assertions look for) rather than dropped.
_AGENT_MARKER: Final[str] = "AGENTLAYERPRESENT"
_PROJECT_MARKER: Final[str] = "PROJECTLAYERPRESENT"
_ORG_MARKER: Final[str] = "ORGLAYERPRESENT"


class _RecordingStrategy:
    """LLM stand-in that records the first prompt it is given, then stops."""

    def __init__(self) -> None:
        self.seen_prompt: str = ""

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        """Capture the concatenated prompt and complete cleanly.

        Returns:
            A deterministic terminal :class:`CompletionResponse`.
        """
        del tools, config
        if not self.seen_prompt:
            self.seen_prompt = "\n".join(m.content or "" for m in messages)
        return CompletionResponse(
            content="Done.",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=8, output_tokens=2, cost=0.0),
            model=model,
        )


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=_AGENT_UUID,
        name="Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )


def _task() -> Task:
    return Task(
        id=as_uuid("task-layered-001"),
        title=_TASK_TITLE,
        description="Harden the checkout flow.",
        type=TaskType.DEVELOPMENT,
        project=_PROJECT_ID,
        created_by="product_manager",
        assigned_to=str(_AGENT_UUID),
        status=TaskStatus.ASSIGNED,
    )


@pytest.fixture
async def org_backend(
    tmp_path: Path,
) -> AsyncGenerator[HybridPromptRetrievalBackend]:
    """A real org-memory backend on a migrated, isolated SQLite store."""
    db_path = str(tmp_path / "org.db")
    persistence = SQLitePersistenceBackend(SQLiteConfig(path=db_path))
    await persistence.connect()
    revisions = migrations.copy_revisions(
        tmp_path / f"revs_{uuid.uuid4().hex}",
        backend="sqlite",
    )
    await migrations.migrate_apply(
        migrations.to_sqlite_url(db_path),
        revisions_path=revisions,
        backend="sqlite",
    )
    backend = build_org_memory_backend(OrgMemoryConfig(), persistence.org_facts)
    await backend.connect()
    try:
        yield backend
    finally:
        await persistence.disconnect()


# Each layer's memory shares exactly one query term with the recall query
# (composed from the task title, objective, role, and department) but carries
# distinct vocabulary otherwise, so all three are retrieved and none is dropped
# as redundant. The query is "checkout resilience. Harden the checkout flow..
# Developer. Engineering".


async def _seed_agent_layer(backend: InMemoryBackend) -> None:
    await backend.store(
        NotBlankStr(str(_AGENT_UUID)),
        MemoryStoreRequest(
            category=MemoryCategory.PROCEDURAL,
            namespace=NotBlankStr("default"),
            content=f"{_AGENT_MARKER}: retry idempotently when the checkout fails.",
            metadata=MemoryMetadata(source="seed", confidence=0.9),
        ),
    )


async def _seed_project_layer(backend: InMemoryBackend) -> None:
    await backend.store(
        NotBlankStr(str(_AGENT_UUID)),
        MemoryStoreRequest(
            category=MemoryCategory.SEMANTIC,
            namespace=write_namespace(_PROJECT_ID),
            content=f"{_PROJECT_MARKER}: standardise on aiosqlite for resilience.",
            metadata=MemoryMetadata(source="seed", confidence=0.9),
        ),
    )


async def _seed_org_layer(backend: HybridPromptRetrievalBackend) -> None:
    await backend.write(
        OrgFactWriteRequest(
            content=f"{_ORG_MARKER}: engineering changes need two reviewer approvals.",
            category=OrgFactCategory.CONVENTION,
        ),
        author=OrgFactAuthor(is_human=True),
    )


async def test_all_three_memory_layers_reach_the_prompt(
    org_backend: HybridPromptRetrievalBackend,
) -> None:
    """Agent, project, and org memory all arrive in a live run's prompt."""
    agent_backend = InMemoryBackend()
    await agent_backend.connect()
    await _seed_agent_layer(agent_backend)
    await _seed_project_layer(agent_backend)
    await _seed_org_layer(org_backend)

    strategy = ContextInjectionStrategy(
        backend=agent_backend,
        # Diversity re-ranking is a separate, orthogonal feature that may drop
        # a redundant entry regardless of its layer; disabling it here isolates
        # the property under test -- that each of the three layers is plumbed
        # into recall and reaches the prompt.
        config=MemoryRetrievalConfig(
            include_shared=True,
            min_relevance=0.0,
            diversity_penalty_enabled=False,
        ),
        shared_store=OrgSharedKnowledgeStore(org_backend),
    )
    recorder = _RecordingStrategy()
    engine = AgentEngine(
        provider=ScriptedDriver("test-provider", strategy=recorder),
        memory_injection_strategy=strategy,
        memory_backend=agent_backend,
    )

    result = await engine.run(identity=_identity(), task=_task())

    assert result.is_success
    assert _AGENT_MARKER in recorder.seen_prompt
    assert _PROJECT_MARKER in recorder.seen_prompt
    assert _ORG_MARKER in recorder.seen_prompt


async def test_project_memory_is_scoped_to_its_own_initiative() -> None:
    """A different project's memory never bleeds into this run's prompt."""
    agent_backend = InMemoryBackend()
    await agent_backend.connect()
    await _seed_project_layer(agent_backend)
    # A second project's memory, which this run must not see.
    other_marker = "OTHERPROJECTLEAK"
    await agent_backend.store(
        NotBlankStr(str(_AGENT_UUID)),
        MemoryStoreRequest(
            category=MemoryCategory.SEMANTIC,
            namespace=write_namespace(sid("other-initiative")),
            content=f"{other_marker}: checkout secrets from another initiative.",
            metadata=MemoryMetadata(source="seed", confidence=0.9),
        ),
    )

    strategy = ContextInjectionStrategy(
        backend=agent_backend,
        config=MemoryRetrievalConfig(
            min_relevance=0.0, diversity_penalty_enabled=False
        ),
    )
    recorder = _RecordingStrategy()
    engine = AgentEngine(
        provider=ScriptedDriver("test-provider", strategy=recorder),
        memory_injection_strategy=strategy,
        memory_backend=agent_backend,
    )

    await engine.run(identity=_identity(), task=_task())

    assert _PROJECT_MARKER in recorder.seen_prompt
    assert other_marker not in recorder.seen_prompt
