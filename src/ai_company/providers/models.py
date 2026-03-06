"""Provider-layer domain models for chat completion requests and responses."""

from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from ai_company.core.types import NotBlankStr  # noqa: TC001

from .enums import FinishReason, MessageRole, StreamEventType


class TokenUsage(BaseModel):
    """Token counts and cost for a single completion call.

    This is the lightweight provider-layer record.  The budget layer's
    ``ai_company.budget.CostRecord`` adds agent/task context around it.

    Attributes:
        input_tokens: Number of input (prompt) tokens.
        output_tokens: Number of output (completion) tokens.
        total_tokens: Sum of input and output tokens (computed).
        cost_usd: Estimated cost in USD for this call.
    """

    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(ge=0, description="Input token count")
    output_tokens: int = Field(ge=0, description="Output token count")
    cost_usd: float = Field(ge=0.0, description="Estimated cost in USD")

    @computed_field(description="Total token count")  # type: ignore[prop-decorator]  # mypy doesn't support stacked decorators on @property
    @property
    def total_tokens(self) -> int:
        """Sum of input and output tokens."""
        return self.input_tokens + self.output_tokens


ZERO_TOKEN_USAGE = TokenUsage(
    input_tokens=0,
    output_tokens=0,
    cost_usd=0.0,
)
"""Additive identity for ``TokenUsage``."""


def add_token_usage(a: TokenUsage, b: TokenUsage) -> TokenUsage:
    """Create a new ``TokenUsage`` with summed token counts and cost.

    Args:
        a: First usage record.
        b: Second usage record.

    Returns:
        New ``TokenUsage`` with summed token counts and cost
        (``total_tokens`` is computed automatically).
    """
    return TokenUsage(
        input_tokens=a.input_tokens + b.input_tokens,
        output_tokens=a.output_tokens + b.output_tokens,
        cost_usd=a.cost_usd + b.cost_usd,
    )


class ToolDefinition(BaseModel):
    """Schema for a tool the model can invoke.

    Uses raw JSON Schema for ``parameters_schema`` because every LLM
    provider (OpenAI, Anthropic, LiteLLM) consumes it natively.

    Note:
        The ``parameters_schema`` dict is shallowly frozen by Pydantic's
        ``frozen=True`` — field reassignment is prevented but nested
        contents can still be mutated in place.  ``BaseTool.to_definition()``
        provides a deep-copied schema, and ``ToolInvoker`` deep-copies
        arguments at the execution boundary, so no additional caller-side
        copying is needed for standard tool/provider workflows.  Direct
        consumers outside these paths should deep-copy if they intend to
        modify the schema.  See DESIGN_SPEC.md section 15.5.

    Attributes:
        name: Tool name.
        description: Human-readable description of the tool.
        parameters_schema: JSON Schema dict describing the tool parameters.
    """

    model_config = ConfigDict(frozen=True)

    name: NotBlankStr = Field(description="Tool name")
    description: str = Field(default="", description="Tool description")
    parameters_schema: dict[str, Any] = Field(
        default_factory=dict,
        description="JSON Schema for tool parameters",
    )


class ToolCall(BaseModel):
    """A tool invocation requested by the model.

    Note:
        The ``arguments`` dict is shallowly frozen by Pydantic's
        ``frozen=True`` — field reassignment is prevented but nested
        contents can still be mutated in place.  The ``ToolInvoker``
        deep-copies arguments before passing them to tool
        implementations.  See DESIGN_SPEC.md section 15.5.

    Attributes:
        id: Provider-assigned tool call identifier.
        name: Name of the tool to invoke.
        arguments: Parsed arguments dict.
    """

    model_config = ConfigDict(frozen=True)

    id: NotBlankStr = Field(description="Tool call identifier")
    name: NotBlankStr = Field(description="Tool name")
    arguments: dict[str, Any] = Field(
        default_factory=dict,
        description="Tool arguments",
    )


