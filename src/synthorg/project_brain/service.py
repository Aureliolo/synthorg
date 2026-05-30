# module-kind: service
"""Top-level service for the long-horizon project brain.

Composes the append-only repository, the chunker, the indexer, the workspace
writer, and the memory backend into a single async entry point. Agents reach it
through :class:`WriteBrainEntryTool` / :class:`SearchBrainTool`; the MCP handlers
and the read-only REST endpoints call it directly.

Write-path contract: the SQL append is the durable commit point. The git
snapshot and the memory index follow and are best-effort. A snapshot or index
failure is logged with context but does NOT fail the call: the entry is already
durably persisted, and re-raising would tempt the caller to retry, which (the
store being append-only) would create a duplicate revision. The next write
re-commits the latest state and re-indexes idempotently by the entry tag.
"""

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.models import MemoryQuery
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.project_brain import (
    BRAIN_ENTRY_APPENDED,
    BRAIN_ENTRY_INDEX_FAILED,
    BRAIN_ENTRY_REVISED,
    BRAIN_SEARCH_COMPLETE,
    BRAIN_SNAPSHOT_FAILED,
)
from synthorg.project_brain.constants import (
    BRAIN_BRANCH_NAME,
    BRAIN_HISTORY_DEFAULT_LIMIT,
    BRAIN_LIST_DEFAULT_LIMIT,
    BRAIN_MEMORY_NAMESPACE,
    BRAIN_PROJECT_TAG_PREFIX,
    BRAIN_SEARCH_DEFAULT_LIMIT,
    BRAIN_SEARCH_MAX_LIMIT,
    BRAIN_WORKSPACE_SUBDIR,
    SYSTEM_BRAIN_AGENT_ID,
)
from synthorg.project_brain.errors import (
    BrainCommitError,
    BrainEntryNotFoundError,
    BrainEntryValidationError,
    BrainIndexError,
)
from synthorg.project_brain.models import (
    BlockerPayload,
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
    BrainEntryVersion,
    BrainPayloadValue,
    BrainSearchHit,
    BrainSummary,
    Citation,
    OpenQuestionPayload,
)
from synthorg.project_brain.mutation import apply_overrides, build_entry
from synthorg.project_brain.query import (
    build_filter_spec,
    build_git_history,
    entry_to_search_hit,
    entry_to_summary,
)

if TYPE_CHECKING:
    from synthorg.engine.workspace.project_workspace_service import (
        ProjectWorkspaceService,
    )
    from synthorg.memory.protocol import MemoryBackend
    from synthorg.persistence.project_brain_protocol import ProjectBrainRepository
    from synthorg.project_brain.chunker import BrainChunker
    from synthorg.project_brain.indexer import BrainIndexer
    from synthorg.project_brain.writer import BrainWriter

logger = get_logger(__name__)

_REVISABLE_BY_RESOLVE = frozenset(
    {BrainEntryKind.OPEN_QUESTION, BrainEntryKind.DEPENDENCY}
)
_SUPERSEDABLE = frozenset({BrainEntryKind.DECISION, BrainEntryKind.PLAN_REVISION})


