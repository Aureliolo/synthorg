"""Provider-layer domain models for chat completion requests and responses."""

from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    computed_field,
    model_validator,
)

from synthorg.core.completion_enums import FinishReason
from synthorg.core.tool_disclosure import (
    ToolL1Metadata,
    ToolL2Body,
    ToolL3Resource,
)
from synthorg.core.types import NotBlankStr

from .enums import (
    ImageDetail,
    ImageMediaType,
    MessageRole,
    StreamEventType,
)


class TokenUsage(BaseModel):
    """Token counts and cost for a single completion call.

    This is the lightweight provider-layer record.  The budget layer's
    ``synthorg.budget.CostRecord`` adds agent/task context around it.

    Attributes:
        input_tokens: Number of input (prompt) tokens.
        output_tokens: Number of output (completion) tokens.
        total_tokens: Sum of input and output tokens (computed).
        cost: Estimated cost in the configured currency for this call.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    input_tokens: int = Field(ge=0, description="Input token count")
    output_tokens: int = Field(ge=0, description="Output token count")
    cost: float = Field(ge=0.0, description="Estimated cost in the configured currency")

    @computed_field(description="Total token count")  # type: ignore[prop-decorator]  # mypy doesn't support stacked decorators on @property
    @property
    def total_tokens(self) -> int:
        """Sum of input and output tokens."""
        return self.input_tokens + self.output_tokens


ZERO_TOKEN_USAGE = TokenUsage(
    input_tokens=0,
    output_tokens=0,
    cost=0.0,
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
        cost=a.cost + b.cost,
    )


class ToolDefinition(BaseModel):
    """Schema for a tool the model can invoke.

    Uses raw JSON Schema for ``parameters_schema`` because every LLM
    provider consumes it natively.

    Note:
        The ``parameters_schema`` dict is shallowly frozen by Pydantic's
        ``frozen=True`` -- field reassignment is prevented but nested
        contents can still be mutated in place.  ``BaseTool.to_definition()``
        provides a deep-copied schema, and ``ToolInvoker`` deep-copies
        arguments at the execution boundary, so no additional caller-side
        copying is needed for standard tool/provider workflows.  Direct
        consumers outside these paths should deep-copy if they intend to
        modify the schema.  See the tech stack page (docs/architecture/tech-stack.md).

    Attributes:
        name: Tool name.
        description: Human-readable description of the tool.
        parameters_schema: JSON Schema dict describing the tool parameters.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Tool name")
    description: str = Field(default="", description="Tool description")
    parameters_schema: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="JSON Schema for tool parameters",
    )

    # ── Progressive disclosure tiers ─────────────────────────────
    l1_metadata: ToolL1Metadata | None = Field(
        default=None,
        description="L1 always-in-context summary",
    )
    l2_body: ToolL2Body | None = Field(
        default=None,
        description="L2 on-demand instruction body",
    )
    l3_resources: tuple[ToolL3Resource, ...] = Field(
        default=(),
        description="L3 explicit-request resources",
    )

    @model_validator(mode="after")
    def _validate_l1_name_matches(self) -> ToolDefinition:
        """Ensure l1_metadata.name matches the tool name.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``l1_metadata`` is set but its ``name`` does
                not match the tool's ``name``.
        """
        if self.l1_metadata is not None and self.l1_metadata.name != self.name:
            msg = (
                f"l1_metadata.name ({self.l1_metadata.name!r}) must "
                f"match tool name ({self.name!r})"
            )
            raise ValueError(msg)
        return self