class ToolResult(BaseModel):
    """Result of executing a tool call, sent back to the model.

    Attributes:
        tool_call_id: The ``ToolCall.id`` this result corresponds to.
        content: String content returned by the tool.
        is_error: Whether the tool execution failed.
    """

    model_config = ConfigDict(frozen=True)

    tool_call_id: NotBlankStr = Field(description="Matching tool call ID")
    content: str = Field(description="Tool output content")
    is_error: bool = Field(default=False, description="Whether tool errored")


class ChatMessage(BaseModel):
    """A single message in a chat completion conversation.

    Attributes:
        role: Message role (system, user, assistant, tool).
        content: Text content of the message.
        tool_calls: Tool calls requested by the assistant (assistant only).
        tool_result: Result of a tool execution (tool role only).
    """

    model_config = ConfigDict(frozen=True)

    role: MessageRole = Field(description="Message role")
    content: str | None = Field(default=None, description="Text content")
    tool_calls: tuple[ToolCall, ...] = Field(
        default=(),
        description="Tool calls (assistant messages only)",
    )
    tool_result: ToolResult | None = Field(
        default=None,
        description="Tool result (tool messages only)",
    )

    @model_validator(mode="after")
    def _validate_role_constraints(self) -> Self:
        """Enforce role-specific field constraints.

        Rules:
            - tool: must have tool_result, must not have tool_calls.
            - assistant: may have content and/or tool_calls, must not
              have tool_result.
            - system/user: must not have tool_calls or tool_result.
            - Non-tool messages must have content or tool_calls.

        Note:
            Empty-string content (``content=""``) is intentionally
            permitted — some providers return it legitimately.

        Raises:
            ValueError: If any role-specific constraint is violated.
        """
        match self.role:
            case MessageRole.TOOL:
                if self.tool_result is None:
                    msg = "tool messages must include a tool_result"
                    raise ValueError(msg)
                if self.tool_calls:
                    msg = "tool messages must not include tool_calls"
                    raise ValueError(msg)
            case MessageRole.ASSISTANT:
                if self.tool_result is not None:
                    msg = "assistant messages must not include a tool_result"
                    raise ValueError(msg)
            case MessageRole.SYSTEM | MessageRole.USER:
                if self.tool_calls:
                    msg = f"{self.role} messages must not include tool_calls"
                    raise ValueError(msg)
                if self.tool_result is not None:
                    msg = f"{self.role} messages must not include a tool_result"
                    raise ValueError(msg)
            case _:
                msg = f"Unhandled message role: {self.role}"  # type: ignore[unreachable]
                raise ValueError(msg)

        if (
            self.role != MessageRole.TOOL
            and self.content is None
            and not self.tool_calls
        ):
            msg = f"{self.role} messages must have content or tool_calls"
            raise ValueError(msg)

        return self


class CompletionConfig(BaseModel):
    """Optional parameters for a completion request.

    All fields are optional — the provider fills in defaults.

    Attributes:
        temperature: Sampling temperature (0.0-2.0). Actual valid range
            may vary by provider.
        max_tokens: Maximum tokens to generate.
        stop_sequences: Sequences that stop generation.
        top_p: Nucleus sampling threshold.
        timeout: Request timeout in seconds.
    """

    model_config = ConfigDict(frozen=True)

    temperature: float | None = Field(
        default=None,
        ge=0.0,
        le=2.0,
        description="Sampling temperature",
    )
    max_tokens: int | None = Field(
        default=None,
        gt=0,
        description="Maximum tokens to generate",
    )
    stop_sequences: tuple[str, ...] = Field(
        default=(),
        description="Stop sequences",
    )
    top_p: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Nucleus sampling threshold",
    )
    timeout: float | None = Field(
        default=None,
        gt=0.0,
        description="Request timeout in seconds",
    )


