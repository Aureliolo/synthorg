"""Decision-recording subsystem for :class:`ReviewGateService`.

Appends an auditable ``DecisionRecord`` to the drop-box after a review
transition. Best-effort: a failed append is logged but never propagates
(the transition has already happened), while programming errors surface
loudly so schema drift is caught in dev/CI.
"""

import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from synthorg.core.enums import DecisionOutcome
from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_DECISION_RECORD_FAILED,
)
from synthorg.observability.events.security import (
    SECURITY_APPROVAL_DECISION_RECORDED,
)
from synthorg.observability.events.versioning import VERSION_FETCH_FAILED

if TYPE_CHECKING:
    from synthorg.core.task import Task

logger = get_logger(__name__)


class ReviewGateRecordMixin:
    """Decision-record drop-box append for the review gate service."""

    # Populated on the concrete ``ReviewGateService``; typed ``Any``
    # because the mixin only reads it. The concrete class carries the
    # authoritative type.
    _persistence: Any

    async def _record_decision(
        self,
        *,
        task: Task,
        decided_by: str,
        approved: bool,
        reason: str | None,
        approval_id: str | None,
    ) -> None:
        """Append a decision record to the drop-box (best-effort).

        Uses ``append_with_next_version`` so version assignment happens
        atomically in SQL -- no TOCTOU race across concurrent reviewers.

        The transition has already happened at this point, so a failed
        append is logged but does not propagate.  Only ``QueryError``
        and ``DuplicateRecordError`` are non-fatal; programming errors
        propagate loudly so schema drift surfaces in dev/CI.
        """
        if self._persistence is None:
            logger.warning(
                APPROVAL_GATE_DECISION_RECORD_FAILED,
                task_id=str(task.id),
                decided_by=decided_by,
                approved=approved,
                error_type="NoPersistence",
                error=(
                    "Decision recording skipped: no persistence backend "
                    "configured on ReviewGateService"
                ),
            )
            return

        if task.assigned_to is None:
            logger.error(
                APPROVAL_GATE_DECISION_RECORD_FAILED,
                task_id=str(task.id),
                decided_by=decided_by,
                approved=approved,
                error_type="UnassignedExecutor",
                error=(
                    "Cannot record decision: task reached review gate "
                    "without an assigned executor"
                ),
            )
            return

        decision = DecisionOutcome.APPROVED if approved else DecisionOutcome.REJECTED
        criteria = self._dedupe_criteria(task)
        executor = task.assigned_to
        metadata = await self._fetch_charter_metadata(executor)
        await self._append_decision(
            task_id=str(task.id),
            executing_agent_id=executor,
            decided_by=decided_by,
            approved=approved,
            approval_id=approval_id,
            decision=decision,
            reason=reason,
            criteria_snapshot=criteria,
            metadata=metadata,
        )

    @staticmethod
    def _dedupe_criteria(task: Task) -> tuple[str, ...]:
        """Dedupe acceptance criteria descriptions preserving order.

        ``DecisionRecord.criteria_snapshot`` rejects duplicates via
        its unique-strings validator; without deduping a task with
        repeated criteria would raise ``ValidationError``.

        Returns:
            Tuple of acceptance-criteria descriptions in their first
            occurrence order, with empty entries dropped.
        """
        seen: set[str] = set()
        result: list[str] = []
        for c in task.acceptance_criteria:
            stripped = c.description.strip()
            if stripped and stripped not in seen:
                seen.add(stripped)
                result.append(stripped)
        return tuple(result)

    async def _fetch_charter_metadata(
        self,
        agent_id: str,
    ) -> dict[str, object] | None:
        """Look up the latest charter version for decision metadata.

        Returns a metadata dict on success, a failure-flag dict on
        ``QueryError``, or ``None`` if no version exists.

        Returns:
            Mapping of charter metadata fields when a version exists;
            a failure-flag mapping when persistence raised; ``None``
            when no charter version is recorded for the agent.
        """
        persistence = self._persistence
        assert persistence is not None  # noqa: S101  # caller checks
        try:
            latest = await persistence.identity_versions.get_latest_version(
                agent_id,
            )
        except QueryError as exc:
            logger.warning(
                VERSION_FETCH_FAILED,
                entity_id=agent_id,
                context="charter_version_lookup",
                error=safe_error_description(exc),
                error_type=type(exc).__name__,
            )
            return {"charter_version_lookup_failed": True}
        if latest is None:
            return None
        return {
            "charter_version": {
                "agent_id": latest.entity_id,
                "version": latest.version,
                "content_hash": latest.content_hash,
            }
        }

    async def _append_decision(  # noqa: PLR0913
        self,
        *,
        task_id: str,
        executing_agent_id: str,
        decided_by: str,
        approved: bool,
        approval_id: str | None,
        decision: DecisionOutcome,
        reason: str | None,
        criteria_snapshot: tuple[str, ...],
        metadata: dict[str, object] | None,
    ) -> None:
        """Append the decision record (best-effort, non-fatal on persistence errors)."""
        persistence = self._persistence
        assert persistence is not None  # noqa: S101  # caller checks
        try:
            record = await persistence.decision_records.append_with_next_version(
                record_id=str(uuid.uuid4()),
                task_id=task_id,
                approval_id=approval_id,
                executing_agent_id=executing_agent_id,
                reviewer_agent_id=decided_by,
                decision=decision,
                reason=reason,
                criteria_snapshot=criteria_snapshot,
                recorded_at=datetime.now(UTC),
                metadata=metadata,
            )
            logger.info(
                SECURITY_APPROVAL_DECISION_RECORDED,
                task_id=task_id,
                decision=record.decision.value,
                version=record.version,
            )
        except (QueryError, DuplicateRecordError) as exc:
            logger.warning(
                APPROVAL_GATE_DECISION_RECORD_FAILED,
                task_id=task_id,
                decided_by=decided_by,
                approved=approved,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
