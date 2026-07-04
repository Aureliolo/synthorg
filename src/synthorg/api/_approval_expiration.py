"""Lazy-expiration behaviour for :class:`ApprovalStore`.

The PENDING -> EXPIRED lazy transition (scalar ``_check_expiration_locked``,
the pure batch ``_compute_expiration`` / ``_compute_page`` companions, the
cache-only list path, the repo-backed list path with batched expiry
persistence, and the best-effort expire callback) is a cohesive slice of
the store. It lives in its own mixin so the main store module stays
focused on the CRUD + CAS + cache-coherency concurrency model.

The mixin reaches back into the host store for shared state (``_clock``,
``_repo``, ``_items``, ``_on_expire``, ``_lock``, ``_generation``); the
``TYPE_CHECKING`` block below declares that surface so ``mypy`` type-checks
the mixin in isolation.
"""

import asyncio
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.pagination import DEFAULT_LIST_LIMIT
from synthorg.core.types import NotBlankStr
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.api import (
    API_APPROVAL_EXPIRE_BATCH_FAILED,
    API_APPROVAL_EXPIRE_CALLBACK_FAILED,
    API_APPROVAL_EXPIRED,
)
from synthorg.observability.events.approval_gate import (
    APPROVAL_STATUS_TRANSITIONED,
)
from synthorg.observability.metrics_hub import record_approval_decision
from synthorg.persistence.approval_protocol import ApprovalFilterSpec

if TYPE_CHECKING:
    # Referenced only by the host-attribute annotations in the class-body
    # ``TYPE_CHECKING`` block below; the mixin never evaluates these names in a
    # runtime signature, so a module-level import would add eager coupling
    # without any typeguard benefit.
    from synthorg.core.clock import Clock
    from synthorg.persistence.approval_protocol import ApprovalRepository

logger = get_logger(__name__)


