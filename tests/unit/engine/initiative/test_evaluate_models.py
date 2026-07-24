"""Tests for the evaluate stage's verdict model and its coverage invariant."""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import InitiativeEvaluationParseError
from synthorg.engine.initiative.evaluate_models import (
    CriterionOutcome,
    args_to_evaluation,
    build_evaluation_tool,
)

pytestmark = pytest.mark.unit

_CRITERIA = (NotBlankStr("the game is playable"), NotBlankStr("it saves scores"))


def _verdict(criterion: str, outcome: str) -> dict[str, str]:
    return {
        "criterion": criterion,
        "outcome": outcome,
        "evidence": f"ran it and observed {outcome}",
    }


class TestParsing:
    """The verdict a session submits through the terminal tool."""

    def test_a_full_verdict_parses(self) -> None:
        report = args_to_evaluation(
            {
                "summary": "Played it end to end",
                "verdicts": [_verdict(c, "met") for c in _CRITERIA],
            },
            criteria=_CRITERIA,
        )

        assert report.objective_met is True
        assert len(report.verdicts) == 2

    def test_a_partial_outcome_does_not_deliver(self) -> None:
        """Mostly met is not met: the gap is what the replan is for."""
        report = args_to_evaluation(
            {
                "summary": "Played it",
                "verdicts": [
                    _verdict(_CRITERIA[0], "met"),
                    _verdict(_CRITERIA[1], "partial"),
                ],
            },
            criteria=_CRITERIA,
        )

        assert report.objective_met is False

    def test_an_unmet_outcome_does_not_deliver(self) -> None:
        report = args_to_evaluation(
            {
                "summary": "Played it",
                "verdicts": [
                    _verdict(_CRITERIA[0], "met"),
                    _verdict(_CRITERIA[1], "unmet"),
                ],
            },
            criteria=_CRITERIA,
        )

        assert report.objective_met is False
        assert report.verdicts[1].outcome is CriterionOutcome.UNMET


class TestCoverage:
    """A criterion cannot be dropped on the way to a pass."""

    def test_an_unjudged_criterion_is_rejected(self) -> None:
        with pytest.raises(InitiativeEvaluationParseError, match=r"does not judge"):
            args_to_evaluation(
                {
                    "summary": "Played it",
                    "verdicts": [_verdict(_CRITERIA[0], "met")],
                },
                criteria=_CRITERIA,
            )

    def test_an_invented_criterion_is_rejected(self) -> None:
        """Judging something the objective never asked for is not evidence."""
        with pytest.raises(InitiativeEvaluationParseError, match=r"does not have"):
            args_to_evaluation(
                {
                    "summary": "Played it",
                    "verdicts": [
                        *(_verdict(c, "met") for c in _CRITERIA),
                        _verdict("it is beautiful", "met"),
                    ],
                },
                criteria=_CRITERIA,
            )

    def test_a_duplicate_verdict_is_rejected(self) -> None:
        with pytest.raises(InitiativeEvaluationParseError, match=r"exactly once"):
            args_to_evaluation(
                {
                    "summary": "Played it",
                    "verdicts": [
                        _verdict(_CRITERIA[0], "met"),
                        _verdict(_CRITERIA[0], "unmet"),
                        _verdict(_CRITERIA[1], "met"),
                    ],
                },
                criteria=_CRITERIA,
            )

    def test_an_empty_verdict_list_is_rejected(self) -> None:
        with pytest.raises(InitiativeEvaluationParseError):
            args_to_evaluation(
                {"summary": "Played it", "verdicts": []}, criteria=_CRITERIA
            )

    def test_missing_evidence_is_rejected(self) -> None:
        """An unevidenced pass is a guess."""
        with pytest.raises(InitiativeEvaluationParseError, match=r"evidence"):
            args_to_evaluation(
                {
                    "summary": "Played it",
                    "verdicts": [
                        {"criterion": _CRITERIA[0], "outcome": "met"},
                        _verdict(_CRITERIA[1], "met"),
                    ],
                },
                criteria=_CRITERIA,
            )


class TestToolDefinition:
    """The terminal tool the session submits through."""

    def test_the_schema_requires_a_verdict_list(self) -> None:
        tool = build_evaluation_tool()
        assert tool.name == "submit_evaluation"
        assert tool.parameters_schema["required"] == ["summary", "verdicts"]
