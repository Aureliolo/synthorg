"""Durable A/B-test record sink, status mapping, and write helper.

Extracted from :mod:`synthorg.meta.rollout.ab_test` so the rollout
strategy stays focused on the observation loop. Holds the narrow write
seam (:class:`AbTestRecordSink`), the rollout-outcome -> record-status
mapping, and the best-effort ``persist_ab_test_record`` that builds and
writes a record without ever sinking the rollout itself.
"""

from datetime import datetime
from typing import Final, Protocol, runtime_checkable
from uuid import UUID

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.models import RolloutOutcome
from synthorg.meta.rollout.ab_models import (
    AbTestArm,
    AbTestRecord,
    AbTestStatus,
    ABTestVerdict,
    GroupAssignment,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import META_ABTEST_RECORD_WRITE_FAILED

logger = get_logger(__name__)

#: Maps a terminal rollout outcome to its durable record status.
TERMINAL_STATUS: Final[dict[RolloutOutcome, AbTestStatus]] = {
    RolloutOutcome.SUCCESS: AbTestStatus.COMPLETED,
    RolloutOutcome.REGRESSED: AbTestStatus.REGRESSED,
    RolloutOutcome.INCONCLUSIVE: AbTestStatus.INCONCLUSIVE,
    RolloutOutcome.FAILED: AbTestStatus.FAILED,
}


@runtime_checkable
class AbTestRecordSink(Protocol):
    """Narrow write seam for persisting durable A/B-test records.

    The durable ``AbTestRepository`` satisfies this structurally via its
    ``save`` upsert; the rollout depends only on this minimal surface so
    it stays decoupled from the persistence layer.
    """

    async def save(self, entity: AbTestRecord, /) -> None:
        """Upsert an A/B-test record keyed by proposal id."""
        ...


async def persist_ab_test_record(  # noqa: PLR0913
    sink: AbTestRecordSink,
    *,
    proposal_id: UUID,
    proposal_title: NotBlankStr,
    assignment: GroupAssignment,
    status: AbTestStatus,
    verdict: ABTestVerdict | None,
    observation_hours_elapsed: float,
    now: datetime,
) -> None:
    """Best-effort durable write of an A/B-test rollout record.

    The ``save`` upsert preserves the first ``created_at`` so a running
    record is replaced in place by its terminal verdict. A write failure
    is logged and swallowed so persistence never sinks the rollout.
    """
    record = AbTestRecord(
        id=NotBlankStr(str(proposal_id)),
        name=proposal_title,
        status=status,
        arms=(
            AbTestArm(
                name=NotBlankStr("control"),
                agent_count=len(assignment.control_agent_ids),
                fraction=assignment.control_fraction,
            ),
            AbTestArm(
                name=NotBlankStr("treatment"),
                agent_count=len(assignment.treatment_agent_ids),
                fraction=1.0 - assignment.control_fraction,
            ),
        ),
        verdict=verdict,
        observation_hours_elapsed=observation_hours_elapsed,
        created_at=now,
        updated_at=now,
    )
    try:
        await sink.save(record)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised below
        reraise_critical(exc)
        logger.warning(
            META_ABTEST_RECORD_WRITE_FAILED,
            proposal_id=str(proposal_id),
            status=status.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


__all__ = ["TERMINAL_STATUS", "AbTestRecordSink", "persist_ab_test_record"]
