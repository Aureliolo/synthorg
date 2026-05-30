# module-kind: declarative
"""Domain models for the long-horizon project brain.

A :class:`BrainEntry` is a structured Pydantic envelope with a discriminated
payload union on ``entry_kind``, stored as a single JSON file in the project
workspace at ``<workspace>/.synthorg/brain/<kind>/<entry_id>.json`` and as an
append-only row in the ``project_brain_entries`` table. The pair
``(entry_id, revision)`` is the version identity: ``entry_id`` is the stable
logical identity of a record, and ``revision`` increments by one each time the
record changes (a change is always a new revision, never an in-place update).

All brain-specific enums live here rather than in ``synthorg.core.enums`` so the
only change to the size-baselined ``core/enums.py`` is the single
``MemoryCategory.PROJECT_BRAIN`` member.
"""

from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from synthorg.core.types import NotBlankStr, validate_unique_strings

# ── Bounded text aliases ─────────────────────────────────────────────

BrainTitle = Annotated[str, StringConstraints(min_length=1, max_length=512)]
"""Bounded non-empty entry title."""

BrainRationale = Annotated[str, StringConstraints(min_length=1, max_length=8192)]
"""Bounded non-empty rationale (the "why")."""

BrainShortText = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
"""Bounded non-empty short text for payload fields (answer, resolution, ...)."""

CitationLocator = Annotated[str, StringConstraints(min_length=1, max_length=512)]
"""Bounded non-empty citation locator (page, line range, anchor)."""


# ── Enums ────────────────────────────────────────────────────────────


class BrainEntryKind(StrEnum):
    """Discriminator over the six project-brain record kinds.

    ``DECISION`` records a choice made and why. ``OPEN_QUESTION`` records an
    unresolved question. ``BLOCKER`` records something halting progress.
    ``RISK`` records a standing risk. ``DEPENDENCY`` records a cross-task or
    external dependency. ``PLAN_REVISION`` records how the plan has evolved.
    """

    DECISION = "decision"
    OPEN_QUESTION = "open_question"
    BLOCKER = "blocker"
    RISK = "risk"
    DEPENDENCY = "dependency"
    PLAN_REVISION = "plan_revision"


class BrainEntryStatus(StrEnum):
    """Lifecycle status shared across kinds.

    A single enum keeps the envelope uniform; each payload validates which
    subset of statuses is legal for its kind, so an open question can never be
    ``MITIGATED`` and a risk can never be ``RESOLVED``.
    """

    OPEN = "open"
    RESOLVED = "resolved"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    BLOCKED = "blocked"
    CLEARED = "cleared"
    ACTIVE = "active"
    MITIGATED = "mitigated"
    RETIRED = "retired"


class BlockerSeverity(StrEnum):
    """How hard a blocker halts progress."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RiskLevel(StrEnum):
    """Qualitative likelihood or impact band for a risk."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DependencyKind(StrEnum):
    """What a dependency depends on.

    ``TASK`` is another task in the project; ``EXTERNAL`` is an outside system,
    vendor, or person; ``DECISION`` is a pending project-brain decision; and
    ``RESOURCE`` is an artefact, credential, or environment.
    """

    TASK = "task"
    EXTERNAL = "external"
    DECISION = "decision"
    RESOURCE = "resource"


class CitationKind(StrEnum):
    """What a citation points at.

    ``TASK`` is a task id; ``DOC_SLUG`` is a living-doc slug;
    ``KNOWLEDGE_SOURCE`` is a knowledge-substrate source id; ``ENTRY`` is
    another brain entry; ``EXTERNAL_URL`` is an outside link.
    """

    TASK = "task"
    DOC_SLUG = "doc_slug"
    KNOWLEDGE_SOURCE = "knowledge_source"
    ENTRY = "entry"
    EXTERNAL_URL = "external_url"


# Legal status set per kind. Enforced by the payload model validators and by the
# envelope cross-check; centralised here so the rule lives in one place.
_LEGAL_STATUS_BY_KIND: dict[BrainEntryKind, frozenset[BrainEntryStatus]] = {
    BrainEntryKind.DECISION: frozenset(
        {BrainEntryStatus.ACCEPTED, BrainEntryStatus.SUPERSEDED}
    ),
    BrainEntryKind.OPEN_QUESTION: frozenset(
        {BrainEntryStatus.OPEN, BrainEntryStatus.RESOLVED}
    ),
    BrainEntryKind.BLOCKER: frozenset(
        {BrainEntryStatus.BLOCKED, BrainEntryStatus.CLEARED}
    ),
    BrainEntryKind.RISK: frozenset(
        {
            BrainEntryStatus.ACTIVE,
            BrainEntryStatus.MITIGATED,
            BrainEntryStatus.RETIRED,
        }
    ),
    BrainEntryKind.DEPENDENCY: frozenset(
        {BrainEntryStatus.OPEN, BrainEntryStatus.RESOLVED}
    ),
    BrainEntryKind.PLAN_REVISION: frozenset(
        {BrainEntryStatus.ACTIVE, BrainEntryStatus.SUPERSEDED}
    ),
}


