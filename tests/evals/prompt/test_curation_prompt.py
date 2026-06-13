"""Prompt eval: LLM training-curation selector temperature + prompt drift."""

import inspect

import pytest

from tests.evals.prompt._harness import (
    completion_temperature_is_config_sourced,
    fingerprint_prompt,
)


@pytest.mark.unit
class TestCurationPromptContract:
    """Guard rails for the LLM curated selector prompt surface."""

    # Fingerprint of the SYSTEM half assembled for a fixed (top_k, role,
    # level, content_type) tuple. The role is fenced into the USER half, so
    # the SYSTEM template is stable for these inputs.
    PINNED_FP = "886af8c0548e28bb"

    def test_temperature_and_max_tokens_are_set(self) -> None:
        """Curation must source temperature from config and pin max_tokens."""
        from synthorg.hr.training.curation import llm_curated

        source = inspect.getsource(llm_curated)
        assert completion_temperature_is_config_sourced(source), (
            "LLMCurated must source temperature from self._temperature."
        )
        assert "max_tokens=_MAX_TOKENS" in source, (
            "LLMCurated must pin max_tokens explicitly (not inherit the "
            "provider default)."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift of the assembled curation SYSTEM prompt."""
        from synthorg.hr.seniority import SeniorityLevel
        from synthorg.hr.training.curation.llm_curated import LLMCurated
        from synthorg.hr.training.models import ContentType

        selector = LLMCurated.__new__(LLMCurated)
        selector._top_k = 5
        system_prompt, _user_prompt = selector._build_prompt(
            (),
            "role",
            SeniorityLevel.JUNIOR,
            ContentType.SEMANTIC,
        )
        fp = fingerprint_prompt(system_prompt)
        assert fp == self.PINNED_FP, (
            f"curation SYSTEM prompt drifted: {fp!r} != {self.PINNED_FP!r}. "
            "Update the pin if intentional."
        )
