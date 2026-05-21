"""Chunker protocol and the chunk-piece value type.

A chunker turns one :class:`RawUnit` into an ordered tuple of
:class:`ChunkPiece` values: text plus a refined provenance locator. The
orchestrator (:func:`chunk_raw_document`) wraps pieces into
:class:`KnowledgeChunk` instances with deterministic positional ids.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.knowledge.models import (  # noqa: TC001 -- Pydantic field annotation
    ChunkText,
    ProvenanceLocator,
)

if TYPE_CHECKING:
    from synthorg.knowledge.models import RawUnit


class ChunkPiece(BaseModel):
    """One chunk's text plus its refined provenance locator.

    ``text`` shares the indexer-wide :data:`ChunkText` bound (non-empty,
    capped at 65536 chars) so an oversized chunker output is rejected at
    the protocol boundary, not deep in the indexer.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    text: ChunkText = Field(description="Chunk text for embedding")
    locator: ProvenanceLocator = Field(description="Refined provenance locator")


@runtime_checkable
class StructureAwareChunker(Protocol):
    """Splits a single loaded unit into structure-aware chunk pieces."""

    def chunk_unit(self, unit: RawUnit) -> tuple[ChunkPiece, ...]:
        """Return ordered chunk pieces for *unit* (never empty for non-blank)."""
        ...