def legal_statuses_for(kind: BrainEntryKind) -> frozenset[BrainEntryStatus]:
    """Return the statuses an entry of ``kind`` may legally carry.

    Args:
        kind: The entry kind.

    Returns:
        The frozen set of legal statuses for that kind.
    """
    return _LEGAL_STATUS_BY_KIND[kind]


def _new_entry_id() -> NotBlankStr:
    """Generate a fresh logical entry id.

    Returns:
        A new UUID4 string wrapped as ``NotBlankStr``.
    """
    return NotBlankStr(str(uuid4()))


# ── Citation ─────────────────────────────────────────────────────────


class Citation(BaseModel):
    """A provenance pointer from a brain entry to its evidence."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    source_ref: NotBlankStr = Field(description="Identifier of the cited source")
    source_kind: CitationKind = Field(description="What the source_ref refers to")
    locator: CitationLocator | None = Field(
        default=None,
        description="Optional in-source locator (page, line range, anchor)",
    )


# ── Per-kind payloads ────────────────────────────────────────────────


class DecisionPayload(BaseModel):
    """Payload for a decision: the chosen outcome and the alternatives weighed."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    entry_kind: Literal[BrainEntryKind.DECISION] = BrainEntryKind.DECISION
    decision_outcome: BrainShortText = Field(description="The option chosen")
    alternatives: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Options considered and not chosen",
    )


class OpenQuestionPayload(BaseModel):
    """Payload for an open question: the answer once resolved."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    entry_kind: Literal[BrainEntryKind.OPEN_QUESTION] = BrainEntryKind.OPEN_QUESTION
    answer: BrainShortText | None = Field(
        default=None,
        description="The answer, present once the question is resolved",
    )


class BlockerPayload(BaseModel):
    """Payload for a blocker: its severity and resolution once cleared."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    entry_kind: Literal[BrainEntryKind.BLOCKER] = BrainEntryKind.BLOCKER
    severity: BlockerSeverity = Field(description="How hard the blocker halts work")
    resolution: BrainShortText | None = Field(
        default=None,
        description="How the blocker was cleared, present once cleared",
    )


class RiskPayload(BaseModel):
    """Payload for a risk: likelihood, impact, and mitigation."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    entry_kind: Literal[BrainEntryKind.RISK] = BrainEntryKind.RISK
    likelihood: RiskLevel = Field(description="How likely the risk is")
    impact: RiskLevel = Field(description="How damaging the risk would be")
    mitigation: BrainShortText | None = Field(
        default=None,
        description="The mitigation, present once one is chosen",
    )


class DependencyPayload(BaseModel):
    """Payload for a dependency: what it depends on and of what kind."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    entry_kind: Literal[BrainEntryKind.DEPENDENCY] = BrainEntryKind.DEPENDENCY
    depends_on: NotBlankStr = Field(description="Identifier of the dependency target")
    dependency_kind: DependencyKind = Field(description="What depends_on refers to")


