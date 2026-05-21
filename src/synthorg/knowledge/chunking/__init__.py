"""Structure-aware chunkers for the knowledge substrate.

A :class:`StructureAwareChunker` turns one :class:`RawUnit` into chunk
pieces with refined provenance locators. The strategy is chosen per unit
``content_kind`` by :func:`build_chunker`; :func:`chunk_raw_document`
orchestrates the per-unit dispatch and assigns deterministic positional
chunk ids.
"""

from synthorg.knowledge.chunking.factory import build_chunker, chunk_raw_document
from synthorg.knowledge.chunking.protocol import ChunkPiece, StructureAwareChunker

__all__ = [
    "ChunkPiece",
    "StructureAwareChunker",
    "build_chunker",
    "chunk_raw_document",
]
