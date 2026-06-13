"""Prompt eval: LLM vision verifier temperature contract + prompt drift.

``LLMVisionVerifier`` checks rendered UI against the requested change. It must
run deterministically (``temperature=0.0``) so a verdict does not flip across
CI shards, and its system prompt must not drift silently.
"""

import inspect

import pytest

from tests.evals.prompt._harness import (
    completion_temperature_is_literal,
    fingerprint_prompt,
)


@pytest.mark.unit
class TestVisionVerifierPromptContract:
    """Guard rails for the LLM vision verifier prompt surface."""

    PINNED_FP = "056f314b87f5e874"

    def test_temperature_is_zero(self) -> None:
        """Vision verdict must be deterministic at temperature=0."""
        from synthorg.security.visionverify.verifiers import llm_vision

        source = inspect.getsource(llm_vision)
        assert completion_temperature_is_literal(source, 0.0), (
            "LLMVisionVerifier must construct CompletionConfig with "
            "temperature=0.0 for a stable verdict."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift of the vision verifier system prompt."""
        from synthorg.security.visionverify.prompt import _SYSTEM_PROMPT

        fp = fingerprint_prompt(_SYSTEM_PROMPT)
        assert fp == self.PINNED_FP, (
            f"vision verifier system-prompt fingerprint drifted: got {fp!r}, "
            f"expected {self.PINNED_FP!r}. If intentional, update the pin and "
            "re-confirm matching-vs-mismatched-UI behaviour."
        )
