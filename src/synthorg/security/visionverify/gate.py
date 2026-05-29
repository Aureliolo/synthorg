"""``VisionVerifierGateService`` -- the vision gate's outward orchestration.

Drives one evaluation cycle for one deliverable:

1. Invoke the configured :class:`VisionVerifier` (noop / heuristic /
   llm_vision) to produce a structured report. A verifier failure is
   converted to a synthetic INFO finding (fail-OPEN) so a verifier fault
   never blocks completion.
2. Compute the verdict over the report's findings under the
   deliverable's autonomy posture.
3. Return a structured :class:`VisionGateResult`.
"""

import asyncio
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.vision_verify import (
    VISION_GATE_BLOCKED,
    VISION_GATE_PASSED,
    VISION_GATE_STARTED,
    VISION_VERIFIER_FAILED,
    VISION_VERIFIER_INVOKED,
)
from synthorg.security.visionverify.models import (
    VisionFinding,
    VisionFindingCategory,
    VisionGateResult,
    VisionReviewInput,
    VisionSeverity,
    VisionVerdict,
    VisionVerificationReport,
)
from synthorg.security.visionverify.protocol import VisionVerifier
from synthorg.security.visionverify.routing import compute_vision_verdict

logger = get_logger(__name__)

_VERIFIER_FAILED_SUMMARY: Final[str] = (
    "Vision verifier raised before producing a report; the gate fell back "
    "to a fail-OPEN INFO finding so completion is not blocked by a fault."
)
_VERIFIER_FAILED_DESCRIPTION: Final[str] = (
    "Vision verifier dispatch failed before returning a structured report. "
    "Treat this as a degraded review."
)


class VisionVerifierGateService:
    """Inline vision gate orchestrator wrapping a :class:`VisionVerifier`.

    Args:
        verifier: The pluggable verification strategy.
        clock: Clock seam. Production passes :class:`SystemClock`; tests
            pass :class:`FakeClock`. Defaults to :class:`SystemClock`.
    """

    def __init__(
        self,
        *,
        verifier: VisionVerifier,
        clock: Clock | None = None,
    ) -> None:
        self._verifier = verifier
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def evaluate(
        self,
        review_input: VisionReviewInput,
    ) -> VisionGateResult:
        """Run one gate cycle for ``review_input`` and return the verdict.

        Fail-OPEN: a verifier exception is logged at WARNING and replaced
        with a synthetic INFO report so a fault never blocks completion.
        Only :class:`asyncio.CancelledError` (and unexpected programming
        errors caught by the runtime) propagate.

        Returns:
            The ``VisionGateResult`` with the verdict, report, and
            elapsed time.
        """
        started_at = self._clock.monotonic()
        logger.info(
            VISION_GATE_STARTED,
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            autonomy=review_input.autonomy.value,
            verifier=self._verifier.kind,
        )
        report = await self._invoke_verifier(review_input)
        verdict = compute_vision_verdict(report.findings, review_input.autonomy)
        elapsed = max(self._clock.monotonic() - started_at, 0.0)

        if verdict is VisionVerdict.BLOCK:
            logger.warning(
                VISION_GATE_BLOCKED,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                findings=len(report.findings),
                elapsed_seconds=elapsed,
            )
        else:
            logger.info(
                VISION_GATE_PASSED,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                verdict=verdict.value,
                findings=len(report.findings),
                elapsed_seconds=elapsed,
            )
        return VisionGateResult(
            verdict=verdict,
            report=report,
            elapsed_seconds=elapsed,
        )

    async def _invoke_verifier(
        self,
        review_input: VisionReviewInput,
    ) -> VisionVerificationReport:
        """Run the verifier with a fail-OPEN fallback report.

        Cancellation propagates so the awaiting parent observes it.

        Returns:
            The verifier's report, or a synthetic fail-OPEN report when
            the verifier raised a non-cancellation error.

        Raises:
            asyncio.CancelledError: Propagated when the verifier run is
                cancelled.
        """
        logger.info(
            VISION_VERIFIER_INVOKED,
            execution_id=review_input.execution_id,
            task_id=review_input.task_id,
            verifier=self._verifier.kind,
        )
        try:
            return await self._verifier.verify(review_input)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                VISION_VERIFIER_FAILED,
                execution_id=review_input.execution_id,
                task_id=review_input.task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                policy="fail_open",
            )
            return self._fail_open_report(review_input)

    def _fail_open_report(
        self,
        review_input: VisionReviewInput,
    ) -> VisionVerificationReport:
        """Build the synthetic report returned when the verifier raises.

        Returns:
            A ``VisionVerificationReport`` carrying a single INFO
            verifier-failed finding.
        """
        finding = VisionFinding(
            category=VisionFindingCategory.VISUAL_DEFECT,
            severity=VisionSeverity.INFO,
            description=_VERIFIER_FAILED_DESCRIPTION,
            evidence=(),
        )
        return VisionVerificationReport(
            task_id=review_input.task_id,
            execution_id=review_input.execution_id,
            findings=(finding,),
            summary=_VERIFIER_FAILED_SUMMARY,
            verifier_kind=self._verifier.kind,
            model_id=None,
            confidence=0.0,
            generator_agent_id=review_input.generator_agent_id,
            evaluator_agent_id=review_input.evaluator_agent_id,
        )
