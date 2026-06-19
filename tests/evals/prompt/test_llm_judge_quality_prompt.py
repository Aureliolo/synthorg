"""Prompt eval: LLM judge quality strategy temperature + prompt drift."""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.providers.protocol import CompletionProvider
from tests._shared import mock_of
from tests.evals.prompt._harness import (
    LabelledExample,
    assert_accuracy_at_least,
    fingerprint_prompt,
    run_grader,
)


@pytest.mark.unit
class TestLlmJudgeQualityPromptContract:
    """Guard rails for the LLM judge quality strategy prompt surface."""

    PINNED_FP = "5dfd6a5962ef1871"

    def test_temperature_is_pinned_low(self) -> None:
        """The judge config must pin temperature=0.3 for stable scoring."""
        from synthorg.hr.performance.llm_judge_quality_strategy import (
            _COMPLETION_CONFIG,
        )

        assert _COMPLETION_CONFIG.temperature == 0.3, (
            "LlmJudgeQualityStrategy._COMPLETION_CONFIG must pin temperature=0.3."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift of the judge system prompt."""
        from synthorg.hr.performance.llm_judge_quality_strategy import (
            _JUDGE_SYSTEM_PROMPT,
        )

        fp = fingerprint_prompt(_JUDGE_SYSTEM_PROMPT)
        assert fp == self.PINNED_FP, (
            f"LLM judge quality system-prompt fingerprint drifted: got {fp!r}, "
            f"expected {self.PINNED_FP!r}. Update the pin if intentional."
        )


@pytest.mark.unit
class TestLlmJudgeQualityParse:
    """Labelled eval for the JSON score-parse contract.

    The prompt asks the LLM for a JSON object carrying ``score`` and
    ``rationale``; this grades that the parse seam returns the score on
    a well-formed payload and fails loudly (``ValueError``, which the
    caller maps to a zero-confidence fallback) on every malformed shape.
    """

    EXAMPLES: tuple[LabelledExample, ...] = (
        LabelledExample(
            name="valid_payload",
            inp='{"score": 0.8, "rationale": "clear and correct"}',
            expected=0.8,
        ),
        LabelledExample(
            name="missing_rationale_key",
            inp='{"score": 0.8}',
            expected=ValueError,
        ),
        LabelledExample(
            name="non_numeric_score",
            inp='{"score": "high", "rationale": "x"}',
            expected=ValueError,
        ),
        LabelledExample(
            name="blank_rationale",
            inp='{"score": 0.5, "rationale": "   "}',
            expected=ValueError,
        ),
        LabelledExample(
            name="not_json",
            inp="this is not json",
            expected=ValueError,
        ),
    )

    def test_parse_matches_labelled_examples(self) -> None:
        """Every labelled (raw JSON, expected outcome) pair grades."""
        from synthorg.hr.performance.llm_judge_quality_strategy import (
            LlmJudgeQualityStrategy,
        )

        strategy = LlmJudgeQualityStrategy(
            provider=mock_of[CompletionProvider](),
            model=NotBlankStr("test-small-001"),
        )

        def _grade(actual_input: object, expected: object) -> bool:
            assert isinstance(actual_input, str)
            try:
                score, _ = strategy._parse_llm_response(
                    actual_input,
                    NotBlankStr("agent-1"),
                    NotBlankStr("task-1"),
                )
            except ValueError:
                return expected is ValueError
            return expected is not ValueError and score == expected

        outcome = run_grader(self.EXAMPLES, _grade)
        assert_accuracy_at_least(outcome, 1.0)
