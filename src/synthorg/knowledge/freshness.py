"""Content-hash freshness diffing for the knowledge substrate.

Re-indexing a source should re-embed only the chunks whose content
changed, not the whole corpus. :func:`diff_chunks` compares the freshly
chunked set against the hashes already on record (from the provenance
repository) and partitions the new chunks into embed / unchanged plus
the set of removed chunk ids.

Chunk identity is positional and deterministic (``source_id#index``),
so editing one section of a large document leaves every other chunk's
id and hash stable and only the touched chunk is re-embedded.
"""

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.knowledge.models import (
    KnowledgeChunk,
)


def make_chunk_id(source_id: NotBlankStr, chunk_index: int) -> NotBlankStr:
    """Return the deterministic positional chunk id for a source."""
    return NotBlankStr(f"{source_id}#{chunk_index}")


class ChunkDiff(BaseModel):
    """Partition of a re-chunked source against its prior provenance.

    ``to_embed`` are new or content-changed chunks that must be embedded
    and have their provenance rewritten. ``unchanged`` chunks keep their
    existing memory entry and provenance row untouched. ``removed_ids``
    are chunk ids that existed before but are absent now (their memory
    entry and provenance row must be deleted).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    to_embed: tuple[KnowledgeChunk, ...] = Field(default=())
    unchanged: tuple[KnowledgeChunk, ...] = Field(default=())
    removed_ids: tuple[NotBlankStr, ...] = Field(default=())

    @property
    def is_noop(self) -> bool:
        """Whether the diff requires no backend writes at all."""
        return not self.to_embed and not self.removed_ids


def diff_chunks(
    *,
    existing_hashes: Mapping[str, str],
    chunks: tuple[KnowledgeChunk, ...],
) -> ChunkDiff:
    """Partition *chunks* against the recorded ``chunk_id -> content_hash``.

    Args:
        existing_hashes: Prior per-chunk content hashes keyed by chunk id
            (from the provenance repository).
        chunks: Freshly produced chunks for the source.

    Returns:
        A :class:`ChunkDiff` separating chunks to embed, unchanged
        chunks, and removed chunk ids.
    """
    new_ids = {chunk.chunk_id for chunk in chunks}
    to_embed = tuple(
        chunk
        for chunk in chunks
        if existing_hashes.get(chunk.chunk_id) != chunk.content_hash
    )
    unchanged = tuple(
        chunk
        for chunk in chunks
        if existing_hashes.get(chunk.chunk_id) == chunk.content_hash
    )
    removed_ids = tuple(
        NotBlankStr(chunk_id)
        for chunk_id in sorted(existing_hashes)
        if chunk_id not in new_ids
    )
    return ChunkDiff(
        to_embed=to_embed,
        unchanged=unchanged,
        removed_ids=removed_ids,
    )
