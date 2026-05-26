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
from collections.abc import Callable  # noqa: TC003
from typing import TYPE_CHECKING

from synthorg.api._approval_expiration import ApprovalExpirationMixin
from synthorg.core.approval import ApprovalItem  # noqa: TC001
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ConflictError
from synthorg.core.enums import (
    ApprovalRiskLevel,
    ApprovalStatus,
)
from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APPROVAL_CONFLICT,
    API_APPROVAL_EXPIRE_BATCH_FAILED,
    API_APPROVAL_EXPIRED,
    API_APPROVAL_STORE_CLEARED,
    API_RESOURCE_NOT_FOUND,
)
from synthorg.observability.events.approval_gate import (
    APPROVAL_STATUS_TRANSITIONED,
)
from synthorg.observability.metrics_hub import record_approval_decision
from synthorg.persistence.approval_protocol import ApprovalFilterSpec

if TYPE_CHECKING:
    from synthorg.persistence.approval_protocol import ApprovalRepository

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
        on_expire: Optional callback for expired items.
        repo: Optional durable repository for persistence
            (protocol-typed; backend-agnostic).
    """

    def __init__(
        self,
        *,
        on_expire: Callable[[ApprovalItem], None] | None = None,
        repo: ApprovalRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._items: dict[str, ApprovalItem] = {}
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

        Returns:
            ``True`` or ``False`` reflecting the condition.
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
        ``clear`` instead.
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
        if self._repo is not None:
            return await self._list_from_repo(
                status=status,
                risk_level=risk_level,
                action_type=action_type,
            )
        async with self._lock:
            return await self._list_from_cache_locked(
                status=status,
                risk_level=risk_level,
                action_type=action_type,
            )

    async def _list_from_repo(
        self,
        *,
        status: ApprovalStatus | None,
        risk_level: ApprovalRiskLevel | None,
        action_type: NotBlankStr | None,
    ) -> tuple[ApprovalItem, ...]:
        """Repo-backed list path with batched expiry persistence.

        Per-page chunked so the store lock is held only for short
        cache-mutation critical sections, never across repo I/O,
        ``save_many``, or callback dispatch. A long unbounded scan
        cannot stall concurrent ``get()`` / ``save()`` callers that
        serialize on the same lock.

        Per-page protocol: read page (no lock) -> compute expirations
        (pure, no lock) -> ``expire_if_pending`` (no lock; compare-and-
        set so concurrent saves can't be clobbered) -> brief lock for
        cache update -> emit audit events + fire callbacks (no lock).
        Each page is independent so a failure on one page does not
        leave a half-applied state on a later page.

        Generation guard: captures ``self._generation`` under the lock
        before any repo I/O, then skips the cache-update step on a
        per-page basis if the captured generation no longer matches
        ``self._generation`` (i.e. a concurrent ``clear()`` landed
        between the capture and the cache write). Without this guard
        an in-flight scan could repopulate ``_items`` after a clear
        finished, undoing the post-clear empty-cache invariant the
        ``save()`` path already protects via the same generation check.

        Cache refresh scope: every row in a fetched page is written
        into ``_items`` (not just the EXPIRED transitions), so a
        non-expired sibling whose authoritative repo state has drifted
        from the cache still gets refreshed. Otherwise a stale cached
        copy could survive a repo read and leak into a later ``get()``
        / ``save_if_pending()`` decision.

        Status filtering:

        * When ``status`` is ``EXPIRED``, ``PENDING``, or ``None``,
          the repo query omits the status filter. ``PENDING`` cannot
          be pushed down because :meth:`_compute_page` flips PENDING
          rows to EXPIRED between pages; a repo-side ``status=pending``
          filter would shrink the result set under the iterator, so
          ``offset += 100`` would skip rows that were still PENDING
          when the previous page was read but should remain visible
          to the caller. ``EXPIRED`` also stays unfiltered so PENDING
          rows that should lazily flip to EXPIRED surface and get
          persisted.
        * When ``status`` is any other terminal value (APPROVED,
          REJECTED, CANCELLED), the repo authoritatively persists that
          status and lazy expiration cannot promote into it -- the
          filter is pushed down so the DB only returns matching rows.

        Side effects after each per-page batch save:

        * Emits one ``APPROVAL_STATUS_TRANSITIONED`` + one
          ``API_APPROVAL_EXPIRED`` audit event per newly-expired item.
        * Fires the optional ``on_expire`` callback for each item via
          :meth:`_fire_expire_callback` (best-effort; failures are
          logged at ERROR but do not unwind the expiration).

        Returns:
            Tuple of the declared element types.
        """
        assert self._repo is not None  # noqa: S101 -- caller invariant
        # Capture generation under the lock before any repo I/O so a
        # concurrent ``clear()`` landing mid-scan can be detected and
        # prevent a post-clear cache resurrection. Mirrors the same
        # guard ``save()`` already applies.
        async with self._lock:
            captured_generation = self._generation
        # Push the status filter down only for terminal non-EXPIRED
        # queries (APPROVED / REJECTED / CANCELLED). PENDING cannot
        # be pushed down because the per-page expiration flip removes
        # rows from the filtered set as the iterator advances --
        # ``offset += 100`` would then skip PENDING rows that should
        # have been visible. EXPIRED also stays unfiltered so the
        # lazy-expire pass can promote the PENDING rows.
        repo_status = (
            None
            if status in {None, ApprovalStatus.PENDING, ApprovalStatus.EXPIRED}
            else status
        )
        page_size = 100
        result: list[ApprovalItem] = []
        offset = 0
        # lint-allow: long-running-loop-kill-switch -- bounded paginated scan
        # (breaks on empty page below); one-shot drain, not a service loop.
        while True:
            # Repo I/O outside the store lock so concurrent get() /
            # save() callers are never blocked by a long scan.
            filter_spec = ApprovalFilterSpec(
                status=repo_status,
                risk_level=risk_level,
                action_type=action_type,
            )
            page = await self._repo.query(
                filter_spec,
                limit=page_size,
                offset=offset,
            )
            if not page:
                break
            page_result, to_persist, page_cache = self._compute_page(
                page,
                status=status,
                risk_level=risk_level,
            )
            actually_expired_ids: set[str] = set()
            if to_persist:
                # Compare-and-set at the repo boundary: only flip rows
                # still PENDING. A concurrent save() that landed a
                # newer terminal status (APPROVED / REJECTED /
                # CANCELLED) between our page read and this call wins
                # the race; ``expire_if_pending`` returns only the ids
                # that actually transitioned, so audit events,
                # callbacks, and cache writes don't fire for rows we
                # never persisted.
                try:
                    actually_expired_ids = set(
                        await self._repo.expire_if_pending(
                            tuple(item.id for item in to_persist),
                        ),
                    )
                except Exception as exc:
                    reraise_critical(exc)
                    # Log the attempted ids before re-raising so a
                    # production failure on the batched expiry path
                    # is diagnosable -- otherwise the caller sees the
                    # ``QueryError`` and has no record of which lazy
                    # expirations were attempted.
                    logger.warning(
                        API_APPROVAL_EXPIRE_BATCH_FAILED,
                        batch_size=len(to_persist),
                        approval_ids=tuple(item.id for item in to_persist),
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                    raise
            # Lost-race rows: rows we tried to flip but the repo
            # already had a newer terminal status. Refetch them so
            # the response reflects the authoritative state instead
            # of either our stale EXPIRED guess or silent omission
            # (an unfiltered ``list_items()`` must not under-report
            # rows just because a concurrent save() raced with the
            # expire pass). Apply the caller's filters to each
            # refetched row; rows where the repo returns ``None``
            # (deleted between page read and refetch) drop out.
            attempted_ids = {item.id for item in to_persist}
            lost_race_ids = attempted_ids - actually_expired_ids
            # Single batch fetch instead of one ``get`` per id; under
            # heavy contention the lost-race set can be large, and a
            # per-id loop turns into N+1 round-trips.
            refetched_batch = await self._repo.get_many(
                tuple(NotBlankStr(lost_id) for lost_id in lost_race_ids)
            )
            refetched_rows: list[ApprovalItem] = [
                item
                for item in refetched_batch
                if (status is None or item.status == status)
                and (risk_level is None or item.risk_level == risk_level)
                and (action_type is None or item.action_type == action_type)
            ]
            # Refresh the entire page slice in the cache (not just the
            # EXPIRED transitions) so stale non-expired siblings can't
            # outlive a fresh repo read; refetched lost-race rows
            # land alongside so subsequent ``get()`` returns the
            # authoritative state. Generation guard: a concurrent
            # ``clear()`` between the I/O and this critical section
            # bumps ``_generation``; skip the cache write so the
            # post-clear empty-cache invariant survives.
            async with self._lock:
                if self._generation == captured_generation:
                    for item_id, cached in page_cache.items():
                        if item_id in lost_race_ids:
                            # Stale local guess; either the refetch
                            # below provides the authoritative copy
                            # (and overwrites this slot a few lines
                            # down) or the row no longer matches
                            # filters and we evict so the next
                            # ``get()`` refetches.
                            self._items.pop(item_id, None)
                        else:
                            self._items[item_id] = cached
                    for refetched in refetched_rows:
                        self._items[refetched.id] = refetched
            for expired in to_persist:
                if expired.id not in actually_expired_ids:
                    continue
                logger.info(
                    APPROVAL_STATUS_TRANSITIONED,
                    approval_id=expired.id,
                    from_status=ApprovalStatus.PENDING.value,
                    to_status=ApprovalStatus.EXPIRED.value,
                )
                logger.info(API_APPROVAL_EXPIRED, approval_id=expired.id)
                record_approval_decision(outcome="expired")
                self._fire_expire_callback(expired)
            result.extend(item for item in page_result if item.id not in lost_race_ids)
            result.extend(refetched_rows)
            if len(page) < page_size:
                break
            offset += page_size
        return tuple(result)

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
                    await asyncio.shield(self._invalidate_cache(item.id))
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
                    self._items.pop(item.id, None)
                    logger.info(
                        API_APPROVAL_STORE_CLEARED,
                        note="save_aborted_by_concurrent_clear",
                        approval_id=item.id,
                    )
                    return item
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
                    self._items[current.id] = current
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
