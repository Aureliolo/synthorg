"""Prompt eval: chief-of-staff chat prompts temperature + drift.

``ChiefOfStaffChat`` drives three templates (proposal explanation, alert
explanation, chat query) and draws its temperature from config. Each template
is pinned against silent drift.
"""

import inspect

import pytest

from tests.evals.prompt._harness import (
    completion_temperature_is_config_sourced,
    fingerprint_prompt,
)


@pytest.mark.unit
class TestCosChatPromptContract:
    """Guard rails for the chief-of-staff chat prompt surfaces."""

    PINNED_PROPOSAL_FP = "0cf259fcbd054236"
    PINNED_ALERT_FP = "98fa3118b80ae623"
    PINNED_QUERY_FP = "c4c4176855df3b46"

    def test_temperature_is_config_sourced(self) -> None:
        """Chat temperature must be drawn from config, not a literal."""
        from synthorg.meta.chief_of_staff import chat

        source = inspect.getsource(chat)
        assert completion_temperature_is_config_sourced(source), (
            "ChiefOfStaffChat must source temperature from "
            "self._config.chat_temperature, not a literal."
        )

    def test_proposal_explanation_fingerprint_is_pinned(self) -> None:
        """Detect drift of the proposal-explanation SYSTEM + USER prompt."""
        from synthorg.meta.chief_of_staff.prompts import (
            PROPOSAL_EXPLANATION_SYSTEM,
            PROPOSAL_EXPLANATION_USER,
        )

        fp = fingerprint_prompt(PROPOSAL_EXPLANATION_SYSTEM + PROPOSAL_EXPLANATION_USER)
        assert fp == self.PINNED_PROPOSAL_FP, (
            f"proposal explanation prompt drifted: {fp!r} != "
            f"{self.PINNED_PROPOSAL_FP!r}. Update the pin if intentional."
        )

    def test_alert_explanation_fingerprint_is_pinned(self) -> None:
        """Detect drift of the alert-explanation SYSTEM + USER prompt."""
        from synthorg.meta.chief_of_staff.prompts import (
            ALERT_EXPLANATION_SYSTEM,
            ALERT_EXPLANATION_USER,
        )

        fp = fingerprint_prompt(ALERT_EXPLANATION_SYSTEM + ALERT_EXPLANATION_USER)
        assert fp == self.PINNED_ALERT_FP, (
            f"alert explanation prompt drifted: {fp!r} != "
            f"{self.PINNED_ALERT_FP!r}. Update the pin if intentional."
        )

    def test_chat_query_fingerprint_is_pinned(self) -> None:
        """Detect drift of the chat-query SYSTEM + USER prompt."""
        from synthorg.meta.chief_of_staff.prompts import (
            CHAT_QUERY_SYSTEM,
            CHAT_QUERY_USER,
        )

        fp = fingerprint_prompt(CHAT_QUERY_SYSTEM + CHAT_QUERY_USER)
        assert fp == self.PINNED_QUERY_FP, (
            f"chat query prompt drifted: {fp!r} != "
            f"{self.PINNED_QUERY_FP!r}. Update the pin if intentional."
        )
