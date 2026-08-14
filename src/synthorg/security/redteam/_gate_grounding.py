# module-kind: code
"""The gate's grounding arm: run the checker, adapt its claims to findings.

Split out of :mod:`gate` so that module carries orchestration rather than
one arm's I/O. The gate wants both halves of the answer at once (the raw
claims travel out on the result for the audit trail, the findings merge
into the verdict), so one call returns both and the two can never
disagree about which claims were seen.
"""

import asyncio
from typing import NamedTuple

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.redteam_review_input import RedTeamReviewInput
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.red_team import (
    RED_TEAM_GROUNDING_CHECK_COMPLETED,
    RED_TEAM_GROUNDING_CHECK_FAILED,
    RED_TEAM_GROUNDING_CHECK_STARTED,
)
from synthorg.security.redteam._grounding_findings import claim_to_finding
from synthorg.security.redteam.grounding.models import UngroundedClaim
from synthorg.security.redteam.grounding.protocol import GroundingChecker
from synthorg.security.redteam.models import RedTeamFinding

logger = get_logger(__name__)


class GroundingOutcome(NamedTuple):
    """What one grounding pass produced.

    Attributes:
        claims: Every claim the checker returned, verbatim, for the audit
            trail.
        findings: The subset that became findings. Smaller than *claims*
            whenever a substrate claim fell below the drop floor.
    """

    claims: tuple[UngroundedClaim, ...]
    findings: tuple[RedTeamFinding, ...]


_EMPTY_OUTCOME: GroundingOutcome = GroundingOutcome(claims=(), findings=())


async def collect_grounding(
    checker: GroundingChecker,
    review_input: RedTeamReviewInput,
) -> GroundingOutcome:
    """Run the grounding checker and adapt what it found.

    Cancellation propagates: ``asyncio.CancelledError`` is re-raised so
    the awaiting parent task observes it. All other exceptions are
    treated as fail-OPEN (the grounding checker is best-effort and
    should not block the gate on transient corpus or provider failures).

    Args:
        checker: The configured checker, heuristic or substrate-backed.
        review_input: The deliverable under scrutiny.

    Returns:
        The claims and the findings they became, both empty on a
        non-cancellation failure (fail-OPEN).

    Raises:
        asyncio.CancelledError: Propagated when the grounding check is
            cancelled.
    """
    logger.info(
        RED_TEAM_GROUNDING_CHECK_STARTED,
        execution_id=review_input.execution_id,
        task_id=review_input.task_id,
    )
    try:
        claims = await checker.check(
            deliverable_content=review_input.deliverable_content,
            execution_id=review_input.execution_id,
            project_id=review_input.project_id,
            task_id=review_input.task_id,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            RED_TEAM_GROUNDING_CHECK_FAILED,
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            policy="fail_open",
        )
        return _EMPTY_OUTCOME
    logger.info(
        RED_TEAM_GROUNDING_CHECK_COMPLETED,
        execution_id=review_input.execution_id,
        task_id=review_input.task_id,
        claims=len(claims),
    )
    return GroundingOutcome(
        claims=claims,
        findings=tuple(
            finding
            for claim in claims
            if (finding := claim_to_finding(claim)) is not None
        ),
    )
