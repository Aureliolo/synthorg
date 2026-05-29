"""Chunker selection and whole-document orchestration.

:func:`build_chunker` picks a strategy by ``content_kind`` (and the
``code_chunker`` config discriminator). :func:`chunk_raw_document`
dispatches each loaded unit to its chunker and assembles the pieces into
:class:`KnowledgeChunk` instances with deterministic positional ids, so
freshness diffing stays stable across re-ingests.
"""

from typing import TYPE_CHECKING

from synthorg.core.enums import ContentKind
from synthorg.knowledge.chunking.code import CodeChunker
from synthorg.knowledge.chunking.document import OffsetChunker
from synthorg.knowledge.freshness import make_chunk_id
from synthorg.knowledge.models import KnowledgeChunk
from synthorg.versioning.hashing import compute_text_hash

if TYPE_CHECKING:
    from synthorg.knowledge.chunking.protocol import (
        ChunkPiece,
        StructureAwareChunker,
    )
    from synthorg.knowledge.config import KnowledgeConfig
    from synthorg.knowledge.models import RawDocument


def build_chunker(
    content_kind: ContentKind,
    config: KnowledgeConfig,
) -> StructureAwareChunker:
    """Return the chunker strategy for *content_kind*.

    ``CODE`` uses the configured code chunker (``tree_sitter`` by
    default); document, ticket-thread, and PDF-page content share the
    offset-based chunker.
    """
    if content_kind is ContentKind.CODE:
        # config.code_chunker is the discriminator; tree_sitter is the
        # only shipped strategy today. A future stdlib-ast strategy
        # branches here.
        _ = config.code_chunker
        return CodeChunker()
    return OffsetChunker()


def chunk_raw_document(
    raw: RawDocument,
    *,
    config: KnowledgeConfig,
) -> tuple[KnowledgeChunk, ...]:
    """Chunk every unit of *raw* into positional :class:`KnowledgeChunk`.

    Returns:
        The positional knowledge chunks for every unit of ``raw``,
        indexed in order.
    """
    typed_pieces: list[tuple[ContentKind, ChunkPiece]] = []
    for unit in raw.units:
        chunker = build_chunker(unit.content_kind, config)
        typed_pieces.extend(
            (unit.content_kind, piece) for piece in chunker.chunk_unit(unit)
        )
    chunks: list[KnowledgeChunk] = []
    for index, (kind, piece) in enumerate(typed_pieces):
        chunks.append(
            KnowledgeChunk(
                chunk_id=make_chunk_id(raw.source_id, index),
                source_id=raw.source_id,
                content_kind=kind,
                chunk_index=index,
                text=piece.text,
                content_hash=compute_text_hash(piece.text),
                locator=piece.locator,
            )
        )
    return tuple(chunks)
