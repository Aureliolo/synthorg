# module-kind: tests
"""Derivation of a promotion recommendation from the measured scoreboard.

The harness adds no selection machinery: its whole output is a pair of values
for the settings that already exist, ``engine.default_loop_type`` and
``engine.loop_complexity_overrides``. So the recommendation must be (a) derived
only from loops that actually cleared the correctness gate, and (b) expressible
in exactly the string form those settings accept, which is asserted here against
the settings' own validator patterns.
"""

import re

import pytest

from evals.loop_ab.promotion import (
    ComplexityWinner,
    PromotionRecommendation,
    recommend_promotion,
)
from evals.loop_ab.rubric import DimensionScores, LoopCellScore
from synthorg.core.task_enums import Complexity
from synthorg.core.types import NotBlankStr
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.registry import get_registry

pytestmark = pytest.mark.unit


def _score(
    loop_type: str, *, composite: float, disqualified: bool = False
) -> LoopCellScore:
    """Build a scored row; dimension detail is irrelevant to promotion."""
    return LoopCellScore(
        loop_type=NotBlankStr(loop_type),
        dimensions=DimensionScores(
            correctness=1.0, tokens=1.0, latency=1.0, turns=1.0, resilience=1.0
        ),
        composite=composite,
        disqualified=disqualified,
        disqualification_reason="below the gate" if disqualified else None,
    )


def _setting_pattern(key: str) -> str:
    """Read a setting's own validator pattern from the live registry."""
    definition = get_registry().get(SettingNamespace.ENGINE, key)
    assert definition is not None, f"engine.{key} is not registered"
    pattern = definition.validator_pattern
    assert pattern is not None, f"engine.{key} declares no validator pattern"
    return pattern


def test_the_highest_scoring_loop_wins_a_complexity_bucket() -> None:
    """A bucket's winner is simply its best non-disqualified composite."""
    recommendation = recommend_promotion(
        {
            Complexity.SIMPLE: (
                _score("react", composite=90.0),
                _score("hybrid", composite=70.0),
            )
        }
    )

    assert recommendation.winners == (
        ComplexityWinner(
            complexity=Complexity.SIMPLE,
            loop_type=NotBlankStr("react"),
            composite=90.0,
        ),
    )


def test_a_disqualified_loop_cannot_win_however_high_it_scores() -> None:
    """The gate is load-bearing: a loop that failed the task is not promotable."""
    recommendation = recommend_promotion(
        {
            Complexity.SIMPLE: (
                _score("react", composite=99.0, disqualified=True),
                _score("hybrid", composite=40.0),
            )
        }
    )

    assert recommendation.winners[0].loop_type == "hybrid"


def test_the_most_frequent_winner_becomes_the_default_loop() -> None:
    """default_loop_type is the fallback, so the broadest winner takes it."""
    recommendation = recommend_promotion(
        {
            Complexity.SIMPLE: (
                _score("react", composite=90.0),
                _score("hybrid", composite=10.0),
            ),
            Complexity.MEDIUM: (
                _score("react", composite=90.0),
                _score("hybrid", composite=10.0),
            ),
            Complexity.COMPLEX: (
                _score("react", composite=10.0),
                _score("hybrid", composite=90.0),
            ),
        }
    )

    assert recommendation.default_loop_type == "react"


def test_only_buckets_differing_from_the_default_become_overrides() -> None:
    """An override restating the default is noise; the setting stays minimal."""
    recommendation = recommend_promotion(
        {
            Complexity.SIMPLE: (
                _score("react", composite=90.0),
                _score("openhands", composite=10.0),
            ),
            Complexity.MEDIUM: (
                _score("react", composite=90.0),
                _score("openhands", composite=10.0),
            ),
            Complexity.COMPLEX: (
                _score("react", composite=10.0),
                _score("openhands", composite=90.0),
            ),
        }
    )

    assert recommendation.loop_complexity_overrides == "complex:openhands"


def test_a_unanimous_winner_needs_no_overrides() -> None:
    """One loop winning everywhere is expressible as the default alone."""
    recommendation = recommend_promotion(
        {
            Complexity.SIMPLE: (_score("react", composite=90.0),),
            Complexity.COMPLEX: (_score("react", composite=90.0),),
        }
    )

    assert recommendation.default_loop_type == "react"
    assert recommendation.loop_complexity_overrides == ""


def test_the_recommendation_matches_the_settings_validator_patterns() -> None:
    """The output is pasted straight into settings, so it must be accepted there.

    Reads the live ``SettingDefinition.validator_pattern`` rather than a copy,
    so a future change to the settings' accepted grammar fails here instead of
    producing a recommendation the settings API would reject.
    """
    recommendation = recommend_promotion(
        {
            Complexity.SIMPLE: (_score("react", composite=90.0),),
            Complexity.MEDIUM: (_score("plan_execute", composite=90.0),),
            Complexity.COMPLEX: (_score("openhands", composite=90.0),),
            Complexity.EPIC: (_score("hybrid", composite=90.0),),
        }
    )

    assert re.match(
        _setting_pattern("default_loop_type"), recommendation.default_loop_type
    )
    assert re.match(
        _setting_pattern("loop_complexity_overrides"),
        recommendation.loop_complexity_overrides,
    )


def test_overrides_are_emitted_in_a_stable_complexity_order() -> None:
    """A recommendation must diff cleanly across re-recordings.

    Buckets are supplied out of order to prove the emitted string is ordered by
    complexity rather than by insertion. react and openhands each win two
    buckets, so the default also exercises the tie-break: equal win counts
    resolve on aggregate score, which react leads.
    """
    recommendation = recommend_promotion(
        {
            Complexity.EPIC: (_score("openhands", composite=90.0),),
            Complexity.SIMPLE: (_score("react", composite=95.0),),
            Complexity.COMPLEX: (_score("openhands", composite=90.0),),
            Complexity.MEDIUM: (_score("react", composite=95.0),),
        }
    )

    assert recommendation.default_loop_type == "react"
    assert (
        recommendation.loop_complexity_overrides == "complex:openhands,epic:openhands"
    )


def test_a_scoreboard_where_every_loop_failed_recommends_nothing() -> None:
    """No loop cleared the gate, so there is no evidence-backed promotion."""
    recommendation = recommend_promotion(
        {Complexity.SIMPLE: (_score("react", composite=99.0, disqualified=True),)}
    )

    assert recommendation.default_loop_type is None
    assert recommendation.winners == ()


def test_an_empty_scoreboard_is_refused() -> None:
    """Recommending from no measurements at all is a contract error."""
    with pytest.raises(ValueError, match="at least one"):
        recommend_promotion({})


def test_the_recommendation_records_why_each_bucket_was_decided() -> None:
    """Evidence-backed means the winning score travels with the recommendation."""
    recommendation: PromotionRecommendation = recommend_promotion(
        {Complexity.MEDIUM: (_score("hybrid", composite=77.5),)}
    )

    assert recommendation.winners[0].composite == pytest.approx(77.5)
