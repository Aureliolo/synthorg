# module-kind: code
"""Lifecycle-transition write operations for the project brain.

``resolve`` / ``supersede`` / ``clear_blocker`` share one shape: take the
per-project write lock, load the current revision, reject kinds the
transition does not apply to, then append a status-changed revision. They
live here as a small helper the :class:`ProjectBrainService` composes, so
the service module keeps the append/search/read surface in focus.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol

from synthorg.core.clock import Clock
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.project_brain import (
    BRAIN_ENTRY_REVISED,
    BRAIN_ENTRY_VALIDATION_FAILED,
)
from synthorg.project_brain._locks import PerKeyLockRegistry
from synthorg.project_brain.errors import BrainEntryValidationError
from synthorg.project_brain.models import (
    BlockerPayload,
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
    BrainPayloadValue,
    OpenQuestionPayload,
)
from synthorg.project_brain.mutation import apply_overrides

logger = get_logger(__name__)

_REVISABLE_BY_RESOLVE = frozenset(
    {BrainEntryKind.OPEN_QUESTION, BrainEntryKind.DEPENDENCY}
)
_SUPERSEDABLE = frozenset({BrainEntryKind.DECISION, BrainEntryKind.PLAN_REVISION})

RequireCurrent = Callable[[NotBlankStr, NotBlankStr], Awaitable[BrainEntry]]


class AppendRevision(Protocol):
    """The service's append-only revision writer."""

    async def __call__(self, entry: BrainEntry, *, event: str) -> BrainEntry:
        """Append ``entry`` as the current revision and return it."""
        ...


class RevisionOps:
    """Status-transition write paths composed by ``ProjectBrainService``.

    Args:
        write_locks: Per-project write-lock registry.
        require_current: Loads the current revision or raises
            ``BrainEntryNotFoundError``.
        clock: Time source for the revision timestamp.
        append_revision: The service's append-only revision writer.
    """

    __slots__ = ("_append_revision", "_clock", "_require_current", "_write_locks")

    def __init__(
        self,
        *,
        write_locks: PerKeyLockRegistry,
        require_current: RequireCurrent,
        clock: Clock,
        append_revision: AppendRevision,
    ) -> None:
        self._write_locks = write_locks
        self._require_current = require_current
        self._clock = clock
        self._append_revision = append_revision

    async def resolve(
        self,
        *,
        project_id: NotBlankStr,
        entry_id: NotBlankStr,
        author: NotBlankStr,
        answer: NotBlankStr | None = None,
    ) -> BrainEntry:
        """Resolve an open question or a dependency.

        For an open question the optional ``answer`` is recorded on the
        payload; for a dependency the status alone moves to ``RESOLVED``.

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
        lock = await self._write_locks.acquire_for(project_id)
        async with lock:
            current = await self._require_current(project_id, entry_id)
            if current.entry_kind not in _REVISABLE_BY_RESOLVE:
                msg = f"cannot resolve a {current.entry_kind.value!r} entry"
                logger.warning(
                    BRAIN_ENTRY_VALIDATION_FAILED,
                    project_id=project_id,
                    entry_id=entry_id,
                    entry_kind=current.entry_kind.value,
                    operation="resolve",
                    error_type=BrainEntryValidationError.__name__,
                )
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

        The target moves to ``SUPERSEDED`` and ``by_entry_id`` is added to
        its ``related_entry_ids`` as the forward link to its replacement.

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
        lock = await self._write_locks.acquire_for(project_id)
        async with lock:
            current = await self._require_current(project_id, entry_id)
            if current.entry_kind not in _SUPERSEDABLE:
                msg = f"cannot supersede a {current.entry_kind.value!r} entry"
                logger.warning(
                    BRAIN_ENTRY_VALIDATION_FAILED,
                    project_id=project_id,
                    entry_id=entry_id,
                    entry_kind=current.entry_kind.value,
                    operation="supersede",
                    error_type=BrainEntryValidationError.__name__,
                )
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
        lock = await self._write_locks.acquire_for(project_id)
        async with lock:
            current = await self._require_current(project_id, entry_id)
            if current.entry_kind is not BrainEntryKind.BLOCKER:
                msg = f"cannot clear a {current.entry_kind.value!r} entry"
                logger.warning(
                    BRAIN_ENTRY_VALIDATION_FAILED,
                    project_id=project_id,
                    entry_id=entry_id,
                    entry_kind=current.entry_kind.value,
                    operation="clear",
                    error_type=BrainEntryValidationError.__name__,
                )
                raise BrainEntryValidationError(msg)
            if not isinstance(current.payload, BlockerPayload):
                msg = (
                    f"blocker entry {entry_id!r} is missing its"
                    f" BlockerPayload; cannot derive a severity to clear"
                )
                logger.warning(
                    BRAIN_ENTRY_VALIDATION_FAILED,
                    project_id=project_id,
                    entry_id=entry_id,
                    entry_kind=current.entry_kind.value,
                    operation="clear",
                    error_type=BrainEntryValidationError.__name__,
                )
                raise BrainEntryValidationError(msg)
            severity = current.payload.severity
            payload = BlockerPayload(severity=severity, resolution=resolution)
            revised = apply_overrides(
                current,
                now=self._clock.now(),
                author=author,
                status=BrainEntryStatus.CLEARED,
                payload=payload,
            )
            return await self._append_revision(revised, event=BRAIN_ENTRY_REVISED)
