"""Prompt eval: LLM security evaluator temperature contract + prompt drift.

``LlmSecurityEvaluator`` is a security-critical surface: it classifies actions
as allow / deny / escalate. The suite pins two deterministic properties so a
silent edit cannot weaken the guard:

1. The completion config draws its temperature from the evaluator's config
   (not a hardcoded literal), so an operator's determinism choice flows
   through.
2. The system prompt bytes have not drifted; an intentional edit must update
   the pinned fingerprint here.
"""

import inspect

import pytest

from tests.evals.prompt._harness import (
    completion_temperature_is_config_sourced,
    fingerprint_prompt,
)


@pytest.mark.unit
class TestLlmSecurityEvaluatorPromptContract:
    """Guard rails for the LLM security evaluator prompt surface."""

    PINNED_FP = "0f0696f91435804a"

    def test_temperature_is_config_sourced(self) -> None:
        """Evaluator temperature must come from config, never a literal."""
        from synthorg.security import llm_evaluator

        source = inspect.getsource(llm_evaluator)
        assert completion_temperature_is_config_sourced(source), (
            "LlmSecurityEvaluator must build CompletionConfig with "
            "temperature drawn from self._config.temperature, not a literal."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift of the security evaluator system prompt."""
        from synthorg.security.llm_evaluator import _SYSTEM_PROMPT

        fp = fingerprint_prompt(_SYSTEM_PROMPT)
        assert fp == self.PINNED_FP, (
            f"LLM security evaluator system-prompt fingerprint drifted: got "
            f"{fp!r}, expected {self.PINNED_FP!r}. If intentional, update the "
            "pinned fingerprint AND re-confirm the allow/deny/escalate contract."
        )