class CompletionResponse(BaseModel):
    """Result of a non-streaming completion call.

    Attributes:
        content: Generated text content (may be ``None`` for tool-use-only responses).
        tool_calls: Tool calls the model wants to execute.
        finish_reason: Why the model stopped generating.
        usage: Token usage and cost breakdown.
        model: Model identifier that served the request.
        provider_request_id: Provider-assigned request ID for debugging.
    """

    model_config = ConfigDict(frozen=True)

    content: str | None = Field(default=None, description="Generated text")
    tool_calls: tuple[ToolCall, ...] = Field(
        default=(),
        description="Requested tool calls",
    )
    finish_reason: FinishReason = Field(description="Reason generation stopped")
    usage: TokenUsage = Field(description="Token usage breakdown")
    model: NotBlankStr = Field(description="Model that served the request")
    provider_request_id: str | None = Field(
        default=None,
        description="Provider request ID",
    )

    @model_validator(mode="after")
    def _validate_has_output(self) -> Self:
        """Ensure normal completions have content or tool_calls.

        Responses with ``content_filter`` or ``error`` finish reasons
        may legitimately have no output.

        Raises:
            ValueError: If a non-filtered/non-error response lacks output.
        """
        if (
            self.content is None
            and not self.tool_calls
            and self.finish_reason
            not in (FinishReason.CONTENT_FILTER, FinishReason.ERROR)
        ):
            msg = (
                f"CompletionResponse with finish_reason="
                f"{self.finish_reason.value} must have content "
                f"or tool_calls"
            )
            raise ValueError(msg)
        return self


class StreamChunk(BaseModel):
    """A single chunk from a streaming completion response.

    The ``event_type`` discriminator determines which optional fields are
    populated.

    Attributes:
        event_type: Type of stream event.
        content: Text delta (for ``content_delta``).
        tool_call_delta: Tool call received during streaming (for ``tool_call_delta``).
        usage: Final token usage (for ``usage`` event).
        error_message: Error description (for ``error`` event).
    """

    model_config = ConfigDict(frozen=True)

    event_type: StreamEventType = Field(description="Stream event type")
    content: str | None = Field(default=None, description="Text delta")
    tool_call_delta: ToolCall | None = Field(
        default=None,
        description="Tool call received during streaming",
    )
    usage: TokenUsage | None = Field(
        default=None,
        description="Final token usage",
    )
    error_message: str | None = Field(
        default=None,
        description="Error description",
    )

    @model_validator(mode="after")
    def _validate_event_fields(self) -> Self:
        """Ensure only the relevant fields are populated for each event_type.

        Each event type requires specific fields and rejects extraneous
        payload fields to maintain strict discriminated-union semantics.

        Raises:
            ValueError: If required fields are missing or extraneous
                fields are set.
        """
        payload: dict[str, object] = {
            "content": self.content,
            "tool_call_delta": self.tool_call_delta,
            "usage": self.usage,
            "error_message": self.error_message,
        }
        required: set[str] = set()
        match self.event_type:
            case StreamEventType.CONTENT_DELTA:
                required = {"content"}
            case StreamEventType.TOOL_CALL_DELTA:
                required = {"tool_call_delta"}
            case StreamEventType.USAGE:
                required = {"usage"}
            case StreamEventType.ERROR:
                required = {"error_message"}
            case StreamEventType.DONE:
                pass  # Terminal event, no required payload fields.
            case _:
                msg = f"Unhandled stream event type: {self.event_type}"  # type: ignore[unreachable]
                raise ValueError(msg)

        for name in required:
            if payload[name] is None:
                msg = f"{self.event_type.value} event must include {name}"
                raise ValueError(msg)

        extraneous = sorted(
            name
            for name, value in payload.items()
            if name not in required and value is not None
        )
        if extraneous:
            fields = ", ".join(extraneous)
            msg = f"{self.event_type.value} event must not include {fields}"
            raise ValueError(msg)
        return self