class PlanRevisionPayload(BaseModel):
    """Payload for a plan revision: a summary and the plan it supersedes."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    entry_kind: Literal[BrainEntryKind.PLAN_REVISION] = BrainEntryKind.PLAN_REVISION
    summary: BrainRationale = Field(description="What this revision changes")
    supersedes_plan_entry_id: NotBlankStr | None = Field(
        default=None,
        description="entry_id of the plan revision this one replaces",
    )


BrainPayload = Annotated[
    DecisionPayload
    | OpenQuestionPayload
    | BlockerPayload
    | RiskPayload
    | DependencyPayload
    | PlanRevisionPayload,
    Field(discriminator="entry_kind"),
]
"""Discriminated union over every concrete payload kind."""


# ── Envelope ─────────────────────────────────────────────────────────


class BrainEntry(BaseModel):
    """One revision of one logical project-brain record.

    ``entry_id`` is the stable logical identity, constant across every revision.
    ``revision`` is server-assigned and monotonic per ``entry_id``; a change is a
    new revision, never an in-place update. The envelope's ``entry_kind`` and
    ``status`` must agree with the discriminated ``payload`` and the legal-status
    table for the kind.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    entry_id: NotBlankStr = Field(
        default_factory=_new_entry_id,
        description="Stable logical identity, constant across revisions",
    )
    revision: int = Field(
        ge=1,
        description="Monotonic version per entry_id; server-assigned",
    )
    project_id: NotBlankStr = Field(description="Owning project")
    entry_kind: BrainEntryKind = Field(description="Record kind (discriminator)")
    title: BrainTitle = Field(description="Human-readable title")
    rationale: BrainRationale = Field(description="Why this entry holds (the why)")
    status: BrainEntryStatus = Field(description="Lifecycle status")
    author: NotBlankStr = Field(description="Agent id or operator id of the writer")
    recorded_at: AwareDatetime = Field(description="When this revision was recorded")
    related_task_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Task IDs this entry references",
    )
    related_entry_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Other brain entry IDs this entry references",
    )
    supersedes_entry_id: NotBlankStr | None = Field(
        default=None,
        description="entry_id this entry supersedes (decision/resolution chain)",
    )
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Free-form classification tags (unique)",
    )
    confidence: float | None = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Optional confidence in the entry, 0..1",
    )
    citations: tuple[Citation, ...] = Field(
        default=(),
        description="Provenance pointers backing this entry",
    )
    payload: BrainPayload = Field(description="Kind-specific payload")

    @model_validator(mode="after")
    def _validate_kind_status_payload(self) -> Self:
        """Enforce envelope/payload agreement and legal status for the kind.

        Returns:
            ``self`` unchanged when consistent.

        Raises:
            ValueError: When ``entry_kind`` disagrees with the payload, or when
                ``status`` is not legal for the kind.
        """
        if self.payload.entry_kind != self.entry_kind:
            msg = (
                f"entry_kind {self.entry_kind!r} disagrees with payload kind "
                f"{self.payload.entry_kind!r}"
            )
            raise ValueError(msg)
        legal = legal_statuses_for(self.entry_kind)
        if self.status not in legal:
            allowed = ", ".join(sorted(s.value for s in legal))
            msg = (
                f"status {self.status.value!r} is not legal for kind "
                f"{self.entry_kind.value!r} (allowed: {allowed})"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_tags_unique(self) -> Self:
        """Reject duplicate tags.

        Returns:
            ``self`` unchanged when every tag is unique.
        """
        validate_unique_strings(self.tags, "tags")
        return self


# ── Persistence and retrieval projections ────────────────────────────


class BrainSummary(BaseModel):
    """Lightweight projection for board and list views."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project")
    entry_id: NotBlankStr = Field(description="Logical entry id")
    revision: int = Field(ge=1, description="Latest revision shown")
    entry_kind: BrainEntryKind = Field(description="Record kind")
    title: NotBlankStr = Field(description="Display title")
    status: BrainEntryStatus = Field(description="Lifecycle status")
    author: NotBlankStr = Field(description="Writer of this revision")
    recorded_at: AwareDatetime = Field(description="When this revision was recorded")
    tags: tuple[NotBlankStr, ...] = Field(default=())


class BrainEntryVersion(BaseModel):
    """One entry in a brain entry's git history."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    commit_hash: NotBlankStr = Field(description="Commit identifier")
    revision: int = Field(ge=1, description="Revision committed")
    author: NotBlankStr = Field(description="Writer of the revision")
    committed_at: AwareDatetime = Field(description="Commit timestamp")
    summary: NotBlankStr = Field(description="Commit subject line")


class BrainChunk(BaseModel):
    """Indexer-ready chunk derived from a brain entry.

    The chunker yields chunks; the indexer forwards them to the memory backend
    under :attr:`synthorg.core.enums.MemoryCategory.PROJECT_BRAIN`.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project")
    entry_id: NotBlankStr = Field(description="Source entry id")
    entry_kind: BrainEntryKind = Field(description="Source entry kind")
    chunk_index: int = Field(ge=0, description="Position within the entry")
    text: NotBlankStr = Field(description="Text content for embedding")
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Indexing tags (project, brain_entry, brain_kind)",
    )


class BrainSearchHit(BaseModel):
    """One retrieval result returned by :meth:`ProjectBrainService.query`."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project")
    entry_id: NotBlankStr = Field(description="Matched entry id")
    entry_kind: BrainEntryKind = Field(description="Matched entry kind")
    chunk_text: NotBlankStr = Field(description="Matching chunk content")
    relevance_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Backend-assigned relevance",
    )
