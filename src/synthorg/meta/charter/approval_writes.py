# module-kind: code
"""The two writes an approval makes to the charter row, and what gates them.

They are separate on purpose. The first records the operator's decision and
runs BEFORE the dispatch it authorises, because the work pipeline verifies the
charter it is handed and all it can do is read the row. The second names the
run once the spine has minted one. Between them sits a charter that is
authorised with nothing behind it, which is a state the approve path resumes
rather than one that should be unrepresentable.
"""

from datetime import datetime

from synthorg.core.types import NotBlankStr
from synthorg.meta.charter.enums import CharterStatus
from synthorg.meta.charter.models import ProjectCharter
from synthorg.meta.errors import (
    CharterAlreadyDecidedError,
    CharterStateInconsistentError,
)
from synthorg.observability import get_logger
from synthorg.observability.events.charter import (
    CHARTER_ALREADY_DECIDED,
    CHARTER_APPROVED,
    CHARTER_STATE_INCONSISTENT,
    CHARTER_STATUS_TRANSITIONED,
)
from synthorg.persistence.charter_protocol import CharterRepository

logger = get_logger(__name__)


def require_dispatchable(charter: ProjectCharter) -> None:
    """Reject a charter this call cannot legitimately dispatch.

    DRAFTED is the ordinary case. APPROVED with no ``task_id`` is the one an
    operator authorised and whose dispatch did not land (the pipeline raised,
    or the process died between the two writes), so approving again resumes it
    rather than refusing: their decision is already on the row, and the work
    they asked for has not run. An APPROVED charter that names a run, and any
    other status, is decided.

    Raises:
        CharterAlreadyDecidedError: When the charter has already run, or was
            cancelled.
    """
    resumable = charter.status is CharterStatus.APPROVED and charter.task_id is None
    if charter.status is CharterStatus.DRAFTED or resumable:
        return
    logger.warning(
        CHARTER_ALREADY_DECIDED,
        charter_id=charter.id,
        status=charter.status.value,
        error_type=CharterAlreadyDecidedError.__name__,
    )
    raise CharterAlreadyDecidedError(charter_id=charter.id)


async def record_approval(
    charter_repo: CharterRepository,
    charter: ProjectCharter,
    *,
    forecast_id: object,
    project_id: NotBlankStr,
    approved_by: NotBlankStr,
    now: datetime,
) -> None:
    """CAS the charter to APPROVED, recording the operator's decision.

    Stamps ``project_id`` (the project the run was filed under, existing or
    freshly created) and clears ``proposed_project_name`` so the charter row
    records the project it became and the existing-vs-new XOR still holds
    after approval. The run this authorises does not exist yet;
    :func:`stamp_dispatched` names it once it does.

    Raises:
        CharterAlreadyDecidedError: When a concurrent decider won the CAS.
    """
    transitioned = await charter_repo.transition_if(
        charter.id,
        from_state=CharterStatus.DRAFTED,
        to_state=CharterStatus.APPROVED,
        updated_at=now,
        approved_at=now,
        approved_by=approved_by,
        forecast_id=forecast_id,
        correlation_id=charter.conversation_id,
        project_id=project_id,
    )
    if not transitioned:
        # A concurrent decider already moved the charter. Nothing has been
        # dispatched yet, so this call simply loses.
        logger.warning(
            CHARTER_ALREADY_DECIDED,
            charter_id=charter.id,
            reason="cas_decision_lost",
            error_type=CharterAlreadyDecidedError.__name__,
        )
        raise CharterAlreadyDecidedError(charter_id=charter.id)
    logger.info(CHARTER_APPROVED, charter_id=charter.id, approved_by=approved_by)
    # Emit the generic status-transition event too (the CAS write above
    # succeeded), so a charter approval appears in the
    # ``charter.status_transitioned`` stream like cancellations do.
    # CHARTER_APPROVED stays for the dispatch-specific observers.
    logger.info(
        CHARTER_STATUS_TRANSITIONED,
        charter_id=charter.id,
        from_state=CharterStatus.DRAFTED.value,
        to_state=CharterStatus.APPROVED.value,
        decided_by=approved_by,
    )


async def stamp_dispatched(
    charter_repo: CharterRepository,
    charter: ProjectCharter,
    *,
    task_id: NotBlankStr,
    now: datetime,
) -> None:
    """Name the run the approval authorised, once the spine minted it.

    A same-state CAS rather than a plain write: it is conditional on the
    charter still being APPROVED, so a cancellation racing the dispatch does
    not have a run id written back onto it.

    Raises:
        CharterStateInconsistentError: When the charter left APPROVED while
            its own dispatch was in flight, so the run that ran is recorded
            nowhere on the charter that asked for it.
    """
    stamped = await charter_repo.transition_if(
        charter.id,
        from_state=CharterStatus.APPROVED,
        to_state=CharterStatus.APPROVED,
        updated_at=now,
        task_id=task_id,
    )
    if not stamped:
        logger.error(
            CHARTER_STATE_INCONSISTENT,
            charter_id=charter.id,
            stage="stamp_dispatched",
            task_id=task_id,
            reason="charter left APPROVED while its dispatch was in flight",
        )
        raise CharterStateInconsistentError(charter_id=charter.id)


__all__ = ["record_approval", "require_dispatchable", "stamp_dispatched"]
