"""A2A protocol models.

Frozen Pydantic v2 models representing the A2A specification's
wire-format types: JSON-RPC 2.0 envelope, Agent Cards, tasks,
messages, and parts.  These are the external-facing models;
internal SynthOrg models are mapped to/from these via the
``task_mapper`` and ``message_mapper`` modules.
"""

import copy
from enum import StrEnum
from typing import Annotated, Any, Literal, Self
from uuid import uuid4

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    model_validator,
)

from synthorg.core.types import NotBlankStr  # noqa: TC001

# ── JSON-RPC 2.0 Envelope ───────────────────────────────────────


class JsonRpcRequest(BaseModel):
    """JSON-RPC 2.0 request envelope.

    Attributes:
        jsonrpc: Protocol version (always ``"2.0"``).
        id: Request identifier (string or int).
        method: RPC method name.
        params: Method parameters.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int = Field(
        default_factory=lambda: str(uuid4()),
        description="Request identifier",
    )
    method: NotBlankStr = Field(description="RPC method name")
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Method parameters",
    )

    @model_validator(mode="after")
    def _deep_copy_params(self) -> Self:
        """Deep-copy mutable params dict at construction."""
        object.__setattr__(self, "params", copy.deepcopy(self.params))
        return self


class JsonRpcErrorData(BaseModel):
    """JSON-RPC 2.0 error object.

    Attributes:
        code: Integer error code.
        message: Human-readable error description.
        data: Additional error data.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    code: int = Field(description="Integer error code")
    message: str = Field(description="Human-readable error description")
    data: dict[str, Any] | None = Field(
        default=None,
        description="Additional error data",
    )

    @model_validator(mode="after")
    def _deep_copy_data(self) -> Self:
        """Deep-copy mutable data dict at construction."""
        if self.data is not None:
            object.__setattr__(self, "data", copy.deepcopy(self.data))
        return self


class JsonRpcResponse(BaseModel):
    """JSON-RPC 2.0 response envelope.

    Exactly one of ``result`` or ``error`` must be set.

    Attributes:
        jsonrpc: Protocol version (always ``"2.0"``).
        id: Request identifier echoed from the request.
        result: Success payload.
        error: Error payload.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int | None = None
    result: dict[str, Any] | None = None
    error: JsonRpcErrorData | None = None

    @model_validator(mode="after")
    def _validate_result_or_error(self) -> Self:
        """Ensure exactly one of result or error is set."""
        if self.result is not None and self.error is not None:
            msg = "JSON-RPC response must have result or error, not both"
            raise ValueError(msg)
        if self.result is None and self.error is None:
            msg = "JSON-RPC response must have result or error"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _deep_copy_result(self) -> Self:
        """Deep-copy mutable result dict at construction."""
        if self.result is not None:
            object.__setattr__(self, "result", copy.deepcopy(self.result))
        return self


# ── Standard JSON-RPC Error Codes ────────────────────────────────

JSONRPC_PARSE_ERROR: int = -32700
JSONRPC_INVALID_REQUEST: int = -32600
JSONRPC_METHOD_NOT_FOUND: int = -32601
JSONRPC_INVALID_PARAMS: int = -32602
JSONRPC_INTERNAL_ERROR: int = -32603

# A2A-specific error codes (application-defined range)
A2A_TASK_NOT_FOUND: int = -32001
A2A_TASK_NOT_CANCELABLE: int = -32002
A2A_AUTH_REQUIRED: int = -32003
A2A_PEER_NOT_ALLOWED: int = -32004
A2A_RATE_LIMITED: int = -32005
A2A_PAYLOAD_TOO_LARGE: int = -32006


# ── A2A Task State ──────────────────────────────────────────────


class A2ATaskState(StrEnum):
    """A2A protocol task states.

    Maps to internal ``TaskStatus`` via ``task_mapper``.
    """

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"
    REJECTED = "rejected"
    AUTH_REQUIRED = "auth-required"


# ── A2A Message Parts ───────────────────────────────────────────


class A2ATextPart(BaseModel):
    """A2A text content part.

    Attributes:
        type: Discriminator (always ``"text"``).
        text: The text content.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    type: Literal["text"] = "text"
    text: NotBlankStr = Field(description="Text content")


class A2ADataPart(BaseModel):
    """A2A structured data part.

    Attributes:
        type: Discriminator (always ``"data"``).
        data: Structured JSON content.
        mime_type: Optional MIME type hint.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    type: Literal["data"] = "data"
    data: dict[str, Any] = Field(description="Structured JSON content")
    mime_type: str | None = Field(
        default=None,
        description="Optional MIME type hint",
    )

    @model_validator(mode="after")
    def _deep_copy_data(self) -> Self:
        """Deep-copy mutable data dict at construction."""
        object.__setattr__(self, "data", copy.deepcopy(self.data))
        return self


class A2AFilePart(BaseModel):
    """A2A file reference part.

    Attributes:
        type: Discriminator (always ``"file"``).
        uri: File URI or URL.
        mime_type: Optional MIME type of the file.
        name: Optional human-readable filename.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    type: Literal["file"] = "file"
    uri: NotBlankStr = Field(description="File URI or URL")
    mime_type: str | None = Field(
        default=None,
        description="Optional MIME type",
    )
    name: NotBlankStr | None = Field(
        default=None,
        description="Optional human-readable filename",
    )


