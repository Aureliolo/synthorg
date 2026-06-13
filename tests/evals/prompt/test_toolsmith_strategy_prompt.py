"""Prompt eval: toolsmith authoring strategy temperature + prompt drift."""

import inspect

import pytest

from tests.evals.prompt._harness import (
    completion_temperature_is_config_sourced,
    fingerprint_prompt,
)


@pytest.mark.unit
class TestToolsmithStrategyPromptContract:
    """Guard rails for the tool-authoring prompt surface."""

    PINNED_FP = "733a608d093fbf48"

    def test_temperature_is_config_sourced(self) -> None:
        """Authoring temperature must be drawn from config, not a literal."""
        from synthorg.meta.toolsmith import strategy

        source = inspect.getsource(strategy)
        assert completion_temperature_is_config_sourced(source), (
            "ToolsmithStrategy must source temperature from "
            "self._config.authoring.temperature, not a literal."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift of the toolsmith system prompt."""
        from synthorg.meta.toolsmith.strategy import _SYSTEM_PROMPT

        fp = fingerprint_prompt(_SYSTEM_PROMPT)
        assert fp == self.PINNED_FP, (
            f"toolsmith system-prompt fingerprint drifted: got {fp!r}, "
            f"expected {self.PINNED_FP!r}. Update the pin if intentional."
        )
