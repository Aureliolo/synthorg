"""Prompt eval: red-team grounding substrate temperature + prompt drift.

The grounding checker runs two structured completions (claim extraction and
entailment) through a path that pins ``LLM_TEMPERATURE = 0.0``. Both system
prompts are fingerprinted against silent drift.
"""

import pytest

from tests.evals.prompt._harness import fingerprint_prompt


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
