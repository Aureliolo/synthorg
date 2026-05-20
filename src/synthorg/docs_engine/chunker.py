"""Block-aware chunker for :class:`LivingDocument`.

Yields one :class:`DocChunk` per body block (with merging of adjacent
small prose blocks up to a target size). The chunker is deterministic:
the same doc + project_id always produces the same chunks in the same
order, so re-indexing replaces prior chunks exactly without phantom
duplicates.

Chunk size is approximated in tokens via a fixed characters-per-token
proxy (no LiteLLM tokeniser dependency in the chunker); the proxy is
defined in :mod:`synthorg.docs_engine.constants`.
"""

from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.constants import (
    CHUNK_CHAR_PER_TOKEN_PROXY,
    DOCS_CHUNK_MAX_TOKENS,
    DOCS_CHUNK_TARGET_TOKENS,
    DOCS_PROJECT_TAG_PREFIX,
    DOCS_SLUG_TAG_PREFIX,
    DOCS_TYPE_TAG_PREFIX,
)
from synthorg.docs_engine.models import (
    BulletListBlock,
    CodeBlock,
    DecisionBlock,
    DocBlock,
    DocChunk,
    HeadingBlock,
    LinkBlock,
    LivingDocument,
    MetricBlock,
    ProseBlock,
)

if TYPE_CHECKING:
    from collections.abc import Sequence


class DocChunker:
    """Block-aware deterministic chunker for living documents."""

    __slots__ = ("_max_tokens", "_target_tokens")

    def __init__(
        self,
        *,
        target_tokens: int = DOCS_CHUNK_TARGET_TOKENS,
        max_tokens: int = DOCS_CHUNK_MAX_TOKENS,
    ) -> None:
        """Configure chunk-size budgets.

        Args:
            target_tokens: Soft target for chunk size; adjacent small
                prose blocks merge up to this budget.
            max_tokens: Hard cap; oversized single blocks become one
                chunk regardless (no in-block splitting day one).
        """
        if max_tokens < target_tokens:
            msg = (
                f"max_tokens ({max_tokens}) must be >= target_tokens ({target_tokens})"
            )
            raise ValueError(msg)
        self._target_tokens = target_tokens
        self._max_tokens = max_tokens

    def chunk(
        self,
        *,
        project_id: NotBlankStr,
        doc: LivingDocument,
    ) -> tuple[DocChunk, ...]:
        """Decompose *doc* into a deterministic sequence of chunks.

        Args:
            project_id: Owning project; embedded in each chunk's tags
                via the ``project:<id>`` prefix.
            doc: Source document.

        Returns:
            Ordered tuple of :class:`DocChunk` instances, one per
            block or merged prose run.
        """
        base_tags = self._base_tags(project_id=project_id, doc=doc)
        chunks: list[DocChunk] = []
        prose_run: list[ProseBlock] = []
        for block in doc.body:
            if isinstance(block, ProseBlock):
                prose_run.append(block)
                if self._token_count(_prose_run_text(prose_run)) >= self._target_tokens:
                    chunks.append(
                        self._make_prose_chunk(
                            project_id=project_id,
                            doc=doc,
                            chunk_index=len(chunks),
                            prose_blocks=prose_run,
                            base_tags=base_tags,
                        )
                    )
                    prose_run = []
                continue
            if prose_run:
                chunks.append(
                    self._make_prose_chunk(
                        project_id=project_id,
                        doc=doc,
                        chunk_index=len(chunks),
                        prose_blocks=prose_run,
                        base_tags=base_tags,
                    )
                )
                prose_run = []
            chunks.append(
                self._make_block_chunk(
                    project_id=project_id,
                    doc=doc,
                    chunk_index=len(chunks),
                    block=block,
                    base_tags=base_tags,
                )
            )
        if prose_run:
            chunks.append(
                self._make_prose_chunk(
                    project_id=project_id,
                    doc=doc,
                    chunk_index=len(chunks),
                    prose_blocks=prose_run,
                    base_tags=base_tags,
                )
            )
        return tuple(chunks)

    def _token_count(self, text: str) -> int:
        """Approximate token count via the chars-per-token proxy."""
        if not text:
            return 0
        return max(1, len(text) // CHUNK_CHAR_PER_TOKEN_PROXY)

    @staticmethod
    def _base_tags(
        *,
        project_id: NotBlankStr,
        doc: LivingDocument,
    ) -> tuple[NotBlankStr, ...]:
        """Tags carried by every chunk: project, slug, doc_type, doc tags."""
        return (
            NotBlankStr(f"{DOCS_PROJECT_TAG_PREFIX}{project_id}"),
            NotBlankStr(f"{DOCS_SLUG_TAG_PREFIX}{doc.slug}"),
            NotBlankStr(f"{DOCS_TYPE_TAG_PREFIX}{doc.doc_type.value}"),
            *doc.tags,
        )

    def _make_prose_chunk(
        self,
        *,
        project_id: NotBlankStr,
        doc: LivingDocument,
        chunk_index: int,
        prose_blocks: Sequence[ProseBlock],
        base_tags: tuple[NotBlankStr, ...],
    ) -> DocChunk:
        text = _prose_run_text(prose_blocks)
        return DocChunk(
            project_id=project_id,
            doc_slug=doc.slug,
            doc_type=doc.doc_type,
            chunk_index=chunk_index,
            block_ids=tuple(b.block_id for b in prose_blocks),
            text=NotBlankStr(text or " "),
            tags=base_tags,
        )

    def _make_block_chunk(
        self,
        *,
        project_id: NotBlankStr,
        doc: LivingDocument,
        chunk_index: int,
        block: DocBlock,
        base_tags: tuple[NotBlankStr, ...],
    ) -> DocChunk:
        text = _block_to_text(block)
        return DocChunk(
            project_id=project_id,
            doc_slug=doc.slug,
            doc_type=doc.doc_type,
            chunk_index=chunk_index,
            block_ids=(block.block_id,),
            text=NotBlankStr(text or " "),
            tags=base_tags,
        )


def _prose_run_text(blocks: Sequence[ProseBlock]) -> str:
    """Join a run of prose blocks with paragraph separators."""
    return "\n\n".join(b.text for b in blocks)


def _block_to_text(block: DocBlock) -> str:  # noqa: PLR0911 -- one branch per block kind
    """Render a single block to embeddable plain text."""
    if isinstance(block, HeadingBlock):
        return block.text
    if isinstance(block, ProseBlock):
        return block.text
    if isinstance(block, BulletListBlock):
        return "\n".join(f"- {item}" for item in block.items)
    if isinstance(block, CodeBlock):
        if block.language:
            return f"[{block.language}]\n{block.code}"
        return block.code
    if isinstance(block, DecisionBlock):
        return f"Decision: {block.decision}\nRationale: {block.rationale}"
    if isinstance(block, MetricBlock):
        unit = f" {block.unit}" if block.unit else ""
        return f"{block.name}: {block.value}{unit}"
    if isinstance(block, LinkBlock):
        return f"{block.label} ({block.url})"
    # Exhaustive over the DocBlock union; mypy already proves this is
    # unreachable, but the ``raise`` is preserved for the runtime path
    # when a new block kind is added without updating this rendering.
    msg = f"Unhandled block kind: {type(block).__name__}"  # type: ignore[unreachable]
    raise ValueError(msg)
