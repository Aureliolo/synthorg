"""Charter approval to work-pipeline dispatch.

Turns an approved :class:`ProjectCharter` into a real project run: it
resolves (or creates) the project, persists an already-APPROVED cost
forecast as the budget record, records the operator's decision on the
charter row, builds the kickoff :class:`WorkItem` (carrying the forecast
id + hard ceiling), drives the work pipeline spine, and stamps the run it
produced back onto the charter.

The decision is written BEFORE the dispatch because the pipeline verifies
it: an item that stands up an initiative names its charter, and the spine
resolves that id and refuses anything not APPROVED. Which leaves the
window between the two writes, where a charter is authorised and no run
stands behind it. That is not a dead end: approving again resumes the
dispatch, because the operator's decision is already recorded and the work
they asked for has still not run.

This mirrors the conversational-intake dispatch seam: the work pipeline
is called directly (the charter approval IS the budget approval, so the
pre-flight ForecastGate is intentionally bypassed) and the forecast row
exists for audit and in-loop ceiling enforcement.
"""

import uuid
from collections.abc import Callable
from datetime import datetime

from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.budget.forecaster import BriefSignal, compute_brief_hash
from synthorg.communication.conversation.enums import ConversationStatus
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.concurrency import RefcountedLockMap
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task_enums import Complexity, Priority, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import ProjectNotFoundError
from synthorg.engine.pipeline.models import WorkItem, WorkSource
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.meta.charter.approval_writes import (
    record_approval,
    require_dispatchable,
    stamp_dispatched,
)
from synthorg.meta.charter.enums import CharterStatus
from synthorg.meta.charter.models import CharterApprovalResult, ProjectCharter
from synthorg.meta.errors import (
    CharterNotFoundError,
    CharterStateInconsistentError,
)
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.charter import (
    CHARTER_DISPATCH_FAILED,
    CHARTER_DISPATCH_UNSUCCESSFUL,
    CHARTER_DISPATCHED,
    CHARTER_NOT_FOUND,
    CHARTER_PROJECT_ALREADY_EXISTS,
    CHARTER_STATE_INCONSISTENT,
)
from synthorg.observability.events.chief_of_staff import (
    COS_CONVERSATION_STATUS_TRANSITIONED,
)
from synthorg.persistence.charter_protocol import CharterRepository
from synthorg.persistence.conversation_protocol import ConversationRepository
from synthorg.persistence.cost_forecast_protocol import CostForecastRepository
from synthorg.persistence.project_protocol import ProjectRepository

logger = get_logger(__name__)

_ORIGIN_ADAPTER_ID: NotBlankStr = NotBlankStr("charter-interview")
# Fixed namespace for deriving a deterministic, retry-stable forecast id
# from the (unique) charter id via uuid5, so a retried approval upserts
# one forecast rather than creating duplicates. The namespace is not a
# secret: only the charter id is hashed against it, and charter ids are
# already opaque uuid4s, so id collisions are bounded by charter id
# uniqueness. Rotating this constant would orphan in-flight forecasts;
# treat it as part of the persistence contract.
_FORECAST_NAMESPACE: uuid.UUID = uuid.UUID("6f1d4c2e-0000-4000-8000-000000000001")
# Public so callers and tests can reproduce the charter-to-project id
# derivation (``uuid5(PROJECT_NAMESPACE, f"charter-{charter_id}")``)
# without importing a module-private symbol.
PROJECT_NAMESPACE: uuid.UUID = uuid.UUID("6f1d4c2e-0000-4000-8000-000000000002")


def _charter_brief_signal(
    brief: str,
    currency: str,
    *,
    project: NotBlankStr,
    requested_by: NotBlankStr,
    correlation_id: NotBlankStr,
) -> BriefSignal:
    """Build the brief signal for the charter's forecast.

    Mirrors the work-entry signal shape ForecastGate uses (single
    ``"default"`` role placeholder, plus the project, requester and
    correlation id that scope the digest) so the forecast lines up if the
    same brief is later re-checked through the gate.

    Returns:
        ``BriefSignal`` instance.
    """
    return BriefSignal(
        brief_text=brief,
        project=project,
        requested_by=requested_by,
        correlation_id=correlation_id,
        role_skeleton=("default",),
        model_assignments={},
        currency=NotBlankStr(currency),
    )


