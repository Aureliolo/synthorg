"""Chunker selection and whole-document orchestration.

:func:`build_chunker` picks a strategy by ``content_kind`` (and the
``code_chunker`` config discriminator). :func:`chunk_raw_document`
dispatches each loaded unit to its chunker and assembles the pieces into
:class:`KnowledgeChunk` instances with deterministic positional ids, so
freshness diffing stays stable across re-ingests.
"""

import asyncio

from synthorg.core.critical_errors import reraise_critical
from synthorg.knowledge.chunking.code import CodeChunker, language_for
from synthorg.knowledge.chunking.document import OffsetChunker
from synthorg.knowledge.chunking.protocol import (
    ChunkPiece,
    StructureAwareChunker,
)
from synthorg.knowledge.config import KnowledgeConfig
from synthorg.knowledge.enums import ContentKind
from synthorg.knowledge.freshness import make_chunk_id
from synthorg.knowledge.models import CodeLocator, KnowledgeChunk, RawDocument, RawUnit
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.knowledge import KNOWLEDGE_GRAMMAR_PREFETCH_FAILED
from synthorg.versioning.hashing import compute_text_hash

logger = get_logger(__name__)


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


def _batch_languages(units: tuple[RawUnit, ...]) -> frozenset[str]:
    """Return the distinct tree-sitter languages this batch's code units need.

    Returns:
        Deduplicated grammar names for every ``CODE`` unit with a
        recognised file extension.
    """
    languages: set[str] = set()
    for unit in units:
        if unit.content_kind is not ContentKind.CODE:
            continue
        if not isinstance(unit.locator, CodeLocator):
            continue
        language = language_for(unit.locator.path)
        if language is not None:
            languages.add(language)
    return frozenset(languages)


def _prefetch_grammars_sync(languages: frozenset[str]) -> None:
    """Download/load every grammar in *languages* in one pass (blocking).

    ``prefetch()`` fails atomically for the whole batch if any single
    requested language is unsupported by the installed pack, so the set
    is pre-filtered through ``has_language()`` -- unlike the per-unit
    lazy path, an unsupported language here must not abort grammars that
    ARE available.
    """
    from tree_sitter_language_pack import has_language, prefetch  # noqa: PLC0415

    supported = [language for language in languages if has_language(language)]
    if supported:
        prefetch(supported)


async def _prefetch_grammars(raw: RawDocument) -> None:
    """Prefetch this batch's tree-sitter grammars before the parallel fan-out.

    Best-effort: the ``tree-sitter`` extras may be absent, or the
    download may fail (network, disk). Either way, chunking still
    proceeds via each unit's own lazy ``get_parser`` call in
    ``CodeChunker``, just without the batched pre-download.
    """
    languages = _batch_languages(raw.units)
    if not languages:
        return
    try:
        await asyncio.to_thread(_prefetch_grammars_sync, languages)
    except ImportError as exc:
        # Optional-extras-absent is the expected case, but a broken (present
        # but half-importable) tree_sitter pack is indistinguishable without
        # a trace; DEBUG keeps it diagnosable without startup noise.
        logger.debug(
            KNOWLEDGE_GRAMMAR_PREFETCH_FAILED,
            languages=sorted(languages),
            reason="tree_sitter_extras_absent",
            error_type=type(exc).__name__,
        )
        return
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            KNOWLEDGE_GRAMMAR_PREFETCH_FAILED,
            languages=sorted(languages),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


def _flatten_exc(exc: BaseException) -> list[BaseException]:
    """Return the leaf exceptions of ``exc``, expanding nested groups.

    Returns:
        Every non-group leaf, in left-to-right order.
    """
    if isinstance(exc, BaseExceptionGroup):
        return [leaf for sub in exc.exceptions for leaf in _flatten_exc(sub)]
    return [exc]


async def chunk_raw_document(
    raw: RawDocument,
    *,
    config: KnowledgeConfig,
) -> tuple[KnowledgeChunk, ...]:
    """Chunk every unit of *raw* into positional :class:`KnowledgeChunk`.

    Units are chunked concurrently (each is synchronous/CPU-bound, so
    offloaded via ``asyncio.to_thread``); grammars for this batch's
    languages are prefetched once beforehand so the concurrent workers
    don't race a per-worker grammar download. Output ordering matches
    ``raw.units`` regardless of completion order: downstream chunk ids
    are positional.

    Returns:
        The positional knowledge chunks for every unit of ``raw``,
        indexed in order.
    """
    await _prefetch_grammars(raw)

    async def _chunk_one(
        unit: RawUnit,
    ) -> tuple[ContentKind, tuple[ChunkPiece, ...]]:
        chunker = build_chunker(unit.content_kind, config)
        pieces = await asyncio.to_thread(chunker.chunk_unit, unit)
        return unit.content_kind, pieces

    # A child failure surfaces from the TaskGroup as an ExceptionGroup.
    # Catch the group directly (not ``except*``) and collapse it to a
    # single leaf so the ingest caller's type-based dispatch (bare
    # KnowledgeError / critical MemoryError / RecursionError) still
    # matches. A plain ``except`` re-raises the chosen leaf as-is; an
    # ``except*`` block would re-wrap a BaseException leaf (e.g. a
    # cancellation) it did not itself match. A critical leaf is surfaced
    # ahead of any sibling ordinary error so it is never downgraded.
    try:
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_chunk_one(unit)) for unit in raw.units]
    except BaseExceptionGroup as eg:
        leaves = _flatten_exc(eg)
        for leaf in leaves:
            reraise_critical(leaf)
        raise leaves[0] from eg

    typed_pieces: list[tuple[ContentKind, ChunkPiece]] = []
    for task in tasks:
        kind, pieces = task.result()
        typed_pieces.extend((kind, piece) for piece in pieces)

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
