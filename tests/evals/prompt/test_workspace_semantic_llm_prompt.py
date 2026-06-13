"""Prompt eval: workspace semantic reviewer temperature + prompt drift."""

import inspect

import pytest

from tests.evals.prompt._harness import (
    completion_temperature_is_config_sourced,
    fingerprint_prompt,
)


@pytest.mark.unit
class TestWorkspaceSemanticLlmPromptContract:
    """Guard rails for the workspace semantic review prompt surface."""

    PINNED_FP = "c6c130da757808ce"

    def test_temperature_and_top_p_are_config_sourced(self) -> None:
        """Both temperature and top_p must be drawn from config."""
        from synthorg.engine.workspace import semantic_llm

        source = inspect.getsource(semantic_llm)
        assert completion_temperature_is_config_sourced(source), (
            "SemanticLLMWorkspaceReviewer must source temperature from "
            "self._config.llm_temperature, not a literal."
        )
        assert "top_p=self._config.llm_top_p" in source, (
            "SemanticLLMWorkspaceReviewer must source top_p from config too."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect drift of the semantic review system message + tool contract."""
        from synthorg.engine.workspace.semantic_llm_prompt import (
            build_semantic_review_tool,
            build_system_message,
        )

        system = build_system_message()
        tool = build_semantic_review_tool()
        surface = f"{system.content}\n{tool.name}\n{tool.description}"
        fp = fingerprint_prompt(surface)
        assert fp == self.PINNED_FP, (
            f"workspace semantic prompt surface drifted: {fp!r} != "
            f"{self.PINNED_FP!r}. Update the pin if intentional."
        )
