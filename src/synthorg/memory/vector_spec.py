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

from typing import Final, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.types import NotBlankStr

MAX_SEARCH_LIMIT: Final[int] = 1000
"""Largest single-arm result set a repository will return.

Public because callers that over-fetch before fusion have to clamp
against it; a caller computing its own recall width from a settings
value can otherwise exceed the bound and turn a tuning choice into a
``ValidationError`` on the retrieval path.
"""


class MemoryVectorSearchSpec(BaseModel):
    """Filter and ranking inputs for one vector-repository search.

    Attributes:
        agent_id: Owning agent whose memories are searched.
        text: Lexical search text, or ``None`` to skip lexical retrieval.
        embedding: Query embedding, or ``None`` to skip dense retrieval.
        namespaces: Restrict to these storage namespaces.
        categories: Restrict to these memory categories.
        tags: Required tags, AND semantics (an entry must carry all).
        excluded_tags: Disqualifying tags (an entry carrying any is
            dropped).
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
    excluded_tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Disqualifying tags (any match drops the entry)",
    )
    limit: int = Field(
        default=10,
        ge=1,
        le=MAX_SEARCH_LIMIT,
        description="Maximum rows in this ranked list",
    )
    oldest_first: bool = Field(
        default=False,
        description="Order a metadata-only listing oldest-first, for eviction",
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
        # ``None`` means "do not filter by expiry" (include expired
        # entries), not "now is unknown". Callers that want live-only
        # results must pass their clock's instant explicitly.
        description="Reference instant for expiry exclusion; None includes expired",
    )

    @model_validator(mode="after")
    def _validate_invariants(self) -> Self:
        """Reject an inverted time window or a contradictory tag filter.

        Returns:
            The validated spec.

        Raises:
            ValueError: If ``since`` is later than ``until``, or a tag
                is both required and excluded (an unsatisfiable filter).
        """
        window_inverted = (
            self.since is not None
            and self.until is not None
            and self.since > self.until
        )
        if window_inverted:
            msg = "since must not be later than until"
            raise ValueError(msg)
        contradictory_tags = set(self.tags) & set(self.excluded_tags)
        if contradictory_tags:
            msg = (
                "a tag cannot be both required and excluded: "
                f"{sorted(contradictory_tags)}"
            )
            raise ValueError(msg)
        return self
