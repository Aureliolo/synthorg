# module-kind: complex_service
"""``CompletionOracleGateService`` -- the peer-review gate orchestration.

Drives one evaluation cycle for one deliverable:

1. Select the roster agent that reviews this deliverable: a holder of the
   ``Completion Reviewer`` role, preferring one already on the reviewed
   project's team, excluding the executor, and matched to the capability the
   reviewed work demands. No holder means no independent reviewer exists, so
   ESCALATE (fail-CLOSED) naming that condition; a self-review would defeat
   the whole point.
2. Invoke the reviewer :class:`ReviewerAgentRunner` inside a trusted runtime
   context that pins ``(execution_id, task_id, reviewer, executor)``.
3. Read the reviewer's :class:`CompletionOracleReport` from the per-execution
   repository. If the reviewer did not file one, or dispatch failed, ESCALATE
   (fail-CLOSED) -- never a silent pass.
4. Archive the verdict to the durable cross-process store (fail-OPEN audit).
5. Return a structured :class:`CompletionOracleGateResult`.

The reviewer's own verdict IS the decision, so unlike the red-team gate there
is no severity-rollup routing matrix here. The fail policy is the deliberate
inverse of the red-team / vision gates: those fail OPEN (a verifier defect
must not block completion); this fails CLOSED to a human decision, because an
independent reviewer that silently vanished must not be read as approval.
"""

import asyncio
from typing import TYPE_CHECKING, Final

