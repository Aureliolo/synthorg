"""Prompt eval: red-team grounding substrate temperature + prompt drift.

The grounding checker runs two structured completions (claim extraction and
entailment) through a path that pins ``LLM_TEMPERATURE = 0.0``. Both system
prompts are fingerprinted against silent drift.
"""

import pytest
from pydantic import JsonValue

from synthorg.core.completion_enums import FinishReason
from synthorg.providers.models import CompletionResponse, TokenUsage, ToolCall
from synthorg.security.redteam.grounding._llm import (
    EXTRACT_CLAIMS_TOOL_NAME,
    GROUNDING_VERDICT_TOOL_NAME,
    parse_extracted_claims,
    parse_grounding_verdict,
)
from tests.evals.prompt._harness import (
    LabelledExample,
    assert_accuracy_at_least,
    fingerprint_prompt,
    run_grader,
)


def _verdict_response(
    *, verdict: JsonValue, confidence: JsonValue
) -> CompletionResponse:
    """A completion whose ``grounding_verdict`` tool call carries the args."""
    return CompletionResponse(
        content=None,
        tool_calls=(
            ToolCall(
                id="tc-1",
                name=GROUNDING_VERDICT_TOOL_NAME,
                arguments={"verdict": verdict, "confidence": confidence},
            ),
        ),
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.0),
        model="test-small-001",
    )


def _claims_response(claims: JsonValue) -> CompletionResponse:
    """A completion whose ``extract_claims`` tool call carries *claims*."""
    return CompletionResponse(
        content=None,
        tool_calls=(
            ToolCall(
                id="tc-1",
                name=EXTRACT_CLAIMS_TOOL_NAME,
                arguments={"claims": claims},
            ),
        ),
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.0),
        model="test-small-001",
    )


def _no_tool_response() -> CompletionResponse:
    """A completion that answers in prose without calling any tool."""
    return CompletionResponse(
        content="I cannot decide.",
        tool_calls=(),
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=10, output_tokens=5, cost=0.0),
        model="test-small-001",
    )


@pytest.mark.unit
class TestRedTeamGroundingPromptContract:
    """Guard rails for the red-team grounding prompt surfaces."""

    PINNED_EXTRACTION_FP = "222f650039d05931"
    PINNED_ENTAILMENT_FP = "70b87efe77f61877"

    def test_temperature_is_zero(self) -> None:
        """Grounding completions must be deterministic at temperature=0."""
        from synthorg.security.redteam.grounding._llm import LLM_TEMPERATURE

        assert LLM_TEMPERATURE == 0.0, (
            "Red-team grounding must keep LLM_TEMPERATURE pinned at 0.0."
        )

    def test_extraction_prompt_fingerprint_is_pinned(self) -> None:
        """Detect drift of the claim-extraction system prompt."""
        from synthorg.security.redteam.grounding._llm import (
            _EXTRACTION_SYSTEM_PROMPT,
        )

        fp = fingerprint_prompt(_EXTRACTION_SYSTEM_PROMPT)
        assert fp == self.PINNED_EXTRACTION_FP, (
            f"grounding extraction prompt drifted: {fp!r} != "
            f"{self.PINNED_EXTRACTION_FP!r}. Update the pin if intentional."
        )

    def test_entailment_prompt_fingerprint_is_pinned(self) -> None:
        """Detect drift of the entailment system prompt."""
        from synthorg.security.redteam.grounding._llm import (
            _ENTAILMENT_SYSTEM_PROMPT,
        )

        fp = fingerprint_prompt(_ENTAILMENT_SYSTEM_PROMPT)
        assert fp == self.PINNED_ENTAILMENT_FP, (
            f"grounding entailment prompt drifted: {fp!r} != "
            f"{self.PINNED_ENTAILMENT_FP!r}. Update the pin if intentional."
        )


@pytest.mark.unit
class TestGroundingVerdictBehaviour:
    """Labelled eval for the grounding-verdict parse contract.

    Only an ``unsupported`` verdict produces an ungrounded claim, so the
    parser must map each valid verdict + clamp confidence, and reject a
    missing/invalid verdict to ``None`` (the caller then treats the claim as
    not-disproven) -- a prompt edit must preserve this.
    """

    EXAMPLES: tuple[LabelledExample, ...] = (
        LabelledExample(
            name="supported_verdict",
            inp=_verdict_response(verdict="supported", confidence=0.8),
            expected=("supported", 0.8),
        ),
        LabelledExample(
            name="unsupported_verdict",
            inp=_verdict_response(verdict="unsupported", confidence=0.95),
            expected=("unsupported", 0.95),
        ),
        LabelledExample(
            name="uncertain_verdict",
            inp=_verdict_response(verdict="uncertain", confidence=0.5),
            expected=("uncertain", 0.5),
        ),
        LabelledExample(
            name="confidence_clamped_to_ceiling",
            inp=_verdict_response(verdict="supported", confidence=1.5),
            expected=("supported", 1.0),
        ),
        LabelledExample(
            name="invalid_verdict_rejected",
            inp=_verdict_response(verdict="maybe", confidence=0.9),
            expected=None,
        ),
        LabelledExample(
            name="missing_tool_call_rejected",
            inp=_no_tool_response(),
            expected=None,
        ),
    )

    def test_parse_verdict_matches_labelled_examples(self) -> None:
        """Every labelled (response, expected verdict tuple) pair grades."""

        def _grade(actual_input: object, expected: object) -> bool:
            assert isinstance(actual_input, CompletionResponse)
            return parse_grounding_verdict(actual_input) == expected

        outcome = run_grader(self.EXAMPLES, _grade)
        assert_accuracy_at_least(outcome, 1.0)


@pytest.mark.unit
class TestExtractedClaimsBehaviour:
    """Labelled eval for the claim-extraction parse contract.

    Grades that the parser returns the cleaned claim tuple, deduplicates
    repeats, and yields an empty tuple when the model returned no claims or
    did not call the extraction tool.
    """

    EXAMPLES: tuple[LabelledExample, ...] = (
        LabelledExample(
            name="two_distinct_claims",
            inp=_claims_response(
                ["The agent deleted prod data.", "It bypassed review."]
            ),
            expected=("The agent deleted prod data.", "It bypassed review."),
        ),
        LabelledExample(
            name="duplicate_claims_deduplicated",
            inp=_claims_response(["same claim", "same claim"]),
            expected=("same claim",),
        ),
        LabelledExample(
            name="missing_tool_call_yields_empty",
            inp=_no_tool_response(),
            expected=(),
        ),
    )

    def test_parse_claims_matches_labelled_examples(self) -> None:
        """Every labelled (response, expected claim tuple) pair grades."""

        def _grade(actual_input: object, expected: object) -> bool:
            assert isinstance(actual_input, CompletionResponse)
            return parse_extracted_claims(actual_input) == expected

        outcome = run_grader(self.EXAMPLES, _grade)
        assert_accuracy_at_least(outcome, 1.0)
