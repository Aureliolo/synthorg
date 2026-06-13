"""Prompt eval: experience compressor temperature + prompt drift."""

import inspect

import pytest

from tests.evals.prompt._harness import (
    completion_temperature_is_config_sourced,
    fingerprint_prompt,
)


@pytest.mark.unit
class TestExperienceCompressorPromptContract:
    """Guard rails for the experience compressor prompt surface."""

    PINNED_FP = "43ab2f94650a869d"

    def test_temperature_is_config_sourced(self) -> None:
        """Compressor temperature must be drawn from config, not a literal."""
        from synthorg.memory.consolidation import compressor

        source = inspect.getsource(compressor)
        assert completion_temperature_is_config_sourced(source), (
            "ExperienceCompressor must source temperature from "
            "self._config.temperature, not a literal."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift of the compressor system prompt."""
        from synthorg.memory.consolidation.compressor import (
            _COMPRESSOR_SYSTEM_PROMPT,
        )

        fp = fingerprint_prompt(_COMPRESSOR_SYSTEM_PROMPT)
        assert fp == self.PINNED_FP, (
            f"experience compressor system-prompt fingerprint drifted: got "
            f"{fp!r}, expected {self.PINNED_FP!r}. Update the pin if intentional."
        )
