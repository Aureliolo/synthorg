# module-kind: service
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
its cache update.  This is an accepted trade-off of FWW semantics;
the alternative (holding the lock across I/O) collapses to
last-writer-wins because the second caller can no longer observe the
first's in-flight marker.
"""

import asyncio
from collections.abc import Callable
from datetime import datetime

from synthorg.api._approval_expiration import ApprovalExpirationMixin
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ConflictError
from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APPROVAL_ADD_CALLBACK_FAILED,
    API_APPROVAL_CONFLICT,
    API_APPROVAL_STORE_CLEARED,
    API_RESOURCE_NOT_FOUND,
)
from synthorg.persistence.approval_protocol import (
    ApprovalFilterSpec,
    ApprovalRepository,
)

logger = get_logger(__name__)


class ApprovalStore(ApprovalExpirationMixin):
    """Approval store with in-memory cache and optional durable persistence.

    Uses a plain ``dict`` for O(1) lookups by ID.  A single instance
    ``asyncio.Lock`` serialises all mutation paths so read-then-write
    sequences (cache check + repo fetch + repo save + cache update)
    are atomic w.r.t. concurrent callers.

    When ``repo`` is provided, all mutations are persisted to the
    database.  The in-memory dict serves as a read-through cache.

    Args:
        on_add: Optional callback for newly added items. The store is the
            only place every producer converges, so an observer that must
            see EVERY new approval (the WebSocket announcement) belongs
            here rather than on one caller: an agent parking a question
            writes straight to the store and never touches the REST
            create endpoint.
        on_expire: Optional callback for expired items.
        repo: Optional durable repository for persistence
            (protocol-typed; backend-agnostic).
    """

    def __init__(
        self,
        *,
        on_add: Callable[[ApprovalItem], None] | None = None,
        on_expire: Callable[[ApprovalItem], None] | None = None,
        repo: ApprovalRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._items: dict[str, ApprovalItem] = {}
        self._on_add = on_add
        self._on_expire = on_expire
        self._repo = repo
        # Clock seam: lazy-expiration checks on both the scalar
        # ``_check_expiration_locked`` and the batch ``_compute_expiration``
        # paths read time through ``self._clock`` so tests can drive
        # expiry deterministically with ``FakeClock`` instead of
        # patching ``datetime.now`` globally.
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._lock = asyncio.Lock()
        # Approval ids whose ``save()`` is currently mid-flight.  A
        # second concurrent ``save(same_id)`` observes the marker and
        # returns ``None`` (first-writer-wins), preventing a lost-update
        # race where two callers' differing payloads silently stomp one
        # another.
        self._saves_in_flight: set[str] = set()
        # Generation counter incremented by ``clear``. A ``save`` that
        # captures the current generation under the lock and observes
        # a different generation when it tries to repopulate
        # ``_items`` aborts the cache write so a clear cannot be
        # silently undone by an in-flight save. Wrapping at 2**64 is
        # not a concern -- the value is only ever compared for
        # equality.
        self._generation: int = 0

    @property
    def has_persistent_repo(self) -> bool:
        """``True`` iff a durable :class:`ApprovalRepository` is wired.

        Used by startup wiring to detect backend combinations where
        conversational-intake approvals cannot be durably persisted.
        Callers should refuse proposer wiring for unsupported
        persistence modes.
        """
        return self._repo is not None

    async def clear(self) -> None:
        """Reset the in-memory approval cache (cache-only).

        ``ApprovalStore`` is an in-memory cache fronting an optional
        durable :class:`ApprovalRepository`; ``clear`` deliberately
        does NOT delete persisted rows. The next ``get`` / ``list_items``
        on a configured repo will repopulate from durable storage --
        which is the intended contract for tenant-teardown and test
        isolation, the only callers. Reaching into the repo to delete
        every persisted approval would be an order-of-magnitude
        wider blast radius than any caller currently needs and there
        is no ``ApprovalRepository.clear``/``delete_all`` method on
        purpose -- destructive bulk deletes belong in administrative
        tooling, not the cache wrapper.

        Holds the same ``self._lock`` as the CRUD methods so a
        concurrent ``save`` / ``get`` / ``list_items`` cannot observe
        a partially-cleared cache. The generation counter is
        bumped under the lock; in-flight saves that captured the old
        generation will refuse to repopulate ``_items`` after the
        clear lands. The ``_saves_in_flight`` markers are deliberately
        preserved so a sibling save still observes the in-flight slot
        for first-writer-wins semantics -- the generation guard
        prevents the post-clear cache resurrection without sacrificing
        save dedup.
        """
        async with self._lock:
            cleared_count = len(self._items)
            self._items.clear()
            self._generation += 1
        logger.info(API_APPROVAL_STORE_CLEARED, cleared_count=cleared_count)

    def reset_for_test_sync(self) -> None:
        """Synchronous reset for sync pytest fixtures only.

        Bypasses ``self._lock`` -- callers must guarantee no async
        operations are in flight. Production code MUST use the async
        ``clear`` instead. This sync companion is intentional: it is
        exposed only through the ``SyncResettableApprovalStore`` protocol
        consumed by test fixtures, never by production wiring.
        """
        cleared_count = len(self._items)
        self._items.clear()
        self._saves_in_flight.clear()
        self._generation += 1
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
            if str(item.id) in self._items:
                msg = f"Approval {str(item.id)!r} already exists"
                logger.warning(
                    API_APPROVAL_CONFLICT,
                    error="duplicate",
                    approval_id=str(item.id),
                )
                raise ConflictError(msg)
            if self._repo is not None:
                existing = await self._repo.get(str(item.id))
                if existing is not None:
                    self._items[str(existing.id)] = existing
                    msg = f"Approval {str(item.id)!r} already exists"
                    logger.warning(
                        API_APPROVAL_CONFLICT,
                        error="duplicate_in_repo",
                        approval_id=str(item.id),
                    )
                    raise ConflictError(msg)
                try:
                    await self._repo.save(item)
                except ConstraintViolationError:
                    msg = f"Approval {str(item.id)!r} already exists"
                    logger.warning(
                        API_APPROVAL_CONFLICT,
                        error="constraint_violation",
                        approval_id=str(item.id),
                    )
                    raise ConflictError(msg) from None
            self._items[str(item.id)] = item
        # Outside the lock: a subscriber that publishes must not be able to
        # stall every other store mutation behind its own I/O.
        self._fire_add_callback(item)

    def _fire_add_callback(self, item: ApprovalItem) -> None:
        """Best-effort fire of ``_on_add`` for a newly stored approval.

        Mirrors :meth:`_fire_expire_callback`: the item is already
        committed to the cache and the repo, so a subscriber failure
        cannot unwind the add and must not surface to the producer.
        """
        if self._on_add is None:
            return
        try:
            self._on_add(item)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised below
            reraise_critical(exc)
            logger.error(
                API_APPROVAL_ADD_CALLBACK_FAILED,
                approval_id=str(item.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def delete(self, approval_id: NotBlankStr) -> bool:
        """Remove a single approval item by id (cache + persistent repo).

        Returns ``True`` iff a row was removed in the cache OR the
        repo (whichever held the item). Used by compensation paths
        that need to undo a just-committed ``add`` -- the multi-
        proposal parking loop in
        ``ChiefOfStaffProposer._record_proposals`` is the current
        caller. The cache pop is unconditional even when the repo
        reports a miss so the in-memory view never holds an item
        that has already been removed durably.

        Returns:
            ``True`` or ``False`` reflecting the condition.
        """
        async with self._lock:
            cached = self._items.pop(approval_id, None)
            repo_removed = False
            if self._repo is not None:
                try:
                    repo_removed = await self._repo.delete(approval_id)
                except Exception as exc:
                    reraise_critical(exc)
                    logger.warning(
                        API_APPROVAL_CONFLICT,
                        phase="delete_failed",
                        approval_id=approval_id,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                    if cached is not None:
                        self._items[approval_id] = cached
                    raise
            return cached is not None or repo_removed

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
                    self._items[str(item.id)] = item
            if item is None:
                return None
            return await self._check_expiration_locked(item)

    async def list_items(
        self,
        *,
        status: ApprovalStatus | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        action_type: NotBlankStr | None = None,
        created_since: datetime | None = None,
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
            created_since: Only items created at or after this instant
                (pushed down to the repo, keeping windowed reads like
                the analytics trend off the full-table scan path).

        Returns:
            Tuple of matching approval items.
        """
        if self._repo is not None:
            return await self._list_from_repo(
                status=status,
                risk_level=risk_level,
                action_type=action_type,
                created_since=created_since,
            )
        async with self._lock:
            return await self._list_from_cache_locked(
                status=status,
                risk_level=risk_level,
                action_type=action_type,
                created_since=created_since,
            )

    async def list_items_page(
        self,
        *,
        action_types: tuple[NotBlankStr, ...] | None = None,
        limit: int,
        offset: int = 0,
    ) -> tuple[ApprovalItem, ...]:
        """Bounded listing read: no drain loop, no lazy expiration.

        That sweep is owned by ``ApprovalTimeoutScheduler`` (works
        against both paths via :meth:`list_items`). Falls back to a
        bounded cache scan when no repository is configured.

        Args:
            action_types: Values to match (``IN``); ``None`` matches every type.
            limit: Maximum rows to return.
            offset: Rows to skip from the head of the ordering.

        Returns:
            Up to ``limit`` matching items.
        """
        if self._repo is not None:
            filter_spec = ApprovalFilterSpec(action_types=action_types)
            return await self._repo.query(filter_spec, limit=limit, offset=offset)
        async with self._lock:
            matches = [
                item
                for item in self._items.values()
                if not action_types or item.action_type in action_types
            ]
        matches.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        return tuple(matches[offset : offset + limit])

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

        Raises:
            CancelledError: Propagated unchanged if the awaiting
                caller cancels the lock acquisition.
        """
        async with self._lock:
            if str(item.id) not in self._items and self._repo is not None:
                existing = await self._repo.get(str(item.id))
                if existing is not None:
                    self._items[str(existing.id)] = existing
            if str(item.id) not in self._items:
                logger.warning(
                    API_RESOURCE_NOT_FOUND,
                    resource="approval",
                    approval_id=str(item.id),
                )
                return None
            if str(item.id) in self._saves_in_flight:
                logger.warning(
                    API_APPROVAL_CONFLICT,
                    error="concurrent_save",
                    approval_id=str(item.id),
                )
                return None
            self._saves_in_flight.add(str(item.id))
            # Capture the generation under the lock so a ``clear``
            # that lands during ``repo.save`` can be detected when
            # we re-acquire the lock to repopulate the cache.
            captured_generation = self._generation
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
                    await asyncio.shield(self._invalidate_cache(str(item.id)))
                    raise
            async with self._lock:
                if self._generation != captured_generation:
                    # ``clear`` ran during the repo write -- do NOT
                    # repopulate the cache, otherwise this save
                    # would silently undo the clear. The repo commit
                    # already landed, so a subsequent ``get`` will
                    # fall through to the repo and observe it. The
                    # save itself succeeded: returning ``None`` here
                    # would misreport a durable write as a not-found
                    # / dedup-skip and confuse callers that branch
                    # on the return value (the conventional contract
                    # is None == "no such id" / "concurrent dedup
                    # claimed it"). Return ``item`` so the caller
                    # sees the persisted state.
                    #
                    # Defensive eviction: if some sibling re-cached an
                    # entry under this id between ``clear`` and our
                    # arrival here, drop it so the next ``get``
                    # / ``list_items`` falls through to the repo and
                    # observes the post-clear truth instead of a
                    # stale cached copy this save did not write.
                    self._items.pop(str(item.id), None)
                    logger.info(
                        API_APPROVAL_STORE_CLEARED,
                        note="save_aborted_by_concurrent_clear",
                        approval_id=str(item.id),
                    )
                    return item
                self._items[str(item.id)] = item
            return item
        finally:
            async with self._lock:
                self._saves_in_flight.discard(str(item.id))

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

        Raises:
            CancelledError: Propagated unchanged if the awaiting
                caller cancels the lock acquisition.
        """
        async with self._lock:
            # Mirror the FWW guard from ``save()``: ``save()`` releases
            # ``self._lock`` while it awaits the repo write, so a
            # naive ``save_if_pending()`` entering that window would
            # see the stale cached ``PENDING`` item and persist a
            # second decision, reopening the lost-update race.
            # Abort early so the caller can retry once the in-flight
            # save finishes.
            if str(item.id) in self._saves_in_flight:
                logger.warning(
                    API_APPROVAL_CONFLICT,
                    error="concurrent_save",
                    approval_id=str(item.id),
                )
                return None
            current = self._items.get(str(item.id))
            if current is None and self._repo is not None:
                current = await self._repo.get(str(item.id))
                if current is not None:
                    self._items[str(current.id)] = current
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
                    self._items.pop(str(item.id), None)
                    raise
            self._items[str(item.id)] = item
            return item

    async def consume_if_approved(
        self,
        approval_id: NotBlankStr,
    ) -> ApprovalItem | None:
        """Atomically mark an APPROVED one-shot grant as consumed.

        Stamps ``consumed_at`` (read through the store clock) iff the
        approval is currently APPROVED and not already consumed, so a
        single grant authorises exactly one action. The authoritative
        compare-and-set runs in the repository when one is configured;
        the in-memory cache is updated only after the CAS wins.

        Args:
            approval_id: The approval id to consume.

        Returns:
            The consumed item on success, or ``None`` when the approval
            is missing, not APPROVED, already consumed, or the CAS lost a
            concurrent race.
        """
        async with self._lock:
            current = self._items.get(approval_id)
            if current is None and self._repo is not None:
                current = await self._repo.get(approval_id)
                if current is not None:
                    self._items[str(current.id)] = current
            if current is None:
                return None
            current = await self._check_expiration_locked(current)
            if (
                current.status != ApprovalStatus.APPROVED
                or current.consumed_at is not None
            ):
                return None
            consumed_at = self._clock.now()
            if self._repo is not None:
                won = await self._repo.consume_if_approved(
                    approval_id,
                    consumed_at=consumed_at,
                )
                if not won:
                    # The backend rejected the CAS (concurrent consume or
                    # state drift); drop the stale cache entry so the next
                    # reader reloads committed truth.
                    self._items.pop(approval_id, None)
                    return None
            consumed = current.model_copy(update={"consumed_at": consumed_at})
            self._items[approval_id] = consumed
            return consumed

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
