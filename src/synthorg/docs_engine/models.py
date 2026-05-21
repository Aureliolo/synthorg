"""Domain models for the living-documentation engine.

A :class:`LivingDocument` is a structured Pydantic object stored as a
single JSON file in the project workspace at
``<workspace>/.synthorg/docs/<doc_type>/<slug>.json``. The body is a
tuple of typed :data:`DocBlock` instances (discriminated union) so
agents author against a typed contract and the dashboard renders one
component per block kind. Blocks carry stable ``block_id`` UUIDs so
re-orders produce meaningful git diffs even though the JSON encoding
reshuffles bytes.

The chunker yields :class:`DocChunk` instances (one per block, or
merged adjacent prose blocks) that the indexer stores under
:attr:`synthorg.core.enums.MemoryCategory.PROJECT_DOC` with
``namespace=f"project:{project_id}"`` and ``tags=("doc_slug:<slug>",)``.
"""

import re
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from synthorg.core.enums import DocType  # noqa: TC001 -- Pydantic field annotation
from synthorg.core.types import NotBlankStr

# ── Block payload constraints ────────────────────────────────────────

_MIN_HEADING_LEVEL: int = 1
_MAX_HEADING_LEVEL: int = 6

_ALLOWED_LINK_SCHEMES: frozenset[str] = frozenset({"http", "https", "mailto"})
_URL_SCHEME_RE = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*):")


def _url_scheme(value: str) -> str | None:
    """Return the lower-cased URI scheme, or ``None`` for a relative URL."""
    match = _URL_SCHEME_RE.match(value)
    return match.group(1).lower() if match is not None else None


HeadingText = Annotated[str, StringConstraints(min_length=1, max_length=512)]
"""Bounded non-empty heading text (caps prevent runaway block payloads)."""

ProseText = Annotated[str, StringConstraints(min_length=1, max_length=8192)]
"""Bounded non-empty prose text."""

BulletItem = Annotated[str, StringConstraints(min_length=1, max_length=1024)]
"""Single bullet entry."""

CodeText = Annotated[str, StringConstraints(min_length=0, max_length=16384)]
"""Bounded code body (empty allowed for placeholder blocks)."""

DecisionText = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
"""Bounded decision / rationale text."""

MetricName = Annotated[str, StringConstraints(min_length=1, max_length=128)]
"""Bounded metric label."""

LinkText = Annotated[str, StringConstraints(min_length=1, max_length=512)]
"""Bounded link label / URL text."""


def _new_block_id() -> NotBlankStr:
    """Generate a fresh block UUID (kebab-stable across reorders)."""
    return NotBlankStr(str(uuid4()))


# ── Block primitives ────────────────────────────────────────────────


class HeadingBlock(BaseModel):
    """Section heading.

    ``level`` mirrors HTML h1..h6; the wiki renderer maps it to the
    matching heading element.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    block_kind: Literal["heading"] = Field(
        default="heading",
        description="Discriminator value for the DocBlock union",
    )
    block_id: NotBlankStr = Field(
        default_factory=_new_block_id,
        description="Stable identifier surviving re-orders",
    )
    level: int = Field(
        ge=_MIN_HEADING_LEVEL,
        le=_MAX_HEADING_LEVEL,
        description="Heading level (1 outer-most)",
    )
    text: HeadingText = Field(description="Heading text")


class ProseBlock(BaseModel):
    """Plain-text prose paragraph.

    Day-one rendering is plain text (no markdown). Adding a constrained
    inline-markdown subset is a future decision tracked in the design
    doc; the block schema is forward-compatible because renderers may
    interpret the text more richly later without breaking storage.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    block_kind: Literal["prose"] = Field(
        default="prose",
        description="Discriminator value for the DocBlock union",
    )
    block_id: NotBlankStr = Field(default_factory=_new_block_id)
    text: ProseText = Field(description="Prose body")


