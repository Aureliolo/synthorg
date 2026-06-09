"""Tests for the LLM-based rubric grader."""

from datetime import UTC, datetime

import pytest
from pydantic import JsonValue

from synthorg.engine.quality.graders._llm_parser import _parse_unit_interval
from synthorg.engine.quality.graders.llm import LLMRubricGrader
from synthorg.engine.quality.verification import (
    AtomicProbe,
    GradeType,
    RubricCriterion,
    VerificationRubric,
    VerificationVerdict,
)
from synthorg.engine.workflow.handoff import HandoffArtifact
from synthorg.providers.enums import FinishReason
from synthorg.providers.models import CompletionResponse, TokenUsage
from tests.unit.engine.quality.scripted_provider import (
    ScriptedProvider,
    build_tool_call_response,
)


def _rubric(*, min_confidence: float = 0.6) -> VerificationRubric:
    return VerificationRubric(
        name="test-rubric",
        criteria=(
            RubricCriterion(
                name="correctness",
                description="Output is correct",
                weight=0.6,
                grade_type=GradeType.SCORE,
            ),
            RubricCriterion(
                name="completeness",
                description="Output covers all asked outputs",
                weight=0.4,
                grade_type=GradeType.SCORE,
            ),
        ),
        min_confidence=min_confidence,
    )


def _artifact() -> HandoffArtifact:
    return HandoffArtifact(
        created_at=datetime.now(UTC),
        from_agent_id="agent-generator",
        to_agent_id="agent-evaluator",
        from_stage="generator",
        to_stage="evaluator",
        payload={"summary": "Implementation complete"},
        artifact_refs=("artifact-001",),
    )


def _probes() -> tuple[AtomicProbe, ...]:
    return (
        AtomicProbe(
            id="p-0",
            probe_text="Is the output correct?",
            source_criterion="correctness",
        ),
        AtomicProbe(
            id="p-1",
            probe_text="Is every required output present?",
            source_criterion="completeness",
        ),
    )


def _response(tool_arguments: dict[str, object]) -> CompletionResponse:
    return build_tool_call_response(
        "emit_rubric_verdict",
        tool_arguments,
        call_id="call-grade-001",
        input_tokens=200,
        output_tokens=60,
        cost=0.0003,
    )


@pytest.mark.unit
class TestLLMRubricGraderConstructor:
    def test_invalid_override_rejected(self) -> None:
        with pytest.raises(ValueError, match="min_confidence_override"):
            LLMRubricGrader(
                provider=ScriptedProvider(
                    response=_response(
                        {
                            "per_criterion_grades": {},
                            "verdict": "pass",
                            "confidence": 1.0,
                            "findings": [],
                        }
                    ),
                ),
                model_id="test-medium-001",
                min_confidence_override=1.5,
            )

    def test_name_is_llm(self) -> None:
        grader = LLMRubricGrader(
            provider=ScriptedProvider(
                response=_response(
                    {
                        "per_criterion_grades": {
                            "correctness": 1.0,
                            "completeness": 1.0,
                        },
                        "verdict": "pass",
                        "confidence": 1.0,
                        "findings": [],
                    }
                ),
            ),
            model_id="test-medium-001",
        )
        assert grader.name == "llm"


