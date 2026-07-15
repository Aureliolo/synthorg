# module-kind: complex_service
"""``CompletionOracleGateService`` -- the peer-review gate orchestration.

Drives one evaluation cycle for one deliverable:

1. Enforce that a reviewer identity distinct from the executor exists. If
   not, ESCALATE (fail-CLOSED); the whole point of an independent reviewer
   is defeated by a self-review.
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

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import DuplicateRecordError
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
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.completion_oracle import (
    COMPLETION_ORACLE_AGENT_FAILED,
    COMPLETION_ORACLE_AGENT_INVOKED,
    COMPLETION_ORACLE_GATE_APPROVED,
    COMPLETION_ORACLE_GATE_ESCALATED,
    COMPLETION_ORACLE_GATE_REJECTED,
    COMPLETION_ORACLE_GATE_STARTED,
    COMPLETION_ORACLE_NO_DISTINCT_REVIEWER,
    COMPLETION_ORACLE_REPORT_ALREADY_ARCHIVED,
    COMPLETION_ORACLE_REPORT_ARCHIVE_FAILED,
    COMPLETION_ORACLE_REPORT_ARCHIVED,
    COMPLETION_ORACLE_VERDICT_MISMATCH,
    COMPLETION_ORACLE_VERDICT_MISSING,
    COMPLETION_ORACLE_VERDICT_RECEIVED,
)

if TYPE_CHECKING:
    from synthorg.persistence.completion_oracle_report_protocol import (
        CompletionOracleReportArchiveRepository,
    )

logger = get_logger(__name__)

_NO_DISTINCT_REVIEWER_SENTINEL: Final[str] = "completion-oracle:unresolved-reviewer"
"""Reviewer id stamped on the escalate report when no distinct reviewer is
resolvable, so the archive record stays valid (distinct from any executor)
while truthfully signalling that no independent review occurred."""

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


class CompletionOracleGateService:
    """Inline peer-review gate orchestrator.

    Args:
        agent_runner: Seam around the reviewer invocation. Production wraps
            :class:`AgentEngine.run`; tests use a scripted runner.
        report_repo: Per-execution storage for the reviewer's verdict.
        reviewer_agent_id: The built-in reviewer's stable agent id, used to
            enforce distinctness and to stamp the trusted context.
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
        reviewer_agent_id: NotBlankStr,
        report_archive: CompletionOracleReportArchiveRepository | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._agent_runner = agent_runner
        self._report_repo = report_repo
        self._reviewer_agent_id = reviewer_agent_id
        self._report_archive = report_archive
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def evaluate(
        self,
        review_input: CompletionOracleReviewInput,
    ) -> CompletionOracleGateResult:
        """Run one gate cycle for ``review_input`` and return the verdict.

        Fail-CLOSED policy: a dispatch failure, a missing verdict, or an
        unresolvable distinct reviewer yields an ESCALATE result (parked for a
        human), never a silent pass. Only :class:`asyncio.CancelledError`
        propagates.

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
        if review_input.executor_agent_id == self._reviewer_agent_id:
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
                    reviewer_agent_id=_NO_DISTINCT_REVIEWER_SENTINEL,
                    summary=_ESCALATE_NO_REVIEWER_SUMMARY,
                ),
                started_at,
            )

        report = await self._invoke_reviewer(review_input)
        return await self._finalise(review_input, report, started_at)

    async def _finalise(
        self,
        review_input: CompletionOracleReviewInput,
        report: CompletionOracleReport,
        started_at: float,
    ) -> CompletionOracleGateResult:
        """Log the verdict, archive it, and build the result.

        Returns:
            The gate result for ``report``.
        """
        elapsed = max(self._clock.monotonic() - started_at, 0.0)
        self._log_verdict(review_input, report, elapsed)
        await self._archive_report(review_input, report)
        return CompletionOracleGateResult(
            verdict=report.verdict,
            report=report,
            elapsed_seconds=elapsed,
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
    ) -> CompletionOracleReport:
        """Dispatch the reviewer and fetch its verdict, escalating on any fault.

        Returns:
            The reviewer's filed report, or a synthetic ESCALATE report when
            dispatch failed, the verdict was missing, or its ids did not match.

        Raises:
            asyncio.CancelledError: Propagated when the run or fetch is
                cancelled.
        """
        logger.info(
            COMPLETION_ORACLE_AGENT_INVOKED,
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
        )
        trusted_ctx = CompletionOracleRuntimeContext(
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            reviewer_agent_id=self._reviewer_agent_id,
            executor_agent_id=review_input.executor_agent_id,
        )
        try:
            with completion_oracle_runtime_context(trusted_ctx):
                await self._agent_runner.run(review_input=review_input)
        except CompletionOracleDispatchError as exc:
            original = exc.__cause__ if exc.__cause__ is not None else exc
            logger.warning(
                COMPLETION_ORACLE_AGENT_FAILED,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                error_type=type(original).__name__,
                error=safe_error_description(original),
                fail_closed=True,
            )
            return self._escalate_report(
                review_input,
                reviewer_agent_id=self._reviewer_agent_id,
                summary=_ESCALATE_DISPATCH_SUMMARY,
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
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                fail_closed=True,
            )
            return self._escalate_report(
                review_input,
                reviewer_agent_id=self._reviewer_agent_id,
                summary=_ESCALATE_DISPATCH_SUMMARY,
            )

        try:
            report = await self._report_repo.get(
                execution_id=review_input.execution_id,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- a failed verdict read fails CLOSED: the
            # handler returns an ESCALATE report so an unreadable verdict parks
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
            return self._escalate_report(
                review_input,
                reviewer_agent_id=self._reviewer_agent_id,
                summary=_ESCALATE_MISSING_SUMMARY,
            )

        if (
            report.execution_id != review_input.execution_id
            or report.task_id != review_input.task_id
            or report.reviewer_agent_id != self._reviewer_agent_id
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
                reviewer_agent_id=self._reviewer_agent_id,
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
    ) -> None:
        """Persist the verdict to the durable archive (fail-OPEN audit side-effect).

        The verdict is authoritative and already decided, so an archive write
        failure is logged but never propagated. A duplicate execution is a
        benign no-op. ``asyncio.CancelledError`` and criticals still propagate.

        Raises:
            asyncio.CancelledError: Propagated when the write is cancelled.
        """
        if self._report_archive is None:
            return
        record = CompletionOracleReportRecord(
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            verdict=report.verdict,
            report=report,
            recorded_at=self._clock.now(),
        )
        try:
            await self._report_archive.append(record)
        except DuplicateRecordError:
            logger.debug(
                COMPLETION_ORACLE_REPORT_ALREADY_ARCHIVED,
                execution_id=review_input.execution_id,
                note="already archived for this execution",
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
        reviewer_agent_id: str,
        summary: str,
    ) -> CompletionOracleReport:
        """Build the synthetic ESCALATE report for a fail-closed path.

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
