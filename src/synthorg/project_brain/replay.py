"""Boot-time RAG re-index replay for the project brain.

The write path persists the SQL row (durable) before the best-effort index, and
records the last-indexed revision per entry in the ``project_brain_index_state``
bookkeeping table only on a successful index. A transient memory-backend outage
can therefore leave an entry persisted but absent from (or stale in) the RAG
index, which makes it invisible to the transparent re-entry retrieval path. The
next revision of that entry re-indexes it idempotently, but a terminal-state
entry that is never revised again would stay behind.

:func:`reindex_unindexed` closes that gap at boot. For each project it reads the
last-indexed revision per entry and re-indexes exactly the entries whose current
revision exceeds it (or that have never been indexed). It touches only the gap,
so boot stays fast as the brain grows, and it detects both never-indexed entries
and entries stuck at an older revision.
"""

from collections.abc import Iterable

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.project_brain import (
    BRAIN_REPLAY_COMPLETE,
    BRAIN_REPLAY_FAILED,
    BRAIN_REPLAY_START,
)
from synthorg.persistence.project_brain_protocol import (
    BrainFilterSpec,
    ProjectBrainRepository,
)
from synthorg.project_brain.chunker import BrainChunker
from synthorg.project_brain.errors import BrainIndexError
from synthorg.project_brain.indexer import BrainIndexer

logger = get_logger(__name__)

_LIST_PAGE_SIZE: int = 500


async def reindex_unindexed(
    *,
    repo: ProjectBrainRepository,
    chunker: BrainChunker,
    indexer: BrainIndexer,
    project_ids: Iterable[NotBlankStr],
) -> int:
    """Re-index current-state entries missing from (or stale in) the index.

    Best-effort: a failure on one project is logged and the sweep continues with
    the next, so a single bad project never aborts boot recovery.

    Args:
        repo: Brain repository (system of record + index-state bookkeeping).
        chunker: Produces chunks for an entry.
        indexer: Stores chunks under the PROJECT_BRAIN memory category.
        project_ids: Projects to sweep.

    Returns:
        The total number of entries re-indexed across all projects.
    """
    total = 0
    for project_id in project_ids:
        try:
            total += await _reindex_project(
                repo=repo,
                chunker=chunker,
                indexer=indexer,
                project_id=project_id,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                BRAIN_REPLAY_FAILED,
                project_id=project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
    return total


async def _reindex_project(
    *,
    repo: ProjectBrainRepository,
    chunker: BrainChunker,
    indexer: BrainIndexer,
    project_id: NotBlankStr,
) -> int:
    """Re-index one project's gap entries (current revision past last indexed).

    Returns:
        The number of entries re-indexed for this project.
    """
    logger.debug(BRAIN_REPLAY_START, project_id=project_id)
    indexed = await repo.indexed_revisions(project_id)
    reindexed = 0
    offset = 0
    # lint-allow: long-running-loop-kill-switch -- bounded pagination drain
    while True:
        page = await repo.list_current(
            BrainFilterSpec(project_id=project_id),
            limit=_LIST_PAGE_SIZE,
            offset=offset,
        )
        if not page:
            break
        for entry in page:
            if indexed.get(entry.entry_id, 0) >= entry.revision:
                continue
            chunks = chunker.chunk(project_id=project_id, entry=entry)
            try:
                await indexer.index(
                    project_id=project_id,
                    entry_id=entry.entry_id,
                    chunks=chunks,
                )
                await repo.mark_indexed(project_id, entry.entry_id, entry.revision)
            except (BrainIndexError, QueryError) as exc:
                logger.warning(
                    BRAIN_REPLAY_FAILED,
                    project_id=project_id,
                    entry_id=entry.entry_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                continue
            reindexed += 1
        if len(page) < _LIST_PAGE_SIZE:
            break
        offset += _LIST_PAGE_SIZE
    if reindexed:
        logger.info(BRAIN_REPLAY_COMPLETE, project_id=project_id, reindexed=reindexed)
    return reindexed
