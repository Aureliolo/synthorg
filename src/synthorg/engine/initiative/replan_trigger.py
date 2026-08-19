# module-kind: service
"""Auto-replan: an initiative that cannot advance revises itself.

``replan_initiative`` has always existed behind a human ``POST
/plans/{id}/replan``. Nothing noticed when a plan ran out of ways to advance,
so a stalled initiative simply hung until someone looked. This service is that
missing driver: the rollup derives the stall, this schedules the re-plan, and
the successor lands in PENDING_REVIEW, which is the human gate the product
already has. The organisation gets itself unstuck; the operator still decides.

Everything here is best-effort and detached, exactly like the SHIP-retro tail:
the rollup is an idempotent best-effort observer, so the trigger must not block
it and must not raise into it.

Two guards keep an unattended chain from running away. The generation cap
refuses a lineage past ``engine.auto_replan_max_generations``, and the master
switch ``engine.auto_replan_enabled`` refuses everything. The re-check on entry
refuses a plan that is no longer replannable or no longer stalled, which is what
makes a redelivered rollup event harmless: the first replan supersedes the plan,
and every later attempt reads a superseded plan and stops.

**A refusal is an answer, not a silence.** Both guards used to be evaluated
inside the detached task, where nothing could see them: the rollup read "a
trigger is attached" as "a replan will happen" and asked again on every pass,
for ever, while one live initiative sat at ``executing`` with every item dead
and a warning rewritten in the log as the only trace. ``consider`` therefore
decides both BEFORE starting anything and hands back a
:class:`~synthorg.engine.initiative.completion.ReplanDisposition`, which its
caller routes on.

There are two doors, and the difference between them is whose authority
applies. ``consider`` is the organisation acting unasked, so the switch and the
cap bound it. ``grant`` answers a person who has just decided this initiative
should continue, so neither does, and the successor carries generation zero on
the same rule a hand-authored re-plan already follows.
"""

