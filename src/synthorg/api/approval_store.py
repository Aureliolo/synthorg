"""Approval store with optional durable persistence.

Provides async CRUD operations for ``ApprovalItem`` instances.
Designed to be attached to ``AppState``.  When an
``ApprovalRepository`` (protocol-typed; backend-agnostic) is
provided, mutations are persisted to the database while the in-memory
dict serves as a read cache.

Concurrency model
-----------------
All mutation paths (``add``, ``save``, ``save_if_pending``,
``_check_expiration_locked`` write-back, and ``list_items``/``get``
cache populate) acquire a single instance-level ``asyncio.Lock`` so
the check-fetch-save-cache-update region cannot interleave across
concurrent callers.

``save()`` additionally tracks in-flight saves per approval id.  When
two concurrent callers target the same id, the second sees the
in-flight marker and returns ``None`` (first-writer-wins).  Sequential
saves on the same id work normally -- the in-flight set is only
populated while a save is actively running.

To keep the first-writer-wins rejection observable under contention,
``save()`` releases the store lock while it awaits ``_repo.save(item)``
so a second caller can enter, detect the in-flight marker, and return
``None`` without blocking.  During that small repo-I/O window a
concurrent ``get()`` may still observe the cache's previous value
while the repository has already committed the new one; readers of a
given id reach consistency as soon as the winning ``save()`` finishes
its cache update.  This is an accepted trade-off of FWW semantics --
the alternative (holding the lock across I/O) collapses to
last-writer-wins because the second caller can no longer observe the
first's in-flight marker.
"""

import asyncio
from collections.abc import Callable  # noqa: TC003
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from synthorg.api.errors import ConflictError
from synthorg.core.approval import ApprovalItem  # noqa: TC001
from synthorg.core.enums import (
    ApprovalRiskLevel,
    ApprovalStatus,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APPROVAL_CONFLICT,
    API_APPROVAL_EXPIRE_CALLBACK_FAILED,
    API_APPROVAL_EXPIRED,
    API_APPROVAL_STORE_CLEARED,
    API_RESOURCE_NOT_FOUND,
)
from synthorg.persistence.errors import ConstraintViolationError

if TYPE_CHECKING:
    from synthorg.persistence.approval_protocol import ApprovalRepository

logger = get_logger(__name__)


