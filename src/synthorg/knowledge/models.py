"""Domain models for the knowledge + provenance substrate.

A :class:`KnowledgeSource` is a registered corpus source (a PDF, a web
page, a repo, or a ticket thread), scoped to a project or globally. A
:class:`SourceLoader` turns it into a :class:`RawDocument` of
:class:`RawUnit` items; a :class:`StructureAwareChunker` turns those into
:class:`KnowledgeChunk` items, each carrying a :data:`ProvenanceLocator`
precise enough to resolve back to an exact source region. Retrieval
returns :class:`KnowledgeHit` items, each bearing a :class:`Citation`.

All models are frozen Pydantic v2 with ``extra="forbid"``.
"""

from typing import Annotated, Literal, Self

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    computed_field,
    model_validator,
)

from synthorg.core.enums import (
    ContentKind,
    SourceStatus,
    SourceType,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field annotation

# ── Field constraints ────────────────────────────────────────────────

Sha256Hex = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
"""A 64-character lowercase hex SHA-256 digest."""

ChunkText = Annotated[str, StringConstraints(min_length=1, max_length=65536)]
"""Bounded non-empty chunk text (cap guards against runaway embeddings)."""

UnitText = Annotated[str, StringConstraints(max_length=1048576)]
"""Bounded loader-unit text; may be empty (e.g. a blank PDF page)."""

TitleText = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
"""Bounded non-empty source title."""


def _check_char_range(char_start: int, char_end: int) -> None:
    """Raise ``ValueError`` when an end offset precedes its start."""
    if char_end < char_start:
        msg = f"char_end ({char_end}) must be >= char_start ({char_start})"
        raise ValueError(msg)


# ── Provenance locators (discriminated union) ────────────────────────


class PdfLocator(BaseModel):
    """Locates a chunk within a PDF page, optionally to a region."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    locator_kind: Literal["pdf"] = Field(default="pdf")
    page: int = Field(ge=1, description="1-indexed page number")
    bbox: tuple[float, float, float, float] | None = Field(
        default=None,
        description="Optional (x0, top, x1, bottom) region in PDF points",
    )
    char_start: int = Field(ge=0, description="Start offset within the page text")
    char_end: int = Field(ge=0, description="End offset within the page text")

    @model_validator(mode="after")
    def _validate_range(self) -> Self:
        _check_char_range(self.char_start, self.char_end)
        return self


class WebLocator(BaseModel):
    """Locates a chunk within a fetched web page."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    locator_kind: Literal["web"] = Field(default="web")
    url: NotBlankStr = Field(description="Source page URL")
    css_path: NotBlankStr | None = Field(
        default=None,
        description="Optional CSS path of the containing element",
    )
    char_start: int = Field(ge=0, description="Start offset within extracted text")
    char_end: int = Field(ge=0, description="End offset within extracted text")

    @model_validator(mode="after")
    def _validate_range(self) -> Self:
        _check_char_range(self.char_start, self.char_end)
        return self


class CodeLocator(BaseModel):
    """Locates a chunk within a source file by line span."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    locator_kind: Literal["code"] = Field(default="code")
    path: NotBlankStr = Field(description="Repo-relative file path")
    line_start: int = Field(ge=1, description="1-indexed first line")
    line_end: int = Field(ge=1, description="1-indexed last line (inclusive)")
    symbol: NotBlankStr | None = Field(
        default=None,
        description="Enclosing symbol name (function / class / method)",
    )
    ast_path: NotBlankStr | None = Field(
        default=None,
        description="Dotted AST node path, e.g. module.ClassName.method",
    )

    @model_validator(mode="after")
    def _validate_range(self) -> Self:
        if self.line_end < self.line_start:
            msg = (
                f"line_end ({self.line_end}) must be >= line_start ({self.line_start})"
            )
            raise ValueError(msg)
        return self


class TicketLocator(BaseModel):
    """Locates a chunk within a ticket thread."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    locator_kind: Literal["ticket"] = Field(default="ticket")
    ticket_id: NotBlankStr = Field(description="Ticket identifier")
    comment_id: NotBlankStr | None = Field(
        default=None,
        description="Originating comment identifier, if any",
    )
    char_start: int = Field(ge=0, description="Start offset within the comment text")
    char_end: int = Field(ge=0, description="End offset within the comment text")

    @model_validator(mode="after")
    def _validate_range(self) -> Self:
        _check_char_range(self.char_start, self.char_end)
        return self


ProvenanceLocator = Annotated[
    PdfLocator | WebLocator | CodeLocator | TicketLocator,
    Field(discriminator="locator_kind"),
]
"""Discriminated union over every concrete locator kind. This is the
citation precision model: each variant captures exactly enough to
resolve a chunk back to its source region."""


# ── Loader output ────────────────────────────────────────────────────


class RawUnit(BaseModel):
    """One structural unit produced by a loader (a page, file, comment).

    The chunker consumes units and emits :class:`KnowledgeChunk` items;
    it refines the unit ``locator`` with sub-unit char offsets.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    text: UnitText = Field(description="Unit text (may be empty)")
    locator: ProvenanceLocator = Field(description="Unit-level provenance locator")
    content_kind: ContentKind = Field(description="Structural kind for chunking")


class RawDocument(BaseModel):
    """A loaded source: its identity plus ordered structural units."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    source_id: NotBlankStr = Field(description="Owning source identifier")
    source_type: SourceType = Field(description="Source origin")
    uri: NotBlankStr = Field(description="Source URI (path / url / repo@ref / id)")
    title: TitleText = Field(description="Human-readable source title")
    content_hash: Sha256Hex = Field(description="Hash of the full source bytes")
    units: tuple[RawUnit, ...] = Field(
        default=(),
        description="Ordered structural units handed to the chunker",
    )


# ── Indexer / retrieval models ───────────────────────────────────────


class KnowledgeChunk(BaseModel):
    """An indexer-ready chunk with provenance for citation."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    chunk_id: NotBlankStr = Field(description="Stable chunk identifier")
    source_id: NotBlankStr = Field(description="Owning source identifier")
    content_kind: ContentKind = Field(description="Structural kind")
    chunk_index: int = Field(ge=0, description="Position within the source")
    text: ChunkText = Field(description="Text content for embedding")
    content_hash: Sha256Hex = Field(description="Hash of this chunk's text")
    locator: ProvenanceLocator = Field(description="Provenance locator")
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Indexing tags (source:, chunk:, project:/scope:, kind:)",
    )


class Citation(BaseModel):
    """A resolvable handle returned with every retrieval hit."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    source_id: NotBlankStr = Field(description="Owning source identifier")
    chunk_id: NotBlankStr = Field(description="Resolved chunk identifier")
    source_type: SourceType = Field(description="Source origin")
    title: TitleText = Field(description="Source title")
    uri: NotBlankStr = Field(description="Source URI")
    locator: ProvenanceLocator = Field(description="Exact source region")
    content_hash: Sha256Hex = Field(description="Chunk content hash at index time")


class KnowledgeHit(BaseModel):
    """One retrieval result: the chunk text, its score, and its citation."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    chunk_text: ChunkText = Field(description="Matching chunk content")
    relevance_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Backend-assigned relevance",
    )
    citation: Citation = Field(description="Resolvable provenance handle")


# ── Persisted source row ─────────────────────────────────────────────


class KnowledgeSource(BaseModel):
    """A registered corpus source.

    ``project_id is None`` means the source is global (shared across
    projects). The ``content_hash`` of the source bytes lets re-ingest
    short-circuit when nothing changed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    source_id: NotBlankStr = Field(description="Primary key")
    source_type: SourceType = Field(description="Source origin")
    project_id: NotBlankStr | None = Field(
        default=None,
        description="Owning project; None means global (cross-project)",
    )
    uri: NotBlankStr = Field(description="Source URI (path / url / repo@ref / id)")
    title: TitleText = Field(description="Human-readable title")
    content_hash: Sha256Hex = Field(description="Hash of source bytes")
    status: SourceStatus = Field(description="Ingestion lifecycle state")
    chunk_count: int = Field(default=0, ge=0, description="Indexed chunk count")
    created_at: AwareDatetime = Field(description="First-ingest timestamp")
    updated_at: AwareDatetime = Field(description="Last-update timestamp")
    last_indexed_at: AwareDatetime | None = Field(
        default=None,
        description="Timestamp of the last successful index",
    )
    last_error: NotBlankStr | None = Field(
        default=None,
        description="Safe error description on the last failure",
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_global(self) -> bool:
        """Whether the source is global (not scoped to a project)."""
        return self.project_id is None

    @model_validator(mode="after")
    def _validate_lifecycle(self) -> Self:
        """Reject lifecycle states that contradict the timestamp fields.

        A row claiming ``INDEXED`` must carry a ``last_indexed_at``; an
        adversarial or partially-migrated backend row that violates this
        would otherwise propagate inconsistency into citation resolution.
        """
        if self.status is SourceStatus.INDEXED and self.last_indexed_at is None:
            msg = "status=INDEXED requires last_indexed_at to be set"
            raise ValueError(msg)
        return self


class ChunkProvenanceRow(BaseModel):
    """Persisted provenance for one indexed chunk.

    The chunk text lives in the memory backend (the vector store); this
    row carries only what citation resolution needs: the locator, the
    content hash at index time, and the source linkage. Keyed by
    ``chunk_id`` and replaced wholesale when a source is re-indexed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    chunk_id: NotBlankStr = Field(description="Primary key")
    source_id: NotBlankStr = Field(description="Owning source identifier")
    content_kind: ContentKind = Field(description="Structural kind")
    chunk_index: int = Field(ge=0, description="Position within the source")
    content_hash: Sha256Hex = Field(description="Chunk content hash at index time")
    locator: ProvenanceLocator = Field(description="Exact source region")
    created_at: AwareDatetime = Field(description="Provenance write timestamp")
