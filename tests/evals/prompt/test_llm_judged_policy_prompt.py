"""Prompt eval: LLM-judged policy gate temperature + prompt drift."""

import pytest

from synthorg.engine.pipeline.models import RoutingVerdict
from synthorg.engine.pipeline.policy.llm_judged import LlmJudgedRoutingPolicy
from tests.evals.prompt._harness import (
    LabelledExample,
    assert_accuracy_at_least,
    fingerprint_prompt,
    run_grader,
)


@pytest.mark.unit
class TestLlmJudgedPolicyPromptContract:
    """Guard rails for the LLM-judged policy gate prompt surface."""

    PINNED_FP = "b05fe24e47b7e1ad"

    def test_temperature_is_zero(self) -> None:
        """The policy judge must be deterministic at temperature=0."""
        from synthorg.engine.pipeline.policy.llm_judged import _LLM_TEMPERATURE

        assert _LLM_TEMPERATURE == 0.0, (
            "LlmJudgedPolicyGate must keep _LLM_TEMPERATURE pinned at 0.0."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift of the policy gate system prompt."""
        from synthorg.engine.pipeline.policy.llm_judged import _SYSTEM_PROMPT

        fp = fingerprint_prompt(_SYSTEM_PROMPT)
        assert fp == self.PINNED_FP, (
            f"LLM-judged policy system-prompt fingerprint drifted: got {fp!r}, "
            f"expected {self.PINNED_FP!r}. Update the pin if intentional."
        )


@pytest.mark.unit
class TestLlmJudgedPolicyVerdictBehaviour:
    """Labelled eval for the leaf/splittable text-parse contract.

    The judge prompt asks for a leaf/splittable verdict; this grades that the
    parser maps a clear verdict correctly and stays ambiguous (``None`` ->
    deterministic fallback) on negated / both-words / empty replies, so a
    prompt edit cannot make the gate act on a misread verdict.
    """

    EXAMPLES: tuple[LabelledExample, ...] = (
        LabelledExample(
            name="splittable_word",
            inp="splittable",
            expected=RoutingVerdict.SPLITTABLE,
        ),
        LabelledExample(
            name="leaf_word",
            inp="leaf",
            expected=RoutingVerdict.LEAF,
        ),
        LabelledExample(
            name="splittable_in_sentence",
            inp="This task is splittable into independent subtasks.",
            expected=RoutingVerdict.SPLITTABLE,
        ),
        LabelledExample(
            name="negated_splittable_is_ambiguous",
            inp="This is not splittable.",
            expected=None,
        ),
        LabelledExample(
            name="both_words_ambiguous",
            inp="Could be a leaf or splittable.",
            expected=None,
        ),
        LabelledExample(
            name="empty_is_ambiguous",
            inp="",
            expected=None,
        ),
        LabelledExample(
            name="missing_content_is_ambiguous",
            inp=None,
            expected=None,
        ),
    )

    def test_parse_verdict_matches_labelled_text(self) -> None:
        """Every labelled (model text, expected verdict) pair grades."""

        def _grade(actual_input: object, expected: object) -> bool:
            assert actual_input is None or isinstance(actual_input, str)
            return LlmJudgedRoutingPolicy._parse_verdict(actual_input) == expected

        outcome = run_grader(self.EXAMPLES, _grade)
        assert_accuracy_at_least(outcome, 1.0)