class ProjectBrainService:
    """Public entry point for project-brain operations."""

    __slots__ = (
        "_backend",
        "_chunker",
        "_clock",
        "_indexer",
        "_locks_guard",
        "_repo",
        "_workspace_service",
        "_write_locks",
        "_writer",
    )

    def __init__(  # noqa: PLR0913 -- engine entry point composes every collaborator
        self,
        *,
        repo: ProjectBrainRepository,
        workspace_service: ProjectWorkspaceService,
        chunker: BrainChunker,
        indexer: BrainIndexer,
        writer: BrainWriter,
        backend: MemoryBackend,
        clock: Clock | None = None,
    ) -> None:
        self._repo = repo
        self._workspace_service = workspace_service
        self._chunker = chunker
        self._indexer = indexer
        self._writer = writer
        self._backend = backend
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._write_locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def _write_lock_for(self, project_id: NotBlankStr) -> asyncio.Lock:
        """Return the per-project write lock, creating it on first use.

        Returns:
            The lock serialising revision assignment, snapshot, and index for
            one project so they happen atomically.
        """
        async with self._locks_guard:
            return self._write_locks.setdefault(project_id, asyncio.Lock())

    async def append_entry(  # noqa: PLR0913 -- envelope fields are explicit
        self,
        *,
        project_id: NotBlankStr,
        title: NotBlankStr,
        rationale: NotBlankStr,
        status: BrainEntryStatus,
        author: NotBlankStr,
        payload: BrainPayloadValue,
        related_task_ids: tuple[NotBlankStr, ...] = (),
        related_entry_ids: tuple[NotBlankStr, ...] = (),
        supersedes_entry_id: NotBlankStr | None = None,
        tags: tuple[NotBlankStr, ...] = (),
        confidence: float | None = None,
        citations: tuple[Citation, ...] = (),
    ) -> BrainEntry:
        """Create a new logical entry at revision 1.

        The entry kind is taken from ``payload.entry_kind``; a fresh logical
        ``entry_id`` is generated. ``recorded_at`` is stamped from the service
        clock.

        Args:
            project_id: Owning project.
            title: Human-readable title.
            rationale: Why the entry holds (the "why").
            status: Lifecycle status; validated against the kind.
            author: Agent id or operator id of the writer.
            payload: The kind-specific discriminated payload.
            related_task_ids: Task IDs this entry references.
            related_entry_ids: Other brain entry IDs this entry references.
            supersedes_entry_id: The entry id this one supersedes, if any.
            tags: Free-form classification tags (unique).
            confidence: Optional confidence in ``[0, 1]``.
            citations: Provenance pointers backing this entry.

        Returns:
            The persisted entry with the server-assigned revision.

        Raises:
            BrainEntryValidationError: If the envelope fails validation (illegal
                status for the kind, duplicate tags, ...).
        """
        entry = build_entry(
            now=self._clock.now(),
            project_id=project_id,
            title=title,
            rationale=rationale,
            status=status,
            author=author,
            payload=payload,
            related_task_ids=related_task_ids,
            related_entry_ids=related_entry_ids,
            supersedes_entry_id=supersedes_entry_id,
            tags=tags,
            confidence=confidence,
            citations=citations,
        )
        lock = await self._write_lock_for(project_id)
        async with lock:
            return await self._append_revision(entry, event=BRAIN_ENTRY_APPENDED)

    async def revise_entry(  # noqa: PLR0913 -- optional overrides are explicit
        self,
        *,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
        author: NotBlankStr,
        status: BrainEntryStatus | None = None,
        title: NotBlankStr | None = None,
        rationale: NotBlankStr | None = None,
        payload: BrainPayloadValue | None = None,
        related_task_ids: tuple[NotBlankStr, ...] | None = None,
        related_entry_ids: tuple[NotBlankStr, ...] | None = None,
        supersedes_entry_id: NotBlankStr | None = None,
        tags: tuple[NotBlankStr, ...] | None = None,
        citations: tuple[Citation, ...] | None = None,
    ) -> BrainEntry:
        """Append the next revision of an existing entry.

        Every supplied override replaces the corresponding field; omitted fields
        inherit the current revision's value (including ``confidence``). The
        kind never changes. The revised envelope is re-validated, so an illegal
        status-for-kind transition is rejected.

        Args:
            project_id: Owning project.
            entry_id: Logical entry id to revise.
            author: Who is making this revision.
            status: New status, or ``None`` to keep the current one.
            title: New title, or ``None`` to keep.
            rationale: New rationale, or ``None`` to keep.
            payload: New payload (same kind), or ``None`` to keep.
            related_task_ids: Replacement task links, or ``None`` to keep.
            related_entry_ids: Replacement entry links, or ``None`` to keep.
            supersedes_entry_id: New supersession link, or ``None`` to keep.
            tags: Replacement tags, or ``None`` to keep.
            citations: Replacement citations, or ``None`` to keep.

        Returns:
            The persisted new revision.

        Raises:
            BrainEntryNotFoundError: If the entry does not exist.
            BrainEntryValidationError: If the revised envelope fails validation.
        """
        lock = await self._write_lock_for(project_id)
        async with lock:
            current = await self._require_current(project_id, entry_id)
            revised = apply_overrides(
                current,
                now=self._clock.now(),
                author=author,
                status=status,
                title=title,
                rationale=rationale,
                payload=payload,
                related_task_ids=related_task_ids,
                related_entry_ids=related_entry_ids,
                supersedes_entry_id=supersedes_entry_id,
                tags=tags,
                citations=citations,
            )
            return await self._append_revision(revised, event=BRAIN_ENTRY_REVISED)

    async def resolve(
        self,
        *,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
        author: NotBlankStr,
        answer: NotBlankStr | None = None,
    ) -> BrainEntry:
        """Resolve an open question or a dependency.

        For an open question the optional ``answer`` is recorded on the payload;
        for a dependency the status alone moves to ``RESOLVED``.

        Args:
            project_id: Owning project.
            entry_id: Logical entry id.
            author: Who resolved it.
            answer: The answer text (open questions only).

        Returns:
            The persisted resolved revision.

        Raises:
            BrainEntryNotFoundError: If the entry does not exist.
            BrainEntryValidationError: If the entry kind cannot be resolved.
        """
        lock = await self._write_lock_for(project_id)
        async with lock:
            current = await self._require_current(project_id, entry_id)
            if current.entry_kind not in _REVISABLE_BY_RESOLVE:
                msg = f"cannot resolve a {current.entry_kind.value!r} entry"
                raise BrainEntryValidationError(msg)
            payload: BrainPayloadValue | None = None
            if current.entry_kind is BrainEntryKind.OPEN_QUESTION:
                payload = OpenQuestionPayload(answer=answer)
            revised = apply_overrides(
                current,
                now=self._clock.now(),
                author=author,
                status=BrainEntryStatus.RESOLVED,
                payload=payload,
            )
            return await self._append_revision(revised, event=BRAIN_ENTRY_REVISED)

    async def supersede(
        self,
        *,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
        by_entry_id: NotBlankStr,
        author: NotBlankStr,
    ) -> BrainEntry:
        """Mark a decision or plan revision superseded and link the successor.

        The target moves to ``SUPERSEDED`` and ``by_entry_id`` is added to its
        ``related_entry_ids`` as the forward link to its replacement.

        Args:
            project_id: Owning project.
            entry_id: The entry being superseded.
            by_entry_id: The successor entry id.
            author: Who superseded it.

        Returns:
            The persisted superseded revision.

        Raises:
            BrainEntryNotFoundError: If the entry does not exist.
            BrainEntryValidationError: If the entry kind cannot be superseded.
        """
        lock = await self._write_lock_for(project_id)
        async with lock:
            current = await self._require_current(project_id, entry_id)
            if current.entry_kind not in _SUPERSEDABLE:
                msg = f"cannot supersede a {current.entry_kind.value!r} entry"
                raise BrainEntryValidationError(msg)
            links = current.related_entry_ids
            if by_entry_id not in links:
                links = (*links, by_entry_id)
            revised = apply_overrides(
                current,
                now=self._clock.now(),
                author=author,
                status=BrainEntryStatus.SUPERSEDED,
                related_entry_ids=links,
            )
            return await self._append_revision(revised, event=BRAIN_ENTRY_REVISED)

    async def clear_blocker(
        self,
        *,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
        author: NotBlankStr,
        resolution: NotBlankStr | None = None,
    ) -> BrainEntry:
        """Clear a blocker, recording how it was resolved.

        Args:
            project_id: Owning project.
            entry_id: The blocker entry id.
            author: Who cleared it.
            resolution: How the blocker was cleared.

        Returns:
            The persisted cleared revision.

        Raises:
            BrainEntryNotFoundError: If the entry does not exist.
            BrainEntryValidationError: If the entry is not a blocker.
        """
        lock = await self._write_lock_for(project_id)
        async with lock:
            current = await self._require_current(project_id, entry_id)
            if current.entry_kind is not BrainEntryKind.BLOCKER:
                msg = f"cannot clear a {current.entry_kind.value!r} entry"
                raise BrainEntryValidationError(msg)
            severity = current.payload.severity  # type: ignore[union-attr]
            payload = BlockerPayload(severity=severity, resolution=resolution)
            revised = apply_overrides(
                current,
                now=self._clock.now(),
                author=author,
                status=BrainEntryStatus.CLEARED,
                payload=payload,
            )
            return await self._append_revision(revised, event=BRAIN_ENTRY_REVISED)

    async def get_entry(
        self,
        *,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
        revision: int | None = None,
    ) -> BrainEntry:
        """Return one entry, latest or at an exact revision.

        Args:
            project_id: Owning project.
            entry_id: Logical entry id.
            revision: Exact revision, or ``None`` for the latest.

        Returns:
            The matching entry.

        Raises:
            BrainEntryNotFoundError: If the entry or revision does not exist.
        """
        if revision is None:
            return await self._require_current(project_id, entry_id)
        entry = await self._repo.get((project_id, entry_id, revision))
        if entry is None:
            msg = f"brain entry {entry_id!r} revision {revision} not found"
            raise BrainEntryNotFoundError(msg)
        return entry

    async def get_current(
        self,
        *,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
    ) -> BrainEntry | None:
        """Return the latest revision of one entry, or ``None`` if absent.

        Returns:
            The latest revision, or ``None``.
        """
        return await self._repo.get_current(project_id, entry_id)

    async def list_current(  # noqa: PLR0913 -- filter dimensions are explicit
        self,
        *,
        project_id: NotBlankStr,
        entry_kind: BrainEntryKind | None = None,
        status: BrainEntryStatus | None = None,
        tag: NotBlankStr | None = None,
        author: NotBlankStr | None = None,
        related_task_id: NotBlankStr | None = None,
        limit: int = BRAIN_LIST_DEFAULT_LIMIT,
        offset: int = 0,
    ) -> tuple[BrainSummary, ...]:
        """Return the current-state projection as list-view summaries.

        Returns:
            Current-state summaries matching the filter, newest-first.
        """
        spec = build_filter_spec(
            project_id=project_id,
            entry_kind=entry_kind,
            status=status,
            tag=tag,
            author=author,
            related_task_id=related_task_id,
        )
        rows = await self._repo.list_current(spec, limit=limit, offset=offset)
        return tuple(entry_to_summary(row) for row in rows)

    async def count_current(  # noqa: PLR0913 -- filter dimensions are explicit
        self,
        *,
        project_id: NotBlankStr,
        entry_kind: BrainEntryKind | None = None,
        status: BrainEntryStatus | None = None,
        tag: NotBlankStr | None = None,
        author: NotBlankStr | None = None,
        related_task_id: NotBlankStr | None = None,
    ) -> int:
        """Count current-state entries matching the filter.

        Returns:
            Number of current-state entries that match (for pagination).
        """
        spec = build_filter_spec(
            project_id=project_id,
            entry_kind=entry_kind,
            status=status,
            tag=tag,
            author=author,
            related_task_id=related_task_id,
        )
        return await self._repo.count(spec)

    async def query(
        self,
        *,
        project_id: NotBlankStr,
        query: NotBlankStr,
        limit: int = BRAIN_SEARCH_DEFAULT_LIMIT,
    ) -> tuple[BrainSearchHit, ...]:
        """Semantic search over indexed brain entries for a project.

        Args:
            project_id: Owning project.
            query: Search text.
            limit: Maximum hits (bounded by ``BRAIN_SEARCH_MAX_LIMIT``).

        Returns:
            Hits ordered by descending relevance.
        """
        effective_limit = min(limit, BRAIN_SEARCH_MAX_LIMIT)
        project_tag = NotBlankStr(f"{BRAIN_PROJECT_TAG_PREFIX}{project_id}")
        entries = await self._backend.retrieve(
            SYSTEM_BRAIN_AGENT_ID,
            MemoryQuery(
                text=query,
                categories=frozenset({MemoryCategory.PROJECT_BRAIN}),
                namespaces=frozenset({BRAIN_MEMORY_NAMESPACE}),
                tags=(project_tag,),
                limit=effective_limit,
            ),
        )
        hits = tuple(
            hit
            for entry in entries
            if (hit := entry_to_search_hit(entry)) is not None
            and hit.project_id == project_id
        )
        logger.info(
            BRAIN_SEARCH_COMPLETE,
            project_id=project_id,
            hit_count=len(hits),
        )
        return hits

    async def history(
        self,
        *,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
        limit: int = BRAIN_HISTORY_DEFAULT_LIMIT,
        offset: int = 0,
    ) -> tuple[BrainEntry, ...]:
        """Return the full structured revision chain of one entry, oldest-first.

        Returns:
            The entry's revisions, oldest-first.

        Raises:
            BrainEntryNotFoundError: If the entry has no revisions.
        """
        rows = await self._repo.history(
            project_id, entry_id, limit=limit, offset=offset
        )
        if not rows:
            msg = f"brain entry {entry_id!r} not found"
            raise BrainEntryNotFoundError(msg)
        return rows

    async def git_history(
        self,
        *,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
        limit: int = BRAIN_HISTORY_DEFAULT_LIMIT,
    ) -> tuple[BrainEntryVersion, ...]:
        """Return the git-versioned history of one entry's snapshot.

        Args:
            project_id: Owning project.
            entry_id: Logical entry id.
            limit: Maximum versions, newest-first.

        Returns:
            The entry's git versions, newest-first (empty when the snapshot was
            never committed).

        Raises:
            BrainEntryNotFoundError: If the entry does not exist in the store.
        """
        current = await self._require_current(project_id, entry_id)
        workspace = await self._workspace_service.get_or_provision(project_id)
        rel_path = (
            f"{BRAIN_WORKSPACE_SUBDIR}/{current.entry_kind.value}/{entry_id}.json"
        )
        return await build_git_history(
            repo_root=Path(workspace.workspace_path),
            rel_path=rel_path,
            branch=BRAIN_BRANCH_NAME,
            limit=limit,
        )

    # ── internals ────────────────────────────────────────────────────

    async def _require_current(
        self,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
    ) -> BrainEntry:
        """Return the latest revision or raise if the entry is absent.

        Returns:
            The latest revision of the entry.

        Raises:
            BrainEntryNotFoundError: If the entry does not exist.
        """
        current = await self._repo.get_current(project_id, entry_id)
        if current is None:
            msg = f"brain entry {entry_id!r} not found"
            raise BrainEntryNotFoundError(msg)
        return current

    async def _append_revision(self, entry: BrainEntry, *, event: str) -> BrainEntry:
        """Persist *entry*, then best-effort snapshot and index it.

        The caller must hold the per-project write lock. The SQL append is the
        durable commit point; a snapshot or index failure is logged but does not
        fail the call.

        Returns:
            The persisted entry with its server-assigned revision.

        Raises:
            BrainEntryRevisionConflictError: If a concurrent writer won the race.
            QueryError: If the durable SQL append fails.
        """
        persisted = await self._repo.append_with_next_revision(entry)
        await self._snapshot_best_effort(persisted)
        await self._index_best_effort(persisted)
        logger.info(
            event,
            project_id=persisted.project_id,
            entry_id=persisted.entry_id,
            entry_kind=persisted.entry_kind.value,
            revision=persisted.revision,
            status=persisted.status.value,
        )
        return persisted

    async def _snapshot_best_effort(self, entry: BrainEntry) -> None:
        """Commit the workspace snapshot, logging (not raising) on failure."""
        try:
            await self._writer.write(project_id=entry.project_id, entry=entry)
        except BrainCommitError as exc:
            logger.warning(
                BRAIN_SNAPSHOT_FAILED,
                project_id=entry.project_id,
                entry_id=entry.entry_id,
                revision=entry.revision,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _index_best_effort(self, entry: BrainEntry) -> None:
        """Re-index the entry's chunks, logging (not raising) on failure.

        On success the entry's last-indexed revision is recorded so boot replay
        can skip it; on failure the index-state row stays behind, marking the
        entry as a gap for the next boot replay (or the next revision) to heal.
        """
        chunks = self._chunker.chunk(project_id=entry.project_id, entry=entry)
        try:
            await self._indexer.index(
                project_id=entry.project_id,
                entry_id=entry.entry_id,
                chunks=chunks,
            )
            await self._repo.mark_indexed(
                entry.project_id, entry.entry_id, entry.revision
            )
        except BrainIndexError as exc:
            logger.warning(
                BRAIN_ENTRY_INDEX_FAILED,
                project_id=entry.project_id,
                entry_id=entry.entry_id,
                revision=entry.revision,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