@pytest.mark.unit
class TestLLMRubricGraderBehavior:
    async def test_happy_path_pass(self) -> None:
        response = _response(
            {
                "per_criterion_grades": {
                    "correctness": 0.9,
                    "completeness": 0.85,
                },
                "verdict": "pass",
                "confidence": 0.82,
                "findings": ["all criteria satisfied"],
            }
        )
        grader = LLMRubricGrader(
            provider=ScriptedProvider(response=response),
            model_id="test-medium-001",
        )
        result = await grader.grade(
            artifact=_artifact(),
            rubric=_rubric(),
            probes=_probes(),
            generator_agent_id="agent-generator",
            evaluator_agent_id="agent-evaluator",
        )
        assert result.verdict == VerificationVerdict.PASS
        assert result.confidence == pytest.approx(0.82)
        assert result.per_criterion_grades["correctness"] == pytest.approx(0.9)
        assert result.findings == ("all criteria satisfied",)

    async def test_happy_path_fail(self) -> None:
        response = _response(
            {
                "per_criterion_grades": {
                    "correctness": 0.2,
                    "completeness": 0.1,
                },
                "verdict": "fail",
                "confidence": 0.85,
                "findings": ["output incorrect"],
            }
        )
        grader = LLMRubricGrader(
            provider=ScriptedProvider(response=response),
            model_id="test-medium-001",
        )
        result = await grader.grade(
            artifact=_artifact(),
            rubric=_rubric(),
            probes=_probes(),
            generator_agent_id="agent-generator",
            evaluator_agent_id="agent-evaluator",
        )
        assert result.verdict == VerificationVerdict.FAIL

    async def test_low_confidence_downgrades_to_refer(self) -> None:
        response = _response(
            {
                "per_criterion_grades": {
                    "correctness": 0.9,
                    "completeness": 0.9,
                },
                "verdict": "pass",
                "confidence": 0.3,
                "findings": ["uncertain"],
            }
        )
        grader = LLMRubricGrader(
            provider=ScriptedProvider(response=response),
            model_id="test-medium-001",
        )
        result = await grader.grade(
            artifact=_artifact(),
            rubric=_rubric(min_confidence=0.6),
            probes=_probes(),
            generator_agent_id="agent-generator",
            evaluator_agent_id="agent-evaluator",
        )
        assert result.verdict == VerificationVerdict.REFER
        assert any("below minimum" in f for f in result.findings)

    async def test_min_confidence_override_takes_precedence(self) -> None:
        response = _response(
            {
                "per_criterion_grades": {
                    "correctness": 0.9,
                    "completeness": 0.9,
                },
                "verdict": "pass",
                "confidence": 0.7,
                "findings": [],
            }
        )
        grader = LLMRubricGrader(
            provider=ScriptedProvider(response=response),
            model_id="test-medium-001",
            min_confidence_override=0.9,
        )
        result = await grader.grade(
            artifact=_artifact(),
            rubric=_rubric(min_confidence=0.5),
            probes=_probes(),
            generator_agent_id="agent-generator",
            evaluator_agent_id="agent-evaluator",
        )
        assert result.verdict == VerificationVerdict.REFER

    async def test_missing_tool_call_returns_refer(self) -> None:
        response = CompletionResponse(
            content="I refuse to grade this",
            finish_reason=FinishReason.STOP,
            usage=TokenUsage(input_tokens=10, output_tokens=10, cost=0.0),
            model="test-medium-001",
        )
        grader = LLMRubricGrader(
            provider=ScriptedProvider(response=response),
            model_id="test-medium-001",
        )
        result = await grader.grade(
            artifact=_artifact(),
            rubric=_rubric(),
            probes=_probes(),
            generator_agent_id="agent-generator",
            evaluator_agent_id="agent-evaluator",
        )
        assert result.verdict == VerificationVerdict.REFER
        assert result.confidence == 0.0
        assert "ambiguous or unexpected tool_call" in result.findings[0]

    async def test_missing_criterion_returns_refer(self) -> None:
        response = _response(
            {
                "per_criterion_grades": {"correctness": 0.9},
                "verdict": "pass",
                "confidence": 0.9,
                "findings": [],
            }
        )
        grader = LLMRubricGrader(
            provider=ScriptedProvider(response=response),
            model_id="test-medium-001",
        )
        result = await grader.grade(
            artifact=_artifact(),
            rubric=_rubric(),
            probes=_probes(),
            generator_agent_id="agent-generator",
            evaluator_agent_id="agent-evaluator",
        )
        assert result.verdict == VerificationVerdict.REFER
        assert "missing grades" in result.findings[0]

    async def test_unknown_criterion_returns_refer(self) -> None:
        response = _response(
            {
                "per_criterion_grades": {
                    "correctness": 0.9,
                    "completeness": 0.9,
                    "extra": 0.5,
                },
                "verdict": "pass",
                "confidence": 0.9,
                "findings": [],
            }
        )
        grader = LLMRubricGrader(
            provider=ScriptedProvider(response=response),
            model_id="test-medium-001",
        )
        result = await grader.grade(
            artifact=_artifact(),
            rubric=_rubric(),
            probes=_probes(),
            generator_agent_id="agent-generator",
            evaluator_agent_id="agent-evaluator",
        )
        assert result.verdict == VerificationVerdict.REFER
        assert "unknown criterion" in result.findings[0]

    async def test_out_of_range_grade_returns_refer(self) -> None:
        response = _response(
            {
                "per_criterion_grades": {
                    "correctness": 1.5,
                    "completeness": 0.5,
                },
                "verdict": "pass",
                "confidence": 0.9,
                "findings": [],
            }
        )
        grader = LLMRubricGrader(
            provider=ScriptedProvider(response=response),
            model_id="test-medium-001",
        )
        result = await grader.grade(
            artifact=_artifact(),
            rubric=_rubric(),
            probes=_probes(),
            generator_agent_id="agent-generator",
            evaluator_agent_id="agent-evaluator",
        )
        assert result.verdict == VerificationVerdict.REFER

    async def test_unknown_verdict_returns_refer(self) -> None:
        response = _response(
            {
                "per_criterion_grades": {
                    "correctness": 0.9,
                    "completeness": 0.9,
                },
                "verdict": "maybe",
                "confidence": 0.9,
                "findings": [],
            }
        )
        grader = LLMRubricGrader(
            provider=ScriptedProvider(response=response),
            model_id="test-medium-001",
        )
        result = await grader.grade(
            artifact=_artifact(),
            rubric=_rubric(),
            probes=_probes(),
            generator_agent_id="agent-generator",
            evaluator_agent_id="agent-evaluator",
        )
        assert result.verdict == VerificationVerdict.REFER
        assert "unknown verdict" in result.findings[0]

    async def test_prompt_includes_rubric_and_probes(self) -> None:
        response = _response(
            {
                "per_criterion_grades": {
                    "correctness": 0.9,
                    "completeness": 0.9,
                },
                "verdict": "pass",
                "confidence": 0.9,
                "findings": [],
            }
        )
        provider = ScriptedProvider(response=response)
        grader = LLMRubricGrader(
            provider=provider,
            model_id="test-medium-001",
        )
        await grader.grade(
            artifact=_artifact(),
            rubric=_rubric(),
            probes=_probes(),
            generator_agent_id="agent-generator",
            evaluator_agent_id="agent-evaluator",
        )
        messages, _, tools, config = provider.complete_calls[0]
        assert tools is not None
        assert tools[0].name == "emit_rubric_verdict"
        assert config is not None
        assert config.temperature == 0.0
        content = messages[-1].content or ""
        assert "correctness" in content
        assert "completeness" in content
        assert "Is the output correct?" in content

    async def test_large_payload_is_truncated(self) -> None:
        """Oversized artifact payloads are truncated; the prompt sent
        to the provider never carries more than the configured cap."""
        large_payload = {"data": "x" * 30_000}
        artifact = HandoffArtifact(
            created_at=datetime.now(UTC),
            from_agent_id="agent-generator",
            to_agent_id="agent-evaluator",
            from_stage="generator",
            to_stage="evaluator",
            payload=large_payload,
            artifact_refs=("artifact-001",),
        )
        response = _response(
            {
                "per_criterion_grades": {
                    "correctness": 0.9,
                    "completeness": 0.9,
                },
                "verdict": "pass",
                "confidence": 0.9,
                "findings": [],
            }
        )
        provider = ScriptedProvider(response=response)
        grader = LLMRubricGrader(
            provider=provider,
            model_id="test-medium-001",
        )
        result = await grader.grade(
            artifact=artifact,
            rubric=_rubric(),
            probes=_probes(),
            generator_agent_id="agent-generator",
            evaluator_agent_id="agent-evaluator",
        )
        assert result.verdict == VerificationVerdict.PASS
        # Prompt payload was capped at 16K chars, not the full 30K.
        messages, _, _, _ = provider.complete_calls[0]
        user_content = messages[-1].content or ""
        # Enforce the declared 16KB payload cap on the inlined artifact.
        assert user_content.count("x") <= 16_000
        # The prompt tells the LLM the payload was truncated so it can
        # fall back to REFER rather than guess.
        assert "truncated" in user_content.lower()


