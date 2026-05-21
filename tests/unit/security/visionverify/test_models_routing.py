"""Unit tests for vision verifier models and verdict routing."""

import pytest
from pydantic import ValidationError

from synthorg.core.enums import AutonomyLevel
from synthorg.security.visionverify.models import (
    VisionFinding,
    VisionFindingCategory,
    VisionReviewInput,
    VisionScreenshotRef,
    VisionSeverity,
    VisionVerdict,
    VisionVerificationReport,
    VisualExpectation,
    VisualExpectationKind,
)
from synthorg.security.visionverify.routing import (
    compute_vision_verdict,
    should_block,
)

pytestmark = pytest.mark.unit

_SHA = "a" * 64


def _ref() -> VisionScreenshotRef:
    return VisionScreenshotRef(workspace_path="x.png", sha256=_SHA)


def _input(**overrides: object) -> VisionReviewInput:
    base: dict[str, object] = {
        "task_id": "t1",
        "execution_id": "e1",
        "brief": "a blue submit button",
        "acceptance_criteria": ("button is blue",),
        "screenshots": (_ref(),),
        "generator_agent_id": "gen",
        "evaluator_agent_id": "vision",
        "autonomy": AutonomyLevel.SUPERVISED,
    }
    base.update(overrides)
    return VisionReviewInput(**base)  # type: ignore[arg-type]


class TestFinding:
    def test_high_requires_evidence(self) -> None:
        with pytest.raises(ValidationError, match="evidence"):
            VisionFinding(
                category=VisionFindingCategory.REQUIREMENTS_MISMATCH,
                severity=VisionSeverity.HIGH,
                description="wrong colour",
            )

    def test_low_allows_no_evidence(self) -> None:
        finding = VisionFinding(
            category=VisionFindingCategory.VISUAL_DEFECT,
            severity=VisionSeverity.LOW,
            description="minor",
        )
        assert finding.evidence == ()


class TestReviewInputSelfEval:
    def test_rejects_self_evaluation(self) -> None:
        with pytest.raises(ValidationError, match="Self-evaluation"):
            _input(evaluator_agent_id="gen")

    def test_requires_at_least_one_screenshot(self) -> None:
        with pytest.raises(ValidationError):
            _input(screenshots=())


class TestReportSelfEval:
    def test_report_rejects_self_evaluation(self) -> None:
        with pytest.raises(ValidationError, match="Self-evaluation"):
            VisionVerificationReport(
                task_id="t1",
                execution_id="e1",
                summary="ok",
                verifier_kind="heuristic",
                confidence=1.0,
                generator_agent_id="same",
                evaluator_agent_id="same",
            )


class TestVisualExpectation:
    def test_rejects_out_of_range_rgb(self) -> None:
        with pytest.raises(ValidationError):
            VisualExpectation(
                kind=VisualExpectationKind.DOMINANT_COLOUR,
                description="blue",
                expected_rgb=(0, 0, 300),
                tolerance=0.1,
            )


class TestRouting:
    def test_empty_findings_pass(self) -> None:
        assert compute_vision_verdict((), AutonomyLevel.LOCKED) is VisionVerdict.PASS

    def test_high_always_blocks(self) -> None:
        for autonomy in AutonomyLevel:
            assert should_block(VisionSeverity.HIGH, autonomy)

    def test_medium_blocks_only_low_autonomy(self) -> None:
        assert should_block(VisionSeverity.MEDIUM, AutonomyLevel.LOCKED)
        assert should_block(VisionSeverity.MEDIUM, AutonomyLevel.SUPERVISED)
        assert not should_block(VisionSeverity.MEDIUM, AutonomyLevel.SEMI)
        assert not should_block(VisionSeverity.MEDIUM, AutonomyLevel.FULL)

    def test_low_never_blocks(self) -> None:
        for autonomy in AutonomyLevel:
            assert not should_block(VisionSeverity.LOW, autonomy)

    def test_block_verdict_on_high_finding(self) -> None:
        finding = VisionFinding(
            category=VisionFindingCategory.REQUIREMENTS_MISMATCH,
            severity=VisionSeverity.HIGH,
            description="wrong colour",
            evidence=("measured red, expected blue",),
        )
        verdict = compute_vision_verdict((finding,), AutonomyLevel.FULL)
        assert verdict is VisionVerdict.BLOCK

    def test_pass_with_findings_on_low(self) -> None:
        finding = VisionFinding(
            category=VisionFindingCategory.VISUAL_DEFECT,
            severity=VisionSeverity.LOW,
            description="minor",
        )
        verdict = compute_vision_verdict((finding,), AutonomyLevel.LOCKED)
        assert verdict is VisionVerdict.PASS_WITH_FINDINGS
