"""``RedTeamGateService`` -- the gate's outward orchestration.

Drives one evaluation cycle for one deliverable:

1. Invoke the :class:`AgentRunner` (production: wraps
   :class:`AgentEngine.run` on a transient red-team task; tests: a
   scripted runner that writes a pre-built report into the repo).
2. Read the agent's :class:`RedTeamReport` from the per-execution
   :class:`RedTeamReportRepository`. If the agent did not file one,
   fail-OPEN with a synthetic INFO finding instead of breaking
   completion.
3. Run the configured :class:`GroundingChecker` and convert its
   :class:`UngroundedClaim` entries into ``source="heuristic"``
   :class:`RedTeamFinding` entries on the GROUNDING attack surface,
   capped at :data:`HEURISTIC_GROUNDING_MAX_SEVERITY`.
4. Compute the verdict over the FULL set of findings (agent + heuristic)
   under the deliverable's autonomy posture.
5. Return a structured :class:`RedTeamGateResult`.
"""

import asyncio
from typing import TYPE_CHECKING, Final

from synthorg.core.clock import Clock, SystemClock
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
    RED_TEAM_REPORT_EXECUTION_ID_MISMATCH,
    RED_TEAM_REPORT_MISSING,
    RED_TEAM_REPORT_RECEIVED,
)
from synthorg.security.redteam.errors import RedTeamDispatchError
from synthorg.security.redteam.grounding.protocol import GroundingChecker  # noqa: TC001
from synthorg.security.redteam.models import (
    RedTeamAttackSurface,
    RedTeamFinding,
    RedTeamGateResult,
    RedTeamReport,
    RedTeamReviewInput,
    RedTeamSeverity,
    RedTeamVerdict,
)
from synthorg.security.redteam.protocol import (
    AgentRunner,  # noqa: TC001
    RedTeamReportRepository,  # noqa: TC001
)
from synthorg.security.redteam.routing import (
    HEURISTIC_GROUNDING_MAX_SEVERITY,
    compute_red_team_verdict,
)
from synthorg.security.redteam.runtime_context import (
    RedTeamRuntimeContext,
    red_team_runtime_context,
)

if TYPE_CHECKING:
    from synthorg.security.redteam.grounding.models import UngroundedClaim

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


_MAX_EVIDENCE_EXCERPT_CHARS: Final[int] = 240
"""Cap on the length of a heuristic-derived evidence excerpt.

Long excerpts pollute the rework brief; the cap keeps a finding
self-contained while leaving room for one full sentence.
"""

_ELLIPSIS: Final[str] = "..."
_ELLIPSIS_OVERHEAD: Final[int] = len(_ELLIPSIS)
"""Characters the truncation ellipsis consumes, reserved from the cap."""


def _evidence_excerpt(
    claim: UngroundedClaim,
    *,
    max_chars: int = _MAX_EVIDENCE_EXCERPT_CHARS,
) -> str:
    """Truncate a claim's excerpt to a bounded length for finding evidence."""
    if len(claim.excerpt) <= max_chars:
        return claim.excerpt
    return f"{claim.excerpt[: max_chars - _ELLIPSIS_OVERHEAD]}{_ELLIPSIS}"


def _claim_to_finding(claim: UngroundedClaim) -> RedTeamFinding:
    """Convert a heuristic :class:`UngroundedClaim` into a :class:`RedTeamFinding`.

    Always at or below :data:`HEURISTIC_GROUNDING_MAX_SEVERITY` (LOW)
    so the stub cannot block on its own; only the LLM agent or a
    substrate-backed checker may file blocking grounding findings.
    """
    return RedTeamFinding(
        attack_surface=RedTeamAttackSurface.GROUNDING,
        severity=HEURISTIC_GROUNDING_MAX_SEVERITY,
        description=f"Ungrounded claim: {claim.reason}",
        evidence=(_evidence_excerpt(claim),),
        suggested_fix=(
            "Cite the originating source for this claim or remove the "
            "assertion. Hedging language is also acceptable for soft claims."
        ),
        source=claim.source,
        citations=(),
    )


