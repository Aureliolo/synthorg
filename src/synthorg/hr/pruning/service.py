"""Pruning service -- performance-driven agent removal with human approval.

Periodically evaluates active agents against pruning policies, creates
approval items for eligible candidates, and delegates to OffboardingService
once human approval is granted.

Note:
    ``_pending_requests`` and ``_processed_approval_ids`` are in-memory
    only. If the service restarts, already-decided approvals may be
    reprocessed.  The ``pruning_request_id`` stored in approval metadata
    mitigates data loss for the audit trail, but full durability requires
    a persistent backend (planned).
"""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import FiringReason
from synthorg.hr.models import FiringRequest
from synthorg.hr.pruning.models import (
    PruningEvaluation,
    PruningJobRun,
    PruningRecord,
    PruningRequest,
    PruningServiceConfig,
)
from synthorg.observability import get_logger
from synthorg.observability.events.hr import (
    HR_PRUNING_AGENT_ELIGIBLE,
    HR_PRUNING_APPROVAL_DEDUP_SKIP,
    HR_PRUNING_APPROVAL_SUBMITTED,
    HR_PRUNING_APPROVED,
    HR_PRUNING_CYCLE_COMPLETE,
    HR_PRUNING_CYCLE_STARTED,
    HR_PRUNING_OFFBOARDED,
    HR_PRUNING_POLICY_ERROR,
    HR_PRUNING_REJECTED,
    HR_PRUNING_SCHEDULER_STARTED,
    HR_PRUNING_SCHEDULER_STOPPED,
    PRUNING_REQUEST_STATUS_TRANSITIONED,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.core.agent import AgentIdentity
    from synthorg.hr.models import OffboardingRecord
    from synthorg.hr.offboarding_service import OffboardingService
    from synthorg.hr.performance.tracker import PerformanceTracker
    from synthorg.hr.pruning.policy import PruningPolicy
    from synthorg.hr.registry import AgentRegistryService

logger = get_logger(__name__)

_ACTION_TYPE = "hr:prune"


class PruningService:
    """Orchestrates performance-driven agent pruning with human approval.

    Args:
        policies: Pruning policy strategies to evaluate.
        registry: Agent registry for listing active agents.
        tracker: Performance tracker for snapshots.
        approval_store: Approval store for human decisions.
        offboarding_service: Service to delegate offboarding to.
        config: Pruning service configuration.
        on_notification: Optional callback for completion notifications.
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        policies: tuple[PruningPolicy, ...],
        registry: AgentRegistryService,
        tracker: PerformanceTracker,
        approval_store: ApprovalStoreProtocol,
        offboarding_service: OffboardingService,
        config: PruningServiceConfig | None = None,
        on_notification: (Callable[[PruningRecord], Awaitable[None]] | None) = None,
    ) -> None:
        self._policies = policies
        self._registry = registry
        self._tracker = tracker
        self._approval_store = approval_store
        self._offboarding_service = offboarding_service
        self._config = config or PruningServiceConfig()
        self._on_notification = on_notification
        self._task: asyncio.Task[None] | None = None
        self._wake_event = asyncio.Event()
        self._pending_requests: dict[str, PruningRequest] = {}
        self._completed: list[PruningRecord] = []
        self._processed_approval_ids: set[str] = set()
        # Approval ids currently being handled by a cycle.  Two
        # concurrent ``_process_decided_approvals`` cycles MUST NOT
        # both enter ``_handle_approved`` / ``_handle_rejected`` for
        # the same id; the in-flight set closes that window without
        # serialising the slow offboarding I/O path.  Transient
        # failures (e.g. offboarding returns ``None``) leave the id
        # out of ``_processed_approval_ids`` so the next cycle
        # retries; absence from the processed set is intentional on
        # those paths, not an oversight.
        self._in_flight_approvals: set[str] = set()
        self._processing_lock = asyncio.Lock()

    @property
    def is_running(self) -> bool:
        """Whether the scheduler loop is currently active."""
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        """Start the background pruning scheduler."""
        if self.is_running:
            return
        self._wake_event.clear()
        self._task = asyncio.create_task(
            self._run_loop(),
            name="pruning-scheduler",
        )
        logger.info(HR_PRUNING_SCHEDULER_STARTED)

    async def stop(self) -> None:
        """Stop the background scheduler gracefully."""
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None
        logger.info(HR_PRUNING_SCHEDULER_STOPPED)

    def wake(self) -> None:
        """Trigger an early pruning cycle."""
        self._wake_event.set()

    async def run_pruning_cycle(
        self,
        *,
        now: datetime | None = None,
    ) -> PruningJobRun:
        """Execute a single pruning evaluation cycle.

        Args:
            now: Override for current time (testing).

        Returns:
            Job run metadata with cycle statistics.
        """
        if now is None:
            now = datetime.now(UTC)

        cycle_start = datetime.now(UTC)
        job_id = NotBlankStr(str(uuid4()))
        logger.info(HR_PRUNING_CYCLE_STARTED, job_id=str(job_id))

        errors: list[NotBlankStr] = []
        await self._process_decided_approvals()

        active_agents = await self._registry.list_active()
        eligible = await self._evaluate_all(active_agents, now, errors)
        approvals = await self._submit_approvals(eligible, now, errors)

        elapsed = (datetime.now(UTC) - cycle_start).total_seconds()
        job_run = PruningJobRun(
            job_id=job_id,
            run_at=now,
            agents_evaluated=len(active_agents),
            agents_eligible=len(eligible),
            approval_requests_created=approvals,
            elapsed_seconds=elapsed,
            errors=tuple(errors),
        )

        logger.info(
            HR_PRUNING_CYCLE_COMPLETE,
            job_id=str(job_id),
            agents_evaluated=len(active_agents),
            agents_eligible=len(eligible),
            approvals_created=approvals,
            elapsed_seconds=elapsed,
        )
        return job_run

    # ── Evaluation ────────────────────────────────────────────

    async def _evaluate_all(
        self,
        agents: tuple[AgentIdentity, ...],
        now: datetime,
        errors: list[NotBlankStr],
    ) -> list[tuple[AgentIdentity, PruningEvaluation]]:
        """Evaluate all agents against policies, collecting errors."""
        eligible: list[tuple[AgentIdentity, PruningEvaluation]] = []
        for agent in agents:
            try:
                evaluation = await self._evaluate_agent(
                    NotBlankStr(str(agent.id)),
                    now=now,
                )
                if evaluation.eligible:
                    eligible.append((agent, evaluation))
                    logger.info(
                        HR_PRUNING_AGENT_ELIGIBLE,
                        agent_id=str(agent.id),
                        policy=str(evaluation.policy_name),
                    )
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                errors.append(NotBlankStr(f"{agent.id}: {exc}"))
                logger.warning(
                    HR_PRUNING_POLICY_ERROR,
                    agent_id=str(agent.id),
                    error=str(exc),
                )
        return eligible

    async def _evaluate_agent(
        self,
        agent_id: NotBlankStr,
        *,
        now: datetime,
    ) -> PruningEvaluation:
        """Evaluate a single agent against all policies.

        Returns the first eligible evaluation, or the last
        ineligible one.
        """
        snapshot = await self._tracker.get_snapshot(agent_id, now=now)

        last_evaluation = None
        for policy in self._policies:
            evaluation = await policy.evaluate(agent_id, snapshot)
            if evaluation.eligible:
                return evaluation
            last_evaluation = evaluation

        if last_evaluation is not None:
            return last_evaluation

        return PruningEvaluation(
            agent_id=agent_id,
            eligible=False,
            reasons=(),
            scores={},
            policy_name=NotBlankStr("none"),
            snapshot=snapshot,
            evaluated_at=now,
        )

    # ── Approval Submission ───────────────────────────────────

    async def _submit_approvals(
        self,
        eligible: list[tuple[AgentIdentity, PruningEvaluation]],
        now: datetime,
        errors: list[NotBlankStr],
    ) -> int:
        """Submit approval requests for eligible agents."""
        pending = await self._approval_store.list_items(
            action_type=_ACTION_TYPE,
            status=ApprovalStatus.PENDING,
        )
        pending_agent_ids = {item.metadata.get("agent_id") for item in pending}

        created = 0
        for agent, evaluation in eligible:
            if created >= self._config.max_approvals_per_cycle:
                break
            try:
                submitted = await self._submit_approval(
                    agent,
                    evaluation,
                    now,
                    pending_agent_ids,
                )
                if submitted:
                    created += 1
            except MemoryError, RecursionError:
                raise
            except Exception as exc:
                errors.append(NotBlankStr(f"approval {agent.id}: {exc}"))
                logger.warning(
                    HR_PRUNING_POLICY_ERROR,
                    agent_id=str(agent.id),
                    error=str(exc),
                )
        return created

    async def _submit_approval(
        self,
        agent: AgentIdentity,
        evaluation: PruningEvaluation,
        now: datetime,
        pending_agent_ids: set[str | None],
    ) -> bool:
        """Create an approval item for a pruning candidate.

        Args:
            agent: Agent identity.
            evaluation: Evaluation result for this agent.
            now: Cycle timestamp for temporal consistency.
            pending_agent_ids: Agent IDs with existing pending approvals.

        Returns:
            True if a new approval was created, False if deduped.
        """
        agent_id = str(agent.id)

        if agent_id in pending_agent_ids:
            logger.debug(
                HR_PRUNING_APPROVAL_DEDUP_SKIP,
                agent_id=agent_id,
            )
            return False

        approval_id = NotBlankStr(str(uuid4()))
        request_id = NotBlankStr(str(uuid4()))
        expires_at = now + timedelta(
            days=self._config.approval_expiry_days,
        )
        reason_summary = ", ".join(str(r) for r in evaluation.reasons)

        approval = self._build_approval_item(
            approval_id,
            agent,
            evaluation,
            reason_summary,
            now,
            expires_at,
            request_id,
        )
        await self._approval_store.add(approval)

        request = PruningRequest(
            id=request_id,
            agent_id=NotBlankStr(agent_id),
            agent_name=agent.name,
            evaluation=evaluation,
            approval_id=approval_id,
            status=ApprovalStatus.PENDING,
            created_at=now,
        )
        self._pending_requests[agent_id] = request
        pending_agent_ids.add(agent_id)

        # State-transition log fires AFTER the request is built and
        # registered. ``model_validator`` raises (deep-copy,
        # type narrowing) skip these logs so the audit trail only
        # records transitions that actually landed.
        logger.info(
            PRUNING_REQUEST_STATUS_TRANSITIONED,
            request_id=request_id,
            agent_id=agent_id,
            approval_id=str(approval_id),
            from_status=None,
            to_status=ApprovalStatus.PENDING.value,
        )
        logger.info(
            HR_PRUNING_APPROVAL_SUBMITTED,
            agent_id=agent_id,
            approval_id=str(approval_id),
            policy=str(evaluation.policy_name),
        )
        return True

    @staticmethod
    def _build_approval_item(  # noqa: PLR0913
        approval_id: NotBlankStr,
        agent: AgentIdentity,
        evaluation: PruningEvaluation,
        reason_summary: str,
        now: datetime,
        expires_at: datetime,
        request_id: NotBlankStr,
    ) -> ApprovalItem:
        """Build an approval item for a pruning candidate."""
        return ApprovalItem(
            id=approval_id,
            action_type=NotBlankStr(_ACTION_TYPE),
            title=NotBlankStr(f"Prune agent {agent.name}"),
            description=NotBlankStr(
                f"Policy {evaluation.policy_name}: {reason_summary}"
                if reason_summary
                else f"Policy {evaluation.policy_name}: eligible for pruning"
            ),
            requested_by=NotBlankStr("system"),
            risk_level=ApprovalRiskLevel.CRITICAL,
            status=ApprovalStatus.PENDING,
            created_at=now,
            expires_at=expires_at,
            metadata={
                "agent_id": str(agent.id),
                "policy_name": str(evaluation.policy_name),
                "reason_summary": reason_summary,
                "pruning_request_id": str(request_id),
            },
        )

    # ── Approval Processing ───────────────────────────────────

    async def _process_decided_approvals(self) -> None:
        """Poll for decided approvals and process them."""
        approved_items = await self._approval_store.list_items(
            action_type=_ACTION_TYPE,
            status=ApprovalStatus.APPROVED,
        )
        for item in approved_items:
            if not await self._try_claim(str(item.id)):
                continue
            try:
                await self._handle_approved(item)
            finally:
                await self._release_claim(str(item.id))

        rejected_items = await self._approval_store.list_items(
            action_type=_ACTION_TYPE,
            status=ApprovalStatus.REJECTED,
        )
        for item in rejected_items:
            if not await self._try_claim(str(item.id)):
                continue
            try:
                self._handle_rejected(item)
            finally:
                await self._release_claim(str(item.id))

    async def _try_claim(self, approval_id: str) -> bool:
        """Atomically claim an approval id for handling.

        Returns ``True`` if this caller won the claim, ``False`` if the
        id is already processed or currently in-flight in another
        cycle.  The claim must be released via ``_release_claim``
        regardless of handler outcome.
        """
        async with self._processing_lock:
            if approval_id in self._processed_approval_ids:
                return False
            if approval_id in self._in_flight_approvals:
                return False
            self._in_flight_approvals.add(approval_id)
            return True

    async def _release_claim(self, approval_id: str) -> None:
        """Release a previously claimed in-flight slot."""
        async with self._processing_lock:
            self._in_flight_approvals.discard(approval_id)

    async def _handle_approved(self, item: ApprovalItem) -> None:
        """Execute offboarding after approval."""
        agent_id = item.metadata.get("agent_id")
        if not agent_id:
            logger.error(
                HR_PRUNING_POLICY_ERROR,
                approval_id=str(item.id),
                error="Missing agent_id in approval metadata",
            )
            self._processed_approval_ids.add(str(item.id))
            return

        agent = await self._registry.get(NotBlankStr(agent_id))
        if agent is None:
            logger.warning(
                HR_PRUNING_POLICY_ERROR,
                agent_id=agent_id,
                approval_id=str(item.id),
                error="Agent not found in registry after approval",
            )
            self._pending_requests.pop(agent_id, None)
            self._processed_approval_ids.add(str(item.id))
            return

        logger.info(
            HR_PRUNING_APPROVED,
            agent_id=agent_id,
            approval_id=str(item.id),
        )

        result = await self._execute_offboarding(item, agent)
        if result is None:
            return

        await self._record_completion(item, agent, result)

    async def _execute_offboarding(
        self,
        item: ApprovalItem,
        agent: AgentIdentity,
    ) -> OffboardingRecord | None:
        """Delegate to OffboardingService. Returns result or None."""
        agent_id = str(agent.id)
        firing_request = FiringRequest(
            agent_id=NotBlankStr(agent_id),
            agent_name=agent.name,
            reason=FiringReason.PERFORMANCE,
            requested_by=NotBlankStr("pruning_service"),
            details=(
                f"Pruning approval {item.id}: "
                f"{item.metadata.get('reason_summary', 'performance-based')}"
            ),
            created_at=datetime.now(UTC),
        )

        try:
            return await self._offboarding_service.offboard(
                firing_request,
            )
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.exception(
                HR_PRUNING_POLICY_ERROR,
                agent_id=agent_id,
                approval_id=str(item.id),
                error="Offboarding failed after approval",
            )
            return None

    async def _record_completion(
        self,
        item: ApprovalItem,
        agent: AgentIdentity,
        offboarding_result: OffboardingRecord,
    ) -> None:
        """Create PruningRecord and notify after successful offboard."""
        agent_id = str(agent.id)
        pending_request = self._pending_requests.pop(agent_id, None)
        self._processed_approval_ids.add(str(item.id))

        if pending_request is not None:
            request_id = pending_request.id
        else:
            # Metadata is populated internally at approval-submission
            # time, but defend against corrupted / missing values so a
            # stale approval cannot crash ``_record_completion`` via
            # ``NotBlankStr("")``.
            metadata_request_id = item.metadata.get(
                "pruning_request_id",
                "",
            ).strip()
            request_id = NotBlankStr(metadata_request_id or "unknown")

        record = PruningRecord(
            agent_id=NotBlankStr(agent_id),
            agent_name=agent.name,
            pruning_request_id=request_id,
            firing_request_id=offboarding_result.firing_request_id,
            reason=NotBlankStr(
                item.metadata.get("reason_summary", "performance-based"),
            ),
            approval_id=item.id,
            initiated_by=NotBlankStr("system"),
            created_at=offboarding_result.started_at,
            completed_at=offboarding_result.completed_at,
        )
        self._completed.append(record)

        logger.info(
            HR_PRUNING_OFFBOARDED,
            agent_id=agent_id,
            approval_id=str(item.id),
        )

        callback = self._on_notification
        if callback is not None:
            try:
                await callback(record)
            except MemoryError, RecursionError:
                raise
            except Exception:
                logger.warning(
                    HR_PRUNING_POLICY_ERROR,
                    agent_id=agent_id,
                    error="notification callback failed",
                    exc_info=True,
                )

    def _handle_rejected(self, item: ApprovalItem) -> None:
        """Clean up after a rejected approval."""
        agent_id = item.metadata.get("agent_id")
        if not agent_id:
            logger.error(
                HR_PRUNING_POLICY_ERROR,
                approval_id=str(item.id),
                error="Missing agent_id in rejected approval metadata",
            )
            self._processed_approval_ids.add(str(item.id))
            return

        self._pending_requests.pop(agent_id, None)
        self._processed_approval_ids.add(str(item.id))
        logger.info(
            HR_PRUNING_REJECTED,
            agent_id=agent_id,
            approval_id=str(item.id),
            reason=str(item.decision_reason) if item.decision_reason else None,
        )

    # ── Scheduler Loop ────────────────────────────────────────

    async def _run_loop(self) -> None:
        """Sleep-and-check scheduler loop."""
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(
                    self._wake_event.wait(),
                    timeout=self._config.evaluation_interval_seconds,
                )
            self._wake_event.clear()
            try:
                await self.run_pruning_cycle()
            except MemoryError, RecursionError:
                raise
            except Exception:
                logger.exception(
                    HR_PRUNING_POLICY_ERROR,
                    error="Unexpected error in pruning scheduler loop",
                )
