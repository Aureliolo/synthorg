"""Prompt eval: research planner / synthesiser / triage temperature + drift.

All three research surfaces complete through ``research/_llm.py``, which pins
``_DETERMINISTIC_TEMPERATURE = 0.0``. Each carries a distinct system prompt;
all three are fingerprinted against silent drift.
"""

import pytest

from tests.evals.prompt._harness import fingerprint_prompt


@pytest.mark.unit
class TestResearchTemperatureContract:
    """The shared research completion path must stay deterministic."""

    def test_deterministic_temperature_is_zero(self) -> None:
        from synthorg.research._llm import _DETERMINISTIC_TEMPERATURE

        assert _DETERMINISTIC_TEMPERATURE == 0.0, (
            "research/_llm.py must keep _DETERMINISTIC_TEMPERATURE pinned at 0.0."
        )


@pytest.mark.unit
class TestResearchPlannerPrompt:
    """Guard rails for the research planner prompt."""

    PINNED_FP = "a5af4dbbbba753a2"

    def test_prompt_fingerprint_is_pinned(self) -> None:
        from synthorg.research.planning.llm_planner import _SYSTEM_PROMPT

        fp = fingerprint_prompt(_SYSTEM_PROMPT)
        assert fp == self.PINNED_FP, (
            f"research planner prompt drifted: {fp!r} != {self.PINNED_FP!r}."
        )


@pytest.mark.unit
class TestResearchSynthesizerPrompt:
    """Guard rails for the research synthesiser prompt."""

    PINNED_FP = "fa2ad9a20a4988e9"

    def test_prompt_fingerprint_is_pinned(self) -> None:
        from synthorg.research.synthesis.llm_synthesizer import _SYSTEM_PROMPT

        fp = fingerprint_prompt(_SYSTEM_PROMPT)
        assert fp == self.PINNED_FP, (
            f"research synthesiser prompt drifted: {fp!r} != {self.PINNED_FP!r}."
        )


@pytest.mark.unit
class TestResearchTriagePrompt:
    """Guard rails for the research triage prompt."""

    PINNED_FP = "0eb4ceeabd787cda"

    def test_prompt_fingerprint_is_pinned(self) -> None:
        from synthorg.research.triage.llm import _SYSTEM_PROMPT

        fp = fingerprint_prompt(_SYSTEM_PROMPT)
        assert fp == self.PINNED_FP, (
            f"research triage prompt drifted: {fp!r} != {self.PINNED_FP!r}."
        )
