# module-kind: service
"""``RedTeamGateService`` -- the gate's outward orchestration.

Drives one evaluation cycle for one deliverable:

0. Select the roster agent that attacks this deliverable: a holder of the
   ``Red Team`` role, preferring one already on the reviewed project's team,
   excluding the executor, matched to the capability the reviewed work
   demands. No holder is the one condition this gate does NOT fail open on:
   an unstaffed role is a configuration state, not a verifier defect, so the
   task parks under its own blocked reason until somebody holds the role.
1. Invoke the :class:`AgentRunner` (production: wraps
   :class:`AgentEngine.run` on a transient red-team task; tests: a
   scripted runner that writes a pre-built report into the repo).
2. Read the agent's :class:`RedTeamReport` from the per-execution
   :class:`RedTeamReportRepository`. If the agent did not file one,
   fail-OPEN with a synthetic INFO finding instead of breaking
   completion.
3. Run the configured :class:`GroundingChecker` and convert its
   :class:`UngroundedClaim` entries into :class:`RedTeamFinding` entries
   on the GROUNDING attack surface. Heuristic-source claims are capped at
   :data:`HEURISTIC_GROUNDING_MAX_SEVERITY` (LOW); substrate-source claims
   escalate by confidence up to :data:`SUBSTRATE_GROUNDING_MAX_SEVERITY`
   (HIGH) and can BLOCK.
4. Compute the verdict over the FULL set of findings (agent + grounding)
   under the deliverable's autonomy posture.
5. Return a structured :class:`RedTeamGateResult`.
"""

import asyncio
from typing import TYPE_CHECKING, Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.project import Project
from synthorg.core.redteam_review_input import RedTeamReviewInput
from synthorg.core.role_catalog import RED_TEAM_ROLE_NAME
from synthorg.core.types import NotBlankStr
from synthorg.engine.routing_policy.capability_ladder import required_capability_for
from synthorg.hr.role_staffing import RoleStaffingSelection, RoleStaffingService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.red_team import (
    RED_TEAM_AGENT_FAILED,
    RED_TEAM_AGENT_INVOKED,
    RED_TEAM_GATE_BLOCKED,
    RED_TEAM_GATE_PASSED,
    RED_TEAM_GATE_STARTED,
    RED_TEAM_GROUNDING_CHECK_COMPLETED,
    RED_TEAM_GROUNDING_CHECK_FAILED,
    RED_TEAM_GROUNDING_CHECK_STARTED,
    RED_TEAM_PROJECT_READ_FAILED,
    RED_TEAM_REPORT_ALREADY_ARCHIVED,
    RED_TEAM_REPORT_ARCHIVE_FAILED,
    RED_TEAM_REPORT_ARCHIVED,
    RED_TEAM_REPORT_EXECUTION_ID_MISMATCH,
    RED_TEAM_REPORT_MISSING,
    RED_TEAM_REPORT_RECEIVED,
    RED_TEAM_UNSTAFFED,
)
from synthorg.security.redteam._grounding_findings import claim_to_finding
from synthorg.security.redteam.errors import RedTeamDispatchError
from synthorg.security.redteam.grounding.models import UngroundedClaim
from synthorg.security.redteam.grounding.protocol import GroundingChecker
from synthorg.security.redteam.models import (
    RedTeamAttackSurface,
    RedTeamFinding,
    RedTeamGateResult,
    RedTeamReport,
    RedTeamReportRecord,
    RedTeamSeverity,
    RedTeamVerdict,
)
from synthorg.security.redteam.protocol import (
    AgentRunner,
    RedTeamReportRepository,
)
from synthorg.security.redteam.routing import compute_red_team_verdict
from synthorg.security.redteam.runtime_context import (
    RedTeamRuntimeContext,
    red_team_runtime_context,
)

if TYPE_CHECKING:
    # ``persistence.red_team_report_protocol`` imports ``redteam.models``, which
    # is mid-init when ``redteam.__init__`` eagerly loads this gate; a
    # module-level import here closes that cycle. Kept guarded.
    from synthorg.persistence.project_protocol import ProjectRepository
    from synthorg.persistence.red_team_report_protocol import (
        RedTeamReportArchiveRepository,
    )

logger = get_logger(__name__)

_AGENT_FAILED_SUMMARY: Final[str] = (
    "Red-team agent did not produce a report; the gate fell back to a "
    "fail-OPEN INFO finding so completion is not blocked by an agent fault."
)
"""Synthetic summary for the report we build when the agent fails."""

_AGENT_FAILED_FINDING_DESCRIPTION: Final[str] = (
    "Red-team agent dispatch returned without filing a "
    "submit_red_team_report tool call. Treat this as a degraded review."
)

