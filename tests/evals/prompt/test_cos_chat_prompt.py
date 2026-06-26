"""Prompt eval: chief-of-staff chat prompts temperature + drift.

``ChiefOfStaffChat`` drives three templates (proposal explanation, alert
explanation, chat query) and draws its temperature from config. Each template
is pinned against silent drift.
"""

import inspect

import pytest

from synthorg.meta.chief_of_staff.prompts import (
    ALERT_EXPLANATION_USER,
    CHAT_QUERY_USER,
    PROPOSAL_EXPLANATION_USER,
)
from tests.evals.prompt._harness import (
    LabelledExample,
    assert_accuracy_at_least,
    completion_temperature_is_config_sourced,
    fingerprint_prompt,
    run_grader,
)


def _proposal_kwargs(**overrides: str) -> dict[str, str]:
    """Full ``PROPOSAL_EXPLANATION_USER`` format kwargs with overrides."""
    base = {
        "proposal_title": "Raise review depth",
        "proposal_description": "Add a second reviewer for high-risk merges",
        "proposal_rationale": "rising defect rate in the payments domain",
        "proposal_confidence": "0.87",
        "rule_name": "defect_rate_spike",
        "rule_severity": "high",
        "signal_context": "defect_rate=0.12 (baseline 0.04)",
        "approval_context": "Historical approval rate: 75%",
    }
    return base | overrides


def _alert_kwargs(**overrides: str) -> dict[str, str]:
    """Full ``ALERT_EXPLANATION_USER`` format kwargs with overrides."""
    base = {
        "alert_type": "budget_overrun",
        "alert_severity": "critical",
        "affected_domains": "finance, hr",
        "signal_context": "spend=128% of cap",
    }
    return base | overrides


def _chat_kwargs(**overrides: str) -> dict[str, str]:
    """Full ``CHAT_QUERY_USER`` format kwargs with overrides."""
    base = {
        "snapshot_summary": "hiring velocity down 12%",
        "recent_context": "No recent proposals or alerts.",
        "user_question": "What changed in hiring?",
    }
    return base | overrides


@pytest.mark.unit
class TestCosChatPromptContract:
    """Guard rails for the chief-of-staff chat prompt surfaces."""

    PINNED_PROPOSAL_FP = "2c0254f1f9781538"
    PINNED_ALERT_FP = "b50bc7c47de8439e"
    PINNED_QUERY_FP = "d40af1415c7b3c51"

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

        fp = fingerprint_prompt(
            "[SYSTEM]\n"
            + PROPOSAL_EXPLANATION_SYSTEM
            + "\n[USER]\n"
            + PROPOSAL_EXPLANATION_USER
        )
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

        fp = fingerprint_prompt(
            "[SYSTEM]\n"
            + ALERT_EXPLANATION_SYSTEM
            + "\n[USER]\n"
            + ALERT_EXPLANATION_USER
        )
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

        fp = fingerprint_prompt(
            "[SYSTEM]\n" + CHAT_QUERY_SYSTEM + "\n[USER]\n" + CHAT_QUERY_USER
        )
        assert fp == self.PINNED_QUERY_FP, (
            f"chat query prompt drifted: {fp!r} != "
            f"{self.PINNED_QUERY_FP!r}. Update the pin if intentional."
        )


@pytest.mark.unit
class TestCosChatPromptGradedExamples:
    """Labelled before/after eval for the three USER prompt surfaces.

    Each example pairs a structured input (``inp``: the ``(template,
    format_kwargs)`` a production call site would build) with the
    expected output facts (``expected``: the substrings that MUST appear
    in the rendered prompt). Where the fingerprint tests catch *any*
    drift, these grade the *intent*: a prompt edit that silently drops a
    field (e.g. removes ``{proposal_confidence}``) renders a prompt that
    no longer carries that fact and fails here with a named example.
    """

    EXAMPLES: tuple[LabelledExample, ...] = (
        LabelledExample(
            name="proposal_surfaces_title_and_confidence",
            inp=(
                PROPOSAL_EXPLANATION_USER,
                _proposal_kwargs(
                    proposal_title="Raise review depth",
                    proposal_confidence="0.87",
                ),
            ),
            expected=("Raise review depth", "0.87"),
        ),
        LabelledExample(
            name="proposal_surfaces_rationale",
            inp=(
                PROPOSAL_EXPLANATION_USER,
                _proposal_kwargs(
                    proposal_rationale="rising defect rate in payments",
                ),
            ),
            expected=("rising defect rate in payments",),
        ),
        LabelledExample(
            name="alert_surfaces_type_and_severity",
            inp=(
                ALERT_EXPLANATION_USER,
                _alert_kwargs(alert_type="budget_overrun", alert_severity="critical"),
            ),
            expected=("budget_overrun", "critical"),
        ),
        LabelledExample(
            name="alert_surfaces_affected_domains",
            inp=(
                ALERT_EXPLANATION_USER,
                _alert_kwargs(affected_domains="finance, hr"),
            ),
            expected=("finance, hr",),
        ),
        LabelledExample(
            name="chat_surfaces_user_question",
            inp=(
                CHAT_QUERY_USER,
                _chat_kwargs(user_question="What changed in hiring?"),
            ),
            expected=("What changed in hiring?",),
        ),
        LabelledExample(
            name="chat_surfaces_snapshot_summary",
            inp=(
                CHAT_QUERY_USER,
                _chat_kwargs(snapshot_summary="hiring velocity down 12%"),
            ),
            expected=("hiring velocity down 12%",),
        ),
    )

    def test_user_prompts_surface_their_key_inputs(self) -> None:
        """Every labelled (input, expected-facts) pair grades at 100%."""

        def _grade(actual_input: object, expected: object) -> bool:
            assert isinstance(actual_input, tuple)
            template, kwargs = actual_input
            assert isinstance(template, str)
            assert isinstance(kwargs, dict)
            assert isinstance(expected, tuple)
            rendered = template.format(**kwargs)
            return all(needle in rendered for needle in expected)

        outcome = run_grader(self.EXAMPLES, _grade)
        assert_accuracy_at_least(outcome, 1.0)
