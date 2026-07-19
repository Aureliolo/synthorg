# module-kind: declarative
"""Search specification and scored-row types for SQL-backed vector memory.

The SQL vector repositories (pgvector on Postgres, sqlite-vec on SQLite)
speak these types. They deliberately expose *two* ranked lists rather
than one fused list: dense (embedding KNN) and lexical (full-text) are
retrieved separately so the fusion step stays in the memory package,
where :func:`synthorg.memory.ranking_rrf.fuse_ranked_lists` already
implements Reciprocal Rank Fusion. Fusing inside SQL would push ranking
policy into the persistence boundary and duplicate an algorithm the
memory package already owns.
"""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr


class MemoryVectorSearchSpec(BaseModel):
    """Filter and ranking inputs for one vector-repository search.

    Attributes:
        agent_id: Owning agent whose memories are searched.
        text: Lexical search text, or ``None`` to skip lexical retrieval.
        embedding: Query embedding, or ``None`` to skip dense retrieval.
        namespaces: Restrict to these storage namespaces.
        categories: Restrict to these memory categories.
        tags: Required tags, AND semantics (an entry must carry all).
        limit: Maximum rows to return from this single ranked list.
        since: Only entries created at or after this instant.
        until: Only entries created strictly before this instant.
        now: Reference instant used to exclude expired entries.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Owning agent identifier")
    text: NotBlankStr | None = Field(
        default=None,
        description="Lexical search text (None skips lexical retrieval)",
    )
    embedding: tuple[float, ...] | None = Field(
        default=None,
        description="Query embedding (None skips dense retrieval)",
    )
    namespaces: frozenset[NotBlankStr] | None = Field(
        default=None,
        description="Restrict to these storage namespaces",
    )
    categories: frozenset[MemoryCategory] | None = Field(
        default=None,
        description="Restrict to these memory categories",
    )
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Required tags (AND semantics)",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=1000,
        description="Maximum rows in this ranked list",
    )
    since: AwareDatetime | None = Field(
        default=None,
        description="Only entries created at or after this instant",
    )
    until: AwareDatetime | None = Field(
        default=None,
        description="Only entries created strictly before this instant",
    )
    now: AwareDatetime | None = Field(
        default=None,
        description="Reference instant for expiry exclusion",
    )
