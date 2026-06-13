"""Prompt eval: evolution separate-analyzer temperature + prompt drift."""

import pytest

from tests.evals.prompt._harness import fingerprint_prompt


@pytest.mark.unit
class TestEvolutionAnalyzerPromptContract:
    """Guard rails for the evolution analyzer prompt surface."""

    PINNED_FP = "8fb33f9a08bb5863"

    def test_default_temperature_is_pinned(self) -> None:
        """The analyzer must default to its pinned temperature constant."""
        from synthorg.engine.evolution.proposers.separate_analyzer import (
            _DEFAULT_TEMPERATURE,
        )

        assert _DEFAULT_TEMPERATURE == 0.3, (
            "SeparateAnalyzerProposer must keep _DEFAULT_TEMPERATURE pinned at 0.3."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift of the evolution analyzer system prompt."""
        from synthorg.engine.evolution.proposers.separate_analyzer import (
            _SYSTEM_PROMPT,
        )

        fp = fingerprint_prompt(_SYSTEM_PROMPT)
        assert fp == self.PINNED_FP, (
            f"evolution analyzer system-prompt fingerprint drifted: got {fp!r}, "
            f"expected {self.PINNED_FP!r}. Update the pin if intentional."
        )
