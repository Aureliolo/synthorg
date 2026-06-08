# module-kind: complex_service
"""Autonomy promotion workflow: request log, approval enqueue, mutation.

Owns the multi-step workflow distinct from the registry's CRUD
surface. A request flows through: audit log -> conditional
approval-store enqueue -> (if strategy-granted) atomic
read-and-apply under the registry lock -> best-effort dual-write of
the APPROVED audit row.

The workflow holds the registry + approval_store as constructor
dependencies; it uses three small public registry helpers
(``snapshot_current_autonomy_level`` for the read-only PENDING path,
``apply_autonomy_update_atomic`` for the GRANTED path's combined
read-and-apply under one lock, and the standalone
``apply_autonomy_level`` for callers that already know the prior
level) to read and write under the registry's own lock, so its
mutations serialise with CRUD writes without reaching into registry
internals.
"""

import uuid
from typing import TYPE_CHECKING

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.security import (
    SECURITY_AUTONOMY_PROMOTION_AUDIT_FAILED,
    SECURITY_AUTONOMY_PROMOTION_DENIED,
    SECURITY_AUTONOMY_PROMOTION_GRANTED,
    SECURITY_AUTONOMY_PROMOTION_REQUESTED,
)

if TYPE_CHECKING:
    from datetime import datetime

    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.core.types import NotBlankStr
    from synthorg.hr.registry import AgentRegistryService
    from synthorg.security.autonomy.models import (
        AutonomyUpdate,
        AutonomyUpdateResult,
    )

logger = get_logger(__name__)


