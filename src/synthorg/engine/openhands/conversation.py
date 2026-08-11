# module-kind: code
"""Conversation seam between the adapter and the OpenHands runtime.

Inverts the SDK dependency: the adapter drives any object satisfying
:class:`OpenHandsConversation` and receives events through an
:data:`EventSink`. The real runtime (SDK + agent-server in the sandbox)
implements the factory; tests supply a scripted fake. The sink returns
``False`` to stop the run early (budget / shutdown / cancellation), which
the conversation honours by ceasing to emit further events.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol, Self, runtime_checkable
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.engine.openhands.events import OpenHandsEvent

EventSink = Callable[[OpenHandsEvent], Awaitable[bool]]
"""Async callback the conversation calls per event.

Returns ``True`` to continue the run, ``False`` to stop at the next safe
boundary (the adapter has recorded a terminal reason).
"""


class OpenHandsRunSpec(BaseModel):
    """Everything the runtime needs to start one OpenHands run.

    The LLM is pointed at ``gateway_base_url`` with ``gateway_token`` as its
    api-key, and its credentialed tools at ``mcp_base_url``; both are the only
    egress the sandbox is allowed. ``workspace_path`` is the project workspace
    mounted into the container. ``conversation_id`` is the stable per-task key
    the runtime persists conversation state under (so a resumed run
    re-attaches to the prior conversation); ``project_id`` selects the mounted
    workspace subtree host-side (never sent into the container).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    task_prompt: NotBlankStr = Field(description="The task to run")
    system_prompt: str | None = Field(
        default=None,
        description=(
            "The agent's own system prompt, applied in-container as the SDK "
            "agent context's system-message suffix. The engine builds this "
            "before either loop runs; dropping it here would run this loop on "
            "the task title and description alone while the native loop keeps "
            "the identity, house style, authority, autonomy and "
            "untrusted-content sections for the same task."
        ),
    )
    model: NotBlankStr = Field(description="Model id the gateway is bound to")
    gateway_base_url: NotBlankStr = Field(description="OpenAI-compatible gateway URL")
    gateway_token: NotBlankStr = Field(description="Per-run gateway bearer")
    mcp_base_url: NotBlankStr = Field(description="Credentialed-MCP endpoint URL")
    workspace_path: NotBlankStr = Field(description="Mounted project workspace path")
    conversation_id: UUID = Field(
        description="Stable per-task conversation key for resume"
    )
    max_turns: int = Field(gt=0, description="Turn ceiling for the run")
    project_id: NotBlankStr | None = Field(
        default=None, description="Owning project for the workspace mount subtree"
    )


class OpenHandsOutcome(BaseModel):
    """Terminal outcome of an OpenHands run."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    finished: bool = Field(description="Whether the agent reached a natural finish")
    error_message: str | None = Field(
        default=None, description="Runtime error message, if the run failed"
    )

    @model_validator(mode="after")
    def _finish_and_error_exclusive(self) -> Self:
        """Reject an outcome that is both finished and carries an error.

        A natural finish and a runtime error are mutually exclusive; a run
        that neither finished nor errored (stopped early at a boundary) is
        valid with both a ``False`` ``finished`` and a ``None`` message.

        Returns:
            The validated outcome.

        Raises:
            ValueError: If ``finished`` is set alongside an ``error_message``.
        """
        if self.finished and self.error_message is not None:
            msg = "a finished outcome cannot also carry an error_message"
            raise ValueError(msg)
        return self


@runtime_checkable
class OpenHandsConversation(Protocol):
    """The minimal conversation surface the adapter drives."""

    async def run(self) -> OpenHandsOutcome:
        """Drive the run to completion or an early stop.

        Emits each event to the sink registered at build time; stops when the
        sink returns ``False``.

        Returns:
            The terminal :class:`OpenHandsOutcome`.
        """
        ...


ConversationFactory = Callable[
    [OpenHandsRunSpec, EventSink], Awaitable[OpenHandsConversation]
]
"""Builds a conversation for a run spec, wiring the sink for its events."""