from synthorg.core.agent import ModelConfig
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import DuplicateRecordError
from synthorg.core.role_catalog import COMPLETION_REVIEWER_ROLE_NAME
from synthorg.core.types import NotBlankStr
from synthorg.engine.completion_oracle.errors import CompletionOracleDispatchError
from synthorg.engine.completion_oracle.protocol import (
    CompletionOracleReportRepository,
    ReviewerAgentRunner,
)
from synthorg.engine.completion_oracle.review_input import CompletionOracleReviewInput
from synthorg.engine.completion_oracle.review_models import (
    CompletionOracleGateResult,
    CompletionOracleReport,
    CompletionOracleReportRecord,
    CompletionOracleVerdict,
)
from synthorg.engine.completion_oracle.runtime_context import (
    CompletionOracleRuntimeContext,
    completion_oracle_runtime_context,
)
from synthorg.engine.routing_policy.capability_ladder import required_capability_for
from synthorg.hr.role_staffing import (
    RoleStaffingSelection,
    RoleStaffingService,
    load_project_for_selection,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.completion_oracle import (
    COMPLETION_ORACLE_AGENT_FAILED,
    COMPLETION_ORACLE_AGENT_INVOKED,
    COMPLETION_ORACLE_GATE_APPROVED,
    COMPLETION_ORACLE_GATE_ESCALATED,
    COMPLETION_ORACLE_GATE_REJECTED,
    COMPLETION_ORACLE_GATE_STARTED,
    COMPLETION_ORACLE_NO_DISTINCT_REVIEWER,
    COMPLETION_ORACLE_PROJECT_READ_FAILED,
    COMPLETION_ORACLE_REPORT_ALREADY_ARCHIVED,
    COMPLETION_ORACLE_REPORT_ARCHIVE_FAILED,
    COMPLETION_ORACLE_REPORT_ARCHIVED,
    COMPLETION_ORACLE_REVIEWER_UNSTAFFED,
    COMPLETION_ORACLE_VERDICT_MISMATCH,
    COMPLETION_ORACLE_VERDICT_MISSING,
    COMPLETION_ORACLE_VERDICT_RECEIVED,
)

if TYPE_CHECKING:
    from synthorg.persistence.completion_oracle_report_protocol import (
        CompletionOracleReportArchiveRepository,
    )
    from synthorg.persistence.project_protocol import ProjectRepository

logger = get_logger(__name__)

_ESCALATE_DISPATCH_SUMMARY: Final[str] = (
    "Completion-reviewer dispatch failed; the gate escalated to a human "
    "decision rather than passing the deliverable unreviewed."
)
_ESCALATE_MISSING_SUMMARY: Final[str] = (
    "Completion-reviewer filed no verdict; the gate escalated to a human "
    "decision rather than passing the deliverable unreviewed."
)
_ESCALATE_NO_REVIEWER_SUMMARY: Final[str] = (
    "No reviewer identity distinct from the executor was resolvable; the gate "
    "escalated to a human decision so the work is not self-reviewed."
)

_ESCALATE_UNSTAFFED_SUMMARY: Final[str] = (
    f"No agent holds the {COMPLETION_REVIEWER_ROLE_NAME} role, so no "
    "independent reviewer could be asked. The gate escalated rather than "
    "passing the deliverable unreviewed; staff the role to resume."
)


class CompletionOracleGateService:
    """Inline peer-review gate orchestrator.

    Args:
        agent_runner: Seam around the reviewer invocation. Production wraps
            :class:`AgentEngine.run`; tests use a scripted runner.
        report_repo: Per-execution storage for the reviewer's verdict.
        staffing: Answers which roster agent holding the Completion Reviewer
            role should judge this deliverable. Asked per review, so the
            reviewer is a peer the org actually staffed rather than a
            singleton the gate carried.
        project_repo: Reads the reviewed work's project so selection can
            prefer a holder already on its team. ``None`` on a
            persistence-less boot, which simply widens selection org-wide.
        report_archive: Optional durable cross-process archive. Wired when
            persistence is connected; ``None`` on a persistence-less boot. The
            archive write is fail-OPEN: an archive error never alters the
            verdict.
        clock: Clock seam. Defaults to :class:`SystemClock`.
    """

    def __init__(
        self,
        *,
        agent_runner: ReviewerAgentRunner,
        report_repo: CompletionOracleReportRepository,
        staffing: RoleStaffingService,
        project_repo: ProjectRepository | None = None,
        report_archive: CompletionOracleReportArchiveRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._agent_runner = agent_runner
        self._report_repo = report_repo
        self._staffing = staffing
        self._project_repo = project_repo
        self._report_archive = report_archive
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def evaluate(
        self,
        review_input: CompletionOracleReviewInput,
    ) -> CompletionOracleGateResult:
        """Run one gate cycle for ``review_input`` and return the verdict.

        Fail-CLOSED policy: an unstaffed reviewer role, a dispatch failure, a
        missing verdict, or an unresolvable distinct reviewer yields an
        ESCALATE result (parked for a human), never a silent pass. Only
        :class:`asyncio.CancelledError` propagates.

        Returns:
            The ``CompletionOracleGateResult`` with the verdict, report, and
            elapsed time.
        """
        started_at = self._clock.monotonic()
        logger.info(
            COMPLETION_ORACLE_GATE_STARTED,
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            executor_agent_id=review_input.executor_agent_id,
        )
        selection: RoleStaffingSelection | None = await self._select_reviewer(
            review_input
        )
        if selection is None:
            logger.warning(
                COMPLETION_ORACLE_REVIEWER_UNSTAFFED,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                role=COMPLETION_REVIEWER_ROLE_NAME,
                executor_agent_id=review_input.executor_agent_id,
                project_id=review_input.project_id,
                fail_closed=True,
            )
            return await self._finalise(
                review_input,
                self._escalate_report(
                    review_input,
                    reviewer_agent_id=None,
                    summary=_ESCALATE_UNSTAFFED_SUMMARY,
                ),
                started_at,
                selection=None,
                reviewer_unstaffed=True,
            )

        reviewer_agent_id = NotBlankStr(str(selection.agent.id))
        if review_input.executor_agent_id == reviewer_agent_id:
            # Selection already excludes the executor, so arriving here means
            # something upstream handed the gate an identity it did not choose.
            logger.warning(
                COMPLETION_ORACLE_NO_DISTINCT_REVIEWER,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                executor_agent_id=review_input.executor_agent_id,
            )
            return await self._finalise(
                review_input,
                self._escalate_report(
                    review_input,
                    reviewer_agent_id=None,
                    summary=_ESCALATE_NO_REVIEWER_SUMMARY,
                ),
                started_at,
                selection=None,
            )

        report, ran_model = await self._invoke_reviewer(review_input, selection)
        return await self._finalise(
            review_input,
            report,
            started_at,
            selection=selection,
            ran_model=ran_model,
        )

    async def _select_reviewer(
        self,
        review_input: CompletionOracleReviewInput,
    ) -> RoleStaffingSelection | None:
        """Choose the roster agent that reviews this deliverable.

        Returns:
            The selection, or ``None`` when no eligible holder exists.
        """
        project = await load_project_for_selection(
            self._project_repo,
            review_input.project_id,
            failure_event=COMPLETION_ORACLE_PROJECT_READ_FAILED,
        )
        required = required_capability_for(
            review_input.stakes,
            review_input.estimated_complexity,
        )
        return await self._staffing.select_holder(
            role=NotBlankStr(COMPLETION_REVIEWER_ROLE_NAME),
            required_capability=required,
            exclude_agent_id=review_input.executor_agent_id,
            project=project,
        )

    async def _finalise(
        self,
        review_input: CompletionOracleReviewInput,
        report: CompletionOracleReport,
        started_at: float,
        *,
        selection: RoleStaffingSelection | None,
        reviewer_unstaffed: bool = False,
        ran_model: ModelConfig | None = None,
    ) -> CompletionOracleGateResult:
        """Log the verdict, archive it, and build the result.

        Args:
            review_input: What was reviewed.
            report: The verdict to record.
            started_at: Monotonic start, for the elapsed measure.
            selection: The reviewer that produced the verdict. ``None`` on
                the paths where no reviewer ran at all.
            reviewer_unstaffed: Whether the escalation is a staffing gap.
            ran_model: The pair the review committed to, which routing or the
                budget may have moved off the selected agent's binding.

        Returns:
            The gate result for ``report``.
        """
        elapsed = max(self._clock.monotonic() - started_at, 0.0)
        self._log_verdict(review_input, report, elapsed)
        await self._archive_report(review_input, report, selection, ran_model)
        return CompletionOracleGateResult(
            verdict=report.verdict,
            report=report,
            elapsed_seconds=elapsed,
            reviewer_unstaffed=reviewer_unstaffed,
        )

    def _log_verdict(
        self,
        review_input: CompletionOracleReviewInput,
        report: CompletionOracleReport,
        elapsed: float,
    ) -> None:
        """Emit the outcome log for a computed verdict."""
        if report.verdict is CompletionOracleVerdict.REJECT:
            event = COMPLETION_ORACLE_GATE_REJECTED
        elif report.verdict is CompletionOracleVerdict.ESCALATE:
            event = COMPLETION_ORACLE_GATE_ESCALATED
        else:
            event = COMPLETION_ORACLE_GATE_APPROVED
        logger.info(
            event,
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            verdict=report.verdict.value,
            findings=len(report.findings),
            elapsed_seconds=elapsed,
        )

    async def _invoke_reviewer(
        self,
        review_input: CompletionOracleReviewInput,
        selection: RoleStaffingSelection,
    ) -> tuple[CompletionOracleReport, ModelConfig | None]:
        """Dispatch the reviewer and fetch its verdict, escalating on any fault.

        Sequences the three reviewer-invocation stages: dispatch the agent
        session, fetch the filed verdict, and validate its pinned identities.
        Any stage that faults short-circuits to a synthetic ESCALATE report so
        an unverifiable review parks the task for a human rather than passing.

        Returns:
            The reviewer's filed report (or a synthetic ESCALATE report when
            dispatch failed, the verdict was missing, or its ids did not
            match), paired with the model the run committed to.

        Raises:
            asyncio.CancelledError: Propagated when the run or fetch is
                cancelled.
        """
        reviewer_agent_id = NotBlankStr(str(selection.agent.id))
        logger.info(
            COMPLETION_ORACLE_AGENT_INVOKED,
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            reviewer_agent_id=reviewer_agent_id,
            capability_fit=selection.capability_fit,
        )
        dispatch_escalation, ran_model = await self._dispatch_reviewer(
            review_input, selection
        )
        if dispatch_escalation is not None:
            return dispatch_escalation, ran_model
        fetched = await self._fetch_verdict(review_input)
        if fetched is None:
            return (
                self._escalate_report(
                    review_input,
                    reviewer_agent_id=reviewer_agent_id,
                    summary=_ESCALATE_MISSING_SUMMARY,
                ),
                ran_model,
            )
        return (
            self._validate_verdict(review_input, fetched, reviewer_agent_id),
            ran_model,
        )

    async def _dispatch_reviewer(
        self,
        review_input: CompletionOracleReviewInput,
        selection: RoleStaffingSelection,
    ) -> tuple[CompletionOracleReport | None, ModelConfig | None]:
        """Run the reviewer agent session inside its trusted runtime context.

        Returns:
            ``(None, ran_model)`` when the reviewer ran, or a synthetic
            ESCALATE report paired with ``None`` when dispatch faulted
            (fail-CLOSED).

        Raises:
            asyncio.CancelledError: Propagated when the run is cancelled.
        """
        reviewer_agent_id = NotBlankStr(str(selection.agent.id))
        trusted_ctx = CompletionOracleRuntimeContext(
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            reviewer_agent_id=reviewer_agent_id,
            executor_agent_id=review_input.executor_agent_id,
        )
        try:
            with completion_oracle_runtime_context(trusted_ctx):
                ran_model = await self._agent_runner.run(
                    review_input=review_input,
                    reviewer=selection.agent,
                )
        except CompletionOracleDispatchError as exc:
            original = exc.__cause__ if exc.__cause__ is not None else exc
            logger.warning(
                COMPLETION_ORACLE_AGENT_FAILED,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                reviewer_agent_id=reviewer_agent_id,
                error_type=type(original).__name__,
                error=safe_error_description(original),
                fail_closed=True,
            )
            return (
                self._escalate_report(
                    review_input,
                    reviewer_agent_id=reviewer_agent_id,
                    summary=_ESCALATE_DISPATCH_SUMMARY,
                ),
                None,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- the runner is only contracted to wrap
            # failures as CompletionOracleDispatchError, but an unwrapped
            # exception must not wedge or silently pass completion: it fails
            # CLOSED to a synthetic ESCALATE so the task parks for a human.
            reraise_critical(exc)
            logger.warning(
                COMPLETION_ORACLE_AGENT_FAILED,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                reviewer_agent_id=reviewer_agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                fail_closed=True,
            )
            return (
                self._escalate_report(
                    review_input,
                    reviewer_agent_id=reviewer_agent_id,
                    summary=_ESCALATE_DISPATCH_SUMMARY,
                ),
                None,
            )
        return None, ran_model

    async def _fetch_verdict(
        self,
        review_input: CompletionOracleReviewInput,
    ) -> CompletionOracleReport | None:
        """Read the reviewer's filed verdict from the per-execution repository.

        Returns:
            The stored report, or ``None`` when the read raised, so the caller
            escalates fail-CLOSED.

        Raises:
            asyncio.CancelledError: Propagated when the fetch is cancelled.
        """
        try:
            return await self._report_repo.get(
                execution_id=review_input.execution_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- a failed verdict read fails CLOSED: the
            # caller returns an ESCALATE report so an unreadable verdict parks
            # the task for a human, never a silent pass.
            reraise_critical(exc)
            logger.warning(
                COMPLETION_ORACLE_VERDICT_MISSING,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                fail_closed=True,
            )
            return None

    def _validate_verdict(
        self,
        review_input: CompletionOracleReviewInput,
        report: CompletionOracleReport,
        reviewer_agent_id: NotBlankStr,
    ) -> CompletionOracleReport:
        """Confirm the filed report's pinned identities match the trusted context.

        Returns:
            ``report`` when all four pinned ids match, else a synthetic
            ESCALATE report (fail-CLOSED).
        """
        if (
            report.execution_id != review_input.execution_id
            or report.task_id != review_input.task_id
            or report.reviewer_agent_id != reviewer_agent_id
            or report.executor_agent_id != review_input.executor_agent_id
        ):
            # All four pinned identities must match the trusted context. Without
            # the reviewer / executor checks a filed report could carry forged
            # identities that satisfy _forbid_self_review() while the real
            # executor reviewed its own work, defeating independent review.
            logger.warning(
                COMPLETION_ORACLE_VERDICT_MISMATCH,
                stored_execution_id=report.execution_id,
                expected_execution_id=review_input.execution_id,
                stored_task_id=report.task_id,
                expected_task_id=review_input.task_id,
                fail_closed=True,
            )
            return self._escalate_report(
                review_input,
                reviewer_agent_id=reviewer_agent_id,
                summary=_ESCALATE_MISSING_SUMMARY,
            )
        logger.info(
            COMPLETION_ORACLE_VERDICT_RECEIVED,
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            verdict=report.verdict.value,
            findings=len(report.findings),
        )
        return report

    async def _archive_report(
        self,
        review_input: CompletionOracleReviewInput,
        report: CompletionOracleReport,
        selection: RoleStaffingSelection | None,
        ran_model: ModelConfig | None,
    ) -> None:
        """Persist the verdict to the durable archive (fail-OPEN audit side-effect).

        The verdict is authoritative and already decided, so an archive write
        failure is logged but never propagated: raising here would un-decide a
        completion the caller has already been given.
        ``asyncio.CancelledError`` and criticals still propagate.

        A uniqueness violation means the exact same insert was replayed: a
        re-review writes its own row rather than colliding. Benign, and
        reported rather than whispered because nothing routine produces it.

        Args:
            review_input: What was reviewed.
            report: The verdict to persist.
            selection: The reviewer, for the attribution columns. ``None``
                leaves them NULL, which is the honest record of a review that
                did not happen.
            ran_model: What the review committed to, for the model columns.

        Raises:
            asyncio.CancelledError: Propagated when the write is cancelled.
        """
        if self._report_archive is None:
            return
        try:
            # Timestamping and record construction sit inside the fail-open
            # boundary too: the verdict is already decided, so a clock or
            # validation error here must be swallowed like an append failure
            # rather than propagate and abort the completion decision.
            record = CompletionOracleReportRecord(
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                verdict=report.verdict,
                report=report,
                recorded_at=self._clock.now(),
                # From the gate's own selection, not the report: a report is
                # written by the thing under scrutiny, so it is not evidence
                # of who wrote it.
                reviewer_agent_id=(
                    None if selection is None else NotBlankStr(str(selection.agent.id))
                ),
                executor_agent_id=review_input.executor_agent_id,
                # What the run committed to, which routing may have raised
                # and the budget may have lowered after selection. The pair
                # the reviewer carries on the roster answers for today; this
                # answers for the review.
                reviewer_provider=None if ran_model is None else ran_model.provider,
                reviewer_model_id=None if ran_model is None else ran_model.model_id,
                reviewer_capability=(
                    None if ran_model is None else ran_model.capability
                ),
            )
            await self._report_archive.append(record)
        except DuplicateRecordError:
            logger.info(
                COMPLETION_ORACLE_REPORT_ALREADY_ARCHIVED,
                execution_id=review_input.execution_id,
                note="identical report already archived; the insert was replayed",
            )
            return
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- the durable archive is a best-effort
            # audit side channel; the verdict is already decided and returned,
            # so an archive-write failure is logged and swallowed rather than
            # altering or blocking the completion decision (fail-OPEN by design).
            reraise_critical(exc)
            logger.warning(
                COMPLETION_ORACLE_REPORT_ARCHIVE_FAILED,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                policy="fail_open",
            )
            return
        logger.info(
            COMPLETION_ORACLE_REPORT_ARCHIVED,
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            verdict=report.verdict.value,
        )

    @staticmethod
    def _escalate_report(
        review_input: CompletionOracleReviewInput,
        *,
        reviewer_agent_id: str | None,
        summary: str,
    ) -> CompletionOracleReport:
        """Build the synthetic ESCALATE report for a fail-closed path.

        Args:
            review_input: What was under review.
            reviewer_agent_id: The reviewer, when one ran. ``None`` when the
                escalation IS that none did, which the verdict and summary
                already spell out; a placeholder id here would enter the
                archive column the per-reviewer surface reads and be counted
                as a judge.
            summary: Prose naming the condition.

        Returns:
            A ``CompletionOracleReport`` carrying an ESCALATE verdict.
        """
        return CompletionOracleReport(
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            reviewer_agent_id=reviewer_agent_id,
            executor_agent_id=review_input.executor_agent_id,
            verdict=CompletionOracleVerdict.ESCALATE,
            findings=(),
            summary=summary,
        )
