"""Durable-repository binding for :class:`ApprovalStore`.

Whether the store has a durable half, and the one seam that gives it one
after the fact, are a cohesive slice: the store is constructed during the
synchronous boot phase, before any backend is connected, so the repository
can only ever be offered later. It lives in its own mixin so the main store
module stays focused on the CRUD + CAS + cache-coherency concurrency model.

The mixin reaches back into the host store for ``_repo``; the class-body
annotation declares that surface so ``mypy`` type-checks the mixin in
isolation.
"""

from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_APPROVAL_REPO_ATTACH_REFUSED,
    API_APPROVAL_REPO_ATTACHED,
)
from synthorg.persistence.approval_protocol import ApprovalRepository

logger = get_logger(__name__)


class ApprovalDurabilityMixin:
    """The store's durable half: whether it has one, and how it gets one."""

    _repo: ApprovalRepository | None

    @property
    def has_persistent_repo(self) -> bool:
        """``True`` iff a durable :class:`ApprovalRepository` is wired.

        Used by startup wiring to detect backend combinations where
        conversational-intake approvals cannot be durably persisted.
        Callers should refuse proposer wiring for unsupported persistence
        modes.
        """
        return self._repo is not None

    def attach_repo(self, repo: ApprovalRepository | None) -> None:
        """Bind the durable repository once persistence is connected.

        Without this seam an approval lives only in memory: a restart
        between it being raised and an operator deciding it destroys the
        queue and strands every plan waiting on one at ``pending_review``,
        with no route to re-create the decision.

        Check-then-set without a lock, and safe only while binding happens
        once during startup, before the store serves a request. Two
        concurrent callers could both read ``None`` and the second's warning
        would never fire; the refusals below are the audit trail for a
        mis-sequenced boot, not a concurrency guard.

        Additive, like the provider registry's credential catalog. A
        ``None`` over an already-bound repository is refused rather than
        silently unbinding a working one, because the only reason to pass
        ``None`` is that the caller has nothing to offer, and losing
        durability that way is exactly the failure this seam exists to end.
        Re-binding a different repository is refused for the same reason:
        the queue would split across two stores.

        Reads are already read-through (``get`` and ``list_items`` fall back
        to the repo on a cache miss), so attaching is all a restart needs to
        see the pending queue again.

        Args:
            repo: The durable repository to bind, or ``None`` when the
                caller has none to offer.
        """
        if repo is None:
            if self._repo is not None:
                logger.warning(
                    API_APPROVAL_REPO_ATTACH_REFUSED,
                    reason="would_unbind_live_repo",
                )
            return
        if self._repo is not None:
            logger.warning(
                API_APPROVAL_REPO_ATTACH_REFUSED,
                reason="already_bound",
            )
            return
        self._repo = repo
        logger.info(API_APPROVAL_REPO_ATTACHED)
