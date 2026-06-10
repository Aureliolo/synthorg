"""Canonical scripted ``CompletionProvider`` test double.

One shared implementation backing both the e2e suite and the engine
quality suites, so the two near-identical copies that previously
existed cannot drift. Response selection reuses the production
strategies from :mod:`synthorg.providers.drivers.scripted`; this module
is only the thin recording / ergonomics layer the tests rely on.

Two construction shapes are supported (both pre-existing call sites):

* ``ScriptedProvider([r1, r2])`` -- replay a list in order
  (records ``received_messages`` / ``call_count``).
* ``ScriptedProvider(response=r)`` / ``ScriptedProvider(error=e)`` --
  single response or error every call (records ``complete_calls``).
* ``ScriptedProvider()`` -- ``complete`` raises ``AssertionError``
  (legacy quality-suite contract: configure a response or error).
"""

import copy
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import date

from pydantic import JsonValue

from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
    ToolPermissions,
)
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.hr.seniority import SeniorityLevel
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.drivers.scripted import (
    ScriptedResponseStrategy,
    SequencedResponseStrategy,
    SingleResponseStrategy,
)
from synthorg.providers.enums import FinishReason, StreamEventType
from synthorg.providers.models import (
    ChatMessage,
    CompletionConfig,
    CompletionResponse,
    StreamChunk,
    TokenUsage,
    ToolCall,
    ToolDefinition,
)
from synthorg.providers.protocol import CompletionProvider
from tests._shared.ids import as_uuid

_TEST_MODEL = "test-model-001"
_TEST_PROVIDER = "test-provider"
_QUALITY_MODEL = "test-medium-001"

# Shared capabilities fixture for engine/quality tests. Generic on
# purpose so decomposer and grader suites need no vendor presets.
TEST_CAPABILITIES = ModelCapabilities(
    model_id=_QUALITY_MODEL,
    provider=_TEST_PROVIDER,
    max_context_tokens=200_000,
    max_output_tokens=8_192,
    supports_tools=True,
    supports_vision=False,
    supports_streaming=True,
    supports_streaming_tool_calls=True,
    supports_system_messages=True,
    cost_per_1k_input=0.001,
    cost_per_1k_output=0.002,
)


class ScriptedProvider:
    """Structural ``CompletionProvider`` returning scripted responses."""

    def __init__(
        self,
        responses: Sequence[CompletionResponse] | None = None,
        *,
        response: CompletionResponse | None = None,
        error: Exception | None = None,
        capabilities: ModelCapabilities | None = None,
    ) -> None:
        configured = (
            int(responses is not None)
            + int(response is not None)
            + int(error is not None)
        )
        if configured > 1:
            msg = "ScriptedProvider accepts only one of: responses, response, or error"
            raise ValueError(msg)
        self._capabilities = copy.deepcopy(capabilities or TEST_CAPABILITIES)
        self._strategy: ScriptedResponseStrategy | None
        if responses is not None:
            self._strategy = SequencedResponseStrategy(tuple(responses))
        elif error is not None:
            self._strategy = SingleResponseStrategy(error=error)
        elif response is not None:
            self._strategy = SingleResponseStrategy(response=response)
        else:
            self._strategy = None
        self._call_count = 0
        self.received_messages: list[list[ChatMessage]] = []
        self.complete_calls: list[
            tuple[
                list[ChatMessage],
                str,
                list[ToolDefinition] | None,
                CompletionConfig | None,
            ]
        ] = []

    @property
    def call_count(self) -> int:
        """Number of ``complete`` calls made so far."""
        return self._call_count

    async def complete(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> CompletionResponse:
        """Record the call and return the next scripted response."""
        self.received_messages.append(copy.deepcopy(messages))
        self.complete_calls.append(
            (
                copy.deepcopy(messages),
                model,
                copy.deepcopy(tools),
                copy.deepcopy(config),
            )
        )
        self._call_count += 1
        if self._strategy is None:
            msg = (
                "ScriptedProvider.complete() called without a configured "
                "response or error"
            )
            raise AssertionError(msg)
        return self._strategy.next_response(messages, model, tools, config)

    async def stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Return a trivial single-chunk stream (protocol conformance)."""
        del messages, model, tools, config

        async def _empty() -> AsyncIterator[StreamChunk]:
            yield StreamChunk(event_type=StreamEventType.DONE)

        return _empty()

    async def get_model_capabilities(self, model: str) -> ModelCapabilities:
        """Return the configured capabilities regardless of ``model``."""
        del model
        return copy.deepcopy(self._capabilities)

    async def batch_get_capabilities(
        self,
        models: tuple[str, ...],
    ) -> Mapping[str, ModelCapabilities | None]:
        """Return the configured capabilities keyed by each model."""
        return {model: copy.deepcopy(self._capabilities) for model in models}


# Verify the double satisfies the runtime-checkable protocol.
assert isinstance(ScriptedProvider([]), CompletionProvider)


def build_tool_call_response(  # noqa: PLR0913
    tool_name: str,
    tool_arguments: dict[str, JsonValue],
    *,
    call_id: str = "call-001",
    input_tokens: int = 100,
    output_tokens: int = 30,
    cost: float = 0.0001,
    model: str = _QUALITY_MODEL,
) -> CompletionResponse:
    """Build a ``CompletionResponse`` wrapping a single scripted tool call."""
    return CompletionResponse(
        tool_calls=(ToolCall(id=call_id, name=tool_name, arguments=tool_arguments),),
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
        ),
        model=model,
    )


def make_tool_call_response(
    *,
    tool_calls: tuple[ToolCall, ...],
    input_tokens: int = 50,
    output_tokens: int = 20,
    cost: float = 0.005,
) -> CompletionResponse:
    """Build a ``CompletionResponse`` with tool calls (e2e helper)."""
    return CompletionResponse(
        content=None,
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
        ),
        model=_TEST_MODEL,
        tool_calls=tool_calls,
    )


def make_text_response(
    content: str,
    *,
    input_tokens: int = 80,
    output_tokens: int = 30,
    cost: float = 0.008,
) -> CompletionResponse:
    """Build a text ``CompletionResponse`` (e2e helper)."""
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
        ),
        model=_TEST_MODEL,
    )


def make_e2e_identity(
    *,
    tools: ToolPermissions | None = None,
    label: str = "e2e-agent",
) -> AgentIdentity:
    """Create an ``AgentIdentity`` with sensible e2e defaults.

    ``label`` derives a deterministic id so a test that needs distinct
    agents can request them and the id stays legible in logs.
    """
    return AgentIdentity(
        id=as_uuid(label),
        name="E2E Agent",
        role="Developer",
        department="Engineering",
        level=SeniorityLevel.MID,
        hiring_date=date(2026, 1, 15),
        personality=PersonalityConfig(traits=("analytical",)),
        model=ModelConfig(provider=_TEST_PROVIDER, model_id=_TEST_MODEL),
        tools=tools or ToolPermissions(),
    )


def make_e2e_task(
    *,
    identity: AgentIdentity,
    title: str = "E2E test task",
    description: str = "End-to-end test task.",
    label: str = "e2e-task",
) -> Task:
    """Create a ``Task`` assigned to the given identity.

    ``label`` derives a deterministic id so a test can correlate the
    task across a create/fetch round-trip and read it legibly in logs.
    """
    return Task(
        id=as_uuid(label),
        title=title,
        description=description,
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-e2e",
        created_by="manager",
        assigned_to=str(identity.id),
        status=TaskStatus.ASSIGNED,
    )
