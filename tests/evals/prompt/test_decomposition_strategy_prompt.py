"""Prompt eval: LLM decomposition strategy temperature + prompt drift."""

import inspect

import pytest

from tests.evals.prompt._harness import (
    completion_temperature_is_config_sourced,
    fingerprint_prompt,
)


@pytest.mark.unit
class TestDecompositionStrategyPromptContract:
    """Guard rails for the LLM decomposition prompt surface."""

    PINNED_FP = "5cd6b1e83feef8c3"

    def test_temperature_is_config_sourced(self) -> None:
        """Decomposition temperature must be drawn from config, not a literal."""
        from synthorg.engine.decomposition import llm

        source = inspect.getsource(llm)
        assert completion_temperature_is_config_sourced(source), (
            "LlmDecompositionStrategy must source temperature from "
            "self._config.temperature, not a literal."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect drift of the decomposition system message + tool contract."""
        from synthorg.engine.decomposition.llm_prompt import (
            build_decomposition_tool,
            build_system_message,
        )

        system = build_system_message()
        tool = build_decomposition_tool()
        surface = f"{system.content}\n{tool.name}\n{tool.description}"
        fp = fingerprint_prompt(surface)
        assert fp == self.PINNED_FP, (
            f"decomposition prompt surface drifted: {fp!r} != "
            f"{self.PINNED_FP!r}. Update the pin if intentional."
        )
