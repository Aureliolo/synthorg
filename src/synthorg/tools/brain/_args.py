"""Typed args models for the project-brain agent tools.

The write tool serves both create and revise: ``entry_id is None`` creates a new
logical entry, otherwise it appends the next revision of an existing one. The
payload reuses the domain :data:`BrainPayload` discriminated union directly (the
per-kind payloads are pure value objects with no server-assigned fields), so the
agent passes ``{"entry_kind": "decision", "decision_outcome": ...}`` and the
boundary validates it into the right concrete payload.
"""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.project_brain.models import (
    BrainEntryStatus,
    BrainPayload,
    BrainRationale,
    BrainTitle,
    Citation,
)

_BRAIN_SEARCH_MIN_LIMIT: int = 1
_BRAIN_SEARCH_MAX_LIMIT: int = 64
_BRAIN_SEARCH_DEFAULT_LIMIT: int = 8


class WriteBrainEntryArgs(BaseModel):
    """Args for :class:`WriteBrainEntryTool` (create or revise).

    On create (``entry_id`` omitted) ``title``, ``rationale``, ``status``, and
    ``payload`` are required. On revise (``entry_id`` supplied) every field is an
    optional override; omitted fields inherit the current revision. The author is
    bound from the calling agent, not passed here.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    entry_id: NotBlankStr | None = Field(
        default=None,
        description="Existing entry to revise; omit to create a new entry",
    )
    title: BrainTitle | None = Field(
        default=None,
        description="Entry title (required on create)",
    )
    rationale: BrainRationale | None = Field(
        default=None,
        description="Why this entry holds, the 'why' (required on create)",
    )
    status: BrainEntryStatus | None = Field(
        default=None,
        description="Lifecycle status (required on create; validated per kind)",
    )
    payload: BrainPayload | None = Field(
        default=None,
        description="Kind-specific payload, discriminated on entry_kind "
        "(required on create)",
    )
    related_task_ids: tuple[NotBlankStr, ...] | None = Field(
        default=None,
        description="Task IDs this entry references; on revise omit to keep, "
        "pass [] to clear",
    )
    related_entry_ids: tuple[NotBlankStr, ...] | None = Field(
        default=None,
        description="Other brain entry IDs referenced; on revise omit to keep, "
        "pass [] to clear",
    )
    supersedes_entry_id: NotBlankStr | None = Field(
        default=None,
        description="Entry id this one supersedes; once set it persists across "
        "revisions (no clear path)",
    )
    tags: tuple[NotBlankStr, ...] | None = Field(
        default=None,
        description="Free-form classification tags; on revise omit to keep, "
        "pass [] to clear",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional confidence in this entry, 0..1",
    )
    citations: tuple[Citation, ...] | None = Field(
        default=None,
        description="Provenance pointers backing this entry; on revise omit to "
        "keep, pass [] to clear",
    )


class SearchBrainArgs(BaseModel):
    """Args for :class:`SearchBrainTool`."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    query: NotBlankStr = Field(description="Search text")
    limit: int = Field(
        default=_BRAIN_SEARCH_DEFAULT_LIMIT,
        ge=_BRAIN_SEARCH_MIN_LIMIT,
        le=_BRAIN_SEARCH_MAX_LIMIT,
        description="Maximum hits to return",
    )