class ToolCall(BaseModel):
    """A tool invocation requested by the model.

    Note:
        The ``arguments`` dict is shallowly frozen by Pydantic's
        ``frozen=True`` -- field reassignment is prevented but nested
        contents can still be mutated in place.  The ``ToolInvoker``
        deep-copies arguments before passing them to tool
        implementations.  See the tech stack page (docs/architecture/tech-stack.md).

    Attributes:
        id: Provider-assigned tool call identifier.
        name: Name of the tool to invoke.
        arguments: Parsed arguments dict.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Tool call identifier")
    name: NotBlankStr = Field(description="Tool name")
    arguments: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Tool arguments",
    )


class ToolResult(BaseModel):
    """Result of executing a tool call, sent back to the model.

    Attributes:
        tool_call_id: The ``ToolCall.id`` this result corresponds to.
        content: String content returned by the tool.
        is_error: Whether the tool execution failed.
        is_timeout: Whether the tool execution timed out specifically
            (a stricter form of ``is_error``). Lets the metric layer
            distinguish a tool that hit its time budget from one
            that returned a deterministic error so dashboards
            don't conflate the two.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    tool_call_id: NotBlankStr = Field(description="Matching tool call ID")
    content: str = Field(description="Tool output content")
    is_error: bool = Field(default=False, description="Whether tool errored")
    is_timeout: bool = Field(
        default=False,
        description="Whether tool errored due to timeout specifically",
    )

    @model_validator(mode="after")
    def _validate_timeout_implies_error(self) -> Self:
        """Reject ``is_timeout=True`` paired with ``is_error=False``.

        Timeout is a stricter form of error; the metric layer maps
        timeout to a distinct outcome label, but a non-error timeout
        is contradictory and would split outcome semantics across
        observability consumers.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``is_timeout=True`` is paired with
                ``is_error=False``.
        """
        if self.is_timeout and not self.is_error:
            msg = (
                "ToolResult.is_timeout requires is_error=True;"
                " timeout is a stricter form of error"
            )
            raise ValueError(msg)
        return self


class ImagePart(BaseModel):
    """An image attached to a user message for multimodal models.

    Carries base64-encoded image bytes (no ``data:`` prefix) plus the
    MIME type and an optional vision-detail hint. The ``data_uri``
    computed field renders the chat-completion ``image_url.url`` value.

    Attributes:
        media_type: Image MIME type.
        base64_data: Base64-encoded image bytes (no ``data:`` prefix).
        detail: Vision-detail hint (``auto`` by default).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    media_type: ImageMediaType = Field(description="Image MIME type")
    base64_data: NotBlankStr = Field(
        description="Base64-encoded image bytes (no data: prefix)",
    )
    detail: ImageDetail = Field(
        default=ImageDetail.AUTO,
        description="Vision-detail hint passed to the provider",
    )

    @computed_field(description="Chat-completion image_url data URI")  # type: ignore[prop-decorator]  # mypy doesn't support stacked decorators on @property
    @property
    def data_uri(self) -> str:
        """Render the ``data:<media_type>;base64,<data>`` URI."""
        return f"data:{self.media_type.value};base64,{self.base64_data}"


class ChatMessage(BaseModel):
    """A single message in a chat completion conversation.

    Attributes:
        role: Message role (system, user, assistant, tool).
        content: Text content of the message.
        tool_calls: Tool calls requested by the assistant (assistant only).
        tool_result: Result of a tool execution (tool role only).
        image_parts: Images attached to a user message (multimodal);
            user role only, requires a vision-capable model.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    image_parts: tuple[ImagePart, ...] = Field(
        default=(),
        description="Attached images (user messages only)",
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
            permitted -- some providers return it legitimately.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

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
            and not self.image_parts
        ):
            msg = f"{self.role} messages must have content or tool_calls"
            raise ValueError(msg)

        return self

    @model_validator(mode="after")
    def _validate_image_parts_user_only(self) -> Self:
        """Reject ``image_parts`` on any role other than user.

        Images attach only to user turns in the chat-completion
        multimodal shape; a non-user message carrying ``image_parts``
        would be silently dropped at the mapper boundary, so reject it
        at construction instead.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``image_parts`` is non-empty on a non-user
                message.
        """
        if self.image_parts and self.role is not MessageRole.USER:
            msg = f"{self.role} messages must not include image_parts"
            raise ValueError(msg)
        return self


class CompletionConfig(BaseModel):
    """Optional parameters for a completion request.

    All fields are optional -- the provider fills in defaults.

    Attributes:
        temperature: Sampling temperature (0.0-2.0). Actual valid range
            may vary by provider.
        max_tokens: Maximum tokens to generate.
        stop_sequences: Sequences that stop generation.
        top_p: Nucleus sampling threshold.
        timeout: Request timeout in seconds.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    top_p: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description=(
            "Nucleus-sampling threshold. Defaults to 1.0 (full "
            "distribution, no truncation) so every completion call "
            "has an explicit deterministic value without each site "
            "having to repeat it. Override when the prompt class "
            "needs a custom value alongside ``temperature``."
        ),
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
        provider_metadata: Provider metadata injected by the base class
            (``_synthorg_*`` keys for latency, retry count, retry reason).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    provider_metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Provider metadata injected by the base class (_synthorg_* keys).",
    )

    @model_validator(mode="after")
    def _validate_has_output(self) -> Self:
        """Ensure normal completions have content or tool_calls.

        Responses with ``content_filter`` or ``error`` finish reasons
        may legitimately have no output.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

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

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

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
