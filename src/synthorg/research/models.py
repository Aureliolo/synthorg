"""Domain models for the research subsystem.

A :class:`ResearchBrief` is the input to a research run. The planner turns
it into a :class:`ResearchQueryPlan` of :class:`SubQuery` items; each
retrieval source returns :class:`RetrievedItem` candidates, each carrying a
:class:`ResearchCitation` precise enough to resolve back to a retrievable
source. Credibility triage emits :class:`SourceCredibility` verdicts; the
synthesiser produces a :class:`ResearchReport` of :class:`ResearchClaim`
items, every claim cited. A :class:`ResearchRun` is the persisted,
replayable record of the whole pipeline.

All models are frozen Pydantic v2 with ``extra="forbid"``. Identifiers are
required fields rather than random defaults so a recorded run replays
byte-for-byte.
"""

from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from synthorg.core.enums import ClaimType, ResearchRunStatus, ResearchSourceType
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field annotation
from synthorg.knowledge.models import (  # noqa: TC001 -- Pydantic field annotation
    Citation,
    Sha256Hex,
    TitleText,
)
from synthorg.research.constants import (
    RESEARCH_DEFAULT_MAX_COST,
    RESEARCH_DEFAULT_MAX_SUBQUERIES,
    RESEARCH_DEFAULT_MAX_WALL_CLOCK_SECONDS,
    RESEARCH_DEFAULT_MIN_CREDIBILITY,
    RESEARCH_MAX_SUBQUERIES_CEILING,
)

# ── Field constraints ────────────────────────────────────────────────

QuestionText = Annotated[str, StringConstraints(min_length=1, max_length=16384)]
"""Bounded non-empty research question / brief description."""

SnippetText = Annotated[str, StringConstraints(min_length=1, max_length=8192)]
"""Bounded non-empty retrieved-source snippet (untrusted; wrapped before
it enters any prompt)."""

SummaryText = Annotated[str, StringConstraints(min_length=1, max_length=16384)]
"""Bounded non-empty report executive summary."""

ClaimText = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
"""Bounded non-empty claim assertion text."""

AngleText = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
"""Bounded non-empty synthesis angle / lens."""

IntentText = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
"""Bounded non-empty sub-query intent rationale."""

AuthorityLevel = Literal["peer_reviewed", "expert", "published", "community", "unknown"]
"""Coarse source-authority bucket used by credibility triage."""


# ── External source locators (discriminated union) ───────────────────


class WebSourceLocator(BaseModel):
    """Resolvable handle for a web source."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    source_type: Literal["web"] = Field(default="web")
    url: NotBlankStr = Field(description="Fetched page URL")
    accessed_at: AwareDatetime = Field(description="When the page was retrieved")


class AcademicSourceLocator(BaseModel):
    """Resolvable handle for an academic source (paper / preprint)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    source_type: Literal["academic"] = Field(default="academic")
    identifier: NotBlankStr = Field(
        description="Stable identifier (arXiv id, DOI, corpus id, or URL)",
    )
    doi: NotBlankStr | None = Field(default=None, description="DOI, if known")
    authors: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Author display names, in listed order",
    )
    year: int | None = Field(
        default=None,
        ge=1500,
        le=2200,
        description="Publication year, if known",
    )


