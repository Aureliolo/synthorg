"""Prompt eval: LLM calibration sampler temperature + prompt drift."""

import pytest

from tests.evals.prompt._harness import fingerprint_prompt


@pytest.mark.unit
class TestLlmCalibrationSamplerPromptContract:
    """Guard rails for the LLM calibration sampler prompt surface."""

    PINNED_FP = "8aa58254d11aea0a"

    def test_temperature_is_pinned_low(self) -> None:
        """The sampler config must pin temperature=0.3."""
        from synthorg.hr.performance.llm_calibration_sampler import _COMPLETION_CONFIG

        assert _COMPLETION_CONFIG.temperature == 0.3, (
            "LlmCalibrationSampler._COMPLETION_CONFIG must pin temperature=0.3."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift of the calibration sampler system prompt."""
        from synthorg.hr.performance.llm_calibration_sampler import (
            _SYSTEM_PROMPT_HEADER,
        )

        fp = fingerprint_prompt(_SYSTEM_PROMPT_HEADER)
        assert fp == self.PINNED_FP, (
            f"calibration sampler system-prompt fingerprint drifted: got {fp!r}, "
            f"expected {self.PINNED_FP!r}. Update the pin if intentional."
        )
