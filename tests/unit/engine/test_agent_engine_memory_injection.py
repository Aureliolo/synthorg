# module-kind: tests
"""Engine-level proof for context memory injection in the live run loop.

The capture half of the loop (``test_agent_engine_success_capture.py``) stores
lessons; this half makes the live engine *use* them.  Before this wiring the
engine only injected memories a caller pre-retrieved and passed via
``run(memory_messages=...)`` -- the engine never consulted its own
``memory_injection_strategy`` for context injection, so a wired CONTEXT
strategy was inert on the real execution path.

These tests drive the real :class:`ContextInjectionStrategy` through the
engine's own ``run()`` dispatch (NOT by calling ``prepare_messages`` in the
test): a pre-seeded lesson must be retrieved, injected, and observed by the
LLM, flipping a would-be failure into a success.  Only the LLM is a
deterministic stand-in, and it keys ONLY on the generic injected-lesson marker
(``<memory-entry>``), never on task identity.
"""

from datetime import date
from typing import Final
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import MemoryCategory, TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.prompt_safety import TAG_MEMORY_ENTRY
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.memory.filter import NON_INFERABLE_TAG
from synthorg.memory.models import MemoryMetadata, MemoryStoreRequest
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolDefinition,
)
from tests._shared import as_uuid

pytestmark = pytest.mark.unit

# Generic marker the context-injection strategy wraps every retrieved memory in
# (``wrap_untrusted(TAG_MEMORY_ENTRY, ...)``).  The deterministic LLM keys on
# its mere presence, never on the lesson text inside it.
_MEMORY_MARKER: Final[str] = f"<{TAG_MEMORY_ENTRY}>"

_AGENT_UUID = uuid4()

# The task title doubles as the retrieval query (the engine queries on it) and
# -- because the InMemoryBackend matches by whole-string substring -- must
# appear verbatim inside the seeded lesson for it to surface.
_TASK_TITLE: Final[str] = "checkout resilience"


class InjectionSensitiveStrategy:
    """Deterministic LLM stand-in for the injection proof.

    Keyed on the GENERIC injected-lesson marker:

    1. A retrieved lesson is present (``<memory-entry>`` in the prompt): take
       the corrected branch and complete cleanly (``STOP``).
    2. Otherwise: the naive branch fails (``FinishReason.ERROR``).
    """

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        """Return the deterministic completion for this call.

        Returns:
            The scripted :class:`CompletionResponse`.
        """
        del tools, config
        usage = TokenUsage(input_tokens=8, output_tokens=4, cost=0.0)
        lesson_injected = any(_MEMORY_MARKER in (m.content or "") for m in messages)
        if lesson_injected:
            return CompletionResponse(
                content="Task completed using the recalled lesson.",
                finish_reason=FinishReason.STOP,
                usage=usage,
                model=model,
            )
        return CompletionResponse(
            content="I could not complete the task on the first attempt.",
            finish_reason=FinishReason.ERROR,
            usage=usage,
            model=model,
        )


def _make_identity() -> AgentIdentity:
    return AgentIdentity(
        id=_AGENT_UUID,
        name="Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
        hiring_date=date(2026, 1, 1),
    )


def _make_task() -> Task:
    return Task(
        id=as_uuid("task-inject-001"),
        title=_TASK_TITLE,
        description="Build the checkout flow end to end.",
        type=TaskType.DEVELOPMENT,
        project="proj-001",
        created_by="product_manager",
        assigned_to=str(_AGENT_UUID),
        status=TaskStatus.ASSIGNED,
    )


async def _seed_lesson(backend: InMemoryBackend) -> None:
    """Store a lesson whose content contains the task title verbatim.

    The InMemoryBackend retrieves by whole-string substring match, so the
    engine's query (the task title) must appear inside the lesson.
    """
    await backend.store(
        NotBlankStr(str(_AGENT_UUID)),
        MemoryStoreRequest(
            category=MemoryCategory.PROCEDURAL,
            content=(
                f"Lesson for '{_TASK_TITLE}': decompose before retrying, "
                "then take the corrected branch."
            ),
            metadata=MemoryMetadata(
                source="seed",
                confidence=0.9,
                tags=(NON_INFERABLE_TAG, "checkout"),
            ),
        ),
    )


def _context_strategy(backend: InMemoryBackend) -> ContextInjectionStrategy:
    return ContextInjectionStrategy(backend=backend, config=MemoryRetrievalConfig())


async def test_engine_injects_context_memory_and_flips_outcome() -> None:
    """A wired CONTEXT strategy injects a seeded lesson via the engine's run()."""
    backend = InMemoryBackend()
    await backend.connect()
    await _seed_lesson(backend)
    provider = ScriptedDriver(
        "test-provider",
        strategy=InjectionSensitiveStrategy(),
    )
    engine = AgentEngine(
        provider=provider,
        memory_injection_strategy=_context_strategy(backend),
        memory_backend=backend,
    )

    result = await engine.run(identity=_make_identity(), task=_make_task())

    assert result.is_success


async def test_engine_without_injection_strategy_does_not_recall() -> None:
    """Without a wired strategy the same seeded lesson is never injected."""
    backend = InMemoryBackend()
    await backend.connect()
    await _seed_lesson(backend)
    provider = ScriptedDriver(
        "test-provider",
        strategy=InjectionSensitiveStrategy(),
    )
    engine = AgentEngine(provider=provider, memory_backend=backend)

    result = await engine.run(identity=_make_identity(), task=_make_task())

    assert result.termination_reason is TerminationReason.ERROR


async def test_engine_injects_nothing_when_backend_empty() -> None:
    """A wired strategy over an empty backend injects nothing (naive branch)."""
    backend = InMemoryBackend()
    await backend.connect()
    provider = ScriptedDriver(
        "test-provider",
        strategy=InjectionSensitiveStrategy(),
    )
    engine = AgentEngine(
        provider=provider,
        memory_injection_strategy=_context_strategy(backend),
        memory_backend=backend,
    )

    result = await engine.run(identity=_make_identity(), task=_make_task())

    assert result.termination_reason is TerminationReason.ERROR
