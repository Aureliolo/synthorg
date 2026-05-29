"""Three-level progressive tool disclosure models.

Defines the L1/L2/L3 descriptor hierarchy for progressive tool
disclosure.  L1 metadata is always in context (~100 tokens per
tool), L2 bodies are loaded on demand (<5K tokens), and L3
resources are fetched explicitly.

See ``docs/design/tools.md`` (Progressive Tool Disclosure section).
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr

# ── L1: Always-in-context summary ────────────────────────────────

CostTier = Literal["cheap", "medium", "expensive"]
"""Relative invocation cost of a tool."""

ContentType = Literal["markdown", "code", "schema", "example_trace"]
"""Allowed content types for L3 resource files."""


class ToolL1Metadata(BaseModel):
    """Always-in-context tool summary (~100 tokens).

    Injected into the system prompt for all permitted tools so the
    agent knows what is available and can decide which tools to load.

    Attributes:
        name: Tool name (matches ``BaseTool.name``).
        short_description: One-sentence purpose (max 200 chars).
        category: Tool taxonomy bucket (e.g. ``"file_system"``).
        typical_cost_tier: Relative invocation cost.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Tool name")
    short_description: str = Field(
        max_length=200,
        description="One-sentence purpose",
    )
    category: NotBlankStr = Field(description="Tool taxonomy bucket")
    typical_cost_tier: CostTier = Field(
        description="Relative invocation cost",
    )


# ── L2: On-demand instruction body ──────────────────────────────


class ToolL2Body(BaseModel):
    """On-demand tool specification (<5K tokens).

    Loaded when the agent calls ``load_tool(name)``.  Contains the
    full description, parameter schema, usage examples, and known
    failure modes.

    Attributes:
        full_description: Detailed usage instructions.
        parameter_schema: JSON Schema for tool parameters.
        usage_examples: Example invocations.
        failure_modes: Known failure scenarios.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    full_description: str = Field(
        min_length=1,
        description="Detailed usage instructions",
    )
    parameter_schema: dict[str, object] = Field(
        default_factory=dict,
        description="JSON Schema for tool parameters",
    )
    usage_examples: tuple[str, ...] = Field(
        default=(),
        description="Example invocations",
    )
    failure_modes: tuple[str, ...] = Field(
        default=(),
        description="Known failure scenarios",
    )


# ── L3: Explicit-request resource ───────────────────────────────


class ToolL3Resource(BaseModel):
    """Explicit-request resource file.

    Fetched when the agent calls
    ``load_tool_resource(name, resource_id)``.  Never auto-injected.

    Attributes:
        resource_id: Unique identifier within the parent tool.
        content_type: Format of the resource content.
        content: The resource payload.
        size_bytes: Byte length of ``content`` (UTF-8 encoded).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    resource_id: NotBlankStr = Field(description="Resource identifier")
    content_type: ContentType = Field(description="Content format")
    content: str = Field(description="Resource payload")
    size_bytes: int = Field(ge=0, description="Content byte length")

    @model_validator(mode="after")
    def _validate_size_bytes(self) -> ToolL3Resource:
        """Ensure size_bytes matches actual content byte length.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``size_bytes`` does not equal the UTF-8 byte
                length of ``content``.
        """
        expected = len(self.content.encode())
        if self.size_bytes != expected:
            msg = (
                f"size_bytes={self.size_bytes} does not match "
                f"actual content byte length={expected}"
            )
            raise ValueError(msg)
        return self
