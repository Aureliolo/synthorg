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

from pathlib import Path
from typing import Final

from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.clock import Clock
from synthorg.core.domain_errors import VersionConflictError
from synthorg.core.evaluation_verdict import CriterionOutcome
from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import (
    REPLAN_IN_PROGRESS_DISPOSITIONS,
    StallReason,
)
from synthorg.engine.initiative.evaluate_brief import (
    build_evaluation_material,
    unmet_verdict_detail,
)
from synthorg.engine.initiative.evaluate_models import EvaluationReport
from synthorg.engine.initiative.evaluate_session import (
    EvaluationSessionConfig,
    InitiativeEvaluator,
    build_evaluation_brief,
)
from synthorg.engine.initiative.evaluate_settings import (
    DEFAULT_TIMEOUT_SECONDS,
    session_config,
    timeout_seconds,
)
from synthorg.engine.initiative.lead import (
    resolve_initiative_lead,
    resolve_lead_provider,
)
from synthorg.engine.initiative.ports import (
    PlanReconcilePort,
    PlanStatusWriter,
    ReplanTriggerResolver,
)
from synthorg.engine.initiative.project_writes import MAX_WRITE_ATTEMPTS
from synthorg.engine.initiative.stage_runner import StageRunner
from synthorg.engine.loop_protocol import ShutdownChecker
from synthorg.engine.workspace.paths import project_workspace_dir
from synthorg.hr.registry import AgentRegistryService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.initiative import (
    INITIATIVE_EVALUATION_COMPLETED,
    INITIATIVE_EVALUATION_FAILED,
    INITIATIVE_EVALUATION_RECORD_FAILED,
    INITIATIVE_EVALUATION_RECORDED,
    INITIATIVE_EVALUATION_SCHEDULED,
    INITIATIVE_EVALUATION_SKIPPED,
)
from synthorg.persistence.evaluation_report_protocol import (
    EvaluationReportRecord,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.providers.protocol import ProviderSelector
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

#: Judgements one plan may start in a process. Each is a paid LLM session and
#: the rollup schedules one on every recompute that reads EVALUATING, so a plan
#: whose verdict never lands must stop spending rather than re-judge forever.
_MAX_EVALUATION_ATTEMPTS: Final[int] = 3


class EvaluationStageService:
    """Scores a delivered initiative and decides delivery or rework.

    Args:
        persistence: Backend supplying the plan and project repositories.
        agent_registry: Resolves the accountable lead.
        provider_selector: Resolves the completion client for the lead's bound
            provider, so the judgement runs on the connection the lead names.
            An unregistered one parks the plan rather than dispatching the
            judgement to a connection nobody chose.
        plan_status_writer: The audited plan-status write path, used for the
            one transition only this stage may make.
        replan_trigger: Reads the trigger fired when the objective is not met,
            so the gap becomes new work. Resolved per verdict rather than
            captured, because the trigger attaches on its own schedule. ``None``
            (or a read yielding ``None``) leaves an unmet initiative parked.
        reconcile: Re-derives the initiative graph after the completion write,
            so the project and objective task follow the plan. ``None`` leaves
            them lagging the plan's COMPLETED write.
        workspace_root: Root the session's read-only file tools are scoped to.
            ``None`` runs the judgement without workspace reads.
        cost_tracker: Optional cost tracker the session records against.
        shutdown_checker: Optional graceful-shutdown signal for the session.
        config_resolver: Live settings source, re-read per evaluation.
        clock: Clock seam seeding the background-task drain deadline.
    """

    __slots__ = (
        "_attempts",
        "_clock",
        "_config_resolver",
        "_cost_tracker",
        "_persistence",
        "_plan_writer",
        "_provider_selector",
        "_reconcile",
        "_registry",
        "_replan_trigger",
        "_runner",
        "_shutdown_checker",
        "_workspace_root",
    )

    def __init__(  # noqa: PLR0913 -- keyword-only dependency injection
        self,
        *,
        persistence: PersistenceBackend,
        agent_registry: AgentRegistryService,
        provider_selector: ProviderSelector,
        plan_status_writer: PlanStatusWriter,
        replan_trigger: ReplanTriggerResolver | None = None,
        reconcile: PlanReconcilePort | None = None,
        workspace_root: Path | None = None,
        cost_tracker: CostTrackerProtocol | None = None,
        shutdown_checker: ShutdownChecker | None = None,
        config_resolver: ConfigResolver | None = None,
        clock: Clock,
    ) -> None:
        self._persistence = persistence
        self._registry = agent_registry
        self._provider_selector = provider_selector
        self._plan_writer = plan_status_writer
        self._replan_trigger = replan_trigger
        self._reconcile = reconcile
        self._workspace_root = workspace_root
        self._cost_tracker = cost_tracker
        self._shutdown_checker = shutdown_checker
        self._config_resolver = config_resolver
        self._clock = clock
        self._runner = StageRunner(
            owner="initiative.evaluate",
            clock=clock,
            skipped_event=INITIATIVE_EVALUATION_SKIPPED,
            failed_event=INITIATIVE_EVALUATION_FAILED,
        )
        # Judgements started per plan this process. Every recompute that reads
        # EVALUATING schedules one, and each is a paid LLM session, so a plan
        # whose verdict never lands must stop costing money rather than
        # re-judging on every passing task event.
        #
        # Deliberately per-process, not persisted. The runaway this guards is a
        # tight re-judge loop within one lifetime; a restart (or a genuine
        # rework that reopens the plan through the regression edge and lands it
        # back at EVALUATING) is a natural reset point that a persisted
        # monotonic counter would instead starve of any fresh judgement. Spend
        # is bounded regardless by the per-session cost ceiling, and an
        # exhausted plan parks fail-closed at EVALUATING for a human rather than
        # completing on a missing verdict.
        self._attempts: dict[str, int] = {}

    def schedule(self, *, plan: Plan) -> None:
        """Schedule the evaluation for a plan that entered EVALUATING.

        Returns immediately; the work runs detached on a tracked task so the
        rollup observer is never blocked. Safe to call from a best-effort
        observer: it never raises.
        """
        plan_id = str(plan.id)
        attempt = self._attempts.get(plan_id, 0)
        if attempt >= _MAX_EVALUATION_ATTEMPTS:
            logger.warning(
                INITIATIVE_EVALUATION_SKIPPED,
                plan_id=plan_id,
                reason="attempt_cap_reached",
                attempts=attempt,
                note="plan parked at evaluating for an operator",
            )
            return
        started = self._runner.start(
            key=plan_id,
            work=self._run(plan),
            deadline=self._timeout_seconds,
            fallback_seconds=DEFAULT_TIMEOUT_SECONDS,
            fields={"plan_id": plan_id},
        )
        if started:
            self._attempts[plan_id] = attempt + 1
            logger.info(
                INITIATIVE_EVALUATION_SCHEDULED,
                plan_id=plan_id,
                attempt=attempt + 1,
            )

    async def drain(self, *, timeout_sec: float) -> None:
        """Wait for outstanding evaluations at shutdown, then bound them."""
        await self._runner.drain(timeout_sec=timeout_sec)

    async def settle(self, *, timeout_sec: float) -> None:
        """Wait for in-flight judgements without closing the stage."""
        await self._runner.settle(timeout_sec=timeout_sec)

    def attempts_for(self, plan: Plan) -> int:
        """Return how many judgements this process has started for *plan*.

        Returns:
            The attempt count, for assertions and operator diagnostics.
        """
        return self._attempts.get(str(plan.id), 0)

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
        if not await self._record(fresh, report):
            # A verdict nobody can read afterwards is, to every later reader,
            # no verdict: the plan would complete with nothing to point at
            # when asked why. No verdict parks the plan, so an unrecordable
            # one parks it too. The next recompute re-judges, up to the
            # attempt cap, so the cost is a re-judgement rather than a
            # delivery permanently marked done on absent evidence.
            return
        await self._apply(fresh, report)

    async def _record(self, plan: Plan, report: EvaluationReport) -> bool:
        """Persist the verdict before anything can act on it.

        Written first so a contended completion write no longer destroys a
        judgement that cost real money: the row outlives the plan's status
        and is the only account an operator gets of which criteria failed.

        Returns:
            Whether the judgement was stored. ``False`` leaves the plan at
            EVALUATING: this stage's whole posture is that an absent verdict
            parks rather than completes, and a verdict that did not persist
            is absent from the moment the process ends.

        The attempt number is derived by reading the current maximum and
        adding one, which is a read-modify-write across a network call. The
        unique key on ``(plan_id, attempt)`` turns a lost race into a
        ``DuplicateRecordError`` rather than an overwrite, and the retry
        re-reads and tries again: a second worker's judgement is a second
        judgement, not a duplicate of the first, so dropping it would lose
        exactly what this table exists to keep.

        The maximum comes from a dedicated read rather than from ``query``,
        which is paginated and ordered by time: the largest attempt is only
        in the first page while the two orders agree, and where they do not
        the writer proposes an attempt that already exists, every retry
        proposes it again, and the budget runs out on a verdict that could
        have been stored.
        """
        repo = self._persistence.evaluation_reports
        plan_id = NotBlankStr(str(plan.id))
        attempt = 0
        for _ in range(MAX_WRITE_ATTEMPTS):
            try:
                attempt = await repo.max_attempt(plan_id) + 1
                await repo.append(
                    EvaluationReportRecord(
                        plan_id=plan_id,
                        project_id=NotBlankStr(str(plan.project)),
                        attempt=attempt,
                        summary=report.summary,
                        verdicts=report.verdicts,
                        objective_met=report.objective_met,
                        evaluated_at=self._clock.now(),
                    )
                )
            except DuplicateRecordError:
                continue
            except QueryError as exc:
                # lint-allow: swallow-ok -- surfaced as a False return, which
                # parks the plan; narrowed to the store's own failure so a
                # genuine bug in this method raises instead of being absorbed.
                logger.error(
                    INITIATIVE_EVALUATION_RECORD_FAILED,
                    plan_id=str(plan.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                return False
            break
        else:
            logger.error(
                INITIATIVE_EVALUATION_RECORD_FAILED,
                plan_id=str(plan.id),
                reason="attempt_number_contended",
            )
            return False
        logger.info(
            INITIATIVE_EVALUATION_RECORDED,
            plan_id=str(plan.id),
            attempt=attempt,
            objective_met=report.objective_met,
            criteria=len(report.verdicts),
        )
        return True

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
            read_tools=self._read_tools(plan),
            project_id=plan.project,
        )

    async def _apply(self, plan: Plan, report: EvaluationReport) -> None:
        """Complete the initiative, or send the gap back as new work."""
        if report.objective_met:
            await self._complete(plan, report)
            return
        unmet = tuple(
            v for v in report.verdicts if v.outcome is not CriterionOutcome.MET
        )
        logger.info(
            INITIATIVE_EVALUATION_COMPLETED,
            plan_id=str(plan.id),
            project=str(plan.project),
            objective_met=False,
            unmet_count=len(unmet),
        )
        trigger = None if self._replan_trigger is None else self._replan_trigger()
        if trigger is None:
            logger.warning(
                INITIATIVE_EVALUATION_SKIPPED,
                plan_id=str(plan.id),
                reason="replan_trigger_unwired",
                note="objective unmet; plan parked at evaluating",
            )
            return
        # The judged evidence is the best account of what went wrong that this
        # initiative will ever produce. Handing the trigger only the enum would
        # leave the successor's planner with generic boilerplate instead.
        disposition = await trigger.consider(
            plan=plan,
            reason=StallReason.EVALUATION_UNMET,
            detail=unmet_verdict_detail(unmet),
        )
        if disposition not in REPLAN_IN_PROGRESS_DISPOSITIONS:
            # The trigger refused and said why. Reported here rather than
            # dropped: this stage is the only place that knows the objective
            # went unmet, and the rollup's next recompute is what raises the
            # decision for the operator.
            logger.warning(
                INITIATIVE_EVALUATION_SKIPPED,
                plan_id=str(plan.id),
                reason="replan_refused",
                disposition=disposition.value,
                note="objective unmet and no automatic replan remains",
            )

    async def _complete(self, plan: Plan, report: EvaluationReport) -> None:
        """Write the one status only this stage may write, then reconcile.

        The write is CAS-guarded, so an operator touching the plan during the
        judgement loses the race. The verdict itself is already persisted by
        then, so a lost race costs the transition rather than the judgement;
        it is still retried against a fresh read rather than abandoned,
        because the plan genuinely met its objective.
        """
        for attempt in range(1, MAX_WRITE_ATTEMPTS + 1):
            fresh = await self._persistence.plans.get(NotBlankStr(str(plan.id)))
            if fresh is None or fresh.status is not PlanStatus.EVALUATING:
                logger.info(
                    INITIATIVE_EVALUATION_SKIPPED,
                    plan_id=str(plan.id),
                    reason="no_longer_evaluating",
                    status=fresh.status.value if fresh else None,
                    note="verdict discarded; the plan moved during the judgement",
                )
                return
            try:
                await self._plan_writer.sync_status(
                    fresh,
                    PlanStatus.COMPLETED,
                    requested_by=ACTOR,
                    reason=_COMPLETION_REASON,
                )
            except VersionConflictError:
                logger.info(
                    INITIATIVE_EVALUATION_SKIPPED,
                    plan_id=str(plan.id),
                    reason="write_contended",
                    attempt=attempt,
                )
                continue
            logger.info(
                INITIATIVE_EVALUATION_COMPLETED,
                plan_id=str(plan.id),
                project=str(plan.project),
                objective_met=True,
                criteria=len(report.verdicts),
            )
            await self._reconcile_graph(plan)
            return
        logger.warning(
            INITIATIVE_EVALUATION_FAILED,
            plan_id=str(plan.id),
            reason="completion_write_contended",
            attempts=MAX_WRITE_ATTEMPTS,
        )

    async def _reconcile_graph(self, plan: Plan) -> None:
        """Re-derive the initiative graph now the plan has completed.

        This write mutates no task, so it emits no task event, and the rollup
        only recomputes on one of those. Without this call the project would
        stay at EVALUATING, the objective task would stay held open, and the
        SHIP retrospective would never fire: the plan would be the only thing
        that finished.
        """
        if self._reconcile is None:
            logger.warning(
                INITIATIVE_EVALUATION_SKIPPED,
                plan_id=str(plan.id),
                reason="reconcile_unwired",
                note="plan completed; project and objective task will lag",
            )
            return
        await self._reconcile.recompute(plan.id)

    def _read_tools(self, plan: Plan) -> tuple[BaseTool, ...]:
        """Build the read-only tools the judgement runs with.

        Scoped to the plan's own project workspace rather than the shared
        base root, so listing a directory returns the deliverable instead of
        a tree of sibling projects, and the paths in the material resolve as
        written.

        Returns:
            Workspace read tools when a root is wired and the project's
            workspace exists; an empty tuple otherwise. Never a write tool:
            a session that could change what it is judging could turn its
            own failing verdict into a pass.

            An absent workspace yields no tools rather than raising. The
            file tools refuse a missing root, and letting that propagate
            would abort the judgement, burn every attempt and park the plan
            over a project that was simply never provisioned.
        """
        if self._workspace_root is None:
            return ()
        workspace = project_workspace_dir(self._workspace_root, str(plan.project))
        if not workspace.is_dir():
            logger.warning(
                INITIATIVE_EVALUATION_SKIPPED,
                plan_id=str(plan.id),
                project_id=str(plan.project),
                reason="workspace_absent",
                note="judging without workspace reads",
            )
            return ()
        return (
            ReadFileTool(workspace_root=workspace),
            ListDirectoryTool(workspace_root=workspace),
        )

    async def _session_config(self) -> EvaluationSessionConfig:
        """Build the session configuration from live settings.

        Returns:
            An :class:`EvaluationSessionConfig` carrying the current turn cap
            and cost ceiling, so an operator's change applies to the next
            evaluation without a restart.
        """
        return await session_config(self._config_resolver)

    async def _timeout_seconds(self) -> float:
        """Resolve the per-evaluation wall-clock ceiling.

        Returns:
            The configured ceiling, or the default when unresolvable.
        """
        return await timeout_seconds(self._config_resolver)
