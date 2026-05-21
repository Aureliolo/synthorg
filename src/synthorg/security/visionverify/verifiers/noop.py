"""No-op vision verifier (the safe, disabled-by-default strategy).

Returns a clean report with no findings. Used when the subsystem is
constructed but the operator has not selected an active verifier.
"""

from typing import Final

from synthorg.security.visionverify.config import VisionVerifierKind
from synthorg.security.visionverify.models import (
    VisionReviewInput,
    VisionVerificationReport,
)

_NOOP_CONFIDENCE: Final[float] = 1.0
_NOOP_SUMMARY: Final[str] = (
    "Vision verification is disabled (noop verifier); no checks run."
)


class NoOpVisionVerifier:
    """Inert verifier that always returns a clean, finding-free report."""

    @property
    def kind(self) -> str:
        """Return the ``noop`` discriminator."""
        return VisionVerifierKind.NOOP.value

    async def verify(
        self,
        review_input: VisionReviewInput,
    ) -> VisionVerificationReport:
        """Return a clean report with no findings."""
        return VisionVerificationReport(
            task_id=review_input.task_id,
            execution_id=review_input.execution_id,
            findings=(),
            summary=_NOOP_SUMMARY,
            verifier_kind=VisionVerifierKind.NOOP.value,
            model_id=None,
            confidence=_NOOP_CONFIDENCE,
            generator_agent_id=review_input.generator_agent_id,
            evaluator_agent_id=review_input.evaluator_agent_id,
        )
