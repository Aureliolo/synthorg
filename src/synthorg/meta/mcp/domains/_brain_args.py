"""Pydantic args models for long-horizon project-brain MCP tools.

Admin tools (append / resolve / supersede / clear-blocker) inherit
:class:`AdminGuardrailFields` so the operator-supplied ``confirm`` + ``reason``
survive the ``extra="forbid"`` boundary and reach ``require_admin_guardrails``.
Read tools inherit :class:`_ArgsBase` (or :class:`PaginationFields` for the
paginated list). The payload reuses the domain :data:`BrainPayload` discriminated
union directly.
"""

from pydantic import Field

from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.domains._common_args import (
    AdminGuardrailFields,
    PaginationFields,
    _ArgsBase,
)
from synthorg.project_brain.constants import (
    BRAIN_HISTORY_DEFAULT_LIMIT,
    BRAIN_LIST_DEFAULT_LIMIT,
    BRAIN_SEARCH_DEFAULT_LIMIT,
    BRAIN_SEARCH_MAX_LIMIT,
)
from synthorg.project_brain.models import (
    BrainEntryKind,
    BrainEntryStatus,
    BrainPayload,
    BrainRationale,
    BrainTitle,
    Citation,
)


class BrainAppendArgs(AdminGuardrailFields):
    """Args for ``brain:append`` (create or revise an entry).

    On create (``entry_id`` omitted) ``title``, ``rationale``, ``status``, and
    ``payload`` are required; on revise they are optional overrides.
    """

    project_id: NotBlankStr = Field(description="Owning project")
    author: NotBlankStr = Field(description="Writer identifier (operator or agent)")
    entry_id: NotBlankStr | None = Field(
        default=None,
        description="Existing entry to revise; omit to create",
    )
    title: BrainTitle | None = Field(default=None, description="Entry title")
    rationale: BrainRationale | None = Field(
        default=None,
        description="Why this entry holds (the 'why')",
    )
    status: BrainEntryStatus | None = Field(
        default=None,
        description="Lifecycle status (validated per kind)",
    )
    payload: BrainPayload | None = Field(
        default=None,
        description="Kind-specific payload, discriminated on entry_kind",
    )
    related_task_ids: tuple[NotBlankStr, ...] | None = Field(
        default=None,
        description="On revise, omit to keep current links; pass [] to clear",
    )
    related_entry_ids: tuple[NotBlankStr, ...] | None = Field(
        default=None,
        description="On revise, omit to keep current links; pass [] to clear",
    )
    supersedes_entry_id: NotBlankStr | None = Field(
        default=None,
        description="Entry this supersedes; once set it persists (no clear path)",
    )
    tags: tuple[NotBlankStr, ...] | None = Field(
        default=None,
        description="On revise, omit to keep current tags; pass [] to clear",
    )
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    citations: tuple[Citation, ...] | None = Field(
        default=None,
        description="On revise, omit to keep current citations; pass [] to clear",
    )


class BrainResolveArgs(AdminGuardrailFields):
    """Args for ``brain:resolve`` (resolve an open question or dependency)."""

    project_id: NotBlankStr = Field(description="Owning project")
    entry_id: NotBlankStr = Field(description="Entry to resolve")
    author: NotBlankStr = Field(description="Who resolved it")
    answer: NotBlankStr | None = Field(
        default=None,
        description="Answer text (open questions only)",
    )


class BrainSupersedeArgs(AdminGuardrailFields):
    """Args for ``brain:supersede`` (supersede a decision or plan revision)."""

    project_id: NotBlankStr = Field(description="Owning project")
    entry_id: NotBlankStr = Field(description="Entry being superseded")
    by_entry_id: NotBlankStr = Field(description="Successor entry id")
    author: NotBlankStr = Field(description="Who superseded it")


class BrainClearBlockerArgs(AdminGuardrailFields):
    """Args for ``brain:clear-blocker`` (clear a blocker with a resolution)."""

    project_id: NotBlankStr = Field(description="Owning project")
    entry_id: NotBlankStr = Field(description="Blocker entry id")
    author: NotBlankStr = Field(description="Who cleared it")
    resolution: NotBlankStr | None = Field(
        default=None,
        description="How the blocker was cleared",
    )


class BrainGetArgs(_ArgsBase):
    """Args for ``brain:get``."""

    project_id: NotBlankStr = Field(description="Owning project")
    entry_id: NotBlankStr = Field(description="Logical entry id")
    revision: int | None = Field(
        default=None,
        ge=1,
        description="Exact revision; omit for the latest",
    )


class BrainListArgs(PaginationFields):
    """Args for ``brain:list`` (current-state projection)."""

    project_id: NotBlankStr = Field(description="Owning project")
    entry_kind: BrainEntryKind | None = Field(default=None)
    status: BrainEntryStatus | None = Field(default=None)
    tag: NotBlankStr | None = Field(default=None)
    author: NotBlankStr | None = Field(default=None)
    related_task_id: NotBlankStr | None = Field(default=None)
    limit: int = Field(
        default=BRAIN_LIST_DEFAULT_LIMIT,
        gt=0,
        le=500,
        description="Page size",
    )


class BrainQueryArgs(_ArgsBase):
    """Args for ``brain:query`` (semantic search)."""

    project_id: NotBlankStr = Field(description="Owning project")
    query: NotBlankStr = Field(description="Search text")
    limit: int = Field(
        default=BRAIN_SEARCH_DEFAULT_LIMIT,
        ge=1,
        le=BRAIN_SEARCH_MAX_LIMIT,
    )


class BrainHistoryArgs(_ArgsBase):
    """Args for ``brain:history`` (structured revision chain)."""

    project_id: NotBlankStr = Field(description="Owning project")
    entry_id: NotBlankStr = Field(description="Logical entry id")
    limit: int = Field(
        default=BRAIN_HISTORY_DEFAULT_LIMIT,
        ge=1,
        le=500,
    )
