# module-kind: declarative
"""Provider-layer domain models for chat completion requests and responses."""

import copy
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    computed_field,
    model_validator,
)

from synthorg.core.completion_enums import FinishReason, ReasoningEffort
from synthorg.core.tool_disclosure import (
    ToolL1Metadata,
    ToolL2Body,
    ToolL3Resource,
)
from synthorg.core.types import NotBlankStr
from synthorg.providers._stream_chunk_validation import validate_stream_chunk_fields

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

    @computed_field(description="Total token count")
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
        is_unresolved: Whether the named tool is not registered, so
            nothing ran at all (also a stricter form of ``is_error``).
            A tool that ran and failed is the agent doing something; a
            name nobody registered is not, and the turn-budget guard
            needs to tell them apart or a run guessing at tool names
            buys every extension it asks for.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    tool_call_id: NotBlankStr = Field(description="Matching tool call ID")
    content: str = Field(description="Tool output content")
    is_error: bool = Field(default=False, description="Whether tool errored")
    is_timeout: bool = Field(
        default=False,
        description="Whether tool errored due to timeout specifically",
    )
    is_unresolved: bool = Field(
        default=False,
        description="Whether the named tool is not registered, so nothing ran",
    )

    @model_validator(mode="after")
    def _validate_timeout_implies_error(self) -> Self:
        """Reject the flag combinations that describe no real outcome.

        Timeout and unresolved are both stricter forms of error; the metric
        layer and the turn-budget guard map each to a distinct outcome, but a
        non-error one is contradictory and would split outcome semantics
        across consumers. They also exclude each other: unresolved means the
        name matched no registered tool, so nothing ran, and nothing that did
        not run can have outlasted a deadline.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``is_timeout`` or ``is_unresolved`` is paired with
                ``is_error=False``, or if both are set at once.
        """
        if self.is_timeout and not self.is_error:
            msg = (
                "ToolResult.is_timeout requires is_error=True;"
                " timeout is a stricter form of error"
            )
            raise ValueError(msg)
        if self.is_unresolved and not self.is_error:
            msg = (
                "ToolResult.is_unresolved requires is_error=True;"
                " an unregistered tool is a stricter form of error"
            )
            raise ValueError(msg)
        if self.is_timeout and self.is_unresolved:
            msg = (
                "ToolResult cannot be both is_timeout and is_unresolved:"
                " unresolved means nothing ran, so there was no work to"
                " outlast a deadline"
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

    @computed_field(description="Chat-completion image_url data URI")
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
        reasoning_effort: Depth of extended reasoning ("thinking") to
            request. ``None`` leaves it unset (provider default). Only
            emitted for a model that advertises reasoning support.
        prompt_caching: Whether to place ``cache_control`` breakpoints on
            the stable prompt prefix (system, tools, and a rolling breakpoint
            at the trailing end of the conversation so far) so a caching-capable
            provider can reuse them across turns. Only applied for a model that
            advertises prompt-caching support.
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
    reasoning_effort: ReasoningEffort | None = Field(
        default=None,
        description=(
            "Depth of extended reasoning to request; dropped for a model "
            "that does not advertise reasoning support"
        ),
    )
    prompt_caching: bool = Field(
        default=False,
        description=(
            "Place cache_control breakpoints on the stable prompt prefix "
            "for a caching-capable model"
        ),
    )


class CompletionResponse(BaseModel):
    """Result of a non-streaming completion call.

    ``content``, ``reasoning`` and ``tool_calls`` are three independent
    channels, not three cases: one turn can carry any combination of them,
    and a reasoning model routinely emits all three at once. Only their
    disjunction is constrained, by ``_validate_has_output``. They are
    deliberately not a discriminated union.

    Attributes:
        content: Generated text content (may be ``None`` for tool-use-only responses).
        reasoning: Extended-reasoning text, when the model answered on that
            channel. Kept apart from ``content`` because it is the model's
            working, not its answer, and feeding it back as an assistant
            message would change what the model sees next. It still counts as
            output: a turn spent entirely on reasoning is a turn that
            happened, and treating it as empty failed the whole task.
        tool_calls: Tool calls the model wants to execute.
        dropped_tool_calls: The provider sent tool calls the driver could not
            parse, so they were dropped and ``tool_calls`` is empty. The two
            ways a turn can claim a tool and deliver none are corrected with
            different words, and a correction that describes the wrong one is
            answered with the same mistake: a live run told a model three
            times that its arguments were not valid JSON when the provider had
            sent no call at all, and got the identical reply each time.
        finish_reason: Why the model stopped generating.
        usage: Token usage and cost breakdown.
        model: Model identifier that served the request.
        provider_request_id: Provider-assigned request ID for debugging.
        provider_metadata: Provider metadata injected by the base class
            (``_synthorg_*`` keys for latency, retry count, retry reason).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    content: str | None = Field(default=None, description="Generated text")
    reasoning: str | None = Field(
        default=None,
        description="Extended-reasoning text, when the model produced any",
    )
    tool_calls: tuple[ToolCall, ...] = Field(
        default=(),
        description="Requested tool calls",
    )
    dropped_tool_calls: bool = Field(
        default=False,
        description="The provider sent tool calls the driver could not parse",
    )
    finish_reason: FinishReason = Field(description="Reason generation stopped")
    usage: TokenUsage = Field(description="Token usage breakdown")
    model: NotBlankStr = Field(description="Model that served the request")
    provider_request_id: NotBlankStr | None = Field(
        default=None,
        description="Provider request ID",
    )
    provider_metadata: dict[str, object] = Field(
        default_factory=dict,
        description="Provider metadata injected by the base class (_synthorg_* keys).",
    )

    @model_validator(mode="after")
    def _validate_has_output(self) -> Self:
        """Ensure normal completions have content, reasoning or tool_calls.

        Reasoning counts: a reasoning model can spend a whole turn on that
        channel, and a response carrying only reasoning is a response.

        Responses with ``content_filter`` or ``error`` finish reasons
        may legitimately have no output.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If a non-filtered/non-error response lacks output.
        """
        if (
            self.content is None
            and self.reasoning is None
            and not self.tool_calls
            and self.finish_reason
            not in (FinishReason.CONTENT_FILTER, FinishReason.ERROR)
        ):
            msg = (
                f"CompletionResponse with finish_reason="
                f"{self.finish_reason.value} must have content, "
                f"reasoning or tool_calls"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _deep_copy_provider_metadata(self) -> Self:
        """Deep-copy provider_metadata so the frozen model cannot be aliased.

        Returns:
            The instance with ``provider_metadata`` deep-copied.
        """
        object.__setattr__(
            self, "provider_metadata", copy.deepcopy(self.provider_metadata)
        )
        return self


class StreamChunk(BaseModel):
    """A single chunk from a streaming completion response.

    The ``event_type`` discriminator determines which optional fields are
    populated.

    Attributes:
        event_type: Type of stream event.
        content: Text delta (for ``content_delta`` and ``reasoning_delta``;
            the discriminator says which of the model's two channels it
            arrived on, so a consumer can accumulate them separately).
        tool_call_delta: Tool call received during streaming (for ``tool_call_delta``).
        usage: Final token usage (for ``usage`` event).
        error_message: Error description (for ``error`` event).
        finish_reason: Why generation stopped, carried on the terminal
            ``done`` event so a consumer reassembling the stream into a
            :class:`CompletionResponse` recovers the faithful finish reason
            (streaming content chunks carry none). Optional and only ever set
            on ``done``.
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
    finish_reason: FinishReason | None = Field(
        default=None,
        description="Finish reason, carried on the terminal done event",
    )

    @model_validator(mode="after")
    def _validate_event_fields(self) -> Self:
        """Ensure only the relevant fields are populated for each event_type.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If required fields are missing or extraneous
                fields are set.
        """
        validate_stream_chunk_fields(
            event_type=self.event_type,
            content=self.content,
            tool_call_delta=self.tool_call_delta,
            usage=self.usage,
            error_message=self.error_message,
            finish_reason=self.finish_reason,
        )
        return self
