"""Prompt eval: charter interview strategy temperature + prompt drift."""

import inspect

import pytest

from tests.evals.prompt._harness import (
    completion_temperature_is_config_sourced,
    fingerprint_prompt,
)


@pytest.mark.unit
class TestCharterStrategyPromptContract:
    """Guard rails for the charter interview prompt surface."""

    PINNED_FP = "d11c39b6990a03f1"

    def test_temperature_is_config_sourced(self) -> None:
        """Interview temperature must be drawn from config, not a literal."""
        from synthorg.meta.charter import strategy

        source = inspect.getsource(strategy)
        assert completion_temperature_is_config_sourced(source), (
            "CharterStrategy must source temperature from "
            "self._config.interview_temperature, not a literal."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift of the charter interview template."""
        from synthorg.meta.charter.prompts import CHARTER_INTERVIEW_PROMPT

        fp = fingerprint_prompt(CHARTER_INTERVIEW_PROMPT)
        assert fp == self.PINNED_FP, (
            f"charter interview prompt drifted: {fp!r} != "
            f"{self.PINNED_FP!r}. Update the pin if intentional."
        )