class CodeSourceLocator(BaseModel):
    """Resolvable handle for a code source (a file or span in a repo)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    source_type: Literal["code"] = Field(default="code")
    repo: NotBlankStr = Field(description="Repository identifier (owner/name or URL)")
    path: NotBlankStr = Field(description="Repo-relative file path")
    line_start: int | None = Field(default=None, ge=1, description="1-indexed start")
    line_end: int | None = Field(default=None, ge=1, description="1-indexed end")
    ref: NotBlankStr | None = Field(
        default=None,
        description="Commit SHA / branch / tag the span was read at",
    )

    @model_validator(mode="after")
    def _validate_line_range(self) -> Self:
        """Reject an end line preceding its start when both are set."""
        if (
            self.line_start is not None
            and self.line_end is not None
            and self.line_end < self.line_start
        ):
            msg = (
                f"line_end ({self.line_end}) must be >= line_start ({self.line_start})"
            )
            raise ValueError(msg)
        return self


ExternalSourceLocator = Annotated[
    WebSourceLocator | AcademicSourceLocator | CodeSourceLocator,
    Field(discriminator="source_type"),
]
"""Discriminated union over the external (non-knowledge) source locators."""


# ── Citation ─────────────────────────────────────────────────────────


class ResearchCitation(BaseModel):
    """A resolvable handle binding a claim back to a retrieved source.

    ``ref_id`` links the citation to the :class:`RetrievedItem` it resolves
    to within the run. For a ``knowledge`` source the citation embeds the
    reused knowledge-substrate :class:`Citation`; for ``web`` / ``academic``
    / ``code`` it carries a typed external locator. Exactly one payload is
    set and must agree with ``source_type``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    ref_id: NotBlankStr = Field(description="Retained-item reference id")
    source_type: ResearchSourceType = Field(description="Originating source kind")
    knowledge: Citation | None = Field(
        default=None,
        description="Knowledge-substrate citation (knowledge sources only)",
    )
    external: ExternalSourceLocator | None = Field(
        default=None,
        description="External source locator (web / academic / code only)",
    )

    @model_validator(mode="after")
    def _validate_payload(self) -> Self:
        """Enforce exactly one payload, consistent with ``source_type``."""
        is_knowledge = self.source_type is ResearchSourceType.KNOWLEDGE
        if is_knowledge:
            if self.knowledge is None or self.external is not None:
                msg = "knowledge citation requires `knowledge` set and `external` unset"
                raise ValueError(msg)
        else:
            if self.external is None or self.knowledge is not None:
                msg = "external citation requires `external` set and `knowledge` unset"
                raise ValueError(msg)
            if self.external.source_type != self.source_type.value:
                msg = (
                    f"external locator kind {self.external.source_type!r} does not "
                    f"match citation source_type {self.source_type.value!r}"
                )
                raise ValueError(msg)
        return self


# ── Brief and query plan ─────────────────────────────────────────────


class ResearchBrief(BaseModel):
    """The input to a research run.

    Source toggles select which retrieval families fan out; at least one
    must be enabled. Limits bound the run's cost and wall-clock time.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    brief_id: NotBlankStr = Field(description="Stable brief identifier")
    project_id: NotBlankStr | None = Field(
        default=None,
        description="Owning project for knowledge scoping; None means global",
    )
    title: TitleText = Field(description="Human-readable brief title")
    question: QuestionText = Field(description="The research question to answer")
    include_knowledge: bool = Field(
        default=True,
        description="Query the internal knowledge substrate",
    )
    include_web: bool = Field(default=True, description="Query web search")
    include_academic: bool = Field(
        default=False,
        description="Query academic search",
    )
    include_code: bool = Field(default=False, description="Query code search")
    max_subqueries: int = Field(
        default=RESEARCH_DEFAULT_MAX_SUBQUERIES,
        ge=1,
        le=RESEARCH_MAX_SUBQUERIES_CEILING,
        description="Ceiling on planner-emitted sub-queries",
    )
    min_credibility: float = Field(
        default=RESEARCH_DEFAULT_MIN_CREDIBILITY,
        ge=0.0,
        le=1.0,
        description="Minimum credibility score a source must reach to be retained",
    )
    max_cost: float = Field(
        default=RESEARCH_DEFAULT_MAX_COST,
        gt=0.0,
        description="Per-run cost ceiling in the configured currency",
    )
    max_wall_clock_seconds: int = Field(
        default=RESEARCH_DEFAULT_MAX_WALL_CLOCK_SECONDS,
        gt=0,
        description="Per-run wall-clock ceiling in seconds",
    )
    created_at: AwareDatetime = Field(description="Brief creation timestamp")

    @property
    def enabled_source_types(self) -> tuple[ResearchSourceType, ...]:
        """The retrieval source kinds this brief opts into, in fixed order.

        A derived helper (not a serialised field) so a brief round-trips
        through JSON cleanly under ``extra="forbid"``.
        """
        toggles = (
            (ResearchSourceType.KNOWLEDGE, self.include_knowledge),
            (ResearchSourceType.WEB, self.include_web),
            (ResearchSourceType.ACADEMIC, self.include_academic),
            (ResearchSourceType.CODE, self.include_code),
        )
        return tuple(kind for kind, enabled in toggles if enabled)

    @model_validator(mode="after")
    def _require_a_source(self) -> Self:
        """Reject a brief that enables no retrieval source."""
        if not self.enabled_source_types:
            msg = "at least one retrieval source must be enabled"
            raise ValueError(msg)
        return self


class SubQuery(BaseModel):
    """One decomposed query targeting a single retrieval source."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    index: int = Field(ge=0, description="Stable position within the plan")
    source_type: ResearchSourceType = Field(description="Target retrieval source")
    query_text: NotBlankStr = Field(description="Query string for the source")
    intent: IntentText = Field(description="Why this query helps answer the brief")


