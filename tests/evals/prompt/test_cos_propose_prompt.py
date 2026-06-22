"""Prompt eval: chief-of-staff proposer temperature + prompt drift."""

import inspect

import pytest

from tests.evals.prompt._harness import (
    completion_temperature_is_config_sourced,
    fingerprint_prompt,
)


@pytest.mark.unit
class TestCosProposePromptContract:
    """Guard rails for the chief-of-staff proposer prompt surface."""

    PINNED_FP = "f499ae83356491df"

    def test_temperature_is_config_sourced(self) -> None:
        """Propose temperature must be drawn from config, not a literal."""
        from synthorg.meta.chief_of_staff import propose

        source = inspect.getsource(propose)
        assert completion_temperature_is_config_sourced(source), (
            "ChiefOfStaffProposer must source temperature from "
            "self._config.propose_temperature, not a literal."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift of the conversational-propose SYSTEM + USER."""
        from synthorg.meta.chief_of_staff.prompts import (
            CONVERSATIONAL_PROPOSE_SYSTEM,
            CONVERSATIONAL_PROPOSE_USER,
        )

        fp = fingerprint_prompt(
            CONVERSATIONAL_PROPOSE_SYSTEM + CONVERSATIONAL_PROPOSE_USER
        )
        assert fp == self.PINNED_FP, (
            f"conversational propose prompt drifted: {fp!r} != "
            f"{self.PINNED_FP!r}. Update the pin if intentional."
        )
