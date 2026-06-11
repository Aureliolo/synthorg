# module-kind: tests
"""Unit tests for the benchmark's quality-varying scripted strategy.

Verifies the strategy returns the active brief's deliverable for the configured
profile, falls back to the generic deliverable for an unmapped brief, and that
the shipped deliverable map renders a competent solution that satisfies the
executable brief's hidden check and a degraded one that does not.
"""

from typing import Final

import pytest

from evals.runner.deliverables import (
    BENCHMARK_DELIVERABLES,
    DEFAULT_DELIVERABLE,
    TEXT_NORMALISATION_BRIEF_ID,
)
from evals.runner.profiles import BenchmarkStrategyProfile
from evals.runner.strategies import QualityVaryingStrategy
from synthorg.core.completion_enums import FinishReason

pytestmark = pytest.mark.unit

_DELIVERABLES: Final[dict[str, dict[BenchmarkStrategyProfile, str]]] = {
    "brief-a": {
        BenchmarkStrategyProfile.COMPETENT: "competent-a",
        BenchmarkStrategyProfile.DEGRADED: "degraded-a",
    },
}


def _strategy(profile: BenchmarkStrategyProfile) -> QualityVaryingStrategy:
    return QualityVaryingStrategy(
        profile=profile,
        deliverables=_DELIVERABLES,
        default_content="fallback",
    )


def _content(strategy: QualityVaryingStrategy, brief_id: str) -> str:
    strategy.activate(brief_id)
    response = strategy.next_response([], "model-001", None, None)
    assert response.finish_reason is FinishReason.STOP
    assert response.content is not None
    return response.content


def test_returns_active_brief_content_for_profile() -> None:
    """The active brief's deliverable is rendered at the configured profile."""
    assert _content(_strategy(BenchmarkStrategyProfile.COMPETENT), "brief-a") == (
        "competent-a"
    )
    assert _content(_strategy(BenchmarkStrategyProfile.DEGRADED), "brief-a") == (
        "degraded-a"
    )


def test_falls_back_to_default_for_unmapped_brief() -> None:
    """A brief absent from the map gets the generic deliverable."""
    assert _content(_strategy(BenchmarkStrategyProfile.DEGRADED), "judged-x") == (
        "fallback"
    )


def test_default_before_activation_is_fallback() -> None:
    """With no brief activated, the strategy returns the default deliverable."""
    strategy = _strategy(BenchmarkStrategyProfile.COMPETENT)
    response = strategy.next_response([], "model-001", None, None)
    assert response.content == "fallback"


def test_turn_cost_is_stamped() -> None:
    """Each completion carries the configured non-zero per-turn cost."""
    strategy = QualityVaryingStrategy(
        profile=BenchmarkStrategyProfile.COMPETENT,
        deliverables={},
        default_content=DEFAULT_DELIVERABLE,
        turn_cost=0.02,
    )
    response = strategy.next_response([], "model-001", None, None)
    assert response.usage.cost == pytest.approx(0.02)


def test_shipped_competent_solution_satisfies_hidden_check() -> None:
    """The shipped competent solution trims + case-folds; the degraded one does not.

    Mirrors the executable brief's hidden check
    (``normalise('  AB ') == 'ab'``) at unit tier without a subprocess, so the
    deliverable fixtures cannot silently drift away from the brief's contract.
    """
    variants = BENCHMARK_DELIVERABLES[TEXT_NORMALISATION_BRIEF_ID]
    competent_ns: dict[str, object] = {}
    exec(variants[BenchmarkStrategyProfile.COMPETENT], competent_ns)  # noqa: S102
    degraded_ns: dict[str, object] = {}
    exec(variants[BenchmarkStrategyProfile.DEGRADED], degraded_ns)  # noqa: S102

    competent_normalise = competent_ns["normalise"]
    degraded_normalise = degraded_ns["normalise"]
    assert callable(competent_normalise)
    assert callable(degraded_normalise)
    assert competent_normalise("  AB ") == "ab"
    assert degraded_normalise("  AB ") != "ab"
