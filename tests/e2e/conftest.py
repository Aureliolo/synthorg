"""End-to-end test configuration and fixtures."""

from datetime import date
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from synthorg.core.agent import (
    AgentIdentity,
    ModelConfig,
    PersonalityConfig,
    ToolPermissions,
)
from synthorg.core.enums import (
    Priority,
    SeniorityLevel,
    TaskStatus,
    TaskType,
)
from synthorg.core.task import Task
from synthorg.providers.capabilities import ModelCapabilities
from synthorg.providers.enums import FinishReason
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

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

_TEST_MODEL = "test-model-001"
_TEST_PROVIDER = "test-provider"


class ScriptedProvider:
    """Mock provider that plays back a list of responses sequentially.

    Records all received messages for post-test conversation flow assertions.
    Raises ``IndexError`` if more calls are made than scripted responses.
    """

    def __init__(self, responses: list[CompletionResponse]) -> None:
        self._responses = list(responses)
        self._call_count = 0
        self.received_messages: list[list[ChatMessage]] = []

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
        """Record the messages and return the next scripted response."""
        idx = self._call_count
        if idx >= len(self._responses):
            msg = (
                f"ScriptedProvider exhausted: call #{idx + 1} but only "
                f"{len(self._responses)} responses were scripted"
            )
            raise IndexError(msg)
        self.received_messages.append(list(messages))
        self._call_count += 1
        return self._responses[idx]

    async def stream(
        self,
        messages: list[ChatMessage],
        model: str,
        *,
        tools: list[ToolDefinition] | None = None,
        config: CompletionConfig | None = None,
    ) -> AsyncIterator[StreamChunk]:
        """Not used in e2e tests; raises ``NotImplementedError``."""
        msg = "stream not supported in ScriptedProvider"
        raise NotImplementedError(msg)

    async def get_model_capabilities(self, model: str) -> ModelCapabilities:
        """Return minimal test capabilities."""
        return ModelCapabilities(
            model_id=model,
            provider=_TEST_PROVIDER,
            supports_tools=True,
            supports_streaming=False,
            max_context_tokens=8192,
            max_output_tokens=4096,
            cost_per_1k_input=0.01,
            cost_per_1k_output=0.03,
        )


# Verify ScriptedProvider satisfies the CompletionProvider protocol.
assert isinstance(ScriptedProvider([]), CompletionProvider)


@pytest.fixture
def e2e_workspace(tmp_path: Path) -> Path:
    """Isolated temporary directory for real file tool operations."""
    workspace = tmp_path / "agent_workspace"
    workspace.mkdir()
    return workspace


def make_e2e_identity(
    *,
    tools: ToolPermissions | None = None,
) -> AgentIdentity:
    """Create an ``AgentIdentity`` with sensible e2e defaults."""
    return AgentIdentity(
        id=uuid4(),
        name="E2E Agent",
        role="Developer",
        department="Engineering",
        level=SeniorityLevel.MID,
        hiring_date=date(2026, 1, 15),
        personality=PersonalityConfig(traits=("analytical",)),
        model=ModelConfig(
            provider=_TEST_PROVIDER,
            model_id=_TEST_MODEL,
        ),
        tools=tools or ToolPermissions(),
    )


def make_e2e_task(
    *,
    identity: AgentIdentity,
    title: str = "E2E test task",
    description: str = "End-to-end test task.",
) -> Task:
    """Create a ``Task`` assigned to the given identity."""
    return Task(
        id=f"task-e2e-{uuid4().hex[:8]}",
        title=title,
        description=description,
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-e2e",
        created_by="manager",
        assigned_to=str(identity.id),
        status=TaskStatus.ASSIGNED,
    )


def make_tool_call_response(
    *,
    tool_calls: tuple[ToolCall, ...],
    input_tokens: int = 50,
    output_tokens: int = 20,
    cost_usd: float = 0.005,
) -> CompletionResponse:
    """Build a ``CompletionResponse`` with tool calls."""
    return CompletionResponse(
        content=None,
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        ),
        model=_TEST_MODEL,
        tool_calls=tool_calls,
    )


def make_text_response(
    content: str,
    *,
    input_tokens: int = 80,
    output_tokens: int = 30,
    cost_usd: float = 0.008,
) -> CompletionResponse:
    """Build a ``CompletionResponse`` with text content (no tool calls)."""
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
        ),
        model=_TEST_MODEL,
    )
