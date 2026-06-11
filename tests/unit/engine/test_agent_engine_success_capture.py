# module-kind: tests
"""Engine-level proof for success-derived procedural capture.

The procedural-memory learning loop has two halves: capture (this file) and
injection (``test_agent_engine_memory_injection.py``).  Before this wiring the
engine only captured procedural lessons on the failure-recovery path
(``_try_procedural_memory``); a *successful* run captured nothing, so a working
approach was never turned into reusable knowledge.

These tests drive the real :class:`SuccessCaptureStrategy` through the engine's
own ``run()`` dispatch (not by poking the post-execution pipeline): a task that
COMPLETES must store a PROCEDURAL ``success:*`` entry, gated by the proposer's
confidence quality score.  Only the LLM is a deterministic stand-in -- the
backend, proposer, capture strategy, and post-execution dispatch are all real.
"""

import json
from datetime import date
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
from synthorg.memory.models import MemoryEntry, MemoryQuery
from synthorg.memory.procedural.capture.success_capture import (
    SuccessCaptureStrategy,
)
from synthorg.memory.procedural.models import ProceduralMemoryConfig
from synthorg.memory.procedural.success_proposer import SuccessMemoryProposer
from synthorg.providers.drivers.scripted import ScriptedDriver
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    TokenUsage,
    ToolDefinition,
)
from tests._shared import as_uuid

pytestmark = pytest.mark.unit

# The success proposer's system prompt opens with this phrase
# (``success_proposer._SYSTEM_PROMPT``).  The scripted LLM keys on it to serve
# the proposer call, never on task identity.
_SUCCESS_PROPOSER_MARKER: Final[str] = "success analysis assistant"

# confidence 0.85 -> quality 8.5 >= the 8.0 default ``min_quality_score`` gate.
_SUCCESS_PROPOSAL_JSON: Final[str] = json.dumps(
    {
        "discovery": "Decompose the checkout flow before wiring payment.",
        "condition": "A multi-step checkout task succeeds after decomposition.",
        "action": "Decompose first, then implement each step.",
        "rationale": "Decomposition kept each step within the turn budget.",
        "execution_steps": ["Decompose", "Implement step by step"],
        "confidence": 0.85,
        "tags": ["checkout"],
    },
)

_AGENT_UUID = as_uuid("success-capture-agent")


class SuccessScriptedStrategy:
    """Deterministic LLM stand-in for the success-capture proof.

    Two branches, both keyed on GENERIC prompt markers:

    1. The success-proposer call (system prompt opens with the proposer
       marker): return a high-confidence proposal so a lesson is captured.
    2. Otherwise (the agent's task run): complete cleanly (``STOP``) so the
       engine terminates COMPLETED and the capture strategy fires.
    """

    def __init__(self, *, proposal_json: str = _SUCCESS_PROPOSAL_JSON) -> None:
        self._proposal_json = proposal_json

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
        is_proposer_call = any(
            m.role == MessageRole.SYSTEM
            and _SUCCESS_PROPOSER_MARKER in (m.content or "").lower()
            for m in messages
        )
        content = (
            self._proposal_json if is_proposer_call else "Task completed successfully."
        )
        return CompletionResponse(
            content=content,
            finish_reason=FinishReason.STOP,
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
        id=as_uuid("task-success-001"),
        title="Implement the checkout flow",
        description="Build the checkout flow end to end.",
        type=TaskType.DEVELOPMENT,
        project="proj-001",
        created_by="product_manager",
        assigned_to=str(_AGENT_UUID),
        status=TaskStatus.ASSIGNED,
    )


def _make_capture_strategy(
    provider: ScriptedDriver,
    config: ProceduralMemoryConfig,
) -> SuccessCaptureStrategy:
    return SuccessCaptureStrategy(
        proposer=SuccessMemoryProposer(provider=provider, config=config),
        config=config,
    )


async def _procedural_entries(
    backend: InMemoryBackend,
) -> tuple[MemoryEntry, ...]:
    return await backend.retrieve(
        NotBlankStr(str(_AGENT_UUID)),
        MemoryQuery(categories=frozenset({MemoryCategory.PROCEDURAL})),
    )


async def test_success_capture_stores_memory_on_completion() -> None:
    """A COMPLETED run captures a success-derived procedural memory."""
    backend = InMemoryBackend()
    await backend.connect()
    provider = ScriptedDriver("test-provider", strategy=SuccessScriptedStrategy())
    config = ProceduralMemoryConfig(model="test-small-001")
    engine = AgentEngine(
        provider=provider,
        capture_strategy=_make_capture_strategy(provider, config),
        memory_backend=backend,
    )

    result = await engine.run(identity=_make_identity(), task=_make_task())

    assert result.is_success
    entries = await _procedural_entries(backend)
    assert entries, "expected a procedural memory captured from the success"
    assert any((e.metadata.source or "").startswith("success:") for e in entries)
    assert any("success-derived" in e.metadata.tags for e in entries)


async def test_no_capture_strategy_is_noop() -> None:
    """Without a capture strategy a successful run stores nothing."""
    backend = InMemoryBackend()
    await backend.connect()
    provider = ScriptedDriver("test-provider", strategy=SuccessScriptedStrategy())
    engine = AgentEngine(provider=provider, memory_backend=backend)

    result = await engine.run(identity=_make_identity(), task=_make_task())

    assert result.is_success
    assert not await _procedural_entries(backend)


async def test_low_confidence_success_not_captured() -> None:
    """A below-threshold proposal is gated out (no capture)."""
    backend = InMemoryBackend()
    await backend.connect()
    # confidence 0.5 -> quality 5.0 < the 8.0 default gate.
    low_conf = json.dumps(
        {
            "discovery": "A marginally useful observation.",
            "condition": "Rarely worth recording.",
            "action": "Proceed as before.",
            "rationale": "Low signal.",
            "execution_steps": ["Note it"],
            "confidence": 0.5,
            "tags": ["checkout"],
        },
    )
    provider = ScriptedDriver(
        "test-provider",
        strategy=SuccessScriptedStrategy(proposal_json=low_conf),
    )
    config = ProceduralMemoryConfig(model="test-small-001")
    engine = AgentEngine(
        provider=provider,
        capture_strategy=_make_capture_strategy(provider, config),
        memory_backend=backend,
    )

    result = await engine.run(identity=_make_identity(), task=_make_task())

    assert result.is_success
    assert not await _procedural_entries(backend)


async def test_capture_failure_does_not_block_result() -> None:
    """A raising capture strategy never fails the run."""

    class _RaisingCapture:
        @property
        def name(self) -> str:
            return "raising"

        async def capture(self, **_kwargs: object) -> str | None:
            msg = "capture boom"
            raise RuntimeError(msg)

    backend = InMemoryBackend()
    await backend.connect()
    provider = ScriptedDriver("test-provider", strategy=SuccessScriptedStrategy())
    engine = AgentEngine(
        provider=provider,
        capture_strategy=_RaisingCapture(),
        memory_backend=backend,
    )

    result = await engine.run(identity=_make_identity(), task=_make_task())

    assert result.is_success
