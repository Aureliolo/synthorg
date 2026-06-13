"""Prompt eval: LLM judge quality strategy temperature + prompt drift."""

import pytest

from tests.evals.prompt._harness import fingerprint_prompt


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
