"""Typed args models for the living-doc agent tools."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import DocType  # noqa: TC001 -- Pydantic field annotation
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field annotation


class WriteLivingDocBlockArg(BaseModel):
    """One body block in a doc-write tool invocation.

    Kept loose (free dict body) at the agent boundary; the service
    re-validates against :data:`DocBlock` discriminated union before
    persistence. This keeps the agent-facing schema small while still
    routing through the strict Pydantic validation downstream.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    block_kind: NotBlankStr = Field(description="Block discriminator")
    text: NotBlankStr | None = Field(
        default=None,
        description="Text payload for heading / prose blocks",
    )
    level: int | None = Field(
        default=None,
        description="Heading level (1..6) for heading blocks",
    )
    items: tuple[NotBlankStr, ...] | None = Field(
        default=None,
        description="Bullet items for bullet_list blocks",
    )
    language: NotBlankStr | None = Field(
        default=None,
        description="Code language hint for code blocks",
    )
    code: str | None = Field(
        default=None,
        description="Code body for code blocks",
    )
    decision: NotBlankStr | None = Field(
        default=None,
        description="Decision summary for decision blocks",
    )
    rationale: NotBlankStr | None = Field(
        default=None,
        description="Decision rationale for decision blocks",
    )
    name: NotBlankStr | None = Field(
        default=None,
        description="Metric label for metric blocks",
    )
    value: NotBlankStr | None = Field(
        default=None,
        description="Metric value for metric blocks",
    )
    unit: NotBlankStr | None = Field(
        default=None,
        description="Metric unit for metric blocks",
    )
    label: NotBlankStr | None = Field(
        default=None,
        description="Link label for link blocks",
    )
    url: NotBlankStr | None = Field(
        default=None,
        description="Link target for link blocks",
    )


class WriteLivingDocArgs(BaseModel):
    """Args for :class:`WriteLivingDocTool`."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    title: NotBlankStr = Field(description="Document title")
    doc_type: DocType = Field(description="Doc taxonomy bucket")
    body: tuple[WriteLivingDocBlockArg, ...] = Field(
        min_length=1,
        description="Ordered tuple of body blocks",
    )
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Free-form classification tags",
    )
    related_task_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Task IDs that produced or reference this doc",
    )
    slug: NotBlankStr | None = Field(
        default=None,
        description="Existing slug to update (omit to create a new doc)",
    )


class SearchLivingDocsArgs(BaseModel):
    """Args for :class:`SearchLivingDocsTool`."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    query: NotBlankStr = Field(description="Search text")
    doc_types: tuple[DocType, ...] | None = Field(
        default=None,
        description="Optional filter on doc taxonomy buckets",
    )
    limit: int = Field(
        default=8,
        ge=1,
        le=64,
        description="Maximum hits to return",
    )
