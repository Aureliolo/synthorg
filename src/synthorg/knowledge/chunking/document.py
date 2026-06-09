"""Offset-based chunker for char-locator content.

Handles units whose locator carries ``char_start`` / ``char_end``
(documents, ticket threads, and PDF pages). Splits the unit text into
paragraph-packed spans and refines the unit's locator with each span's
offsets, so a citation resolves to an exact character range within the
page / document / comment (and, for PDFs, the originating page + bbox).
"""

from synthorg.knowledge.chunking._packing import pack_text_spans
from synthorg.knowledge.chunking.protocol import ChunkPiece
from synthorg.knowledge.models import RawUnit


class OffsetChunker:
    """Paragraph-packed chunker for offset-addressable units."""

    def chunk_unit(self, unit: RawUnit) -> tuple[ChunkPiece, ...]:
        """Split *unit* into char-offset chunk pieces.

        Returns:
            The packed chunk pieces, each carrying its char-offset
            locator.

        Raises:
            TypeError: When ``unit.locator`` exposes no char offsets.
        """
        if not hasattr(unit.locator, "char_start"):
            msg = (
                "OffsetChunker requires a locator with char offsets; got "
                f"{type(unit.locator).__name__}"
            )
            raise TypeError(msg)
        pieces: list[ChunkPiece] = []
        for start, end in pack_text_spans(unit.text):
            text = unit.text[start:end]
            if not text.strip():
                continue
            locator = unit.locator.model_copy(
                update={"char_start": start, "char_end": end}
            )
            pieces.append(ChunkPiece(text=text, locator=locator))
        return tuple(pieces)
