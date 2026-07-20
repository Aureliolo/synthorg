"""Golden recall eval: the proof that retrieval is not naive.

The issue this work closes warns that switching memory on over a naive
retriever is worse than leaving it off, so "it works" is not a claim
this repo is allowed to make without a number. These tests are that
number, and they double as the permanent regression guard against recall
rot: a change that quietly degrades ranking fails here rather than
surfacing months later as agents acting on the wrong memory.
"""

from pathlib import Path

import pytest

from synthorg.memory.injection import InjectionPoint
from synthorg.memory.retrieval_config import MemoryRetrievalConfig
from tests.evals.memory_recall.golden_set import CASES, score_recall
from tests.evals.memory_recall.harness import (
    reopened_backend,
    run_suite,
    seeded_backend,
    seeded_naive_backend,
    write_report,
)

# Marked ``unit`` rather than a bespoke eval tier on purpose: the harness
# is fast and deterministic (a fake embedder over in-process SQLite), so
# this must run on every push as a regression guard, not sit in a slow
# benchmark tier that only runs on demand.
pytestmark = pytest.mark.unit


def _stock_config() -> MemoryRetrievalConfig:
    """The naive baseline: a wide budget with diversity off.

    These were the pre-tuning defaults. They are constructed explicitly
    here rather than by reaching for ``MemoryRetrievalConfig()`` so the
    baseline stays fixed as the shipped defaults move; the comparison is
    only honest if the thing we beat cannot drift toward us.
    """
    return MemoryRetrievalConfig(
        diversity_penalty_enabled=False,
        max_memories=20,
        injection_point=InjectionPoint.SYSTEM,
    )


def _tuned_config() -> MemoryRetrievalConfig:
    """The shipped defaults, measured as-shipped.

    This is exactly ``MemoryRetrievalConfig()``: MMR on a bounded
    top-five budget injected at a position extremum are the shipped
    values, so the number this eval proves is the number production
    runs. Note what is deliberately *not* varied: ``fusion_strategy`` is
    inert for the durable backend, which fuses its dense and BM25 arms
    with RRF inside ``retrieve`` before the strategy sees them.
    """
    config = MemoryRetrievalConfig()
    assert config.diversity_penalty_enabled is True
    assert config.max_memories == 5
    assert config.injection_point is InjectionPoint.SYSTEM
    return config


class TestDurableBeatsNaiveBaseline:
    """The acceptance criterion, as a number rather than an assertion.

    The baseline is the substance of the issue, not a config variant:
    before this work the shared backend was an ephemeral keyword store,
    so "beats the naive baseline" means the durable hybrid substrate
    beats term matching on the same corpus and the same questions.
    """

    async def test_durable_hybrid_beats_keyword_matching(self, tmp_path: Path) -> None:
        config = _tuned_config()
        async with seeded_backend(tmp_path / "durable.db") as backend:
            durable = score_recall(await run_suite(backend, config))
        async with seeded_naive_backend() as backend:
            naive = score_recall(await run_suite(backend, config))

        write_report(
            tmp_path / "scorecard.json",
            {
                "durable": {
                    "precision": durable.precision,
                    "recall": durable.recall,
                    "f1": durable.f1,
                    "pollution": durable.pollution,
                },
                "naive": {
                    "precision": naive.precision,
                    "recall": naive.recall,
                    "f1": naive.f1,
                    "pollution": naive.pollution,
                },
            },
        )

        assert durable.f1 > naive.f1, (
            f"durable F1 {durable.f1:.3f} did not beat naive {naive.f1:.3f}; "
            "the substrate swap has to earn its keep"
        )

    async def test_config_tuning_never_regresses_quality(self, tmp_path: Path) -> None:
        """Stock defaults must not outperform the tuned ones."""
        async with seeded_backend(tmp_path / "tuned.db") as backend:
            tuned = score_recall(await run_suite(backend, _tuned_config()))
        async with seeded_backend(tmp_path / "stock.db") as backend:
            stock = score_recall(await run_suite(backend, _stock_config()))

        assert tuned.f1 >= stock.f1
        assert tuned.pollution <= stock.pollution


class TestRecallQuality:
    """Absolute floors, so "no worse than stock" cannot mean "both bad"."""

    async def test_expected_memories_are_recalled(self, tmp_path: Path) -> None:
        async with seeded_backend(tmp_path / "recall.db") as backend:
            score = score_recall(await run_suite(backend, _tuned_config()))

        assert score.recall == 1.0, f"missed an expected memory: {score.recall:.3f}"

    async def test_precision_holds(self, tmp_path: Path) -> None:
        """Over-retrieval passes a recall-only eval while degrading answers."""
        async with seeded_backend(tmp_path / "precision.db") as backend:
            score = score_recall(await run_suite(backend, _tuned_config()))

        assert score.precision >= 0.5, f"precision too low: {score.precision:.3f}"

    async def test_nothing_forbidden_is_ever_recalled(self, tmp_path: Path) -> None:
        async with seeded_backend(tmp_path / "pollution.db") as backend:
            score = score_recall(await run_suite(backend, _tuned_config()))

        assert score.pollution == 0.0, f"pollution rate {score.pollution:.3f}"


class TestCompoundingAcrossRestart:
    """The acceptance criterion the ephemeral store could never meet.

    A second run of a similar objective must benefit from the first
    across a process restart. The old shared backend was an in-process
    dict, so its recall was always zero after a restart; this proves the
    durable substrate answers the same questions after the connection is
    dropped and a fresh backend opens the same file.
    """

    async def test_recall_survives_a_restart(self, tmp_path: Path) -> None:
        db_path = tmp_path / "compounding.db"

        # Run one: seed the store, then let the connection close.
        async with seeded_backend(db_path) as backend:
            first = score_recall(await run_suite(backend, _tuned_config()))
        assert first.recall == 1.0

        # Run two: a brand-new backend over the same file, no re-seed.
        async with reopened_backend(db_path) as backend:
            second = score_recall(await run_suite(backend, _tuned_config()))

        assert second.recall == 1.0, (
            "memory did not survive the restart: a reopened backend "
            f"recalled {second.recall:.3f} of the expected memories"
        )
        assert second.pollution == 0.0


class TestAbstention:
    """Recalling nothing is a first-class correct answer."""

    async def test_unrelated_task_recalls_nothing(self, tmp_path: Path) -> None:
        async with seeded_backend(tmp_path / "abstain.db") as backend:
            score = score_recall(await run_suite(backend, _tuned_config()))

        assert score.abstention_accuracy == 1.0


class TestLayerIsolation:
    """One agent's memory must never surface for another."""

    async def test_other_agents_memory_never_surfaces(self, tmp_path: Path) -> None:
        case = next(c for c in CASES if c.name == "layer-isolation")

        async with seeded_backend(tmp_path / "isolation.db") as backend:
            results = await run_suite(backend, _tuned_config())

        assert not (case.forbidden & results[case.name])
