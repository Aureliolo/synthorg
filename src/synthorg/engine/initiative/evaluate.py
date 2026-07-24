# module-kind: service
"""The EVALUATE stage: score the delivered whole against the objective.

The last question in the loop, and the only one that can complete an
initiative. Integration proves the pieces run together; this asks whether what
runs is what was asked for, criterion by criterion, with evidence.

**Fail closed.** No report, an unresolvable lead, no provider, a timeout: every
one of those leaves the plan sitting at EVALUATING with a warning. Nothing here
completes an initiative on a missing verdict, which is the deliberate inverse
of the red-team gate's policy and the whole reason the tail exists. An operator
resolves a parked plan by replanning or cancelling; the org never resolves it
by assuming.

A verdict where every criterion is met completes the plan. Anything less fires
the replan trigger with the gap: the organisation goes back to work rather than
shipping something that does not meet its own objective.
"""

import asyncio
from pathlib import Path
from typing import Final

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import StallReason
from synthorg.engine.initiative.evaluate_brief import build_evaluation_material
from synthorg.engine.initiative.evaluate_models import (
    CriterionOutcome,
    EvaluationReport,
)
from synthorg.engine.initiative.evaluate_session import (
    EvaluationSessionConfig,
    InitiativeEvaluator,
    build_evaluation_brief,
)
from synthorg.engine.initiative.lead import (
    resolve_initiative_lead,
    resolve_lead_provider,
)
from synthorg.engine.initiative.ports import PlanStatusWriter, ReplanTriggerPort
from synthorg.engine.loop_protocol import ShutdownChecker
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.background_tasks import BackgroundTaskRegistry
from synthorg.observability.events.initiative import (
    INITIATIVE_EVALUATION_COMPLETED,
    INITIATIVE_EVALUATION_FAILED,
    INITIATIVE_EVALUATION_SKIPPED,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.protocol import CompletionProvider, ProviderSelector
from synthorg.settings.resolver import ConfigResolver
from synthorg.tools.base import BaseTool
from synthorg.tools.file_system.list_directory import ListDirectoryTool
from synthorg.tools.file_system.read_file import ReadFileTool

logger = get_logger(__name__)

#: Identity recorded on the completion write, so the audit log shows the stage
#: that delivered the initiative rather than an operator who did not.
ACTOR: Final[str] = "initiative-evaluate"

#: Reason recorded on the completion transition.
_COMPLETION_REASON: Final[str] = "evaluation: every success criterion met"

#: Fallbacks for when no resolver is wired or a read fails.
_DEFAULT_MAX_TURNS: Final[int] = 10
_DEFAULT_COST_CEILING: Final[float] = 1.0
_DEFAULT_TIMEOUT_SECONDS: Final[float] = 300.0


class EvaluationStageService:
    """Scores a delivered initiative and decides delivery or rework.

    Args:
        persistence: Backend supplying the plan and project repositories.
        agent_registry: Resolves the accountable lead.
        provider_selector: Resolves the completion client for the lead's bound
            provider, so the judgement runs on the lead's provider.
        default_provider: Fallback completion client (the explicit system
            default) used when the lead's provider is unresolvable; ``None``
            parks the plan rather than dispatching to an arbitrary provider.
        plan_status_writer: The audited plan-status write path, used for the
            one transition only this stage may make.
        replan_trigger: Fired when the objective is not met, so the gap becomes
            new work. ``None`` leaves an unmet initiative parked for a human.
        workspace_root: Root the session's read-only file tools are scoped to.
            ``None`` runs the judgement without workspace reads.
        cost_tracker: Optional cost tracker the session records against.
        shutdown_checker: Optional graceful-shutdown signal for the session.
        config_resolver: Live settings source, re-read per evaluation.
        clock: Clock seam seeding the background-task drain deadline.
    """

    __slots__ = (
        "_clock",
        "_config_resolver",
        "_cost_tracker",
        "_default_provider",
        "_inflight",
        "_persistence",
        "_plan_writer",
        "_provider_selector",
        "_registry",
        "_replan_trigger",
        "_shutdown_checker",
        "_tasks",
        "_workspace_root",
    )

    def __init__(  # noqa: PLR0913 -- keyword-only dependency injection
        self,
        *,
        persistence: PersistenceBackend,
        agent_registry: AgentRegistryService,
        provider_selector: ProviderSelector,
        default_provider: CompletionProvider | None,
        plan_status_writer: PlanStatusWriter,
        replan_trigger: ReplanTriggerPort | None = None,
        workspace_root: Path | None = None,
        cost_tracker: CostTrackerProtocol | None = None,
        shutdown_checker: ShutdownChecker | None = None,
        config_resolver: ConfigResolver | None = None,
        clock: Clock,
    ) -> None:
        self._persistence = persistence
        self._registry = agent_registry
        self._provider_selector = provider_selector
        self._default_provider = default_provider
        self._plan_writer = plan_status_writer
        self._replan_trigger = replan_trigger
        self._workspace_root = workspace_root
        self._cost_tracker = cost_tracker
        self._shutdown_checker = shutdown_checker
        self._config_resolver = config_resolver
        self._clock = clock
        self._tasks = BackgroundTaskRegistry(owner="initiative.evaluate", clock=clock)
        # Plan ids with an evaluation in flight this process. The rollup fires
        # on every recompute that reads EVALUATING, so without this a burst of
        # task events would each start their own judgement of the same plan.
        self._inflight: set[str] = set()

    def schedule(self, *, plan: Plan) -> None:
        """Schedule the evaluation for a plan that entered EVALUATING.

        Returns immediately; the work runs detached on a tracked task so the
        rollup observer is never blocked. Safe to call from a best-effort
        observer: it never raises.
        """
        plan_id = str(plan.id)
        if plan_id in self._inflight:
            logger.debug(
                INITIATIVE_EVALUATION_SKIPPED,
                plan_id=plan_id,
                reason="already_inflight",
            )
            return
        self._inflight.add(plan_id)
        _ = self._tasks.spawn(
            self._evaluate_bounded(plan),
            event=INITIATIVE_EVALUATION_FAILED,
            plan_id=plan_id,
        )

    async def drain(self, *, timeout_sec: float) -> None:
        """Wait for outstanding evaluations at shutdown, then bound them."""
        await self._tasks.drain(timeout_sec=timeout_sec)

    async def _evaluate_bounded(self, plan: Plan) -> None:
        """Run one evaluation, swallowing every non-critical failure.

        Every failure path leaves the plan EVALUATING, never COMPLETED: an
        evaluation that did not happen is not a pass.

        Raises:
            asyncio.CancelledError: If the task is cancelled at shutdown; it
                propagates so the background registry can reap it.
        """
        try:
            await asyncio.wait_for(
                self._run(plan), timeout=await self._timeout_seconds()
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            logger.warning(
                INITIATIVE_EVALUATION_FAILED, plan_id=str(plan.id), reason="timeout"
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- fail-closed tail; the plan stays
            # EVALUATING and the next rollup event re-fires the stage
            reraise_critical(exc)
            logger.warning(
                INITIATIVE_EVALUATION_FAILED,
                plan_id=str(plan.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        finally:
            self._inflight.discard(str(plan.id))

    async def _run(self, plan: Plan) -> None:
        """Judge the initiative, then complete it or send it back for rework."""
        fresh = await self._persistence.plans.get(NotBlankStr(str(plan.id)))
        if fresh is None or fresh.status is not PlanStatus.EVALUATING:
            logger.debug(
                INITIATIVE_EVALUATION_SKIPPED,
                plan_id=str(plan.id),
                reason="no_longer_evaluating",
                status=fresh.status.value if fresh else None,
            )
            return
        if not fresh.objective_criteria:
            # Nothing to judge against. Parking is honest; passing would be a
            # verdict nobody reached, and the fix is an operator adding the
            # objective's criteria and replanning.
            logger.warning(
                INITIATIVE_EVALUATION_SKIPPED,
                plan_id=str(fresh.id),
                reason="objective_has_no_criteria",
            )
            return
        project = await self._persistence.projects.get(NotBlankStr(str(fresh.project)))
        if project is None:
            logger.warning(
                INITIATIVE_EVALUATION_FAILED,
                plan_id=str(fresh.id),
                reason="project_missing",
            )
            return
        report = await self._judge(fresh, project)
        if report is None:
            return
        await self._apply(fresh, report)

    async def _judge(self, plan: Plan, project: Project) -> EvaluationReport | None:
        """Run the bounded session that produces the verdict.

        Returns:
            The verdict, or ``None`` when the session could not run or ended
            without one (in which case the plan stays EVALUATING).
        """
        lead = await resolve_initiative_lead(self._registry, project)
        if lead is None:
            logger.warning(
                INITIATIVE_EVALUATION_FAILED,
                plan_id=str(plan.id),
                reason="no_lead",
            )
            return None
        provider = resolve_lead_provider(
            self._provider_selector,
            lead,
            default_provider=self._default_provider,
            skipped_event=INITIATIVE_EVALUATION_SKIPPED,
        )
        if provider is None:
            logger.warning(
                INITIATIVE_EVALUATION_FAILED,
                plan_id=str(plan.id),
                lead_id=str(lead.id),
                reason="no_provider",
            )
            return None
        evaluator = InitiativeEvaluator(
            config=await self._session_config(),
            cost_tracker=self._cost_tracker,
            shutdown_checker=self._shutdown_checker,
        )
        return await evaluator.evaluate(
            lead=lead,
            provider=provider,
            brief=build_evaluation_brief(
                material=await build_evaluation_material(self._persistence, plan)
            ),
            criteria=plan.objective_criteria,
            read_tools=self._read_tools(),
        )

    async def _apply(self, plan: Plan, report: EvaluationReport) -> None:
        """Complete the initiative, or send the gap back as new work."""
        if report.objective_met:
            await self._plan_writer.sync_status(
                plan,
                PlanStatus.COMPLETED,
                requested_by=ACTOR,
                reason=_COMPLETION_REASON,
            )
            logger.info(
                INITIATIVE_EVALUATION_COMPLETED,
                plan_id=str(plan.id),
                project=str(plan.project),
                objective_met=True,
                criteria=len(report.verdicts),
            )
            return
        unmet = [
            v.criterion
            for v in report.verdicts
            if v.outcome is not CriterionOutcome.MET
        ]
        logger.info(
            INITIATIVE_EVALUATION_COMPLETED,
            plan_id=str(plan.id),
            project=str(plan.project),
            objective_met=False,
            unmet_count=len(unmet),
        )
        if self._replan_trigger is None:
            logger.warning(
                INITIATIVE_EVALUATION_SKIPPED,
                plan_id=str(plan.id),
                reason="replan_trigger_unwired",
                note="objective unmet; plan parked at evaluating",
            )
            return
        self._replan_trigger.schedule(plan=plan, reason=StallReason.EVALUATION_UNMET)

    def _read_tools(self) -> tuple[BaseTool, ...]:
        """Build the read-only tools the judgement runs with.

        Returns:
            Workspace read tools when a root is wired; an empty tuple
            otherwise. Never a write tool: a session that could change what it
            is judging could turn its own failing verdict into a pass.
        """
        if self._workspace_root is None:
            return ()
        return (
            ReadFileTool(workspace_root=self._workspace_root),
            ListDirectoryTool(workspace_root=self._workspace_root),
        )

    async def _session_config(self) -> EvaluationSessionConfig:
        """Build the session configuration from live settings.

        Returns:
            An :class:`EvaluationSessionConfig` carrying the current turn cap
            and cost ceiling, so an operator's change applies to the next
            evaluation without a restart.
        """
        return EvaluationSessionConfig(
            max_turns=await self._resolve_int(
                "evaluation_session_max_turns", _DEFAULT_MAX_TURNS
            ),
            cost_ceiling=await self._resolve_float(
                "evaluation_session_cost_ceiling", _DEFAULT_COST_CEILING
            ),
        )

    async def _timeout_seconds(self) -> float:
        """Resolve the per-evaluation wall-clock ceiling.

        Returns:
            The configured ceiling, or the default when unresolvable.
        """
        resolved = await self._resolve_float(
            "evaluation_session_timeout_seconds", _DEFAULT_TIMEOUT_SECONDS
        )
        return resolved if resolved > 0 else _DEFAULT_TIMEOUT_SECONDS

    async def _resolve_int(self, key: str, default: int) -> int:
        """Resolve a live ``engine.<key>`` int, falling back to *default*.

        Returns:
            The configured value, or *default* when no resolver is wired or the
            read fails.
        """
        if self._config_resolver is None:
            return default
        try:
            return await self._config_resolver.get_int("engine", key)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort settings read
            reraise_critical(exc)
            self._log_settings_degraded(key, exc)
            return default

    async def _resolve_float(self, key: str, default: float) -> float:
        """Resolve a live ``engine.<key>`` float, falling back to *default*.

        Returns:
            The configured value, or *default* when no resolver is wired or the
            read fails.
        """
        if self._config_resolver is None:
            return default
        try:
            return await self._config_resolver.get_float("engine", key)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort settings read
            reraise_critical(exc)
            self._log_settings_degraded(key, exc)
            return default

    def _log_settings_degraded(self, key: str, exc: Exception) -> None:
        """Warn that a best-effort ``engine.<key>`` read fell back to a default."""
        logger.warning(
            INITIATIVE_EVALUATION_SKIPPED,
            key=key,
            reason="settings_read_degraded",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
