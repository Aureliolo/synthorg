"""Prompt eval: procedural success proposer temperature + prompt drift."""

import inspect

import pytest

from tests.evals.prompt._harness import (
    completion_temperature_is_config_sourced,
    fingerprint_prompt,
)


@pytest.mark.unit
class TestSuccessProposerPromptContract:
    """Guard rails for the procedural success proposer prompt surface."""

    PINNED_FP = "4f0137904a74c9f0"

    def test_temperature_is_config_sourced(self) -> None:
        """Proposer temperature must be drawn from config, not a literal."""
        from synthorg.memory.procedural import success_proposer

        source = inspect.getsource(success_proposer)
        assert completion_temperature_is_config_sourced(source), (
            "SuccessProposer must source temperature from config.temperature, "
            "not a literal."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift of the success proposer system prompt."""
        from synthorg.memory.procedural.success_proposer import _SYSTEM_PROMPT

        fp = fingerprint_prompt(_SYSTEM_PROMPT)
        assert fp == self.PINNED_FP, (
            f"success proposer system-prompt fingerprint drifted: got {fp!r}, "
            f"expected {self.PINNED_FP!r}. Update the pin if intentional."
        )