class BulletListBlock(BaseModel):
    """Bulleted list of short items."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    block_kind: Literal["bullet_list"] = Field(
        default="bullet_list",
        description="Discriminator value for the DocBlock union",
    )
    block_id: NotBlankStr = Field(default_factory=_new_block_id)
    items: tuple[BulletItem, ...] = Field(
        min_length=1,
        description="Non-empty tuple of bullet entries",
    )


class CodeBlock(BaseModel):
    """Code or pre-formatted block.

    ``language`` is informational only (renderer hint); the chunker
    indexes the body as-is without language-aware tokenisation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    block_kind: Literal["code"] = Field(
        default="code",
        description="Discriminator value for the DocBlock union",
    )
    block_id: NotBlankStr = Field(default_factory=_new_block_id)
    language: NotBlankStr | None = Field(
        default=None,
        description="Renderer language hint",
    )
    code: CodeText = Field(description="Code body")


class DecisionBlock(BaseModel):
    """Record of a decision plus its rationale.

    Distinct from prose so renderers can surface decisions visually and
    so retrieval can boost decision blocks under planning queries.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    block_kind: Literal["decision"] = Field(
        default="decision",
        description="Discriminator value for the DocBlock union",
    )
    block_id: NotBlankStr = Field(default_factory=_new_block_id)
    decision: DecisionText = Field(description="What was decided")
    rationale: DecisionText = Field(description="Why this decision")


class MetricBlock(BaseModel):
    """Single numeric measurement with optional units.

    Stored as a string to preserve operator-supplied precision and to
    avoid float imprecision in serialised JSON; renderers may parse for
    sparklines if they choose. Empty unit means "dimensionless".
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    block_kind: Literal["metric"] = Field(
        default="metric",
        description="Discriminator value for the DocBlock union",
    )
    block_id: NotBlankStr = Field(default_factory=_new_block_id)
    name: MetricName = Field(description="Metric label")
    value: NotBlankStr = Field(description="Metric value (as string)")
    unit: NotBlankStr | None = Field(
        default=None,
        description="Optional unit suffix",
    )


class LinkBlock(BaseModel):
    """External or internal link reference."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    block_kind: Literal["link"] = Field(
        default="link",
        description="Discriminator value for the DocBlock union",
    )
    block_id: NotBlankStr = Field(default_factory=_new_block_id)
    label: LinkText = Field(description="Link display label")
    url: LinkText = Field(description="Link target URL")

    @field_validator("url")
    @classmethod
    def _reject_unsafe_scheme(cls, value: str) -> str:
        """Block ``javascript:`` / ``data:`` / ``file:`` (stored-XSS sinks).

        Relative URLs (no scheme) and ``http`` / ``https`` / ``mailto``
        are permitted; any other explicit scheme is rejected because the
        wiki renders the value as an anchor ``href``.
        """
        scheme = _url_scheme(value)
        if scheme is not None and scheme not in _ALLOWED_LINK_SCHEMES:
            msg = f"link url scheme {scheme!r} is not permitted"
            raise ValueError(msg)
        return value


# ── Discriminated union ─────────────────────────────────────────────


DocBlock = Annotated[
    HeadingBlock
    | ProseBlock
    | BulletListBlock
    | CodeBlock
    | DecisionBlock
    | MetricBlock
    | LinkBlock,
    Field(discriminator="block_kind"),
]
"""Discriminated union over every concrete block kind."""


# ── Top-level living document ────────────────────────────────────────


class LivingDocument(BaseModel):
    """Top-level living-document model.

    Attributes:
        slug: URL-safe identifier unique per project + doc_type
            combination. Generated by the service from the title; agents
            never supply directly via the write tool.
        title: Human-readable title. Drives slug derivation.
        doc_type: Taxonomy bucket (status_report / deliverable /
            knowledge_note).
        tags: Free-form classification tags.
        related_task_ids: IDs of tasks that produced or reference this
            doc; the wiki may render these as links.
        author_agent_id: Identifier of the agent that performed the last
            write; useful for attribution in the wiki.
        body: Ordered sequence of typed blocks.
        created_at: First-write timestamp.
        updated_at: Last-write timestamp.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    slug: NotBlankStr = Field(description="URL-safe identifier")
    title: NotBlankStr = Field(description="Human-readable title")
    doc_type: DocType = Field(description="Doc taxonomy bucket")
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Free-form classification tags",
    )
    related_task_ids: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Task IDs that produced or reference this doc",
    )
    author_agent_id: NotBlankStr = Field(
        description="Agent that performed the last write",
    )
    body: tuple[DocBlock, ...] = Field(
        default=(),
        description="Ordered typed-block body",
    )
    created_at: AwareDatetime = Field(description="First-write timestamp")
    updated_at: AwareDatetime = Field(description="Last-write timestamp")


