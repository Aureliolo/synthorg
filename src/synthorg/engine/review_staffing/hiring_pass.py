# module-kind: code
"""Opening and finishing the hires a staffing gap asks for.

The sweep that finds a gap does not fill it: hiring is approval-gated, so the
most it can do is put one request in front of an operator and, on a later
pass, turn an approved request into a registered agent. Both halves live here
because both are about the hiring pipeline rather than about parked tasks, and
because "exactly one open request per role" is an invariant with two readers
otherwise.
"""

import asyncio

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import DomainError, ServiceUnavailableError
from synthorg.core.role_catalog import get_builtin_role
from synthorg.core.types import NotBlankStr
from synthorg.engine.review_staffing.notices import (
    DispatcherSource,
    hire_request_reason,
    notify_hire_waiting,
)
from synthorg.hr.errors import HRError
from synthorg.hr.hiring_service import HiringService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.review_staffing import (
    REVIEW_STAFFING_HIRE_ALREADY_OPEN,
    REVIEW_STAFFING_HIRE_COMPLETED,
    REVIEW_STAFFING_HIRE_COMPLETION_FAILED,
    REVIEW_STAFFING_HIRE_REQUEST_FAILED,
    REVIEW_STAFFING_HIRE_REQUESTED,
)

logger = get_logger(__name__)


async def finish_approved_hires(hiring: HiringService | None) -> int:
    """Instantiate every request a human approved but nobody hired.

    Args:
        hiring: The live hiring pipeline, or ``None`` on a boot without one.

    Returns:
        How many approved requests became registered agents.

    Raises:
        asyncio.CancelledError: Propagated so a stopping scheduler is not
            recorded as a hydration failure.
    """
    if hiring is None:
        return 0
    # Boot survives a hydration failure so the pipeline comes up degraded
    # rather than not at all, which leaves a request approved before the
    # restart invisible. Nothing else re-reads the durable set, so the sweep
    # is what makes that degradation temporary. A still-failing read is
    # reported and the pass continues on the in-memory set: a hydration fault
    # must not also cost the release half its cadence.
    try:
        await hiring.ensure_hydrated()
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- a durable read that is still failing must
        # not also cost the release half of the pass its cadence; the gap is
        # reported and the next pass retries it.
        reraise_critical(exc)
        logger.warning(
            REVIEW_STAFFING_HIRE_COMPLETION_FAILED,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="durable hiring requests still unread; retrying next pass",
        )
    completed = 0
    for request in hiring.find_approved_requests():
        try:
            identity = await hiring.instantiate_agent(request)
        except (HRError, ServiceUnavailableError) as exc:
            # Deliberately not fatal to the pass: one request blocked on its
            # own condition (an unbound new-hire pair) must not stop the
            # others, and the next pass retries this one anyway.
            logger.warning(
                REVIEW_STAFFING_HIRE_COMPLETION_FAILED,
                request_id=str(request.id),
                role=str(request.role),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            continue
        completed += 1
        logger.info(
            REVIEW_STAFFING_HIRE_COMPLETED,
            request_id=str(request.id),
            role=str(request.role),
            agent_id=str(identity.id),
        )
    return completed


async def ensure_hire_open(
    hiring: HiringService | None,
    role: str,
    *,
    notifications: DispatcherSource,
    actor: str,
) -> bool:
    """Keep exactly one approval-gated hire request open for *role*.

    Args:
        hiring: The live hiring pipeline, or ``None`` on a boot without one.
        role: The unstaffed role.
        notifications: Where the operator is told a hire is waiting.
        actor: Who the request is recorded as coming from.

    Returns:
        ``True`` when this pass opened a new request.
    """
    if hiring is None:
        return False
    if (existing := hiring.find_in_flight_request_for_role(role)) is not None:
        logger.info(
            REVIEW_STAFFING_HIRE_ALREADY_OPEN,
            role=role,
            request_id=str(existing.id),
            request_status=existing.status.value,
        )
        return False
    catalogued = get_builtin_role(role)
    if catalogued is None:
        logger.warning(
            REVIEW_STAFFING_HIRE_REQUEST_FAILED,
            role=role,
            error="role is not in the built-in catalog; cannot describe the hire",
        )
        return False
    try:
        request = await hiring.create_request(
            requested_by=NotBlankStr(actor),
            department=NotBlankStr(catalogued.department),
            role=NotBlankStr(catalogued.name),
            required_skills=tuple(NotBlankStr(s) for s in catalogued.required_skills),
            reason=NotBlankStr(hire_request_reason(catalogued.name)),
        )
        with_candidate = await hiring.generate_candidate(request)
        if not with_candidate.candidates:
            # Nothing to submit, so there is no hire to open. Indexing here
            # would raise IndexError out of a pass whose whole contract is to
            # report a failure and let the next pass retry.
            logger.warning(
                REVIEW_STAFFING_HIRE_REQUEST_FAILED,
                role=role,
                error="candidate generation produced nobody to put forward",
            )
            return False
        # The APPENDED one, not the first: generate_candidate re-reads the
        # request from the store before appending, so a stored request already
        # carrying a candidate would put an older one at index 0 and submit
        # that for approval instead of the one just built.
        submitted = await hiring.submit_for_approval(
            with_candidate, str(with_candidate.candidates[-1].id)
        )
    except DomainError as exc:
        # Deliberately the shared ancestor, not HRError: submitting the
        # request writes an approval item, so a durable-store refusal arrives
        # as ConflictError or ConstraintViolationError, which are HRError's
        # siblings rather than its subclasses. The gap stays visible in the
        # still-parked log and the next pass tries again.
        logger.warning(
            REVIEW_STAFFING_HIRE_REQUEST_FAILED,
            role=role,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return False
    logger.info(
        REVIEW_STAFFING_HIRE_REQUESTED,
        role=role,
        request_id=str(submitted.id),
        approval_id=submitted.approval_id,
    )
    await notify_hire_waiting(notifications, catalogued.name)
    return True


__all__ = ["ensure_hire_open", "finish_approved_hires"]