class ResearchQueryPlan(BaseModel):
    """The planner's decomposition of a brief into targeted sub-queries."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    brief_id: NotBlankStr = Field(description="Owning brief identifier")
    research_angle: AngleText = Field(description="Lens guiding synthesis")
    sub_queries: tuple[SubQuery, ...] = Field(
        min_length=1,
        description="Targeted sub-queries; indices must be unique",
    )

    @model_validator(mode="after")
    def _validate_indices(self) -> Self:
        """Reject duplicate sub-query indices (they key replay routing)."""
        indices = [sq.index for sq in self.sub_queries]
        if len(set(indices)) != len(indices):
            msg = "sub_query indices must be unique"
            raise ValueError(msg)
        return self


# ── Retrieval, triage, synthesis ─────────────────────────────────────


class RetrievedItem(BaseModel):
    """One candidate source returned by a retrieval source.

    ``ref_id`` is the stable handle the synthesiser cites and the citation
    binder resolves. ``snippet`` is untrusted external content; it is
    wrapped via :func:`~synthorg.engine.prompt_safety.wrap_untrusted` only
    where it enters a prompt, never at storage.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    ref_id: NotBlankStr = Field(description="Stable reference id for citation")
    sub_query_index: int = Field(ge=0, description="Originating sub-query index")
    source_type: ResearchSourceType = Field(description="Originating source kind")
    title: TitleText = Field(description="Source title")
    uri: NotBlankStr = Field(description="Resolvable source URI / identifier")
    snippet: SnippetText = Field(description="Untrusted excerpt of the source")
    content_hash: Sha256Hex = Field(description="Hash of the snippet content")
    relevance_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Source-assigned relevance for this sub-query",
    )
    citation: ResearchCitation = Field(description="Resolvable provenance handle")

    @model_validator(mode="after")
    def _validate_citation(self) -> Self:
        """Citation must agree with this item's ``ref_id`` and source kind."""
        if self.citation.ref_id != self.ref_id:
            msg = (
                f"citation ref_id {self.citation.ref_id!r} does not match item "
                f"ref_id {self.ref_id!r}"
            )
            raise ValueError(msg)
        if self.citation.source_type is not self.source_type:
            msg = (
                f"citation source_type {self.citation.source_type.value!r} does not "
                f"match item source_type {self.source_type.value!r}"
            )
            raise ValueError(msg)
        return self


class SourceCredibility(BaseModel):
    """A credibility-triage verdict for one retrieved item."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    ref_id: NotBlankStr = Field(description="Item this verdict scores")
    score: float = Field(ge=0.0, le=1.0, description="Composite credibility score")
    authority: AuthorityLevel = Field(description="Coarse authority bucket")
    recency_months: int | None = Field(
        default=None,
        ge=0,
        description="Age of the source in months, if known",
    )
    domain_alignment: float = Field(
        ge=0.0,
        le=1.0,
        description="How on-topic the source is for the brief",
    )
    red_flags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Markers of low quality (e.g. marketing, unverified)",
    )
    passed: bool = Field(description="Whether the item met the brief's threshold")


class ResearchClaim(BaseModel):
    """One assertion in the synthesised report, backed by >= 1 citation."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    claim_id: NotBlankStr = Field(description="Stable claim identifier")
    text: ClaimText = Field(description="The assertion")
    claim_type: ClaimType = Field(description="Nature of the assertion")
    citations: tuple[ResearchCitation, ...] = Field(
        min_length=1,
        description="Sources backing this claim (at least one)",
    )
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Synthesiser confidence in the claim",
    )


