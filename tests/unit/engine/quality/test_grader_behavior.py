"""Behavioral tests for rubric grader implementations."""

from datetime import UTC, datetime

import pytest

from synthorg.engine.quality.graders.heuristic import (
    HeuristicGraderConfig,
    HeuristicRubricGrader,
)
from synthorg.engine.quality.graders.llm import LLMRubricGrader
from synthorg.engine.quality.verification import (
    AtomicProbe,
    GradeType,
    RubricCriterion,
    VerificationRubric,
    VerificationVerdict,
)
from synthorg.engine.workflow.handoff import HandoffArtifact


def _rubric(
    min_confidence: float = 0.7,
) -> VerificationRubric:
    return VerificationRubric(
        name="test-rubric",
        criteria=(
            RubricCriterion(
                name="quality",
                description="Quality",
                weight=1.0,
                grade_type=GradeType.SCORE,
            ),
        ),
        min_confidence=min_confidence,
    )


def _artifact(payload_text: str = "feature complete") -> HandoffArtifact:
    return HandoffArtifact(
        from_agent_id="gen-agent",
        to_agent_id="eval-agent",
        from_stage="generator",
        to_stage="evaluator",
        payload={"output": payload_text},
        created_at=datetime.now(UTC),
    )


def _probe(text: str = "Feature complete") -> AtomicProbe:
    return AtomicProbe(
        id="probe-1",
        probe_text=f"Is it done: {text}",
        source_criterion=text,
    )


@pytest.mark.unit
class TestHeuristicGraderBehavior:
    @pytest.mark.parametrize(
        ("payload_text", "min_confidence", "probe_text", "expected"),
        [
            (
                "feature complete and done",
                0.7,
                "feature complete",
                VerificationVerdict.PASS,
            ),
            (
                "something unrelated",
                0.0,
                "completely different",
                VerificationVerdict.FAIL,
            ),
            (
                "something unrelated",
                0.95,
                "completely different",
                VerificationVerdict.REFER,
            ),
        ],
    )
    async def test_verdict_routing(
        self,
        payload_text: str,
        min_confidence: float,
        probe_text: str,
        expected: VerificationVerdict,
    ) -> None:
        grader = HeuristicRubricGrader()
        result = await grader.grade(
            artifact=_artifact(payload_text),
            rubric=_rubric(min_confidence=min_confidence),
            probes=(_probe(probe_text),),
            generator_agent_id="gen-agent",
            evaluator_agent_id="eval-agent",
        )
        assert result.verdict == expected

    async def test_empty_probes_refer(self) -> None:
        grader = HeuristicRubricGrader()
        result = await grader.grade(
            artifact=_artifact(),
            rubric=_rubric(),
            probes=(),
            generator_agent_id="gen-agent",
            evaluator_agent_id="eval-agent",
        )
        assert result.verdict == VerificationVerdict.REFER

    async def test_per_criterion_grades_populated(self) -> None:
        grader = HeuristicRubricGrader()
        result = await grader.grade(
            artifact=_artifact("feature complete"),
            rubric=_rubric(),
            probes=(_probe("feature complete"),),
            generator_agent_id="gen-agent",
            evaluator_agent_id="eval-agent",
        )
        assert "quality" in result.per_criterion_grades
        assert 0.0 <= result.per_criterion_grades["quality"] <= 1.0

    async def test_rubric_name_in_result(self) -> None:
        grader = HeuristicRubricGrader()
        result = await grader.grade(
            artifact=_artifact(),
            rubric=_rubric(),
            probes=(),
            generator_agent_id="gen-agent",
            evaluator_agent_id="eval-agent",
        )
        assert result.rubric_name == "test-rubric"


@pytest.mark.unit
class TestLLMGraderBehavior:
    async def test_name_property(self) -> None:
        from tests.unit.providers.conftest import FakeProvider

        grader = LLMRubricGrader(
            provider=FakeProvider(),
            model_id="test-medium-001",
        )
        assert grader.name == "llm"


@pytest.mark.unit
class TestHeuristicGraderConfigInjection:
    """Verify operator-tunable thresholds flow through the grader."""

    def test_default_config_matches_historical_values(self) -> None:
        config = HeuristicGraderConfig()
        assert config.pass_threshold == pytest.approx(0.5)
        assert config.pass_grade == pytest.approx(0.8)
        assert config.fail_grade == pytest.approx(0.3)
        assert config.confidence_ceiling == pytest.approx(0.9)
        assert config.confidence_bias == pytest.approx(0.1)

    async def test_custom_pass_grade_changes_per_criterion_score(self) -> None:
        """Changing pass_grade changes the per-criterion score on PASS."""
        config = HeuristicGraderConfig(pass_grade=0.95, fail_grade=0.05)
        grader = HeuristicRubricGrader(config=config)
        rubric = _rubric()
        artifact = _artifact("Feature complete")
        probes = (_probe("Feature complete"),)
        result = await grader.grade(
            artifact=artifact,
            rubric=rubric,
            probes=probes,
            generator_agent_id="gen",
            evaluator_agent_id="eval",
        )
        assert result.verdict == VerificationVerdict.PASS
        assert result.per_criterion_grades["quality"] == pytest.approx(0.95)

    async def test_custom_pass_threshold_inverts_verdict(self) -> None:
        """Lifting pass_threshold above the probe ratio flips PASS to FAIL."""
        config = HeuristicGraderConfig(
            pass_threshold=0.99, confidence_bias=0.5, confidence_ceiling=1.0
        )
        grader = HeuristicRubricGrader(config=config)
        rubric = _rubric()
        artifact = _artifact("Feature complete")
        # One matching probe, one not -> ratio = 0.5, below 0.99.
        probes = (_probe("Feature complete"), _probe("missing thing"))
        result = await grader.grade(
            artifact=artifact,
            rubric=rubric,
            probes=probes,
            generator_agent_id="gen",
            evaluator_agent_id="eval",
        )
        assert result.verdict == VerificationVerdict.FAIL

    def test_from_bridge_config_extracts_quality_subset(self) -> None:
        """Bridge-config projection wires every heuristic grader field."""
        from synthorg.settings.bridge_configs import EngineBridgeConfig

        bridge = EngineBridgeConfig(
            quality_heuristic_pass_threshold=0.6,
            quality_heuristic_pass_grade=0.85,
            quality_heuristic_fail_grade=0.25,
            quality_heuristic_confidence_ceiling=0.95,
            quality_heuristic_confidence_bias=0.05,
        )
        config = HeuristicGraderConfig.from_bridge_config(bridge)
        assert config.pass_threshold == pytest.approx(0.6)
        assert config.pass_grade == pytest.approx(0.85)
        assert config.fail_grade == pytest.approx(0.25)
        assert config.confidence_ceiling == pytest.approx(0.95)
        assert config.confidence_bias == pytest.approx(0.05)