_UNSTAFFED_SUMMARY: Final[str] = (
    f"No agent holds the {RED_TEAM_ROLE_NAME} role, so no adversary could be "
    "asked to attack this deliverable. The gate blocked rather than passing "
    "work its configured adversarial review never saw; staff the role to "
    "resume."
)

_UNSTAFFED_FINDING_DESCRIPTION: Final[str] = (
    f"The {RED_TEAM_ROLE_NAME} role is unstaffed, so the configured "
    "adversarial review did not run. This is a staffing gap, not a finding "
    "about the deliverable."
)


class RedTeamGateService:
    """Inline gate orchestrator.

    Args:
        agent_runner: Seam around the agent invocation. Production
            implementation wraps :class:`AgentEngine.run`; tests use
            a scripted runner that writes the report directly.
        report_repo: Per-execution storage for the agent's report.
        staffing: Answers which roster agent holding the ``Red Team`` role
            attacks this deliverable. Asked per evaluation, so the adversary
            is a peer the org actually staffed rather than a singleton the
            gate carried.
        grounding_checker: Configured grounding checker (heuristic or
            substrate-backed). Capped at
            :data:`HEURISTIC_GROUNDING_MAX_SEVERITY` when source is
            ``"heuristic"``.
        project_repo: Reads the reviewed work's project so selection can
            prefer a holder already on its team. ``None`` on a
            persistence-less boot, which simply widens selection org-wide.
        report_archive: Optional durable cross-process archive. When
            wired (persistence is connected), every evaluation's merged
            report + verdict is persisted as a
            :class:`RedTeamReportRecord` so the flight-recorder read
            surface can surface the verdict long after the run. ``None``
            in a persistence-less boot; archival is then skipped. The
            write is fail-OPEN: an archive error never alters the verdict.
        clock: Clock seam. Production passes :class:`SystemClock`;
            tests pass :class:`FakeClock`. Defaults to
            :class:`SystemClock` so wiring stays terse for operators.
    """

    def __init__(
        self,
        *,
        agent_runner: AgentRunner,
        report_repo: RedTeamReportRepository,
        staffing: RoleStaffingService,
        grounding_checker: GroundingChecker,
        project_repo: ProjectRepository | None = None,
        report_archive: RedTeamReportArchiveRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._agent_runner = agent_runner
        self._report_repo = report_repo
        self._staffing = staffing
        self._grounding_checker = grounding_checker
        self._project_repo = project_repo
        self._report_archive = report_archive
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def evaluate(
        self,
        review_input: RedTeamReviewInput,
    ) -> RedTeamGateResult:
        """Run one gate cycle for ``review_input`` and return the verdict.

        Fail-OPEN policy: any single internal failure (agent did not
        file a report, grounding checker raised) is logged at WARNING and
        the gate proceeds with whatever signal it has, surfacing an
        informational finding on the audit trail. The gate raises only
        when the input itself is malformed (caught by Pydantic at the
        boundary, not here).

        Returns:
            The ``RedTeamGateResult`` with the verdict, merged report,
            grounding claims, and elapsed time.
        """
        started_at = self._clock.monotonic()
        logger.info(
            RED_TEAM_GATE_STARTED,
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            autonomy=review_input.autonomy.value,
        )
        selection = await self._select_red_teamer(review_input)
        if selection is None:
            return await self._unstaffed_result(review_input, started_at)
        report = await self._invoke_agent(review_input, selection)
        grounding_claims = await self._run_grounding(review_input)

        grounding_findings = tuple(
            finding
            for claim in grounding_claims
            if (finding := claim_to_finding(claim)) is not None
        )
        all_findings = report.findings + grounding_findings

        verdict = compute_red_team_verdict(all_findings, review_input.autonomy)
        elapsed = max(self._clock.monotonic() - started_at, 0.0)

        if verdict is RedTeamVerdict.BLOCK:
            logger.warning(
                RED_TEAM_GATE_BLOCKED,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                findings=len(all_findings),
                grounding_findings=len(grounding_findings),
                elapsed_seconds=elapsed,
            )
        else:
            logger.info(
                RED_TEAM_GATE_PASSED,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                verdict=verdict.value,
                findings=len(all_findings),
                elapsed_seconds=elapsed,
            )

        merged_report = report.model_copy(update={"findings": all_findings})
        await self._archive_report(review_input, merged_report, verdict, selection)
        return RedTeamGateResult(
            verdict=verdict,
            report=merged_report,
            grounding_claims=grounding_claims,
            elapsed_seconds=elapsed,
        )

    async def _select_red_teamer(
        self,
        review_input: RedTeamReviewInput,
    ) -> RoleStaffingSelection | None:
        """Choose the roster agent that attacks this deliverable.

        Returns:
            The selection, or ``None`` when no eligible holder exists.
        """
        return await self._staffing.select_holder(
            role=NotBlankStr(RED_TEAM_ROLE_NAME),
            required_capability=required_capability_for(
                review_input.stakes,
                review_input.estimated_complexity,
            ),
            exclude_agent_id=review_input.assigned_agent_id,
            project=await self._load_project(review_input.project_id),
        )

    async def _load_project(self, project_id: NotBlankStr | None) -> Project | None:
        """Read the reviewed work's project, tolerating an unavailable store.

        A read failure only costs the on-team PREFERENCE: a gate role reaches
        every project regardless, so selection widens org-wide rather than
        blocking.

        Returns:
            The project, or ``None`` when there is none to read.

        Raises:
            asyncio.CancelledError: Propagated when the read is cancelled.
        """
        if self._project_repo is None or project_id is None:
            return None
        try:
            return await self._project_repo.get(project_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                RED_TEAM_PROJECT_READ_FAILED,
                project_id=project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="selecting an adversary org-wide instead of preferring the team",
            )
            return None

    async def _unstaffed_result(
        self,
        review_input: RedTeamReviewInput,
        started_at: float,
    ) -> RedTeamGateResult:
        """Block the completion because the ``Red Team`` role is unstaffed.

        The deliberate exception to this gate's fail-OPEN posture. That
        ruling covers a VERIFIER DEFECT: an adversary that ran and faulted
        must not hold up the org. Nobody holding the role is a different
        thing entirely, a configuration state an operator can fix, and
        passing work that an enabled adversarial gate never saw would make
        the gate's guarantee depend on whether anyone remembered to staff it.

        Returns:
            A BLOCK result flagged ``red_team_unstaffed`` so the caller parks
            the task under its own reason rather than sending it to rework
            the agent cannot do.
        """
        logger.warning(
            RED_TEAM_UNSTAFFED,
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            role=RED_TEAM_ROLE_NAME,
            executor_agent_id=review_input.assigned_agent_id,
            project_id=review_input.project_id,
            fail_closed=True,
        )
        report = RedTeamReport(
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            findings=(
                RedTeamFinding(
                    attack_surface=RedTeamAttackSurface.CORRECTNESS,
                    severity=RedTeamSeverity.INFO,
                    description=_UNSTAFFED_FINDING_DESCRIPTION,
                    evidence=(),
                    source="agent",
                ),
            ),
            summary=_UNSTAFFED_SUMMARY,
        )
        elapsed = max(self._clock.monotonic() - started_at, 0.0)
        await self._archive_report(review_input, report, RedTeamVerdict.BLOCK, None)
        return RedTeamGateResult(
            verdict=RedTeamVerdict.BLOCK,
            report=report,
            grounding_claims=(),
            elapsed_seconds=elapsed,
            red_team_unstaffed=True,
        )

    async def _archive_report(
        self,
        review_input: RedTeamReviewInput,
        merged_report: RedTeamReport,
        verdict: RedTeamVerdict,
        selection: RoleStaffingSelection | None,
    ) -> None:
        """Persist the merged report + verdict to the durable archive.

        Fail-OPEN audit side-effect: the gate verdict is authoritative and
        already drives the block decision, so an archive write failure is
        logged but never propagated and never alters the result. A
        duplicate execution (a re-run for the same ``execution_id``) is a
        benign no-op logged at DEBUG. ``asyncio.CancelledError`` and true
        programming errors still propagate.

        Args:
            review_input: The evaluated input (supplies the keys).
            merged_report: The merged report (agent + grounding findings).
            verdict: The aggregate verdict the gate computed.
            selection: The adversary that produced it, so the archive records
                who attacked and on what pair. ``None`` when none ran.

        Raises:
            asyncio.CancelledError: Propagated when the archive write is
                cancelled, so the awaiting parent observes the cancel.
        """
        if self._report_archive is None:
            return
        model = selection.agent.model if selection is not None else None
        record = RedTeamReportRecord(
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            verdict=verdict,
            report=merged_report,
            recorded_at=self._clock.now(),
            # From the gate's own selection, not the report: a report is
            # written by the thing under scrutiny, so it is not evidence of
            # who wrote it or what it ran on.
            red_team_agent_id=(
                None if selection is None else NotBlankStr(str(selection.agent.id))
            ),
            executor_agent_id=review_input.assigned_agent_id,
            red_team_provider=None if model is None else model.provider,
            red_team_model_id=None if model is None else model.model_id,
            red_team_capability=None if model is None else model.capability,
        )
        try:
            await self._report_archive.append(record)
        except DuplicateRecordError:
            logger.debug(
                RED_TEAM_REPORT_ALREADY_ARCHIVED,
                execution_id=review_input.execution_id,
                note="already archived for this execution",
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                RED_TEAM_REPORT_ARCHIVE_FAILED,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                policy="fail_open",
            )
            return
        logger.info(
            RED_TEAM_REPORT_ARCHIVED,
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            verdict=verdict.value,
            findings=len(merged_report.findings),
        )

    async def _invoke_agent(
        self,
        review_input: RedTeamReviewInput,
        selection: RoleStaffingSelection,
    ) -> RedTeamReport:
        """Dispatch the agent and fetch its report, with fail-OPEN fallback.

        Cancellation propagates: an ``asyncio.CancelledError`` from the
        agent run must NOT be converted to a fail-OPEN finding, because
        the cancelling parent task needs to observe the cancellation.
        Only :class:`RedTeamDispatchError` from the runner (and the
        engine's own non-cancellation faults wrapped by it) trigger the
        fail-OPEN policy.

        Returns:
            The agent's filed report, or a synthetic fail-OPEN report
            when the agent failed, the report was missing, or its
            execution/task ids did not match.

        Raises:
            asyncio.CancelledError: Propagated when the agent run or
                report fetch is cancelled.
        """
        logger.info(
            RED_TEAM_AGENT_INVOKED,
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            red_team_agent_id=str(selection.agent.id),
            capability_fit=selection.capability_fit,
        )
        trusted_ctx = RedTeamRuntimeContext(
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
        )
        try:
            with red_team_runtime_context(trusted_ctx):
                await self._agent_runner.run(
                    review_input=review_input,
                    red_teamer=selection.agent,
                )
        except RedTeamDispatchError as exc:
            original = exc.__cause__ if exc.__cause__ is not None else exc
            logger.warning(
                RED_TEAM_AGENT_FAILED,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                error_type=type(original).__name__,
                error=safe_error_description(original),
                gate_degraded=True,
            )
            return self._fail_open_report(review_input)

        try:
            report = await self._report_repo.get(
                execution_id=review_input.execution_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                RED_TEAM_REPORT_MISSING,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                gate_degraded=True,
            )
            return self._fail_open_report(review_input)

        if (
            report.execution_id != review_input.execution_id
            or report.task_id != review_input.task_id
        ):
            logger.warning(
                RED_TEAM_REPORT_EXECUTION_ID_MISMATCH,
                stored_execution_id=report.execution_id,
                expected_execution_id=review_input.execution_id,
                stored_task_id=report.task_id,
                expected_task_id=review_input.task_id,
                gate_degraded=True,
            )
            return self._fail_open_report(review_input)
        logger.info(
            RED_TEAM_REPORT_RECEIVED,
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            findings=len(report.findings),
        )
        return report

    async def _run_grounding(
        self,
        review_input: RedTeamReviewInput,
    ) -> tuple[UngroundedClaim, ...]:
        """Run the grounding checker; return empty tuple on non-cancellation failure.

        Cancellation propagates: ``asyncio.CancelledError`` is re-raised so
        the awaiting parent task observes it. All other exceptions are
        treated as fail-OPEN (the grounding checker is best-effort and
        should not block the gate on transient corpus or provider
        failures).

        Returns:
            The grounding claims, or an empty tuple on non-cancellation
            failure (fail-OPEN).

        Raises:
            asyncio.CancelledError: Propagated when the grounding check
                is cancelled.
        """
        logger.info(
            RED_TEAM_GROUNDING_CHECK_STARTED,
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
        )
        try:
            claims = await self._grounding_checker.check(
                deliverable_content=review_input.deliverable_content,
                execution_id=review_input.execution_id,
                project_id=review_input.project_id,
                task_id=review_input.task_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                RED_TEAM_GROUNDING_CHECK_FAILED,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                policy="fail_open",
            )
            return ()
        logger.info(
            RED_TEAM_GROUNDING_CHECK_COMPLETED,
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            claims=len(claims),
        )
        return claims

    @staticmethod
    def _fail_open_report(review_input: RedTeamReviewInput) -> RedTeamReport:
        """Build the synthetic report we return when the agent fails to file.

        Returns:
            A ``RedTeamReport`` carrying a single INFO agent-failed
            finding.
        """
        synthetic_finding = RedTeamFinding(
            attack_surface=RedTeamAttackSurface.CORRECTNESS,
            severity=RedTeamSeverity.INFO,
            description=_AGENT_FAILED_FINDING_DESCRIPTION,
            evidence=(),
            source="agent",
        )
        return RedTeamReport(
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            findings=(synthetic_finding,),
            summary=_AGENT_FAILED_SUMMARY,
        )