class ResearchReport(BaseModel):
    """The synthesised, citation-backed research deliverable."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    report_id: NotBlankStr = Field(description="Stable report identifier")
    brief_id: NotBlankStr = Field(description="Owning brief identifier")
    title: TitleText = Field(description="Report title")
    summary: SummaryText = Field(description="Executive summary")
    claims: tuple[ResearchClaim, ...] = Field(
        min_length=1,
        description="Cited claims comprising the report body",
    )
    sources_consulted: int = Field(ge=0, description="Items retrieved before triage")
    sources_retained: int = Field(ge=0, description="Items retained after triage")
    research_angle: AngleText = Field(description="Synthesis lens used")
    synthesis_model: NotBlankStr = Field(description="Model that produced the report")
    created_at: AwareDatetime = Field(description="Report creation timestamp")

    @model_validator(mode="after")
    def _validate_counts(self) -> Self:
        """Retained sources cannot exceed those consulted."""
        if self.sources_retained > self.sources_consulted:
            msg = (
                f"sources_retained ({self.sources_retained}) cannot exceed "
                f"sources_consulted ({self.sources_consulted})"
            )
            raise ValueError(msg)
        return self


# ── Persisted run ────────────────────────────────────────────────────


class ResearchRun(BaseModel):
    """The persisted, replayable record of one research execution.

    Owns an immutable snapshot of its ``brief`` (the run is self-contained
    and needs no join to replay) plus the plan, retrieved items, credibility
    verdicts, and final report. ``brief_id`` / ``project_id`` are
    denormalised onto the row so runs can be listed and filtered without
    decoding the brief blob.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    run_id: NotBlankStr = Field(description="Primary key")
    brief_id: NotBlankStr = Field(description="Owning brief identifier")
    project_id: NotBlankStr | None = Field(
        default=None,
        description="Owning project; None means global",
    )
    status: ResearchRunStatus = Field(description="Run lifecycle state")
    brief: ResearchBrief = Field(description="Immutable snapshot of the input brief")
    query_plan: ResearchQueryPlan | None = Field(
        default=None,
        description="Planner output, once planning completes",
    )
    retrieved_items: tuple[RetrievedItem, ...] = Field(
        default=(),
        description="All retrieved candidates (drives deterministic replay)",
    )
    credibility: tuple[SourceCredibility, ...] = Field(
        default=(),
        description="Credibility verdicts for retrieved items",
    )
    report: ResearchReport | None = Field(
        default=None,
        description="Final deliverable, once synthesis completes",
    )
    error: NotBlankStr | None = Field(
        default=None,
        description="Safe error description when status is FAILED",
    )
    cost: float = Field(
        default=0.0, ge=0.0, description="Accrued run cost in the configured currency"
    )
    wall_clock_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Elapsed wall-clock time in seconds",
    )
    created_by: NotBlankStr = Field(
        description="Agent or operator that started the run"
    )
    created_at: AwareDatetime = Field(description="Run start timestamp")
    completed_at: AwareDatetime | None = Field(
        default=None,
        description="Terminal-state timestamp (completed or failed)",
    )

    @model_validator(mode="after")
    def _validate_consistency(self) -> Self:
        """Enforce brief linkage and status / payload invariants."""
        if self.brief.brief_id != self.brief_id:
            msg = (
                f"brief.brief_id {self.brief.brief_id!r} does not match run "
                f"brief_id {self.brief_id!r}"
            )
            raise ValueError(msg)
        if self.brief.project_id != self.project_id:
            msg = "brief.project_id does not match run project_id"
            raise ValueError(msg)
        if self.query_plan is not None and self.query_plan.brief_id != self.brief_id:
            msg = "query_plan.brief_id does not match run brief_id"
            raise ValueError(msg)
        if self.report is not None and self.report.brief_id != self.brief_id:
            msg = "report.brief_id does not match run brief_id"
            raise ValueError(msg)
        if self.status is ResearchRunStatus.COMPLETED and (
            self.report is None or self.completed_at is None
        ):
            msg = "status=COMPLETED requires report and completed_at to be set"
            raise ValueError(msg)
        if self.status is ResearchRunStatus.FAILED and self.error is None:
            msg = "status=FAILED requires error to be set"
            raise ValueError(msg)
        return self