class ApprovalExpirationMixin:
    """Lazy-expiration methods mixed into :class:`ApprovalStore`."""

    if TYPE_CHECKING:
        _clock: Clock
        _repo: ApprovalRepository | None
        _items: dict[str, ApprovalItem]
        _on_expire: Callable[[ApprovalItem], None] | None
        _lock: asyncio.Lock
        _generation: int

    def _compute_page(
        self,
        page: tuple[ApprovalItem, ...],
        *,
        status: ApprovalStatus | None,
        risk_level: ApprovalRiskLevel | None,
    ) -> tuple[
        list[ApprovalItem],
        list[ApprovalItem],
        dict[str, ApprovalItem],
    ]:
        """Pure: classify a repo page into (filtered, to_persist, page_cache).

        Companion to :meth:`ApprovalStore._list_from_repo`. Walks ``page``
        once, computing lazy expiration via :meth:`_compute_expiration` and
        applying caller-supplied filters. No I/O, no lock acquisition.

        ``page_cache`` carries every row from the page (with the
        possibly-EXPIRED replacement substituted in) so the caller
        can refresh the entire page slice in ``_items``, not just the
        EXPIRED transitions. ``to_persist`` carries only the rows
        that flipped locally, which is the candidate set the caller
        feeds to ``expire_if_pending`` for the compare-and-set.

        Returns:
            Tuple of the declared element types.
        """
        page_result: list[ApprovalItem] = []
        to_persist: list[ApprovalItem] = []
        page_cache: dict[str, ApprovalItem] = {}
        for item in page:
            checked = self._compute_expiration(item)
            page_cache[str(item.id)] = checked
            if checked is not item:
                to_persist.append(checked)
            if status is not None and checked.status != status:
                continue
            if risk_level is not None and checked.risk_level != risk_level:
                continue
            page_result.append(checked)
        return page_result, to_persist, page_cache

    async def _list_from_cache_locked(
        self,
        *,
        status: ApprovalStatus | None,
        risk_level: ApprovalRiskLevel | None,
        action_type: NotBlankStr | None,
        created_since: datetime | None,
    ) -> tuple[ApprovalItem, ...]:
        """Cache-only list path (no repository wired).

        Falls through ``_check_expiration_locked`` per item because
        without a repository there is no batch endpoint to amortise;
        a per-item save is also a no-op (the in-memory cache is
        already updated by ``_check_expiration_locked``).

        Returns:
            Tuple of the declared element types.
        """
        checked_items: list[ApprovalItem] = []
        for stored in list(self._items.values()):
            checked = await self._check_expiration_locked(stored)
            if status is not None and checked.status != status:
                continue
            if risk_level is not None and checked.risk_level != risk_level:
                continue
            if action_type is not None and checked.action_type != action_type:
                continue
            if created_since is not None and checked.created_at < created_since:
                continue
            checked_items.append(checked)
        return tuple(checked_items)

    async def _list_from_repo(
        self,
        *,
        status: ApprovalStatus | None,
        risk_level: ApprovalRiskLevel | None,
        action_type: NotBlankStr | None,
        created_since: datetime | None,
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
        page_size = DEFAULT_LIST_LIMIT
        result: list[ApprovalItem] = []
        offset = 0
        # lint-allow: long-running-loop-kill-switch -- bounded paginated scan
        # (breaks on empty page below); one-shot drain, not a service loop.
        while True:
            # Repo I/O outside the store lock so concurrent get() /
            # save() callers are never blocked by a long scan.
            # ``created_since`` pushes down unconditionally: creation
            # time is immutable, so it cannot shrink the result set
            # under the iterator the way a PENDING status filter can.
            filter_spec = ApprovalFilterSpec(
                status=repo_status,
                risk_level=risk_level,
                action_types=(action_type,) if action_type is not None else None,
                created_since=created_since,
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
                            tuple(str(item.id) for item in to_persist),
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
                        approval_ids=tuple(str(item.id) for item in to_persist),
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
            attempted_ids = {str(item.id) for item in to_persist}
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
                and (created_since is None or item.created_at >= created_since)
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
                        self._items[str(refetched.id)] = refetched
            for expired in to_persist:
                if str(expired.id) not in actually_expired_ids:
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
            result.extend(i for i in page_result if str(i.id) not in lost_race_ids)
            result.extend(refetched_rows)
            if len(page) < page_size:
                break
            offset += page_size
        return tuple(result)

    async def _check_expiration_locked(
        self,
        item: ApprovalItem,
    ) -> ApprovalItem:
        """Lazy expiration, assuming ``self._lock`` is held.

        If the item is PENDING and has expired, transition it to
        EXPIRED in both the cache and the repository.  Callers MUST
        hold ``self._lock``; the method performs cache + repo mutations
        without re-acquiring it.

        When a repository is wired the flip goes through the
        ``expire_if_pending`` compare-and-set rather than a blind
        ``save``: the store lock is process-local, so another worker
        can decide the same row (APPROVED / REJECTED) between this
        instance's read and write. The CAS only transitions rows still
        in PENDING, so a concurrent terminal decision wins the race and
        this path reloads committed truth instead of clobbering it with
        EXPIRED.

        Args:
            item: The item to check.

        Returns:
            The original, expired, or repo-reloaded item.
        """
        if not (
            item.status == ApprovalStatus.PENDING
            and item.expires_at is not None
            and self._clock.now() >= item.expires_at
        ):
            return item
        expired = item.model_copy(
            update={"status": ApprovalStatus.EXPIRED},
        )
        if self._repo is not None:
            transitioned = await self._repo.expire_if_pending((str(item.id),))
            if str(item.id) not in transitioned:
                # CAS lost: a concurrent APPROVED / REJECTED decision
                # landed first. Reload committed state so the cache and
                # the returned item reflect the decision that actually
                # won, never a stale EXPIRED overwrite.
                current = await self._repo.get(str(item.id))
                if current is not None:
                    self._items[str(item.id)] = current
                    return current
                self._items.pop(str(item.id), None)
                return item
        self._items[str(item.id)] = expired
        # State-transition log fires AFTER persistence + cache
        # update succeed so the audit stream only records hops
        # that actually landed. Pairs with the
        # APPROVAL_STATUS_TRANSITIONED emissions on PENDING ->
        # APPROVED / REJECTED in ``api/controllers/approvals.py``;
        # ``API_APPROVAL_EXPIRED`` below is the terminal-state
        # summary event that subscribers can use as a single
        # signal that an approval has expired.
        logger.info(
            APPROVAL_STATUS_TRANSITIONED,
            approval_id=str(item.id),
            from_status=ApprovalStatus.PENDING.value,
            to_status=ApprovalStatus.EXPIRED.value,
        )
        logger.info(
            API_APPROVAL_EXPIRED,
            approval_id=str(item.id),
        )
        record_approval_decision(outcome="expired")
        if self._on_expire is not None:
            try:
                self._on_expire(expired)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                # ERROR (matching ``_fire_expire_callback``): the
                # approval is already EXPIRED in cache + repo, so
                # the callback failure can't unwind the expiration,
                # but a dropped downstream side effect (webhook,
                # audit dispatch, workflow resume) is operationally
                # meaningful and operators must be able to alert
                # on it. Both paths emit at ERROR so alerting is
                # not sensitive to which expiration path fired.
                log_exception_redacted(
                    logger,
                    API_APPROVAL_EXPIRE_CALLBACK_FAILED,
                    exc,
                    approval_id=str(item.id),
                )
        return expired

    def _compute_expiration(self, item: ApprovalItem) -> ApprovalItem:
        """Pure: return the (possibly-EXPIRED) item without I/O.

        Companion to ``_check_expiration_locked`` for the batch path
        in :meth:`ApprovalStore.list_items`. Returns the input unchanged
        when no transition applies, or a fresh EXPIRED copy otherwise.
        Persistence + audit logging + callback fire AFTER the batch
        save in the caller, not here -- this method must be safe to
        call inside a tight loop with no side effects.

        Returns:
            ``ApprovalItem`` instance.
        """
        if (
            item.status == ApprovalStatus.PENDING
            and item.expires_at is not None
            and self._clock.now() >= item.expires_at
        ):
            return item.model_copy(update={"status": ApprovalStatus.EXPIRED})
        return item

    def _fire_expire_callback(self, expired: ApprovalItem) -> None:
        """Best-effort fire of ``_on_expire`` for a batched expiration.

        Mirrors the callback handling in
        :meth:`_check_expiration_locked`: a callback failure must not
        unwind the expiration (the row is already EXPIRED in cache +
        repo); emit ``API_APPROVAL_EXPIRE_CALLBACK_FAILED`` so
        operators can filter callback failures from real expirations.
        """
        if self._on_expire is None:
            return
        try:
            self._on_expire(expired)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # ERROR rather than WARNING: the approval is already
            # EXPIRED in cache + repo, so the callback can't
            # propagate, but a failed downstream side effect (webhook,
            # audit dispatch, workflow resume) is operationally
            # meaningful and operators must be able to alert on it.
            log_exception_redacted(
                logger,
                API_APPROVAL_EXPIRE_CALLBACK_FAILED,
                exc,
                approval_id=str(expired.id),
            )
