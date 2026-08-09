"""Attach the durable approval repository once persistence is connected.

The approval store is constructed in the synchronous boot phase, before
the backend is connected, so it can only be handed its repository here.
Until this ran, every shipped deployment kept the operator's decision
queue in memory alone: a restart between an approval being raised and a
human deciding it destroyed the queue outright, leaving each plan parked
at ``PENDING_REVIEW`` with nothing left to approve and no route to
re-create the decision.

Degrades rather than raising. An unknown or unreachable backend leaves
the store in memory, which is the behaviour that shipped, so a boot that
cannot offer durability still boots and says so.
"""

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.state import AppState
from synthorg.approval.state import approval_store_of
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.approval_factory import build_approval_repo
from synthorg.persistence.protocol import PersistenceBackend

logger = get_logger(__name__)


def wire_durable_approvals(
    app_state: AppState,
    persistence: PersistenceBackend | None,
) -> None:
    """Give the approval store its durable repository.

    A substituted store (a test double, or an operator-injected one) owns
    its own persistence and is left alone, matching how the construction
    phase treats an injected store.

    Args:
        app_state: Application state carrying the approval store.
        persistence: The connected persistence backend, or ``None``.
    """
    store = approval_store_of(app_state)
    if not isinstance(store, ApprovalStore):
        logger.info(
            API_APP_STARTUP,
            service="approval_store",
            note="substituted store owns its own persistence; not attaching",
            store_type=type(store).__name__,
        )
        return
    repo = build_approval_repo(persistence)
    if repo is None:
        logger.warning(
            API_APP_STARTUP,
            service="approval_store",
            note=(
                "no durable approval repository; pending decisions will not "
                "survive a restart"
            ),
        )
        return
    store.attach_repo(repo)
    logger.info(
        API_APP_STARTUP,
        service="approval_store",
        note="durable approval repository attached",
    )


__all__ = ["wire_durable_approvals"]
