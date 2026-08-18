"""Coordination middleware implementations.

Concrete middleware for the coordination pipeline:

1. TaskLedgerMiddleware -- populates TaskLedger from decomposition
2. PlanReviewGateMiddleware -- gates dispatch on autonomy level
3. AuthorityDeferenceCoordinationMiddleware -- in s1_constraints.py

There is deliberately no stall detection here. Whether a run is stuck is
asked at two levels that can answer it: the execution loop's stagnation
detector, which sees the turns, and the initiative rollup's
``stall_reason``, which derives it exactly from persisted item status and
routes it to a replan. A wave-level third opinion has strictly less
information than either and no authority to act on it.
"""

from datetime import UTC, datetime
from typing import override

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.engine.middleware.coordination_protocol import (
    BaseCoordinationMiddleware,
    CoordinationMiddlewareContext,
)
from synthorg.engine.middleware.errors import PlanReviewGatedError
from synthorg.engine.middleware.models import TaskLedger
from synthorg.engine.prompt_safety import TAG_TASK_FACT, wrap_untrusted
from synthorg.observability import get_logger
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_EMPTY_PLAN_TEXT,
)
from synthorg.observability.events.middleware import (
    MIDDLEWARE_PLAN_REVIEW_GATED,
    MIDDLEWARE_TASK_LEDGER_CREATED,
)

logger = get_logger(__name__)


# ── TaskLedgerMiddleware ──────────────────────────────────────────


class TaskLedgerMiddleware(BaseCoordinationMiddleware):
    """Populates a TaskLedger from the decomposition plan.

    Runs in the ``before_dispatch`` hook after decomposition and
    routing have completed.  Extracts plan text, known facts from
    the task context, and stores the ledger on the context.
    """

    def __init__(self, **_kwargs: object) -> None:
        super().__init__(name="task_ledger")

    @override
    async def before_dispatch(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """Create TaskLedger from decomposition result.

        Returns:
            The context with a new :class:`TaskLedger` stored, or the
            input ``ctx`` unchanged when the decomposition is missing
            or yields empty plan text.
        """
        decomp = ctx.decomposition_result
        if decomp is None:
            return ctx

        task = ctx.coordination_context.task

        # Extract plan text from decomposition
        plan_text = str(decomp).strip()
        if not plan_text:
            logger.warning(
                DECOMPOSITION_EMPTY_PLAN_TEXT,
                task_id=str(task.id),
            )
            return ctx

        # Extract known facts from task description + criteria. Each
        # fact is user-controllable content, so we wrap it in a
        # ``<task-fact>`` fence: downstream prompts that render the
        # ledger treat each fact as untrusted data, not instructions.
        known_facts: list[str] = []
        if task.description:
            known_facts.append(wrap_untrusted(TAG_TASK_FACT, task.description))
        known_facts.extend(
            wrap_untrusted(TAG_TASK_FACT, c.description)
            for c in task.acceptance_criteria
            if c.description and c.description.strip()
        )

        # Determine version from existing ledger
        existing = ctx.task_ledger
        version = (existing.plan_version + 1) if existing else 1

        ledger = TaskLedger(
            plan_text=plan_text,
            known_facts=tuple(known_facts) if known_facts else (),
            plan_version=version,
            created_at=datetime.now(UTC),
        )

        logger.info(
            MIDDLEWARE_TASK_LEDGER_CREATED,
            task_id=str(task.id),
            plan_version=version,
            known_fact_count=len(known_facts),
        )

        return ctx.model_copy(update={"task_ledger": ledger})


# ── PlanReviewGateMiddleware ──────────────────────────────────────


class PlanReviewGateMiddleware(BaseCoordinationMiddleware):
    """Gates dispatch based on autonomy level.

    Per-autonomy-level gating:

    * ``full``: gate off -- dispatch proceeds
    * ``semi``: opt-in -- dispatch proceeds, plan logged
    * ``supervised``: gate on -- logs for approval
    * ``locked``: enforced -- logs for approval

    Args:
        default_autonomy_level: Autonomy level to use when not
            available from context.
    """

    def __init__(
        self,
        *,
        default_autonomy_level: AutonomyLevel = AutonomyLevel.FULL,
        **_kwargs: object,
    ) -> None:
        super().__init__(name="plan_review_gate")
        self._default_level = default_autonomy_level

    @override
    async def before_dispatch(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """Gate dispatch based on autonomy level.

        Reads autonomy level from the coordination context's config
        when available, otherwise falls back to ``default_autonomy_level``.

        Returns:
            The context with ``plan_review_gate`` metadata recording
            ``gated=False`` and the resolved autonomy level.

        Raises:
            PlanReviewGatedError: When the resolved level is
                ``SUPERVISED`` or ``LOCKED``: dispatch is blocked and
                the plan is logged for review.
        """
        task = ctx.coordination_context.task

        # An approved-plan resume (coordinate(precomputed_plan=...)) has already
        # cleared the human gate; never re-gate the dispatch it is resuming.
        if ctx.metadata.get("plan_review_approved") is True:
            return ctx.with_metadata(
                "plan_review_gate",
                {"gated": False, "reason": "plan_already_approved"},
            )

        # Read autonomy from context config if available
        config = getattr(ctx.coordination_context, "config", None)
        level = getattr(config, "autonomy_level", None) or self._default_level

        if level in (AutonomyLevel.SUPERVISED, AutonomyLevel.LOCKED):
            logger.info(
                MIDDLEWARE_PLAN_REVIEW_GATED,
                task_id=str(task.id),
                autonomy_level=level.value,
                plan_present=ctx.task_ledger is not None,
            )
            raise PlanReviewGatedError(
                task_id=str(task.id),
                autonomy_level=level.value,
            )

        if level == AutonomyLevel.SEMI:
            logger.debug(
                MIDDLEWARE_PLAN_REVIEW_GATED,
                task_id=str(task.id),
                autonomy_level=level.value,
                action="logged_for_async_review",
            )

        return ctx.with_metadata(
            "plan_review_gate",
            {
                "gated": False,
                "autonomy_level": level.value,
            },
        )