A2AMessagePart = Annotated[
    A2ATextPart | A2ADataPart | A2AFilePart,
    Discriminator("type"),
]
"""Discriminated union of A2A message content parts."""


# ── A2A Message ─────────────────────────────────────────────────


class A2AMessageRole(StrEnum):
    """Role of the message sender in A2A protocol."""

    USER = "user"
    AGENT = "agent"


class A2AMessage(BaseModel):
    """A2A protocol message.

    Attributes:
        role: Sender role (user or agent).
        parts: Ordered content parts.
        metadata: Optional metadata key-value pairs.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    role: A2AMessageRole = Field(description="Sender role")
    parts: tuple[A2AMessagePart, ...] = Field(
        min_length=1,
        description="Ordered content parts",
    )
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Optional metadata",
    )

    @model_validator(mode="after")
    def _deep_copy_metadata(self) -> Self:
        """Deep-copy mutable metadata dict at construction."""
        object.__setattr__(self, "metadata", copy.deepcopy(self.metadata))
        return self


# ── A2A Task ────────────────────────────────────────────────────


class A2ATask(BaseModel):
    """A2A protocol task.

    Attributes:
        id: Unique task identifier.
        state: Current task state.
        messages: Conversation messages.
        metadata: Task-level metadata.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr = Field(
        default_factory=lambda: str(uuid4()),
        description="Unique task identifier",
    )
    state: A2ATaskState = Field(
        default=A2ATaskState.SUBMITTED,
        description="Current task state",
    )
    messages: tuple[A2AMessage, ...] = Field(
        default=(),
        description="Conversation messages",
    )
    metadata: dict[str, str] = Field(
        default_factory=dict,
        description="Task-level metadata",
    )

    @model_validator(mode="after")
    def _deep_copy_metadata(self) -> Self:
        """Deep-copy mutable metadata dict at construction."""
        object.__setattr__(self, "metadata", copy.deepcopy(self.metadata))
        return self


# ── A2A Agent Skill ─────────────────────────────────────────────


class A2AAgentSkill(BaseModel):
    """A2A protocol agent skill descriptor.

    Attributes:
        id: Unique skill identifier.
        name: Human-readable skill name.
        description: What the skill does.
        tags: Searchable tags.
        input_modes: Accepted input content types.
        output_modes: Produced output content types.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr = Field(description="Unique skill identifier")
    name: NotBlankStr = Field(description="Human-readable skill name")
    description: str = Field(
        default="",
        description="What the skill does",
    )
    tags: tuple[str, ...] = Field(
        default=(),
        description="Searchable tags",
    )
    input_modes: tuple[str, ...] = Field(
        default=("text",),
        description="Accepted input content types",
    )
    output_modes: tuple[str, ...] = Field(
        default=("text",),
        description="Produced output content types",
    )


# ── A2A Agent Card ──────────────────────────────────────────────


class A2AAuthSchemeInfo(BaseModel):
    """Authentication scheme advertised in an Agent Card.

    Attributes:
        scheme: Auth scheme identifier.
        service_url: Optional URL for auth service (e.g. token
            endpoint).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    scheme: NotBlankStr = Field(description="Auth scheme identifier")
    service_url: str | None = Field(
        default=None,
        description="Optional auth service URL",
    )


class A2AAgentProvider(BaseModel):
    """Provider metadata in an Agent Card.

    Attributes:
        organization: Organization name.
        url: Organization URL.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    organization: NotBlankStr = Field(description="Organization name")
    url: str | None = Field(
        default=None,
        description="Organization URL",
    )


class A2AAgentCard(BaseModel):
    """A2A protocol Agent Card.

    A safe-subset projection of ``AgentIdentity`` that is served
    at well-known URIs for external discovery.

    Attributes:
        name: Agent display name.
        description: Human-readable description of the agent.
        url: Base URL where this agent's A2A endpoint lives.
        skills: Capabilities advertised by this agent.
        auth_schemes: Supported authentication schemes.
        provider: Organization metadata.
        version: Agent Card schema version.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    name: NotBlankStr = Field(description="Agent display name")
    description: str = Field(
        default="",
        description="Human-readable agent description",
    )
    url: NotBlankStr = Field(
        description="Base URL for this agent's A2A endpoint",
    )
    skills: tuple[A2AAgentSkill, ...] = Field(
        default=(),
        description="Advertised capabilities",
    )
    auth_schemes: tuple[A2AAuthSchemeInfo, ...] = Field(
        default=(),
        description="Supported authentication schemes",
    )
    provider: A2AAgentProvider | None = Field(
        default=None,
        description="Organization metadata",
    )
    version: str = Field(
        default="1.0",
        description="Agent Card schema version",
    )
