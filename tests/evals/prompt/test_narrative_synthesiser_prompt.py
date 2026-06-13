"""Prompt eval: run-narrative synthesiser temperature + prompt drift."""

import inspect

import pytest

from tests.evals.prompt._harness import (
    completion_temperature_is_config_sourced,
    fingerprint_prompt,
)


@pytest.mark.unit
class TestNarrativeSynthesiserPromptContract:
    """Guard rails for the narrative synthesiser prompt surface."""

    PINNED_FP = "083730125b1adf3f"

    def test_temperature_is_config_sourced(self) -> None:
        """Narrative temperature must be drawn from config, not a literal."""
        from synthorg.meta.chief_of_staff.narrative import synthesiser

        source = inspect.getsource(synthesiser)
        assert completion_temperature_is_config_sourced(source), (
            "NarrativeSynthesiser must source temperature from "
            "self._config.narrative_temperature, not a literal."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift of the run-narrative prose template."""
        from synthorg.meta.chief_of_staff.prompts import RUN_NARRATIVE_PROSE_PROMPT

        fp = fingerprint_prompt(RUN_NARRATIVE_PROSE_PROMPT)
        assert fp == self.PINNED_FP, (
            f"run narrative prose prompt drifted: {fp!r} != "
            f"{self.PINNED_FP!r}. Update the pin if intentional."
        )
