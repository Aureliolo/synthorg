"""Prompt eval: intervention proposer temperature + prompt drift."""

import pytest

from tests.evals.prompt._harness import fingerprint_prompt


@pytest.mark.unit
class TestInterventionProposerPromptContract:
    """Guard rails for the intervention proposer prompt surface."""

    PINNED_FP = "bfe9bdf85e33393e"

    def test_temperature_is_pinned(self) -> None:
        """The proposer must keep its temperature constant pinned."""
        from synthorg.engine.intervention.proposer import _PROPOSER_TEMPERATURE

        assert _PROPOSER_TEMPERATURE == 0.1, (
            "InterventionProposer must keep _PROPOSER_TEMPERATURE pinned at 0.1."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift of the intervention proposer system prompt."""
        from synthorg.engine.intervention.proposer import _SYSTEM_PROMPT

        fp = fingerprint_prompt(_SYSTEM_PROMPT)
        assert fp == self.PINNED_FP, (
            f"intervention proposer system-prompt fingerprint drifted: got "
            f"{fp!r}, expected {self.PINNED_FP!r}. Update the pin if intentional."
        )