class RedTeamGateService:
    """Inline gate orchestrator.

    Args:
        agent_runner: Seam around the agent invocation. Production
            implementation wraps :class:`AgentEngine.run`; tests use
            a scripted runner that writes the report directly.
        report_repo: Per-execution storage for the agent's report.
        grounding_checker: Configured grounding checker (heuristic or
            substrate-backed). Capped at
            :data:`HEURISTIC_GROUNDING_MAX_SEVERITY` when source is
            ``"heuristic"``.
        clock: Clock seam. Production passes :class:`SystemClock`;
            tests pass :class:`FakeClock`. Defaults to
            :class:`SystemClock` so wiring stays terse for operators.
    """

    def __init__(
        self,
        *,
        agent_runner: AgentRunner,
        report_repo: RedTeamReportRepository,
        grounding_checker: GroundingChecker,
        clock: Clock | None = None,
    ) -> None:
        self._agent_runner = agent_runner
        self._report_repo = report_repo
        self._grounding_checker = grounding_checker
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def evaluate(
        self,
        review_input: RedTeamReviewInput,
    ) -> RedTeamGateResult:
        """Run one gate cycle for ``review_input`` and return the verdict.

        Fail-OPEN policy: any single internal failure (agent did not
        file a report, grounding stub raised) is logged at WARNING and
        the gate proceeds with whatever signal it has, surfacing an
        informational finding on the audit trail. The gate raises only
        when the input itself is malformed (caught by Pydantic at the
        boundary, not here).
        """
        started_at = self._clock.monotonic()
        logger.info(
            RED_TEAM_GATE_STARTED,
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            autonomy=review_input.autonomy.value,
        )
        report = await self._invoke_agent(review_input)
        grounding_claims = await self._run_grounding(review_input)

        heuristic_findings = tuple(_claim_to_finding(c) for c in grounding_claims)
        all_findings = report.findings + heuristic_findings

        verdict = compute_red_team_verdict(all_findings, review_input.autonomy)
        elapsed = max(self._clock.monotonic() - started_at, 0.0)

        if verdict is RedTeamVerdict.BLOCK:
            logger.warning(
                RED_TEAM_GATE_BLOCKED,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                findings=len(all_findings),
                heuristic_findings=len(heuristic_findings),
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
        return RedTeamGateResult(
            verdict=verdict,
            report=merged_report,
            grounding_claims=grounding_claims,
            elapsed_seconds=elapsed,
        )

    async def _invoke_agent(
        self,
        review_input: RedTeamReviewInput,
    ) -> RedTeamReport:
        """Dispatch the agent and fetch its report, with fail-OPEN fallback.

        Cancellation propagates: an ``asyncio.CancelledError`` from the
        agent run must NOT be converted to a fail-OPEN finding, because
        the cancelling parent task needs to observe the cancellation.
        Only :class:`RedTeamDispatchError` from the runner (and the
        engine's own non-cancellation faults wrapped by it) trigger the
        fail-OPEN policy.
        """
        logger.info(
            RED_TEAM_AGENT_INVOKED,
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
        )
        trusted_ctx = RedTeamRuntimeContext(
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
        )
        try:
            with red_team_runtime_context(trusted_ctx):
                await self._agent_runner.run(review_input=review_input)
        except RedTeamDispatchError as exc:
            original = exc.__cause__ if exc.__cause__ is not None else exc
            logger.warning(
                RED_TEAM_AGENT_FAILED,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                error_type=type(original).__name__,
                error=safe_error_description(original),
            )
            return self._fail_open_report(review_input)

        try:
            report = await self._report_repo.get(
                execution_id=review_input.execution_id,
            )
        except asyncio.CancelledError:
            raise
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                RED_TEAM_REPORT_MISSING,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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
        treated as fail-OPEN (heuristic stub is best-effort, substrate
        implementations should not block the gate on transient corpus
        failures).
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
            )
        except asyncio.CancelledError:
            raise
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
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
        """Build the synthetic report we return when the agent fails to file."""
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
