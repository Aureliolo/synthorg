"""Routes a ``/meta/chat`` question by which scoping id is present.

Sibling of ``meta.py``: keeps the routing decision (dedicated alert
explanation vs. proposal-scoped free-form vs. plain free-form) out of
the controller handler so that module stays under its size-budget tier.

``alert_id`` routes to :meth:`ChiefOfStaffChat.explain_alert` when it
resolves to a persisted alert. ``proposal_id`` cannot route to
:meth:`ChiefOfStaffChat.explain_proposal`: a full ``ImprovementProposal``
is not reconstructable from the approval-queue item a proposal survives
into (rationale / rollback plan / change tuples don't survive the
park), so a resolved item's title/description/metadata are instead
folded into the free-form answer via ``ask(..., scoped_proposal=...)``.
A stale/unresolvable id, or no id at all, falls back to (or stays on)
the plain free-form path. Alert takes priority when both ids are set.
"""

from synthorg.api.state import AppState
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.persistence_errors import PersistenceError
from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.chat import ChiefOfStaffChat
from synthorg.meta.chief_of_staff.models import ChatQuery, ChatResponse
from synthorg.meta.chief_of_staff.org_state import OrgStateSnapshot
from synthorg.meta.signal_models import OrgSignalSnapshot
from synthorg.meta.state import alert_repo_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import (
    META_CHAT_DEPENDENCY_UNAVAILABLE,
    META_CHAT_SCOPE_NOT_FOUND,
)

logger = get_logger(__name__)


async def resolve_chat_answer(
    app_state: AppState,
    chat_backend: ChiefOfStaffChat,
    query: ChatQuery,
    snapshot: OrgSignalSnapshot,
    org_state: OrgStateSnapshot | None,
) -> ChatResponse:
    """Answer a chat question, routing by whichever scoping id is set.

    The free-form paths (plain and proposal-scoped) ground the answer in
    the real ``org_state`` read model; the dedicated alert-explain path
    stays scoped to the alert's own signal context.

    Returns:
        The chat response from whichever path was taken.
    """
    if query.alert_id is not None:
        alert_repo = alert_repo_of(app_state)
        if alert_repo is None:
            logger.warning(
                META_CHAT_DEPENDENCY_UNAVAILABLE,
                dependency="alert_repo",
                scope="alert_id",
            )
        else:
            try:
                alert = await alert_repo.get_by_id(query.alert_id)
            except PersistenceError as exc:
                logger.warning(
                    META_CHAT_DEPENDENCY_UNAVAILABLE,
                    dependency="alert_repo",
                    scope="alert_id",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
            else:
                if alert is not None:
                    return await chat_backend.explain_alert(alert, snapshot)
                logger.warning(
                    META_CHAT_SCOPE_NOT_FOUND,
                    scope="alert_id",
                    value=str(query.alert_id),
                )
        return await chat_backend.ask(query, snapshot, org_state=org_state)

    if query.proposal_id is not None:
        store = app_state.slice(ApprovalStateSlice).store
        if store is None:
            logger.warning(
                META_CHAT_DEPENDENCY_UNAVAILABLE,
                dependency="approval_store",
                scope="proposal_id",
            )
        else:
            try:
                item = await store.get(NotBlankStr(str(query.proposal_id)))
            except PersistenceError as exc:
                logger.warning(
                    META_CHAT_DEPENDENCY_UNAVAILABLE,
                    dependency="approval_store",
                    scope="proposal_id",
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
            else:
                if item is not None:
                    return await chat_backend.ask(
                        query, snapshot, scoped_proposal=item, org_state=org_state
                    )
                logger.warning(
                    META_CHAT_SCOPE_NOT_FOUND,
                    scope="proposal_id",
                    value=str(query.proposal_id),
                )
        return await chat_backend.ask(query, snapshot, org_state=org_state)

    return await chat_backend.ask(query, snapshot, org_state=org_state)


def chat_answer_payload(result: ChatResponse) -> dict[str, object]:
    """Flatten a chat response into the wire body shared by both paths.

    Used by the buffered ``/meta/chat`` response and the streaming
    ``complete`` frame so the two never drift on the field set.

    Returns:
        The ``answer`` / ``sources`` / ``cited_records`` / ``confidence`` dict.
    """
    return {
        "answer": result.answer,
        "sources": list(result.sources),
        "cited_records": [r.model_dump(mode="json") for r in result.cited_records],
        "confidence": result.confidence,
    }


__all__ = ["chat_answer_payload", "resolve_chat_answer"]
