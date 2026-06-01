# module-kind: tests
"""Load-bearing proof for the learning curve.

The provable claim "an org that learns" rests on this experiment, not on
per-release scorecards (there is only one release). The honest construction:

* The capture -> store -> retrieve -> inject pipeline is the REAL pipeline.
* Only the LLM is a deterministic stand-in, and it keys ONLY on the GENERIC
  presence of an injected-lesson marker (``<memory-entry>``) in the prompt,
  never on task identity. "If a relevant lesson is injected, take the
  corrected branch" is a general behaviour, not a rigged demo.
* The procedural-memory proposer is itself an LLM call, so it gets a
  deterministic stand-in too (the same scripted provider, detected by the
  proposer's stable system-prompt marker), so the round-over-round
  improvement cannot flake.

With the pipeline active the first failure is captured as a lesson, injected
on the next pass, and flips the outcome to success. With procedural memory
disabled the same failure recurs every pass: a flat curve. The experiment
tests the learning MACHINERY end to end, not "do LLMs get smarter".
"""

import json
from datetime import date
from typing import Final
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_engine import AgentEngine
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.prompt_safety import TAG_MEMORY_ENTRY
from synthorg.engine.recovery import FailAndReassignStrategy
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.memory.procedural.models import ProceduralMemoryConfig
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from synthorg.memory.retriever import ContextInjectionStrategy
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.enums import FinishReason, MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolDefinition,
)

pytestmark = pytest.mark.unit

# Generic marker the context-injection strategy wraps every retrieved memory
# in (``wrap_untrusted(TAG_MEMORY_ENTRY, ...)``). The deterministic LLM keys
# on its mere presence, never on the task text inside it.
_MEMORY_MARKER: Final[str] = f"<{TAG_MEMORY_ENTRY}>"

# Stable marker in the procedural-memory proposer's system prompt
# (``proposer._SYSTEM_PROMPT`` opens with this phrase). Lets the single
# scripted strategy serve the proposer call without ordering fragility.
_PROPOSER_MARKER: Final[str] = "failure analysis assistant"

# A token shared by the task and the captured lesson so the InMemoryBackend
# substring retrieval surfaces the lesson for this task's query.
_DOMAIN_TOKEN: Final[str] = "checkout"

_PROPOSAL_JSON: Final[str] = json.dumps(
    {
        "discovery": (
            f"When the {_DOMAIN_TOKEN} task fails, decompose it before retrying."
        ),
        "condition": f"A {_DOMAIN_TOKEN} task fails on the first attempt.",
        "action": "Apply the recorded corrected approach.",
        "rationale": "The prior failure already mapped the corrected path.",
        "execution_steps": ["Recall the lesson", "Take the corrected branch"],
        "confidence": 0.85,
        "tags": [_DOMAIN_TOKEN],
    },
)

_AGENT_UUID = uuid4()


class LearningSensitiveStrategy:
    """Deterministic LLM stand-in for the learning experiment.

    Three branches, all keyed on GENERIC prompt markers:

    1. The proposer call (system prompt opens with the proposer marker):
       return a valid procedural-memory proposal so a lesson is captured.
    2. A retrieved lesson is present (``<memory-entry>`` in the prompt):
       take the corrected branch and complete cleanly.
    3. Otherwise (no lesson): the naive branch fails (``FinishReason.ERROR``),
       which drives recovery + procedural capture.
    """

    def next_response(
        self,
        messages: list[ChatMessage],
        model: str,
        tools: list[ToolDefinition] | None,
        config: CompletionConfig | None,
    ) -> CompletionResponse:
        """Return the deterministic completion for this call."""
        del tools, config
        usage = TokenUsage(input_tokens=8, output_tokens=4, cost=0.0)
        is_proposer_call = any(
            m.role == MessageRole.SYSTEM
            and _PROPOSER_MARKER in (m.content or "").lower()
            for m in messages
        )
        if is_proposer_call:
            return CompletionResponse(
                content=_PROPOSAL_JSON,
                finish_reason=FinishReason.STOP,
                usage=usage,
                model=model,
            )
        lesson_injected = any(_MEMORY_MARKER in (m.content or "") for m in messages)
        if lesson_injected:
            return CompletionResponse(
                content="Task completed correctly using the recalled lesson.",
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
        id="task-learn-001",
        title=f"Implement the {_DOMAIN_TOKEN} flow",
        description=f"Build the {_DOMAIN_TOKEN} flow end to end.",
        type=TaskType.DEVELOPMENT,
        project="proj-001",
        created_by="product_manager",
        assigned_to=str(_AGENT_UUID),
        status=TaskStatus.ASSIGNED,
    )


def _build_engine(
    *, learning_enabled: bool
) -> tuple[AgentEngine, InMemoryBackend | None]:
    provider = ScriptedDriver("test-provider", strategy=LearningSensitiveStrategy())
    if learning_enabled:
        backend: InMemoryBackend | None = InMemoryBackend()
        config = ProceduralMemoryConfig(model="test-small-001")
    else:
        backend = None
        config = ProceduralMemoryConfig(model="test-small-001", enabled=False)
    engine = AgentEngine(
        provider=provider,
        recovery_strategy=FailAndReassignStrategy(),
        procedural_memory_config=config,
        memory_backend=backend,
    )
    return engine, backend


async def _retrieve_lesson_messages(
    backend: InMemoryBackend | None,
) -> tuple[ChatMessage, ...]:
    if backend is None:
        return ()
    strategy = ContextInjectionStrategy(
        backend=backend,
        config=MemoryRetrievalConfig(),
    )
    return await strategy.prepare_messages(
        NotBlankStr(str(_AGENT_UUID)),
        NotBlankStr(_DOMAIN_TOKEN),
        token_budget=2000,
    )


async def test_learning_pipeline_flips_outcome() -> None:
    """With the real pipeline active, a captured lesson flips round 2 to success."""
    engine, backend = _build_engine(learning_enabled=True)
    assert backend is not None
    await backend.connect()
    identity = _make_identity()

    # First pass: no lesson yet -> naive branch fails -> recovery -> proposer
    # (scripted) -> lesson stored. This is the REAL capture path.
    round_one = await engine.run(identity=identity, task=_make_task())
    assert round_one.termination_reason is TerminationReason.ERROR
    assert await backend.count(NotBlankStr(str(_AGENT_UUID))) >= 1

    # Retrieve + inject through the real ContextInjectionStrategy.
    lesson_messages = await _retrieve_lesson_messages(backend)
    assert lesson_messages
    assert any(_MEMORY_MARKER in (m.content or "") for m in lesson_messages)

    # Second pass: the injected lesson flips the deterministic LLM to the
    # corrected branch -> success.
    round_two = await engine.run(
        identity=identity,
        task=_make_task(),
        memory_messages=lesson_messages,
    )
    assert round_two.is_success


async def test_disabled_learning_curve_stays_flat() -> None:
    """With procedural memory disabled, the same failure recurs: a flat curve."""
    engine, backend = _build_engine(learning_enabled=False)
    assert backend is None
    identity = _make_identity()

    round_one = await engine.run(identity=identity, task=_make_task())
    assert round_one.termination_reason is TerminationReason.ERROR

    # No backend -> nothing captured -> nothing to inject.
    lesson_messages = await _retrieve_lesson_messages(backend)
    assert not lesson_messages

    round_two = await engine.run(
        identity=identity,
        task=_make_task(),
        memory_messages=lesson_messages,
    )
    assert round_two.termination_reason is TerminationReason.ERROR