def _render_intent(charter: ProjectCharter) -> NotBlankStr:
    """Fold the charter content into the work item's intent body.

    Returns:
        ``NotBlankStr`` instance.
    """
    lines: list[str] = [charter.brief]
    if charter.goals:
        lines.append("\nGoals:\n" + "\n".join(f"- {g}" for g in charter.goals))
    if charter.constraints:
        lines.append(
            "\nConstraints:\n" + "\n".join(f"- {c}" for c in charter.constraints)
        )
    if charter.scope.in_scope:
        lines.append(
            "\nIn scope:\n" + "\n".join(f"- {s}" for s in charter.scope.in_scope)
        )
    if charter.scope.out_of_scope:
        lines.append(
            "\nOut of scope:\n"
            + "\n".join(f"- {s}" for s in charter.scope.out_of_scope)
        )
    return NotBlankStr("\n".join(lines))


class CharterDispatcher:
    """Approve a charter and drive its project run through the spine.

    Args:
        charter_repo: Project charter store.
        forecast_repo: Cost forecast store (budget record of truth).
        project_repo: Project repository (resolve / create).
        work_pipeline: Callable returning the spine that is current now
            (``run`` per item); resolved per approval, because a runtime
            reload replaces the instance and nothing rebuilds this dispatcher
            when it does.
        conversation_repo: Conversation store (for closing the interview).
        budget_currency: Callable returning the live ``budget.currency``;
            the forecast currency must match it.
        clock: Injectable time source.
    """

    def __init__(
        self,
        *,
        charter_repo: CharterRepository,
        forecast_repo: CostForecastRepository,
        project_repo: ProjectRepository,
        work_pipeline: Callable[[], WorkPipeline],
        conversation_repo: ConversationRepository,
        budget_currency: Callable[[], str],
        clock: Clock | None = None,
    ) -> None:
        self._charter_repo = charter_repo
        self._forecast_repo = forecast_repo
        self._project_repo = project_repo
        # Annotated: an unannotated instance attribute holding a plain
        # function reads as a bound method to a type checker.
        self._work_pipeline: Callable[[], WorkPipeline] = work_pipeline
        self._conversation_repo = conversation_repo
        self._budget_currency = budget_currency
        self._clock: Clock = clock or SystemClock()
        self._charter_locks: RefcountedLockMap[str] = RefcountedLockMap()

    async def approve(
        self,
        charter_id: NotBlankStr,
        *,
        approved_by: NotBlankStr,
    ) -> CharterApprovalResult:
        """Approve a DRAFTED charter and dispatch its project run.

        Raises:
            CharterNotFoundError: When the id is unknown.
            CharterAlreadyDecidedError: When the charter is not DRAFTED.
            MixedCurrencyAggregationError: When the charter envelope
                currency does not match the live ``budget.currency``.
            ProjectNotFoundError: When an existing referenced
                project does not exist.

        Returns:
            ``CharterApprovalResult`` instance.
        """
        async with self._charter_locks.acquire(charter_id):
            return await self._approve(charter_id, approved_by=approved_by)

    async def _approve(
        self,
        charter_id: NotBlankStr,
        *,
        approved_by: NotBlankStr,
    ) -> CharterApprovalResult:
        """Body of approve() under the per-charter lock.

        Returns:
            ``CharterApprovalResult`` instance.

        Raises:
            CharterNotFoundError: Raised on the corresponding failure path.
            CharterAlreadyDecidedError: Raised on the corresponding failure path.
            Exception: Raised on the corresponding failure path.
            CharterStateInconsistentError: Raised on the corresponding failure path.
        """
        charter = await self._charter_repo.get(charter_id)
        if charter is None:
            logger.warning(
                CHARTER_NOT_FOUND,
                charter_id=charter_id,
                error_type=CharterNotFoundError.__name__,
            )
            raise CharterNotFoundError(charter_id=charter_id)
        # Approve is intentionally NOT ownership-fenced: the REST surface
        # is gated to CEO / Manager / Board Member via require_approval_roles
        # and the MCP surface is admin-gated via require_admin_guardrails,
        # so an approval-tier role can legitimately dispatch a junior's
        # charter (charter authorship is preserved separately on
        # ``created_by`` for audit).
        require_dispatchable(charter)
        currency = self._budget_currency()
        self._require_matching_currency(charter, currency)
        now = self._clock.now()

        project_id = await self._resolve_project(charter)
        forecast = self._build_forecast(
            charter, currency, approved_by, now, project_id=project_id
        )
        await self._forecast_repo.save(forecast)
        # Before the dispatch, not after: the pipeline refuses to stand up an
        # initiative whose charter is not APPROVED, and it can only read the
        # row. Recording the decision first is also the honest order on its
        # own terms, since the operator took it before any of this ran.
        if charter.status is CharterStatus.DRAFTED:
            await record_approval(
                self._charter_repo,
                charter,
                forecast_id=forecast.forecast_id,
                project_id=project_id,
                approved_by=approved_by,
                now=now,
            )
        work_item = self._build_work_item(charter, project_id, forecast, now)

        try:
            result = await self._work_pipeline().run(work_item)
        except Exception as exc:
            reraise_critical(exc)
            log_exception_redacted(
                logger, CHARTER_DISPATCH_FAILED, exc, charter_id=charter_id
            )
            raise

        await stamp_dispatched(
            self._charter_repo, charter, task_id=result.task_id, now=now
        )
        await self._close_conversation(charter.conversation_id, now)
        if result.is_success:
            logger.info(
                CHARTER_DISPATCHED,
                charter_id=charter_id,
                project_id=project_id,
                task_id=result.task_id,
                is_success=True,
            )
        else:
            # The charter transition to APPROVED is correct (a human
            # approved it and the pipeline was dispatched), but the run
            # itself produced no successful work. Surface that at WARNING
            # so an empty / failed dispatch is never masked by a routine
            # ``charter.dispatched`` INFO line; the caller still receives
            # the truthful ``is_success`` on the result.
            logger.warning(
                CHARTER_DISPATCH_UNSUCCESSFUL,
                charter_id=charter_id,
                project_id=project_id,
                task_id=result.task_id,
                is_success=False,
            )
        approved = await self._charter_repo.get(charter_id)
        if approved is None:
            # ``_stamp_dispatched`` only returns after a winning CAS, so
            # a missing row here is a storage-contract violation, not
            # an ownership race. Returning the pre-transition charter
            # would leak ``DRAFTED`` status to the client; log the
            # inconsistency before raising so operators see the row
            # disappearance even though the exception bubbles past.
            logger.error(
                CHARTER_STATE_INCONSISTENT,
                charter_id=charter_id,
                stage="approve_charter",
                pre_transition_status=charter.status.value,
                task_id=result.task_id,
                refreshed=None,
            )
            raise CharterStateInconsistentError(charter_id=charter_id)
        return CharterApprovalResult(
            charter=approved,
            project_id=project_id,
            task_id=result.task_id,
            is_success=result.is_success,
        )

    @staticmethod
    def _require_matching_currency(charter: ProjectCharter, currency: str) -> None:
        """Reject a charter whose envelope currency is not the budget one.

        Raises:
            MixedCurrencyAggregationError: Raised on the corresponding failure path.
        """
        if charter.envelope.currency != currency:
            msg = "Charter envelope currency does not match live budget.currency"
            logger.warning(
                CHARTER_STATE_INCONSISTENT,
                charter_id=charter.id,
                reason="envelope_currency_mismatch",
                error_type=MixedCurrencyAggregationError.__name__,
            )
            raise MixedCurrencyAggregationError(
                msg,
                currencies=frozenset({charter.envelope.currency, currency}),
            )

    async def _resolve_project(self, charter: ProjectCharter) -> NotBlankStr:
        """Verify an existing project or create the proposed new one.

        New-project creation is idempotent: the project id is derived
        from the charter id, so a retried approval reuses the same
        project rather than minting a duplicate.

        Returns:
            ``NotBlankStr`` instance.

        Raises:
            ProjectNotFoundError: Raised on the corresponding failure path.
        """
        if charter.project_id is not None:
            existing = await self._project_repo.get(charter.project_id)
            if existing is None:
                logger.warning(
                    CHARTER_STATE_INCONSISTENT,
                    charter_id=charter.id,
                    project_id=charter.project_id,
                    reason="referenced_project_missing",
                    error_type=ProjectNotFoundError.__name__,
                )
                raise ProjectNotFoundError(project_id=charter.project_id)
            return charter.project_id
        project_uuid = uuid.uuid5(PROJECT_NAMESPACE, f"charter-{charter.id}")
        project_id = NotBlankStr(str(project_uuid))
        deadline = (
            charter.envelope.deadline.isoformat()
            if charter.envelope.deadline is not None
            else None
        )
        project = Project(
            id=project_uuid,
            name=charter.proposed_project_name or NotBlankStr(charter.title),
            description=charter.proposed_project_description,
            budget=charter.envelope.amount,
            deadline=deadline,
            status=ProjectStatus.PLANNING,
        )
        try:
            await self._project_repo.create(project)
        except DuplicateRecordError:
            # Idempotent retry: the project from a prior attempt stands.
            # No charter state changed here, so the transition-event
            # stream stays reserved for actual ``DRAFTED -> *`` moves.
            logger.info(
                CHARTER_PROJECT_ALREADY_EXISTS,
                charter_id=charter.id,
                project_id=project_id,
                note="project already created on a prior attempt",
            )
        return project_id

    def _build_forecast(
        self,
        charter: ProjectCharter,
        currency: str,
        approved_by: NotBlankStr,
        now: datetime,
        *,
        project_id: NotBlankStr,
    ) -> Forecast:
        """Build the already-APPROVED forecast that is the budget record.

        Returns:
            ``Forecast`` instance.
        """
        amount = charter.envelope.amount
        return Forecast(
            forecast_id=uuid.uuid5(_FORECAST_NAMESPACE, charter.id),
            brief_hash=NotBlankStr(
                compute_brief_hash(
                    _charter_brief_signal(
                        charter.brief,
                        currency,
                        project=project_id,
                        requested_by=charter.created_by,
                        correlation_id=charter.conversation_id,
                    )
                )
            ),
            estimated_cost=amount,
            lower_bound=0.0,
            upper_bound=amount,
            currency=currency,
            decision=ForecastDecision.APPROVED,
            decided_at=now,
            decided_by=approved_by,
            ceiling_amount=amount,
            created_at=now,
            updated_at=now,
        )

    def _build_work_item(
        self,
        charter: ProjectCharter,
        project_id: NotBlankStr,
        forecast: Forecast,
        now: datetime,
    ) -> WorkItem:
        """Compose the kickoff work item for the charter's project run.

        Returns:
            ``WorkItem`` instance.
        """
        return WorkItem(
            origin_adapter_id=_ORIGIN_ADAPTER_ID,
            source=WorkSource.CONVERSATIONAL,
            title=charter.title,
            raw_intent=_render_intent(charter),
            project=project_id,
            requested_by=charter.created_by,
            priority=Priority.HIGH,
            task_type=TaskType.DEVELOPMENT,
            estimated_complexity=Complexity.MEDIUM,
            acceptance_criteria=charter.success_criteria,
            correlation_id=charter.conversation_id,
            created_at=now,
            forecast_id=forecast.forecast_id,
            hard_ceiling=charter.envelope.amount,
            # A charter is an objective: the spine always decomposes it into a
            # plan, never runs it as a single solo agent. The charter travels
            # with it because standing up an initiative is only legal on an
            # operator's approval, and the brief has to carry the evidence of
            # the one they gave.
            plan_required=True,
            charter_id=charter.id,
        )

    async def _close_conversation(
        self, conversation_id: NotBlankStr, now: datetime
    ) -> None:
        """Best-effort close of the interview conversation (idempotent).

        The dispatch already drove the work pipeline and stamped the
        charter as ``APPROVED``; a failure to close the conversation
        must not retroactively fail the approval response. Swallow
        unexpected errors with a structured log so operators still
        see the dispatch attempt, then return.
        """
        try:
            closed = await self._conversation_repo.transition_if(
                conversation_id,
                from_state=ConversationStatus.ACTIVE,
                to_state=ConversationStatus.CLOSED,
                updated_at=now.isoformat(),
            )
            if closed:
                logger.info(
                    COS_CONVERSATION_STATUS_TRANSITIONED,
                    conversation_id=conversation_id,
                    from_state=ConversationStatus.ACTIVE.value,
                    to_state=ConversationStatus.CLOSED.value,
                )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                CHARTER_DISPATCH_FAILED,
                conversation_id=conversation_id,
                stage="close_conversation",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


__all__ = ["CharterDispatcher"]
