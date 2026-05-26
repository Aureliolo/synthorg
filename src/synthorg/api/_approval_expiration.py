"""Lazy-expiration behaviour for :class:`ApprovalStore`.

The PENDING -> EXPIRED lazy transition (scalar ``_check_expiration_locked``,
the pure batch ``_compute_expiration`` / ``_compute_page`` companions, the
cache-only list path, and the best-effort expire callback) is a cohesive
slice of the store. It lives in its own mixin so the main store module
stays focused on the CRUD + CAS + cache-coherency concurrency model.

The mixin reaches back into the host store for shared state (``_clock``,
``_repo``, ``_items``, ``_on_expire``); the ``TYPE_CHECKING`` block below
declares that surface so ``mypy`` type-checks the mixin in isolation.
"""

from typing import TYPE_CHECKING

from synthorg.core.approval import ApprovalItem  # noqa: TC001
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.api import (
    API_APPROVAL_EXPIRE_CALLBACK_FAILED,
    API_APPROVAL_EXPIRED,
)
from synthorg.observability.events.approval_gate import (
    APPROVAL_STATUS_TRANSITIONED,
)
from synthorg.observability.metrics_hub import record_approval_decision

if TYPE_CHECKING:
    from collections.abc import Callable

    from synthorg.core.clock import Clock
    from synthorg.core.types import NotBlankStr
    from synthorg.persistence.approval_protocol import ApprovalRepository

logger = get_logger(__name__)


class ApprovalExpirationMixin:
    """Lazy-expiration methods mixed into :class:`ApprovalStore`."""

    if TYPE_CHECKING:
        _clock: Clock
        _repo: ApprovalRepository | None
        _items: dict[str, ApprovalItem]
        _on_expire: Callable[[ApprovalItem], None] | None

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
            page_cache[item.id] = checked
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
            checked_items.append(checked)
        return tuple(checked_items)

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
            transitioned = await self._repo.expire_if_pending((item.id,))
            if item.id not in transitioned:
                # CAS lost: a concurrent APPROVED / REJECTED decision
                # landed first. Reload committed state so the cache and
                # the returned item reflect the decision that actually
                # won, never a stale EXPIRED overwrite.
                current = await self._repo.get(item.id)
                if current is not None:
                    self._items[item.id] = current
                    return current
                self._items.pop(item.id, None)
                return item
        self._items[item.id] = expired
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
            approval_id=item.id,
            from_status=ApprovalStatus.PENDING.value,
            to_status=ApprovalStatus.EXPIRED.value,
        )
        logger.info(
            API_APPROVAL_EXPIRED,
            approval_id=item.id,
        )
        record_approval_decision(outcome="expired")
        if self._on_expire is not None:
            try:
                self._on_expire(expired)
            except Exception as exc:
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
                    approval_id=item.id,
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
        except Exception as exc:
            reraise_critical(exc)
            # ERROR rather than WARNING: the approval is already
            # EXPIRED in cache + repo, so the callback can't
            # propagate, but a failed downstream side effect (webhook,
            # audit dispatch, workflow resume) is operationally
            # meaningful and operators must be able to alert on it.
            log_exception_redacted(
                logger, API_APPROVAL_EXPIRE_CALLBACK_FAILED, exc, approval_id=expired.id
            )
