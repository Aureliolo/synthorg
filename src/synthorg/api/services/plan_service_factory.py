# module-kind: code
"""The single construction site for :class:`PlanService`.

Six call sites built the service by hand, each naming the repositories it
remembered. That is exactly how a plan status write ends up recorded in one
caller's path and not another's, so the ledger looks incomplete without any
one place being wrong. Building it here means a new collaborator reaches
every writer at once.
"""

from synthorg.api.services.plan_service import PlanService
from synthorg.core.clock import Clock
from synthorg.persistence.protocol import PersistenceBackend


def build_plan_service(persistence: PersistenceBackend, *, clock: Clock) -> PlanService:
    """Construct the plan service bound to *persistence*.

    Args:
        persistence: The connected backend supplying the plan store and the
            lifecycle-transition ledger.
        clock: Time seam for ``updated_at`` and ``occurred_at`` stamps.

    Returns:
        A plan service whose status writes reach the durable ledger.
    """
    return PlanService(
        repo=persistence.plans,
        clock=clock,
        transitions=persistence.lifecycle_transitions,
    )


__all__ = ["build_plan_service"]