@pytest.mark.unit
class TestLLMRubricGraderInvalidGrades:
    """Malformed per-criterion grade values must downgrade to REFER."""

    async def test_multiple_tool_calls_returns_refer(self) -> None:
        """Two matching tool calls in one response must fail closed."""
        from synthorg.providers.enums import FinishReason
        from synthorg.providers.models import (
            CompletionResponse,
            TokenUsage,
            ToolCall,
        )

        args: dict[str, JsonValue] = {
            "per_criterion_grades": {"correctness": 0.9, "completeness": 0.9},
            "verdict": "pass",
            "confidence": 0.9,
            "findings": [],
        }
        response = CompletionResponse(
            tool_calls=(
                ToolCall(id="call-a", name="emit_rubric_verdict", arguments=args),
                ToolCall(id="call-b", name="emit_rubric_verdict", arguments=args),
            ),
            finish_reason=FinishReason.TOOL_USE,
            usage=TokenUsage(input_tokens=10, output_tokens=10, cost=0.0),
            model="test-medium-001",
        )
        provider = ScriptedProvider(response=response)
        grader = LLMRubricGrader(
            provider=provider,
            model_id="test-medium-001",
        )
        result = await grader.grade(
            artifact=_artifact(),
            rubric=_rubric(),
            probes=_probes(),
            generator_agent_id="agent-generator",
            evaluator_agent_id="agent-evaluator",
        )
        assert result.verdict == VerificationVerdict.REFER
        assert "ambiguous" in result.findings[0]

    async def test_unexpected_tool_call_returns_refer(self) -> None:
        """Unrelated tool call in the response must fail closed."""
        from synthorg.providers.enums import FinishReason
        from synthorg.providers.models import (
            CompletionResponse,
            TokenUsage,
            ToolCall,
        )

        response = CompletionResponse(
            tool_calls=(ToolCall(id="call-x", name="some_other_tool", arguments={}),),
            finish_reason=FinishReason.TOOL_USE,
            usage=TokenUsage(input_tokens=10, output_tokens=10, cost=0.0),
            model="test-medium-001",
        )
        provider = ScriptedProvider(response=response)
        grader = LLMRubricGrader(
            provider=provider,
            model_id="test-medium-001",
        )
        result = await grader.grade(
            artifact=_artifact(),
            rubric=_rubric(),
            probes=_probes(),
            generator_agent_id="agent-generator",
            evaluator_agent_id="agent-evaluator",
        )
        assert result.verdict == VerificationVerdict.REFER
        assert "unexpected" in result.findings[0]

    @pytest.mark.parametrize(
        "findings_value",
        [
            [42, "real finding"],
            ["", "ok"],
            "not a list",
        ],
        ids=["non_string_entry", "blank_entry", "not_a_list"],
    )
    async def test_malformed_findings_returns_refer(
        self,
        findings_value: object,
    ) -> None:
        """Any malformed entry in ``findings`` fails closed to REFER."""
        response = _response(
            {
                "per_criterion_grades": {
                    "correctness": 0.9,
                    "completeness": 0.9,
                },
                "verdict": "pass",
                "confidence": 0.9,
                "findings": findings_value,
            }
        )
        provider = ScriptedProvider(response=response)
        grader = LLMRubricGrader(
            provider=provider,
            model_id="test-medium-001",
        )
        result = await grader.grade(
            artifact=_artifact(),
            rubric=_rubric(),
            probes=_probes(),
            generator_agent_id="agent-generator",
            evaluator_agent_id="agent-evaluator",
        )
        assert result.verdict == VerificationVerdict.REFER

    async def test_provider_exception_is_logged_and_reraised(self) -> None:
        """An arbitrary provider failure logs ``VERIFICATION_GRADER_FAILED``
        with grading context before re-raising."""
        from structlog.testing import capture_logs

        from synthorg.observability.events.verification import (
            VERIFICATION_GRADER_FAILED,
        )

        class _BoomError(Exception):
            pass

        provider = ScriptedProvider(error=_BoomError("upstream exploded"))
        grader = LLMRubricGrader(
            provider=provider,
            model_id="test-medium-001",
        )
        with capture_logs() as logs, pytest.raises(_BoomError):
            await grader.grade(
                artifact=_artifact(),
                rubric=_rubric(),
                probes=_probes(),
                generator_agent_id="agent-generator",
                evaluator_agent_id="agent-evaluator",
            )
        failures = [
            entry for entry in logs if entry.get("event") == VERIFICATION_GRADER_FAILED
        ]
        assert failures, logs
        record = failures[0]
        assert record["rubric_name"] == "test-rubric"
        assert record["grader"] == "llm"
        assert record["model_id"] == "test-medium-001"
        assert record["generator_agent_id"] == "agent-generator"
        assert record["evaluator_agent_id"] == "agent-evaluator"

    @pytest.mark.parametrize(
        "non_finite_grade",
        [float("nan"), float("inf"), float("-inf")],
        ids=["nan", "+inf", "-inf"],
    )
    def test_non_finite_grade_rejected_as_not_finite(
        self,
        non_finite_grade: float,
    ) -> None:
        """The grader's defence-in-depth rejects a non-finite grade.

        Non-finite values can no longer reach the grader through a
        ``ToolCall`` (its ``arguments`` forbid them via
        ``allow_inf_nan=False``, and ``mappers._parse_arguments`` drops
        any that ``json.loads`` accepted), so the end-to-end REFER path is
        unreachable for these inputs by construction. This exercises the
        retained safety-net function directly: a non-finite grade must be
        rejected (a reason string, which the grader maps to REFER) rather
        than parsed into a trusted float.
        """
        result = _parse_unit_interval(non_finite_grade, label="correctness")
        # Rejection returns a reason string (not the float); assert the
        # behaviour and the cause without pinning the exact message.
        assert isinstance(result, str)
        assert "finite" in result
