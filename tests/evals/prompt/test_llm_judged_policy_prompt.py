"""Prompt eval: LLM-judged policy gate temperature + prompt drift."""

import pytest

from tests.evals.prompt._harness import fingerprint_prompt


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