class AutonomyWorkflow:
    """Run the autonomy-promotion request flow against the agent registry.

    Constructor-injected with the registry and an optional approval
    store. The flow logs the request, enqueues a PENDING approval item
    when ``approval_store`` is wired, applies the level change
    immediately when ``update.granted_by_strategy`` is set, and
    best-effort dual-writes an APPROVED audit row on the granted path.

    Args:
        registry: Agent registry holding the identity to update.
        approval_store: Optional approval store. When ``None`` the
            workflow still runs (the request is logged + the response
            describes the outcome) but no approval row is persisted.
            Required for any deployment that wants the human-approval
            queue to drive autonomy promotions; absent in pure-test
            harnesses that only need the audit log and result envelope.
        clock: Optional clock seam. Approval-row timestamps come from
            ``clock.now()`` so tests can advance virtual time via a
            ``FakeClock``; defaults to ``SystemClock`` for production.
    """

    __slots__ = ("_approval_store", "_clock", "_registry")

    def __init__(
        self,
        registry: AgentRegistryService,
        *,
        approval_store: ApprovalStoreProtocol | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._registry = registry
        self._approval_store = approval_store
        self._clock = clock if clock is not None else SystemClock()

    async def request(
        self,
        agent_id: NotBlankStr,
        update: AutonomyUpdate,
    ) -> AutonomyUpdateResult:
        """Submit an autonomy change request and return the outcome.

        Returns:
            Result of type ``AutonomyUpdateResult``.

        Raises:
            AgentNotFoundError: If the agent is not registered.
        """
        key = str(agent_id)
        current_level = await self._registry.snapshot_current_autonomy_level(agent_id)

        logger.info(
            SECURITY_AUTONOMY_PROMOTION_REQUESTED,
            agent_id=key,
            requested_level=update.requested_level.value,
            current_level=current_level.value,
            reason=update.reason,
            requested_by=update.requested_by,
        )

        granted = update.granted_by_strategy is not None
        now = self._clock.now()
        approval_id = str(uuid.uuid4())
        requested_by = update.requested_by or "system"
        base_metadata = {
            "agent_id": key,
            "current_level": current_level.value,
            "requested_level": update.requested_level.value,
        }
        title = (
            f"Autonomy change for {key}: "
            f"{current_level.value} -> {update.requested_level.value}"
        )

        if not granted:
            return await self._handle_pending(
                key=key,
                update=update,
                approval_id=approval_id,
                now=now,
                title=title,
                requested_by=requested_by,
                base_metadata=base_metadata,
                current_level=current_level,
            )

        return await self._handle_granted(
            agent_id=agent_id,
            key=key,
            update=update,
            approval_id=approval_id,
            now=now,
            title=title,
            requested_by=requested_by,
            base_metadata=base_metadata,
        )

    async def _handle_pending(  # noqa: PLR0913
        self,
        *,
        key: str,
        update: AutonomyUpdate,
        approval_id: str,
        now: datetime,
        title: str,
        requested_by: str,
        base_metadata: dict[str, str],
        current_level: object,
    ) -> AutonomyUpdateResult:
        """HUMAN_ONLY path: enqueue PENDING approval, defer mutation.

        Nothing mutates the agent's identity until a human decides. A
        PENDING row is non-terminal, so persisting it before any
        mutation is the designed behaviour, not a false audit.

        Returns:
            Result of type ``AutonomyUpdateResult``.
        """
        from synthorg.core.approval import (  # noqa: PLC0415
            ApprovalItem as _ApprovalItem,
        )
        from synthorg.security.autonomy.models import (  # noqa: PLC0415
            AutonomyUpdateResult,
        )

        approval_enqueued = False
        if self._approval_store is not None:
            await self._approval_store.add(
                _ApprovalItem(
                    id=uuid.UUID(approval_id),
                    action_type="autonomy:promote",
                    title=title,
                    description=update.reason,
                    requested_by=requested_by,
                    risk_level=ApprovalRiskLevel.HIGH,
                    status=ApprovalStatus.PENDING,
                    created_at=now,
                    metadata=base_metadata,
                ),
            )
            approval_enqueued = True
        logger.info(
            SECURITY_AUTONOMY_PROMOTION_DENIED,
            agent_id=key,
            requested_level=update.requested_level.value,
            reason="Autonomy level changes require human approval",
        )
        return AutonomyUpdateResult(
            agent_id=key,
            current_level=current_level,  # type: ignore[arg-type]
            requested_level=update.requested_level,
            promotion_pending=True,
            approval_enqueued=approval_enqueued,
            approval_id=approval_id if approval_enqueued else None,
        )

    async def _handle_granted(  # noqa: PLR0913
        self,
        *,
        agent_id: NotBlankStr,
        key: str,
        update: AutonomyUpdate,
        approval_id: str,
        now: datetime,
        title: str,
        requested_by: str,
        base_metadata: dict[str, str],
    ) -> AutonomyUpdateResult:
        """Strategy-granted path: atomic mutation + dual-write audit row.

        Reads the prior autonomy level and applies the new one inside
        a single registry-lock acquisition so the APPROVED audit row
        and ``SECURITY_AUTONOMY_PROMOTION_GRANTED`` log stamp the
        actual pre-mutation level, never a stale snapshot read before
        the lock. The mutation is the source of truth; the APPROVED
        audit row is a best-effort artefact (failure logs but does
        not roll back the mutation, since the run-time change has
        already taken effect).

        Returns:
            Result of type ``AutonomyUpdateResult``.
        """
        from synthorg.core.approval import (  # noqa: PLC0415
            ApprovalItem as _ApprovalItem,
        )
        from synthorg.security.autonomy.models import (  # noqa: PLC0415
            AutonomyUpdateResult,
        )

        # Atomic read-and-apply under the registry lock. The returned
        # previous_level reflects what was actually in the registry the
        # instant the mutation landed, so the audit metadata + GRANTED
        # log cannot describe a level that a concurrent writer
        # already overwrote.
        previous_level, _applied = await self._registry.apply_autonomy_update_atomic(
            agent_id,
            update.requested_level,
            saved_by=f"autonomy_strategy_grant:{key}",
        )
        granted_metadata = {
            **base_metadata,
            "current_level": previous_level.value,
            "granted_by_strategy": str(update.granted_by_strategy),
        }

        approval_enqueued = False
        if self._approval_store is not None:
            try:
                await self._approval_store.add(
                    _ApprovalItem(
                        id=uuid.UUID(approval_id),
                        action_type="autonomy:promote",
                        title=title,
                        description=update.reason,
                        requested_by=requested_by,
                        risk_level=ApprovalRiskLevel.HIGH,
                        # Auto-decided: the queue stays the apply driver
                        # and the audit trail is intact. ``decided_at``
                        # / ``decided_by`` satisfy the APPROVED
                        # invariant.
                        status=ApprovalStatus.APPROVED,
                        created_at=now,
                        decided_at=now,
                        decided_by=f"strategy:{update.granted_by_strategy}",
                        metadata=granted_metadata,
                    ),
                )
                approval_enqueued = True
            except Exception as exc:
                reraise_critical(exc)
                log_exception_redacted(
                    logger,
                    SECURITY_AUTONOMY_PROMOTION_AUDIT_FAILED,
                    exc,
                    agent_id=key,
                    approval_id=approval_id,
                    note=(
                        "autonomy promotion applied; audit row write "
                        "failed; promotion is the source of truth"
                    ),
                )
        result_id = approval_id if approval_enqueued else None
        # State transition logged AFTER the persistence write.
        logger.info(
            SECURITY_AUTONOMY_PROMOTION_GRANTED,
            agent_id=key,
            previous_level=previous_level.value,
            requested_level=update.requested_level.value,
            granted_by_strategy=str(update.granted_by_strategy),
            approval_id=result_id,
        )
        return AutonomyUpdateResult(
            agent_id=key,
            current_level=update.requested_level,
            requested_level=update.requested_level,
            promotion_pending=False,
            approval_enqueued=approval_enqueued,
            approval_id=result_id,
        )