class ApprovalStore:
    """Approval store with in-memory cache and optional durable persistence.

    Uses a plain ``dict`` for O(1) lookups by ID.  A single instance
    ``asyncio.Lock`` serialises all mutation paths so read-then-write
    sequences (cache check + repo fetch + repo save + cache update)
    are atomic w.r.t. concurrent callers.

    When ``repo`` is provided, all mutations are persisted to the
    database.  The in-memory dict serves as a read-through cache.

    Args:
        on_expire: Optional callback for expired items.
        repo: Optional durable repository for persistence
            (protocol-typed; backend-agnostic).
    """

    def __init__(
        self,
        *,
        on_expire: Callable[[ApprovalItem], None] | None = None,
        repo: ApprovalRepository | None = None,
    ) -> None:
        self._items: dict[str, ApprovalItem] = {}
        self._on_expire = on_expire
        self._repo = repo
        self._lock = asyncio.Lock()
        # Approval ids whose ``save()`` is currently mid-flight.  A
        # second concurrent ``save(same_id)`` observes the marker and
        # returns ``None`` (first-writer-wins), preventing a lost-update
        # race where two callers' differing payloads silently stomp one
        # another.
        self._saves_in_flight: set[str] = set()

    async def clear(self) -> None:
        """Reset all approval items.

        Holds the same ``self._lock`` as the CRUD methods so a
        concurrent ``save`` / ``get`` / ``list_items`` cannot observe
        a partially-cleared cache (#1599).
        """
        async with self._lock:
            cleared_count = len(self._items)
            self._items.clear()
            self._saves_in_flight.clear()
        logger.info(API_APPROVAL_STORE_CLEARED, cleared_count=cleared_count)

    def reset_for_test_sync(self) -> None:
        """Synchronous reset for sync pytest fixtures only.

        Bypasses ``self._lock`` -- callers must guarantee no async
        operations are in flight. Production code MUST use the async
        ``clear`` instead.
        """
        cleared_count = len(self._items)
        self._items.clear()
        self._saves_in_flight.clear()
        logger.info(API_APPROVAL_STORE_CLEARED, cleared_count=cleared_count)

    async def add(self, item: ApprovalItem) -> None:
        """Add a new approval item.

        Checks both the in-memory cache and the repository for
        duplicates so restarts cannot silently overwrite persisted
        items.

        Args:
            item: The approval item to store.

        Raises:
            ConflictError: If an item with the same ID already exists.
        """
        async with self._lock:
            if item.id in self._items:
                msg = f"Approval {item.id!r} already exists"
                logger.warning(
                    API_APPROVAL_CONFLICT,
                    error="duplicate",
                    approval_id=item.id,
                )
                raise ConflictError(msg)
            if self._repo is not None:
                existing = await self._repo.get(item.id)
                if existing is not None:
                    self._items[existing.id] = existing
                    msg = f"Approval {item.id!r} already exists"
                    logger.warning(
                        API_APPROVAL_CONFLICT,
                        error="duplicate_in_repo",
                        approval_id=item.id,
                    )
                    raise ConflictError(msg)
                try:
                    await self._repo.save(item)
                except ConstraintViolationError:
                    msg = f"Approval {item.id!r} already exists"
                    logger.warning(
                        API_APPROVAL_CONFLICT,
                        error="constraint_violation",
                        approval_id=item.id,
                    )
                    raise ConflictError(msg) from None
            self._items[item.id] = item

    async def get(self, approval_id: NotBlankStr) -> ApprovalItem | None:
        """Get an approval item by ID, applying lazy expiration.

        Falls through to the repository on cache miss when a repo is
        configured, ensuring persisted items survive restarts.

        Args:
            approval_id: The approval identifier.

        Returns:
            The approval item, or ``None`` if not found.
        """
        async with self._lock:
            item = self._items.get(approval_id)
            if item is None and self._repo is not None:
                item = await self._repo.get(approval_id)
                if item is not None:
                    self._items[item.id] = item
            if item is None:
                return None
            return await self._check_expiration_locked(item)

    async def list_items(
        self,
        *,
        status: ApprovalStatus | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        action_type: NotBlankStr | None = None,
    ) -> tuple[ApprovalItem, ...]:
        """List approval items with optional filters.

        When a repository is configured, queries the repo (source of
        truth) and refreshes the in-memory cache.  Otherwise falls
        back to the cache alone.

        Applies lazy expiration to all items before filtering.

        Args:
            status: Filter by approval status.
            risk_level: Filter by risk level.
            action_type: Filter by action type.

        Returns:
            Tuple of matching approval items.
        """
        async with self._lock:
            if self._repo is not None:
                repo_items = await self._repo.list_items(
                    status=status,
                    risk_level=risk_level,
                    action_type=action_type,
                )
                for item in repo_items:
                    self._items[item.id] = item
                # Re-filter after expiration: _check_expiration may
                # transition PENDING -> EXPIRED, invalidating the
                # original status filter from the repo query.
                result: list[ApprovalItem] = []
                for item in repo_items:
                    checked = await self._check_expiration_locked(item)
                    if status is not None and checked.status != status:
                        continue
                    if risk_level is not None and checked.risk_level != risk_level:
                        continue
                    result.append(checked)
                return tuple(result)
            checked_items: list[ApprovalItem] = []
            for stored in list(self._items.values()):
                checked = await self._check_expiration_locked(stored)
                if status is not None and checked.status != status:
                    continue
                if risk_level is not None and checked.risk_level != risk_level:
                    continue
                if action_type is not None and checked.action_type != action_type:
                    continue
                checked_items.append(checked)
            return tuple(checked_items)

    async def save(self, item: ApprovalItem) -> ApprovalItem | None:
        """Update an existing approval item (first-writer-wins).

        Two concurrent ``save(same_id)`` calls are resolved so that
        exactly one writes: the first caller claims an in-flight slot
        under the lock and proceeds; the second caller observes the
        slot and returns ``None``.  Sequential saves on the same id
        proceed normally because the slot is released after each
        write.

        Args:
            item: The updated approval item.

        Returns:
            The saved item, or ``None`` if the ID was not found or a
            concurrent save already claimed it.
        """
        async with self._lock:
            if item.id not in self._items and self._repo is not None:
                existing = await self._repo.get(item.id)
                if existing is not None:
                    self._items[existing.id] = existing
            if item.id not in self._items:
                logger.warning(
                    API_RESOURCE_NOT_FOUND,
                    resource="approval",
                    approval_id=item.id,
                )
                return None
            if item.id in self._saves_in_flight:
                logger.warning(
                    API_APPROVAL_CONFLICT,
                    error="concurrent_save",
                    approval_id=item.id,
                )
                return None
            self._saves_in_flight.add(item.id)
        try:
            if self._repo is not None:
                try:
                    await self._repo.save(item)
                except asyncio.CancelledError:
                    # The repo commit may have landed before
                    # cancellation was delivered to us; evict the
                    # cache entry so the next reader falls through
                    # to the repository and observes the committed
                    # state instead of the stale cached copy.
                    # ``shield`` protects the eviction from a second
                    # cancellation arriving while we acquire the
                    # lock.
                    await asyncio.shield(self._invalidate_cache(item.id))
                    raise
            async with self._lock:
                self._items[item.id] = item
            return item
        finally:
            async with self._lock:
                self._saves_in_flight.discard(item.id)

    async def save_if_pending(
        self,
        item: ApprovalItem,
    ) -> ApprovalItem | None:
        """Conditionally update an approval item if it is still pending.

        A lazy expiration check is applied before comparing status.

        Args:
            item: The updated approval item (must have an existing ID).

        Returns:
            The saved item on success, or ``None`` if:

            * no item with the given ID exists in the store,
            * the stored item has expired,
            * the stored item is no longer ``PENDING`` (e.g. a
              concurrent decision was made), or
            * a concurrent ``save()`` on the same id is mid-flight
              (its outcome is still committing, so the cached status
              may be stale).
        """
        async with self._lock:
            # Mirror the FWW guard from ``save()``: ``save()`` releases
            # ``self._lock`` while it awaits the repo write, so a
            # naive ``save_if_pending()`` entering that window would
            # see the stale cached ``PENDING`` item and persist a
            # second decision, reopening the lost-update race.
            # Abort early so the caller can retry once the in-flight
            # save finishes.
            if item.id in self._saves_in_flight:
                logger.warning(
                    API_APPROVAL_CONFLICT,
                    error="concurrent_save",
                    approval_id=item.id,
                )
                return None
            current = self._items.get(item.id)
            if current is None and self._repo is not None:
                current = await self._repo.get(item.id)
                if current is not None:
                    self._items[current.id] = current
            if current is None:
                return None
            # Apply lazy expiration check before comparing status.
            current = await self._check_expiration_locked(current)
            if current.status != ApprovalStatus.PENDING:
                return None
            if self._repo is not None:
                try:
                    await self._repo.save(item)
                except asyncio.CancelledError:
                    # The lock is still held here (we are still inside
                    # the outer ``async with``); evict the cache entry
                    # so the next reader reloads the committed state
                    # from the repo instead of the stale ``PENDING``
                    # cached copy.
                    self._items.pop(item.id, None)
                    raise
            self._items[item.id] = item
            return item

    async def _invalidate_cache(self, approval_id: str) -> None:
        """Evict a cache entry, acquiring the lock first.

        Invoked from ``save()`` under ``asyncio.shield`` when a repo
        write is cancelled: the commit may have landed already, and
        the cached copy would otherwise serve stale data to the next
        reader.  Dropping the entry forces the next ``get`` / ``list``
        to fall through to the repository and repopulate from truth.

        Args:
            approval_id: Identifier of the cache entry to evict.
        """
        async with self._lock:
            self._items.pop(approval_id, None)

    async def _check_expiration_locked(
        self,
        item: ApprovalItem,
    ) -> ApprovalItem:
        """Lazy expiration, assuming ``self._lock`` is held.

        If the item is PENDING and has expired, transition it to
        EXPIRED in both the cache and the repository.  Callers MUST
        hold ``self._lock``; the method performs cache + repo mutations
        without re-acquiring it.

        Args:
            item: The item to check.

        Returns:
            The original or expired item.
        """
        if (
            item.status == ApprovalStatus.PENDING
            and item.expires_at is not None
            and datetime.now(UTC) >= item.expires_at
        ):
            expired = item.model_copy(
                update={"status": ApprovalStatus.EXPIRED},
            )
            if self._repo is not None:
                await self._repo.save(expired)
            self._items[item.id] = expired
            logger.info(
                API_APPROVAL_EXPIRED,
                approval_id=item.id,
            )
            if self._on_expire is not None:
                try:
                    self._on_expire(expired)
                except MemoryError, RecursionError:
                    raise
                except Exception as exc:
                    # Best-effort: the approval is already transitioned
                    # to EXPIRED in cache + repo at this point; callback
                    # failure must not unwind the expiration itself.
                    # Emit a dedicated event so operators can filter
                    # callback failures from successful expirations.
                    logger.warning(
                        API_APPROVAL_EXPIRE_CALLBACK_FAILED,
                        approval_id=item.id,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
            return expired
        return item
