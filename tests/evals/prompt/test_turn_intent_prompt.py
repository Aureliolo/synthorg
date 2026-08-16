"""Prompt eval: turn-intent classifier prompt drift.

This prompt IS the routing taxonomy: its capability descriptions decide which
surface a message reaches, and therefore whether an operator is interviewed
before an initiative is stood up. Wording that sat unexamined for a month sent
every "build me X" straight past the charter interview, so a change here is a
routing decision and has to be a deliberate one.
"""

import pytest

from tests.evals.prompt._harness import fingerprint_prompt


@pytest.mark.unit
class TestTurnIntentPromptContract:
    """Guard rails for the per-turn capability classifier prompt."""

    PINNED_FP = "6d88ac4fe378ec19"

    def test_prompt_fingerprint_is_pinned(self) -> None:
        """Detect silent drift of the turn-intent SYSTEM + USER prompt."""
        from synthorg.meta.chief_of_staff.prompts import (
            TURN_INTENT_SYSTEM,
            TURN_INTENT_USER,
        )

        fp = fingerprint_prompt(
            "[SYSTEM]\n" + TURN_INTENT_SYSTEM + "\n[USER]\n" + TURN_INTENT_USER
        )
        assert fp == self.PINNED_FP, (
            f"turn intent prompt drifted: {fp!r} != {self.PINNED_FP!r}. "
            f"Update the pin if intentional."
        )

    def test_building_something_routes_to_the_charter_interview(self) -> None:
        """The taxonomy must send a request to build to the interview.

        The classifier is only ever as good as the definitions it is handed.
        Asserting on the wording is the only check available without spending
        on a live call, and it is the half that was wrong: ``propose`` claimed
        "something be built" while ``charter`` read as company-level, so no
        project request could ever reach the one surface that asks questions.
        """
        from synthorg.meta.chief_of_staff.prompts import TURN_INTENT_SYSTEM

        charter_line = next(
            line
            for line in TURN_INTENT_SYSTEM.splitlines()
            if line.strip().startswith('- "charter"')
        ).lower()
        propose_start = TURN_INTENT_SYSTEM.index('- "propose"')
        propose_block = TURN_INTENT_SYSTEM[propose_start:].split("\n- ")[0].lower()

        assert "built" in charter_line or "build" in charter_line
        assert "build" not in propose_block
        assert "built" not in propose_block
