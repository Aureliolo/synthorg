"""Charter approval to work-pipeline dispatch.

Turns an approved :class:`ProjectCharter` into a real project run: it
resolves (or creates) the project, persists an already-APPROVED cost
forecast as the budget record, builds the kickoff :class:`WorkItem`
(carrying the forecast id + hard ceiling), and drives the work pipeline
spine. The charter is the authoritative input; on success it is stamped
with the dispatch provenance and transitioned ``DRAFTED -> APPROVED``.

This mirrors the conversational-intake dispatch seam: the work pipeline
is called directly (the charter approval IS the budget approval, so the
pre-flight ForecastGate is intentionally bypassed) and the forecast row
exists for audit and in-loop ceiling enforcement.
"""

import asyncio
import uuid
from typing import TYPE_CHECKING

from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.budget.forecaster import BriefSignal, compute_brief_hash
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.enums import (
    CharterStatus,
    Complexity,
    ConversationStatus,
    Priority,
    ProjectStatus,
    TaskType,
)
from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.engine.pipeline.errors import WorkProjectNotFoundError
from synthorg.engine.pipeline.models import WorkItem, WorkSource
from synthorg.meta.charter.models import CharterApprovalResult, ProjectCharter
from synthorg.meta.errors import (
    CharterAlreadyDecidedError,
    CharterNotFoundError,
    CharterStateInconsistentError,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.charter import (
    CHARTER_APPROVED,
    CHARTER_DISPATCH_FAILED,
    CHARTER_DISPATCHED,
    CHARTER_PROJECT_ALREADY_EXISTS,
    CHARTER_STATE_INCONSISTENT,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from synthorg.api.services.project_service import ProjectService
    from synthorg.engine.pipeline.protocol import WorkPipeline
    from synthorg.persistence.charter_protocol import CharterRepository
    from synthorg.persistence.conversation_protocol import ConversationRepository
    from synthorg.persistence.cost_forecast_protocol import CostForecastRepository

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


def _charter_brief_signal(brief: str, currency: str) -> BriefSignal:
    """Build the brief signal for the charter's forecast.

    Mirrors the work-entry signal shape ForecastGate uses (single
    ``"default"`` role placeholder) so the forecast lines up if the
    same brief is later re-checked through the gate.
    """
    return BriefSignal(
        brief_text=brief,
        role_skeleton=("default",),
        model_assignments={},
        currency=NotBlankStr(currency),
    )


def _render_intent(charter: ProjectCharter) -> NotBlankStr:
    """Fold the charter content into the work item's intent body."""
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
        project_service: Project admin service (resolve / create).
        work_pipeline: The work pipeline spine entry (``run`` per item).
        conversation_repo: Conversation store (for closing the interview).
        budget_currency: Callable returning the live ``budget.currency``;
            the forecast currency must match it.
        clock: Injectable time source.
    """

    def __init__(  # noqa: PLR0913 -- DI seam: independently-wired collaborators
        self,
        *,
        charter_repo: CharterRepository,
        forecast_repo: CostForecastRepository,
        project_service: ProjectService,
        work_pipeline: WorkPipeline,
        conversation_repo: ConversationRepository,
        budget_currency: Callable[[], str],
        clock: Clock | None = None,
    ) -> None:
        self._charter_repo = charter_repo
        self._forecast_repo = forecast_repo
        self._project_service = project_service
        self._work_pipeline = work_pipeline
        self._conversation_repo = conversation_repo
        self._budget_currency = budget_currency
        self._clock: Clock = clock or SystemClock()
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard: asyncio.Lock | None = None

    async def _lock_for(self, charter_id: str) -> asyncio.Lock:
        """Return the per-charter lock, creating it once."""
        if self._locks_guard is None:
            self._locks_guard = asyncio.Lock()
        async with self._locks_guard:
            lock = self._locks.get(charter_id)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[charter_id] = lock
            return lock

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
            WorkProjectNotFoundError: When an existing referenced
                project does not exist.
        """
        async with await self._lock_for(charter_id):
            return await self._approve(charter_id, approved_by=approved_by)

    async def _approve(
        self,
        charter_id: NotBlankStr,
        *,
        approved_by: NotBlankStr,
    ) -> CharterApprovalResult:
        """Body of approve() under the per-charter lock."""
        charter = await self._charter_repo.get(charter_id)
        if charter is None:
            raise CharterNotFoundError(charter_id=charter_id)
        # Approve is intentionally NOT ownership-fenced: the REST surface
        # is gated to CEO / Manager / Board Member via require_approval_roles
        # and the MCP surface is admin-gated via require_admin_guardrails,
        # so an approval-tier role can legitimately dispatch a junior's
        # charter (charter authorship is preserved separately on
        # ``created_by`` for audit).
        if charter.status is not CharterStatus.DRAFTED:
            raise CharterAlreadyDecidedError(charter_id=charter_id)
        currency = self._budget_currency()
        self._require_matching_currency(charter, currency)
        now = self._clock.now()

        project_id = await self._resolve_project(charter)
        forecast = self._build_forecast(charter, currency, approved_by, now)
        await self._forecast_repo.save(forecast)
        work_item = self._build_work_item(charter, project_id, forecast, now)

        try:
            result = await self._work_pipeline.run(work_item)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.error(
                CHARTER_DISPATCH_FAILED,
                charter_id=charter_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

        await self._stamp_approved(charter, forecast, result.task_id, approved_by, now)
        await self._close_conversation(charter.conversation_id, now)
        logger.info(
            CHARTER_DISPATCHED,
            charter_id=charter_id,
            project_id=project_id,
            task_id=result.task_id,
            is_success=result.is_success,
        )
        approved = await self._charter_repo.get(charter_id)
        if approved is None:
            # ``_stamp_approved`` only returns after a winning CAS, so
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
        """Reject a charter whose envelope currency is not the budget one."""
        if charter.envelope.currency != currency:
            msg = "Charter envelope currency does not match live budget.currency"
            raise MixedCurrencyAggregationError(
                msg,
                currencies=frozenset({charter.envelope.currency, currency}),
            )

    async def _resolve_project(self, charter: ProjectCharter) -> NotBlankStr:
        """Verify an existing project or create the proposed new one.

        New-project creation is idempotent: the project id is derived
        from the charter id, so a retried approval reuses the same
        project rather than minting a duplicate.
        """
        if charter.project_id is not None:
            existing = await self._project_service.get(charter.project_id)
            if existing is None:
                raise WorkProjectNotFoundError
            return charter.project_id
        project_id = NotBlankStr(f"charter-{charter.id}")
        deadline = (
            charter.envelope.deadline.isoformat()
            if charter.envelope.deadline is not None
            else None
        )
        project = Project(
            id=project_id,
            name=charter.proposed_project_name or NotBlankStr(charter.title),
            description=charter.proposed_project_description,
            budget=charter.envelope.amount,
            deadline=deadline,
            status=ProjectStatus.PLANNING,
        )
        try:
            await self._project_service.create(project)
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
    ) -> Forecast:
        """Build the already-APPROVED forecast that is the budget record."""
        amount = charter.envelope.amount
        return Forecast(
            forecast_id=uuid.uuid5(_FORECAST_NAMESPACE, charter.id),
            brief_hash=NotBlankStr(
                compute_brief_hash(_charter_brief_signal(charter.brief, currency))
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
        """Compose the kickoff work item for the charter's project run."""
        return WorkItem(
            origin_adapter_id=_ORIGIN_ADAPTER_ID,
            source=WorkSource.CONVERSATIONAL,
            title=charter.title,
            raw_intent=_render_intent(charter),
            project=project_id,
            requested_by=charter.created_by,
            priority=Priority.HIGH,
            task_type=TaskType.DEVELOPMENT,
            # The charter carries no complexity; the spine's decompose +
            # routing phases decide solo-vs-team from the brief + agent pool.
            estimated_complexity=Complexity.MEDIUM,
            acceptance_criteria=charter.success_criteria,
            correlation_id=charter.conversation_id,
            created_at=now,
            forecast_id=forecast.forecast_id,
            hard_ceiling=charter.envelope.amount,
        )

    async def _stamp_approved(
        self,
        charter: ProjectCharter,
        forecast: Forecast,
        task_id: NotBlankStr,
        approved_by: NotBlankStr,
        now: datetime,
    ) -> None:
        """CAS the charter to APPROVED with full dispatch provenance."""
        transitioned = await self._charter_repo.transition_if(
            charter.id,
            from_state=CharterStatus.DRAFTED,
            to_state=CharterStatus.APPROVED,
            updated_at=now,
            approved_at=now,
            approved_by=approved_by,
            forecast_id=forecast.forecast_id,
            correlation_id=charter.conversation_id,
            task_id=task_id,
        )
        if not transitioned:
            # A concurrent decider already moved the charter. The run we
            # just drove still happened; surface the no-op rather than
            # claim an approval we did not commit.
            raise CharterAlreadyDecidedError(charter_id=charter.id)
        logger.info(
            CHARTER_APPROVED,
            charter_id=charter.id,
            approved_by=approved_by,
            task_id=task_id,
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
            await self._conversation_repo.transition_if(
                conversation_id,
                from_state=ConversationStatus.ACTIVE,
                to_state=ConversationStatus.CLOSED,
                updated_at=now.isoformat(),
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                CHARTER_DISPATCH_FAILED,
                conversation_id=conversation_id,
                stage="close_conversation",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


__all__ = ["CharterDispatcher"]
