"""Coordination middleware implementations.

Concrete middleware for the coordination pipeline:

1. TaskLedgerMiddleware -- populates TaskLedger from decomposition
2. ProgressLedgerMiddleware -- analyzes rollup for stall detection
3. ReplanMiddleware -- wraps CoordinationReplanHook protocol
4. PlanReviewGateMiddleware -- gates dispatch on autonomy level
5. AuthorityDeferenceCoordinationMiddleware -- in s1_constraints.py
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, Protocol, override, runtime_checkable

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.middleware.coordination_protocol import (
    BaseCoordinationMiddleware,
    CoordinationMiddlewareContext,
)
from synthorg.engine.middleware.errors import PlanReviewGatedError
from synthorg.engine.middleware.models import (
    ProgressLedger,
    TaskLedger,
)
from synthorg.engine.prompt_safety import TAG_TASK_FACT, wrap_untrusted
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_EMPTY_PLAN_TEXT,
)
from synthorg.observability.events.middleware import (
    COORDINATION_REPLAN,
    COORDINATION_REPLAN_BUDGET_BLOCKED,
    COORDINATION_REPLAN_CAP_REACHED,
    MIDDLEWARE_PLAN_REVIEW_GATED,
    MIDDLEWARE_PROGRESS_LEDGER_EMITTED,
    MIDDLEWARE_TASK_LEDGER_CREATED,
)

if TYPE_CHECKING:
    from synthorg.budget.affordability import BudgetAffordabilityChecker

logger = get_logger(__name__)
_DEFAULT_ESCALATION_THRESHOLD: Final[int] = 3


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


# ── ProgressLedgerMiddleware ──────────────────────────────────────


class ProgressLedgerMiddleware(BaseCoordinationMiddleware):
    """Emits a ProgressLedger after rollup analysis.

    Analyzes the ``SubtaskStatusRollup`` to determine whether
    progress was made, increments stall counters, and recommends
    the next action.

    Args:
        escalation_threshold: Stall count triggering escalation.
    """

    def __init__(
        self,
        *,
        escalation_threshold: int = _DEFAULT_ESCALATION_THRESHOLD,
        **_kwargs: object,
    ) -> None:
        super().__init__(name="progress_ledger")
        self._escalation_threshold = escalation_threshold

    @override
    async def after_rollup(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """Analyse rollup and emit ProgressLedger.

        Returns:
            The context with the new :class:`ProgressLedger` stored,
            carrying stall counters, blocking issues, and the
            recommended next action (``continue`` / ``replan`` /
            ``escalate``).
        """
        rollup = ctx.status_rollup
        existing = ctx.progress_ledger

        # Determine round number
        round_number = (existing.round_number + 1) if existing else 1

        # Analyse progress via monotonic comparison of completed-count.
        # ``SubtaskStatusRollup.completed`` is the canonical name on
        # the rollup; ``ProgressLedger.completed_count`` is the
        # snapshot we persist for round-over-round comparison.
        completed = rollup.completed if rollup is not None else 0
        prev_completed = existing.completed_count if existing else 0
        progress_made = completed > prev_completed

        # Stall detection
        prev_stall = existing.stall_count if existing else 0
        prev_reset = existing.reset_count if existing else 0

        stall_count = 0 if progress_made else prev_stall + 1

        # Blocking issues from phases
        blocking = [
            f"Phase {phase.phase}: {phase.error}"
            for phase in ctx.phases
            if not phase.success and phase.error
        ]

        # Decide next action
        if stall_count >= self._escalation_threshold:
            next_action = "escalate"
        elif stall_count >= 1:
            next_action = "replan"
        else:
            next_action = "continue"

        ledger = ProgressLedger(
            round_number=round_number,
            progress_made=progress_made,
            completed_count=completed,
            stall_count=stall_count,
            reset_count=prev_reset,
            blocking_issues=tuple(blocking),
            next_action=next_action,
        )

        task = ctx.coordination_context.task
        logger.info(
            MIDDLEWARE_PROGRESS_LEDGER_EMITTED,
            task_id=str(task.id),
            round_number=round_number,
            progress_made=progress_made,
            stall_count=stall_count,
            next_action=next_action,
        )

        return ctx.model_copy(update={"progress_ledger": ledger})


# ── CoordinationReplanHook protocol ──────────────────────────────


@runtime_checkable
class CoordinationReplanHook(Protocol):
    """Protocol for coordination replan decisions.

    Sits between ``after_rollup`` and ``before_update_parent``.
    """

    async def should_replan(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> bool:
        """Decide whether to trigger a replan cycle."""
        ...

    async def replan(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """Execute a replan cycle and return updated context."""
        ...


class NoOpReplanHook:
    """Default replan hook: never replans."""

    async def should_replan(
        self,
        ctx: CoordinationMiddlewareContext,  # noqa: ARG002
    ) -> bool:
        """Always returns False.

        Returns:
            ``False`` unconditionally; this hook never triggers a
            replan.
        """
        return False

    async def replan(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """No-op: returns context unchanged.

        Returns:
            The same ``ctx`` instance passed in.
        """
        return ctx


class MagenticReplanHook:
    """Magentic-style replan hook with stall detection.

    Monitors ``ProgressLedger.stall_count`` and triggers replans
    when stalls are detected, up to hard caps.

    Args:
        max_stall_count: Maximum consecutive stalls before escalation.
        max_reset_count: Maximum replan cycles before escalation.
        budget_enforcer: Optional affordability checker gating an
            affordable replan.
    """

    def __init__(
        self,
        *,
        max_stall_count: int,
        max_reset_count: int,
        budget_enforcer: BudgetAffordabilityChecker | None = None,
    ) -> None:
        self._max_stall_count = max_stall_count
        self._max_reset_count = max_reset_count
        self._budget_enforcer = budget_enforcer

    async def should_replan(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> bool:
        """Check stall count against caps.

        Returns:
            ``True`` when the progress ledger reports a stall, both
            stall and reset caps are below their maxima, and the
            budget enforcer allows the replan call; ``False`` in any
            other case (no progress ledger, no stall, cap reached, or
            budget block).
        """
        progress = ctx.progress_ledger
        if progress is None:
            return False

        if progress.stall_count == 0:
            return False

        task = ctx.coordination_context.task
        if progress.stall_count >= self._max_stall_count:
            logger.warning(
                COORDINATION_REPLAN_CAP_REACHED,
                task_id=str(task.id),
                stall_count=progress.stall_count,
                cap="max_stall_count",
            )
            return False

        if progress.reset_count >= self._max_reset_count:
            logger.warning(
                COORDINATION_REPLAN_CAP_REACHED,
                task_id=str(task.id),
                reset_count=progress.reset_count,
                cap="max_reset_count",
            )
            return False

        # Budget affordability check
        if self._budget_enforcer is not None:
            try:
                await self._budget_enforcer.check_can_execute(
                    agent_id="coordination-replan",
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    COORDINATION_REPLAN_BUDGET_BLOCKED,
                    task_id=str(task.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                return False

        return True

    async def replan(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """Execute replan by incrementing reset count.

        The actual re-decomposition is handled by the coordination
        pipeline's outer loop.  This hook signals the intent and
        updates the progress ledger.

        Returns:
            The context with the progress ledger's ``reset_count``
            incremented and ``next_action`` set to ``"replan"``; the
            input ``ctx`` is returned unchanged when no progress
            ledger is attached.
        """
        progress = ctx.progress_ledger
        task = ctx.coordination_context.task

        logger.info(
            COORDINATION_REPLAN,
            task_id=str(task.id),
            stall_count=progress.stall_count if progress else 0,
            reset_count=(progress.reset_count if progress else 0) + 1,
        )

        if progress is not None:
            updated_progress = ProgressLedger(
                round_number=progress.round_number,
                progress_made=progress.progress_made,
                stall_count=progress.stall_count,
                reset_count=progress.reset_count + 1,
                blocking_issues=progress.blocking_issues,
                next_action="replan",
            )
            ctx = ctx.model_copy(
                update={"progress_ledger": updated_progress},
            )

        return ctx


class ReplanMiddleware(BaseCoordinationMiddleware):
    """Wraps a ``CoordinationReplanHook`` as coordination middleware.

    Runs between ``after_rollup`` and ``before_update_parent``.

    Args:
        replan_hook: The replan strategy to apply.
    """

    def __init__(
        self,
        *,
        replan_hook: CoordinationReplanHook | None = None,
        **_kwargs: object,
    ) -> None:
        super().__init__(name="coordination_replan")
        self._hook = replan_hook or NoOpReplanHook()

    @override
    async def after_rollup(
        self,
        ctx: CoordinationMiddlewareContext,
    ) -> CoordinationMiddlewareContext:
        """Check for replan after rollup.

        Returns:
            The context after invoking the configured replan hook; the
            input ``ctx`` is returned unchanged when the hook says no
            replan is needed.
        """
        if await self._hook.should_replan(ctx):
            ctx = await self._hook.replan(ctx)
        return ctx


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
        # Read autonomy from context config if available
        config = getattr(ctx.coordination_context, "config", None)
        level = getattr(config, "autonomy_level", None) or self._default_level
        task = ctx.coordination_context.task

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