# ── Persistence + retrieval projections ──────────────────────────────


class DocMetadata(BaseModel):
    """Persisted metadata row for a living document.

    Body bytes live in the project git workspace; this row carries the
    pointers needed to find them and the indexing state needed to
    replay any unindexed commits on boot.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project")
    slug: NotBlankStr = Field(description="Doc slug")
    doc_type: DocType = Field(description="Doc taxonomy bucket")
    title: NotBlankStr = Field(description="Display title")
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Classification tags",
    )
    head_commit_sha: NotBlankStr = Field(
        description="Latest commit SHA on synthorg/docs that touched this doc",
    )
    last_indexed_commit_sha: NotBlankStr | None = Field(
        default=None,
        description="Most recent SHA reflected in the PROJECT_DOC index",
    )
    created_at: AwareDatetime = Field(description="First-write timestamp")
    updated_at: AwareDatetime = Field(description="Last-write timestamp")


class DocSummary(BaseModel):
    """Lightweight projection for wiki list views."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project")
    slug: NotBlankStr = Field(description="Doc slug")
    title: NotBlankStr = Field(description="Display title")
    doc_type: DocType = Field(description="Doc taxonomy bucket")
    tags: tuple[NotBlankStr, ...] = Field(default=())
    updated_at: AwareDatetime = Field(description="Last-write timestamp")


class DocVersion(BaseModel):
    """One entry in a doc's git history."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    commit_sha: NotBlankStr = Field(description="Commit identifier")
    author_agent_id: NotBlankStr = Field(description="Agent that authored the write")
    committed_at: AwareDatetime = Field(description="Commit timestamp")
    summary: NotBlankStr = Field(description="Commit subject line")


class DocChunk(BaseModel):
    """Indexer-ready chunk derived from a doc body.

    One chunk corresponds to one block (or a merged adjacent run of
    small prose blocks). The chunker yields chunks; the indexer
    forwards them to the memory backend under
    :attr:`synthorg.core.enums.MemoryCategory.PROJECT_DOC`.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project")
    doc_slug: NotBlankStr = Field(description="Source doc slug")
    doc_type: DocType = Field(description="Source doc taxonomy bucket")
    chunk_index: int = Field(ge=0, description="Position within the doc body")
    block_ids: tuple[NotBlankStr, ...] = Field(
        min_length=1,
        description="Block IDs this chunk represents",
    )
    text: NotBlankStr = Field(description="Text content for embedding")
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Indexing tags (carries doc_slug:... and doc-tags)",
    )


class DocSearchHit(BaseModel):
    """One retrieval result returned by :meth:`DocsService.search`."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project_id: NotBlankStr = Field(description="Owning project")
    doc_slug: NotBlankStr = Field(description="Matched doc slug")
    doc_type: DocType = Field(description="Matched doc taxonomy bucket")
    chunk_text: NotBlankStr = Field(description="Matching chunk content")
    relevance_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Backend-assigned relevance",
    )