from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.agent import AgentIdentity
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import REPLANNABLE_STATUSES, PlanStatus
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import (
    DecompositionContext,
    roster_from_agents,
)
from synthorg.engine.decomposition.plan_mapping import items_from_decomposition
from synthorg.engine.decomposition.service import DecompositionService
from synthorg.engine.initiative.completion import (
    ITEM_DERIVED_STALLS,
    ItemProgress,
    ReplanDisposition,
    StallReason,
    stall_reason,
)
from synthorg.engine.initiative.item_progress import collect_item_progress
from synthorg.engine.initiative.ports import InitiativeReplanPort
from synthorg.engine.initiative.replan_brief import build_replan_brief
from synthorg.engine.initiative.stage_runner import StageRunner
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry_protocol import AgentRegistryProtocol
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.initiative import (
    INITIATIVE_REPLAN_COMPLETED,
    INITIATIVE_REPLAN_FAILED,
    INITIATIVE_REPLAN_GRANTED,
    INITIATIVE_REPLAN_REFUSED,
    INITIATIVE_REPLAN_SCHEDULED,
    INITIATIVE_REPLAN_SETTINGS_DEGRADED,
    INITIATIVE_REPLAN_SKIPPED,
    INITIATIVE_REPLAN_STARTED,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

#: Identity recorded on the writes a self-driven re-plan makes, so the audit
#: log never attributes one to an operator.
ACTOR: Final[str] = "initiative-replan"

#: Fallbacks for when no resolver is wired or a read fails. Capture stays on
#: (a settings outage must not silently stop the org unsticking itself) and the
#: generation cap stays tight (the runaway is the expensive failure).
_DEFAULT_ENABLED: Final[bool] = True
_DEFAULT_MAX_GENERATIONS: Final[int] = 2
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 600.0

#: The tail stage each stage-derived verdict came from. A plan that has left
#: that stage has already been dealt with (a human replanned it, or the stage
#: re-ran), so the verdict is stale and the replan is dropped.
_STAGE_OF_REASON: Final[dict[StallReason, PlanStatus]] = {
    StallReason.INTEGRATION_FAILED: PlanStatus.INTEGRATING,
    StallReason.EVALUATION_UNMET: PlanStatus.EVALUATING,
}


class ConfirmedStall(BaseModel):
    """A stall the trigger has re-confirmed against persistence.

    Attributes:
        plan: The freshly read plan, still replannable and still stalled.
        reason: The stall shape derived from the live item statuses.
        items: Those item statuses, carried forward so the brief is built from
            the same read the verdict came from.
        detail: What the scheduling stage observed, when it knows something the
            item statuses do not.
        granted_by: Who authorised this replan, when a person did. Present
            means the successor is a human decision rather than the org acting
            unasked, which is what decides its generation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    plan: Plan = Field(description="The freshly read, still-stalled plan")
    reason: StallReason = Field(description="Live stall shape")
    items: tuple[ItemProgress, ...] = Field(description="Live item progress")
    detail: str | None = Field(
        default=None,
        description="What the scheduling stage observed",
    )
    granted_by: str | None = Field(
        default=None,
        description="Who authorised this replan, when a person did",
    )

    @model_validator(mode="after")
    def _validate_reason_matches_evidence(self) -> Self:
        """Reject a stall whose reason does not match what it carries.

        The type's whole claim is that it has been confirmed, and every
        consumer builds the successor's brief on that basis. Checking it here
        keeps the guarantee attached to the type rather than resting on the one
        private method that happens to construct it correctly today.

        Returns:
            The validated model.

        Raises:
            ValueError: When the reason contradicts the items or the plan's
                own status.
        """
        if self.reason in ITEM_DERIVED_STALLS:
            if stall_reason(self.items) is not self.reason:
                msg = "reason does not match the live item stall shape"
                raise ValueError(msg)
        elif self.plan.status is not _STAGE_OF_REASON[self.reason]:
            msg = "reason does not match the plan's tail stage"
            raise ValueError(msg)
        return self


class ReplanTriggerService:
    """Replans an initiative whose plan can no longer make progress.

    Args:
        persistence: Backend supplying the plan and task repositories.
        task_engine: Reads the objective task the successor is planned from.
        decomposition_service: Produces the successor's items.
        replan: The compensated retire-and-open-successor path (injected so the
            engine does not import the api controller layer).
        config_resolver: Live settings source, re-read per fire so an operator
            can disable auto-replan or retune the cap without a restart.
        clock: Clock seam seeding the background-task drain deadline.
    """

    __slots__ = (
        "_agent_registry",
        "_config_resolver",
        "_decomposition",
        "_persistence",
        "_replan",
        "_runner",
        "_task_engine",
    )

    def __init__(
        self,
        *,
        persistence: PersistenceBackend,
        task_engine: TaskEngine,
        decomposition_service: DecompositionService,
        replan: InitiativeReplanPort,
        config_resolver: ConfigResolver | None = None,
        agent_registry: AgentRegistryProtocol | None = None,
        clock: Clock,
    ) -> None:
        self._persistence = persistence
        self._task_engine = task_engine
        self._decomposition = decomposition_service
        self._replan = replan
        self._config_resolver = config_resolver
        # Held rather than snapshotted: the org can be staffed between boot
        # and a stall, and the successor plan must be owned by whoever is
        # there when it is drafted.
        self._agent_registry = agent_registry
        self._runner = StageRunner(
            owner="initiative.replan",
            clock=clock,
            skipped_event=INITIATIVE_REPLAN_SKIPPED,
            failed_event=INITIATIVE_REPLAN_FAILED,
        )

    async def consider(
        self,
        *,
        plan: Plan,
        reason: StallReason,
        detail: str | None = None,
    ) -> ReplanDisposition:
        """Re-plan a stalled initiative if the org may still do so unasked.

        Both refusals this owns are decided HERE, before any work is started,
        so the caller learns of them. Deciding them inside the detached task
        left every refusal invisible: the rollup read "a trigger is attached"
        as "a replan will happen" and scheduled one that was refused on every
        pass, for ever.

        Returns immediately once the work is started; the replan itself runs
        detached on a tracked task so the rollup observer is never blocked.
        Safe to call from a best-effort observer: it never raises.

        Returns:
            What became of the ask.
        """
        plan_id = str(plan.id)
        if not await self._enabled():
            return self._refuse(plan, reason, ReplanDisposition.DISABLED)
        if await self._budget_exhausted(plan):
            return self._refuse(plan, reason, ReplanDisposition.BUDGET_EXHAUSTED)
        if plan_id in self._runner.inflight:
            return ReplanDisposition.ALREADY_RUNNING
        started = self._runner.start(
            key=plan_id,
            work=self._run(plan, reason, detail),
            deadline=self._timeout_seconds,
            fallback_seconds=_DEFAULT_TIMEOUT_SECONDS,
            fields={"plan_id": plan_id, "stall_reason": reason.value},
        )
        if not started:
            return ReplanDisposition.UNAVAILABLE
        logger.info(
            INITIATIVE_REPLAN_SCHEDULED,
            plan_id=plan_id,
            stall_reason=reason.value,
        )
        return ReplanDisposition.SCHEDULED

    async def grant(
        self,
        *,
        plan: Plan,
        reason: StallReason,
        requested_by: str,
        detail: str | None = None,
    ) -> bool:
        """Re-plan *plan* once on a person's authority.

        Neither the master switch nor the generation cap applies: both bound
        what the organisation may do UNASKED, and somebody has just asked. The
        successor carries generation zero for the reason a hand-authored
        re-plan already does, that a human decision is not a runaway.

        Returns:
            Whether the detached replan started.
        """
        plan_id = str(plan.id)
        if plan_id in self._runner.inflight:
            logger.info(
                INITIATIVE_REPLAN_SKIPPED,
                plan_id=plan_id,
                reason="already_inflight",
                requested_by=requested_by,
            )
            return False
        started = self._runner.start(
            key=plan_id,
            work=self._run(plan, reason, detail, granted_by=requested_by),
            deadline=self._timeout_seconds,
            fallback_seconds=_DEFAULT_TIMEOUT_SECONDS,
            fields={"plan_id": plan_id, "stall_reason": reason.value},
        )
        if started:
            logger.info(
                INITIATIVE_REPLAN_GRANTED,
                plan_id=plan_id,
                stall_reason=reason.value,
                requested_by=requested_by,
            )
        return started

    async def drain(self, *, timeout_sec: float) -> None:
        """Wait for outstanding replans at shutdown, then bound them."""
        await self._runner.drain(timeout_sec=timeout_sec)

    def _refuse(
        self,
        plan: Plan,
        reason: StallReason,
        disposition: ReplanDisposition,
    ) -> ReplanDisposition:
        """Log a refusal at WARNING and hand it back to the caller.

        Warning rather than debug because both refusals mean the initiative
        has no automatic route left; the caller escalates on this answer, and
        the log line is the trace of why.

        Returns:
            *disposition*, unchanged, so the caller reads one expression.
        """
        logger.warning(
            INITIATIVE_REPLAN_REFUSED,
            plan_id=str(plan.id),
            project=str(plan.project),
            stall_reason=reason.value,
            disposition=disposition.value,
            generation=plan.replan_generation,
        )
        return disposition

    async def _run(
        self,
        plan: Plan,
        reason: StallReason,
        detail: str | None = None,
        *,
        granted_by: str | None = None,
    ) -> None:
        """Re-check the stall, then plan and open the successor.

        *granted_by* names the person who authorised this one, which makes the
        cap re-check below inapplicable: the staleness guard exists to stop a
        second automatic replan racing the first past the cap, and a granted
        replan was never bounded by it.
        """
        confirmed = await self._confirm_stalled(
            plan, reason, detail, granted_by=granted_by
        )
        if confirmed is None:
            return
        parent = await self._task_engine.get_task(confirmed.plan.parent_task_id)
        if parent is None:
            logger.warning(
                INITIATIVE_REPLAN_SKIPPED,
                plan_id=str(confirmed.plan.id),
                reason="objective_task_missing",
            )
            return
        logger.info(
            INITIATIVE_REPLAN_STARTED,
            plan_id=str(confirmed.plan.id),
            project=str(confirmed.plan.project),
            stall_reason=confirmed.reason.value,
            generation=confirmed.plan.replan_generation,
        )
        await self._open_successor(confirmed, parent)

    async def _budget_exhausted(self, plan: Plan) -> bool:
        """Whether *plan*'s lineage has used its automatic replan budget.

        The single owner of the cap question. ``consider`` asks it before
        starting anything, so the answer reaches the caller; ``_run`` asks it
        again against the freshly read row, which is a staleness re-check on
        the same shape as the plan re-read around it rather than a second
        authority. That re-check can only turn a scheduled attempt into a
        no-op, and the next level-triggered pass then reports the refusal
        through ``consider``.

        Returns:
            ``True`` when the generation is at or past the live cap.
        """
        return plan.replan_generation >= await self._max_generations()

    async def _confirm_stalled(
        self,
        plan: Plan,
        reason: StallReason,
        detail: str | None = None,
        *,
        granted_by: str | None = None,
    ) -> ConfirmedStall | None:
        """Re-read *plan* and confirm it is still a replan candidate.

        The scheduling verdict was derived before this task ran, so everything
        can have changed since: the plan may have been superseded by a human
        replan, an operator may have unblocked an item, or a previous
        auto-replan may already have run. Re-confirming from persistence is
        what makes a redelivered rollup event harmless.

        How the reason is re-confirmed depends on where it came from. An
        item-derived stall is re-derived outright, and a plan that recovered
        stops here. A tail-stage verdict is invisible to that derivation (every
        item is done when integration fails), so it is re-confirmed by the plan
        still sitting in the stage that produced it.

        *granted_by* names the person who authorised this attempt, and its
        presence lifts the cap re-check: the cap bounds what the org does
        unasked, so re-imposing it here would refuse the very ask that was
        raised because the cap had been reached.

        Returns:
            The confirmed stall, or ``None`` when the plan is no longer
            replannable, no longer stalled, or capped out.
        """
        fresh = await self._persistence.plans.get(NotBlankStr(str(plan.id)))
        if fresh is None:
            logger.debug(
                INITIATIVE_REPLAN_SKIPPED, plan_id=str(plan.id), reason="missing"
            )
            return None
        if fresh.status not in REPLANNABLE_STATUSES:
            logger.debug(
                INITIATIVE_REPLAN_SKIPPED,
                plan_id=str(fresh.id),
                reason="not_replannable",
                status=fresh.status.value,
            )
            return None
        if granted_by is None and await self._budget_exhausted(fresh):
            # Not a park: ``consider`` already refused on this and its caller
            # escalated. Reaching it here means the generation advanced after
            # this attempt was started, so the attempt is simply dropped and
            # the next pass re-asks.
            logger.info(
                INITIATIVE_REPLAN_SKIPPED,
                plan_id=str(fresh.id),
                project=str(fresh.project),
                reason="generation_cap_reached",
                generation=fresh.replan_generation,
            )
            return None
        items = await collect_item_progress(self._persistence, fresh)
        if reason not in ITEM_DERIVED_STALLS:
            if fresh.status is not _STAGE_OF_REASON[reason]:
                logger.info(
                    INITIATIVE_REPLAN_SKIPPED,
                    plan_id=str(fresh.id),
                    reason="stage_verdict_superseded",
                    status=fresh.status.value,
                    stall_reason=reason.value,
                )
                return None
            return ConfirmedStall(
                plan=fresh,
                reason=reason,
                items=items,
                detail=detail,
                granted_by=granted_by,
            )
        live_reason = stall_reason(items)
        if live_reason is None:
            logger.info(
                INITIATIVE_REPLAN_SKIPPED,
                plan_id=str(fresh.id),
                reason="no_longer_stalled",
            )
            return None
        return ConfirmedStall(
            plan=fresh,
            reason=live_reason,
            items=items,
            detail=detail,
            granted_by=granted_by,
        )

    async def _roster(self) -> tuple[NotBlankStr, ...]:
        """Read the roles the org staffs right now.

        Returns:
            The distinct roles behind the active agents, or empty when no
            registry is wired, which leaves the successor's owners unchecked
            rather than rejecting every one of them.
        """
        if self._agent_registry is None:
            return ()
        return roster_from_agents(await self._agent_registry.list_active())

    async def _owner(self, plan: Plan) -> AgentIdentity | None:
        """Resolve the agent already accountable for *plan*'s initiative.

        Without an identity the agent-session strategy declines to the
        single-shot fallback decomposer, so a successor would be planned by a
        different mechanism than the plan it replaces. Read from the project's
        durable lead rather than staffed afresh: the initiative already has an
        owner, and picking a second one would plan the successor as somebody
        who does not own the work.

        Returns:
            The lead's identity, or ``None`` when no registry is wired, the
            project names no lead, or the lead no longer resolves.
        """
        if self._agent_registry is None:
            return None
        project = await self._persistence.projects.get(plan.project)
        if project is None or project.lead is None:
            return None
        return await self._agent_registry.get(project.lead)

    async def _open_successor(self, stall: ConfirmedStall, parent: Task) -> None:
        """Decompose the objective afresh and open the successor plan.

        A granted replan carries generation zero and the granter's name: it is
        a human decision, and the shipped rule is that a human decision is not
        a runaway, so the successor starts the automatic budget afresh rather
        than inheriting a lineage that had already spent it.
        """
        plan = stall.plan
        brief = build_replan_brief(plan, stall.items, stall.reason, detail=stall.detail)
        # The brief rides on a copy of the objective task, never the persisted
        # row: the planner needs the stall context, but the objective itself
        # has not changed and must not accumulate one brief per re-plan.
        briefed = parent.model_copy(
            update={"description": f"{parent.description}\n\n{brief}"}
        )
        result = await self._decomposition.decompose_task(
            briefed,
            DecompositionContext(
                owner_identity=await self._owner(plan),
                available_roles=await self._roster(),
            ),
        )
        # No empty-successor guard: DecompositionPlan rejects an empty subtask
        # tree, so a decomposition that produced nothing raised above.
        revised = items_from_decomposition(result)
        successor = await self._replan.replan(
            plan,
            items=revised,
            requested_by=stall.granted_by or ACTOR,
            replan_generation=(
                0 if stall.granted_by is not None else plan.replan_generation + 1
            ),
        )
        logger.info(
            INITIATIVE_REPLAN_COMPLETED,
            plan_id=str(successor.id),
            supersedes=str(plan.id),
            project=str(plan.project),
            item_count=len(revised),
            generation=successor.replan_generation,
        )

    async def _enabled(self) -> bool:
        """Return whether auto-replan is switched on right now.

        Returns:
            The live ``engine.auto_replan_enabled`` value, or the default when
            no resolver is wired or the read fails.
        """
        if self._config_resolver is None:
            return _DEFAULT_ENABLED
        try:
            return await self._config_resolver.get_bool("engine", "auto_replan_enabled")
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort settings read
            reraise_critical(exc)
            self._log_settings_degraded("auto_replan_enabled", exc)
            return _DEFAULT_ENABLED

    async def _max_generations(self) -> int:
        """Resolve the live generation cap.

        Returns:
            The ``engine.auto_replan_max_generations`` value, or the default
            when no resolver is wired or the read fails.
        """
        if self._config_resolver is None:
            return _DEFAULT_MAX_GENERATIONS
        try:
            return await self._config_resolver.get_int(
                "engine", "auto_replan_max_generations"
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort settings read
            reraise_critical(exc)
            self._log_settings_degraded("auto_replan_max_generations", exc)
            return _DEFAULT_MAX_GENERATIONS

    async def _timeout_seconds(self) -> float:
        """Resolve the per-replan wall-clock ceiling.

        Returns:
            The ``engine.auto_replan_timeout_seconds`` value, or the default
            when no resolver is wired or the read fails.
        """
        if self._config_resolver is None:
            return _DEFAULT_TIMEOUT_SECONDS
        try:
            resolved = await self._config_resolver.get_float(
                "engine", "auto_replan_timeout_seconds"
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort settings read
            reraise_critical(exc)
            self._log_settings_degraded("auto_replan_timeout_seconds", exc)
            return _DEFAULT_TIMEOUT_SECONDS
        return resolved if resolved > 0 else _DEFAULT_TIMEOUT_SECONDS

    def _log_settings_degraded(self, key: str, exc: Exception) -> None:
        """Warn that a best-effort ``engine.<key>`` read fell back to a default."""
        logger.warning(
            INITIATIVE_REPLAN_SETTINGS_DEGRADED,
            key=key,
            reason="settings_read_degraded",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
