"""Deterministic heuristic vision verifier.

Checks the structured :class:`VisualExpectation` entries on the review
input against the captured screenshots without any LLM call, so the
simulation harness can assert a brief-mismatch BLOCK reproducibly.

Today it supports ``dominant_colour`` expectations: the mean RGB of the
target screenshot must be within the expectation's tolerance of the
expected colour. A violation becomes a HIGH-severity
``requirements_mismatch`` finding (which always blocks), carrying the
measured-vs-expected colours as evidence.
"""

import asyncio
from pathlib import Path
from typing import Final

from synthorg.observability import get_logger
from synthorg.observability.events.vision_verify import (
    VISION_HEURISTIC_CHECK_COMPLETED,
)
from synthorg.security.visionverify.config import VisionVerifierKind
from synthorg.security.visionverify.models import (
    VisionFinding,
    VisionFindingCategory,
    VisionReviewInput,
    VisionVerificationReport,
    VisualExpectation,
)
from synthorg.security.visionverify.verifiers._image import (
    mean_rgb,
    normalised_rgb_distance,
    resolve_screenshot,
)

logger = get_logger(__name__)

_FULL_CONFIDENCE: Final[float] = 1.0
_CLEAN_SUMMARY: Final[str] = (
    "Heuristic vision verification passed: all structured expectations "
    "held on the captured screenshots."
)
_MISMATCH_SUMMARY: Final[str] = (
    "Heuristic vision verification flagged {count} expectation "
    "mismatch(es) between the running UI and the brief."
)


class HeuristicVisionVerifier:
    """Deterministic verifier that checks structured visual expectations."""

    def __init__(self, *, workspace: Path) -> None:
        """Bind the verifier to the workspace holding the screenshots.

        Raises:
            ValueError: If ``workspace`` is not an absolute path.
        """
        if not workspace.is_absolute():
            msg = f"workspace must be absolute, got {workspace!r}"
            raise ValueError(msg)
        self._workspace = workspace.resolve()

    @property
    def kind(self) -> str:
        """Return the ``heuristic`` discriminator."""
        return VisionVerifierKind.HEURISTIC.value

    async def verify(
        self,
        review_input: VisionReviewInput,
    ) -> VisionVerificationReport:
        """Check every expectation against the final screenshot.

        Returns:
            The verification report listing one finding per failed
            expectation.
        """
        target = review_input.screenshots[-1]
        path = resolve_screenshot(self._workspace, target.workspace_path)
        # Pillow decode + numpy averaging is blocking CPU/IO; keep it off
        # the event loop so concurrent verifications are not serialised.
        measured = await asyncio.to_thread(mean_rgb, path)
        findings = tuple(
            finding
            for expectation in review_input.expectations
            if (
                finding := self._check_expectation(
                    expectation,
                    measured=measured,
                    screenshot_path=target.workspace_path,
                )
            )
            is not None
        )
        logger.info(
            VISION_HEURISTIC_CHECK_COMPLETED,
            task_id=review_input.task_id,
            execution_id=review_input.execution_id,
            expectations=len(review_input.expectations),
            findings=len(findings),
        )
        summary = (
            _CLEAN_SUMMARY
            if not findings
            else _MISMATCH_SUMMARY.format(count=len(findings))
        )
        return VisionVerificationReport(
            task_id=review_input.task_id,
            execution_id=review_input.execution_id,
            findings=findings,
            summary=summary,
            verifier_kind=VisionVerifierKind.HEURISTIC.value,
            model_id=None,
            confidence=_FULL_CONFIDENCE,
            generator_agent_id=review_input.generator_agent_id,
            evaluator_agent_id=review_input.evaluator_agent_id,
        )

    def _check_expectation(
        self,
        expectation: VisualExpectation,
        *,
        measured: tuple[int, int, int],
        screenshot_path: str,
    ) -> VisionFinding | None:
        """Return a finding when ``expectation`` is violated, else None.

        Only ``DOMINANT_COLOUR`` expectations exist today, so the colour
        check is applied unconditionally. A new ``VisualExpectationKind``
        member must extend this method with explicit dispatch.
        """
        distance = normalised_rgb_distance(measured, expectation.expected_rgb)
        if distance <= expectation.tolerance:
            return None
        evidence = (
            f"expected dominant colour rgb{expectation.expected_rgb}, "
            f"measured rgb{measured} (normalised distance {distance:.3f} "
            f"exceeds tolerance {expectation.tolerance:.3f})",
        )
        return VisionFinding(
            category=VisionFindingCategory.REQUIREMENTS_MISMATCH,
            severity=expectation.severity,
            description=f"Dominant-colour mismatch: {expectation.description}",
            evidence=evidence,
            suggested_fix=(
                "Adjust the UI so its dominant colour matches the brief, "
                "or correct the brief if the colour is intentional."
            ),
            screenshot_path=screenshot_path,
        )
