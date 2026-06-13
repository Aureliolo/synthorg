"""Prompt eval: abstractive summariser temperature + prompt drift."""

import pytest

from tests.evals.prompt._harness import fingerprint_prompt


@pytest.mark.unit
class TestAbstractiveSummarizerPromptContract:
    """Guard rails for the abstractive summariser prompt surface."""

    PINNED_FP = "1b5f6344abf4983d"

    def test_default_temperature_is_pinned(self) -> None:
        """The summariser must default to the pinned temperature constant."""
        from synthorg.memory.consolidation.abstractive import _DEFAULT_TEMPERATURE

        assert _DEFAULT_TEMPERATURE == 0.3, (
            "AbstractiveSummarizer must keep _DEFAULT_TEMPERATURE pinned at 0.3."
        )

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect drift; the untrusted-content directive must remain present."""
        from synthorg.memory.consolidation.abstractive import _SYSTEM_PROMPT

        assert "<untrusted" in _SYSTEM_PROMPT, (
            "AbstractiveSummarizer prompt must keep the untrusted-content fence."
        )
        fp = fingerprint_prompt(_SYSTEM_PROMPT)
        assert fp == self.PINNED_FP, (
            f"abstractive summariser system-prompt fingerprint drifted: got "
            f"{fp!r}, expected {self.PINNED_FP!r}. Update the pin if intentional."
        )
