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

    PINNED_FP = "c54193a357e19243"

    def test_temperature_is_config_sourced(self) -> None:
        """Narrative temperature must be drawn from config, not a literal."""
        from synthorg.meta.chief_of_staff.narrative import synthesiser

        source = inspect.getsource(synthesiser)
        assert completion_temperature_is_config_sourced(source), (
            "NarrativeSynthesiser must source temperature from "
            "self._config.narrative_temperature, not a literal."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift of the run-narrative prose SYSTEM + USER prompt."""
        from synthorg.meta.chief_of_staff.prompts import (
            RUN_NARRATIVE_PROSE_SYSTEM,
            RUN_NARRATIVE_PROSE_USER,
        )

        fp = fingerprint_prompt(
            "[SYSTEM]\n"
            + RUN_NARRATIVE_PROSE_SYSTEM
            + "\n[USER]\n"
            + RUN_NARRATIVE_PROSE_USER
        )
        assert fp == self.PINNED_FP, (
            f"run narrative prose prompt drifted: {fp!r} != "
            f"{self.PINNED_FP!r}. Update the pin if intentional."
        )
