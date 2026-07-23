# module-kind: tests
"""The matrix runner, driven offline against the real loops.

The loops here are the real registered implementations, not doubles. Only the
LLM is scripted, which is what makes the discrimination assertion meaningful:
given the *same* scripted responses, react, plan_execute and hybrid genuinely
differ in how many turns they take to reach an answer, so the scoreboard
separates them on measurements they actually produced rather than on behaviour
the test invented.

The OpenHands leg is exercised in its unwired state, pinning the reporting rule
that matters most for an honest scoreboard: a loop that cannot be measured is
reported with its reason, never dropped and never scored as a zero.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

import pytest

from evals.loader.briefs import load_brief_suite
from evals.loop_ab.manifest import LoopAbManifest, TierEntry
from evals.loop_ab.models import Provenance
from evals.loop_ab.runner import (
    LoopAbDeps,
    _CellCoordinates,
    _run_cell,
    run_matrix,
)
from evals.models.brief import Brief
from synthorg.core.completion_enums import FinishReason
from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_selector import registered_loop_types
from synthorg.providers.models import CompletionResponse, TokenUsage
from tests._shared.scripted_provider import ScriptedProvider

pytestmark = pytest.mark.integration

_SUITE: Final = Path(__file__).resolve().parents[3] / "evals" / "loop_ab" / "briefs"


def _provenance() -> Provenance:
    """Fixed provenance so assertions do not depend on the live repository."""
    return Provenance(
        generated_at=datetime(2026, 7, 22, 12, 0, tzinfo=UTC),
        git_commit=NotBlankStr("b" * 40),
        git_dirty=False,
        manifest_sha256=NotBlankStr("sha256:" + "f" * 64),
        brief_suite_version=NotBlankStr("sha256:beef"),
    )


def _tier(label: str = "large", model_id: str = "example-large-001") -> TierEntry:
    """A tier bound to an explicit vendor-agnostic provider and model."""
    return TierEntry(
        tier=NotBlankStr(label),
        provider=NotBlankStr("example-provider"),
        model_id=NotBlankStr(model_id),
    )


def _manifest(
    *, loops: tuple[str, ...] | None = None, repetitions: int = 1
) -> LoopAbManifest:
    """A manifest over every registered loop unless narrowed."""
    return LoopAbManifest(
        brief_suite=NotBlankStr("evals/loop_ab/briefs"),
        loops=tuple(NotBlankStr(name) for name in (loops or registered_loop_types())),
        tiers=(_tier(),),
        repetitions=repetitions,
    )


#: A valid two-step plan. The planning loops parse this and go on to execute
#: both steps; the reactive loop treats the same text as its answer and stops.
#: That asymmetry is the whole point: one scripted response, genuinely different
#: measured behaviour, because the loops themselves differ.
_PLAN_JSON: Final[str] = json.dumps(
    {
        "steps": [
            {
                "step_number": 1,
                "description": "Read the specification",
                "expected_outcome": "The required behaviour is understood",
            },
            {
                "step_number": 2,
                "description": "Write the module",
                "expected_outcome": "The module exists and imports",
            },
        ]
    }
)


def _scripted_deps() -> LoopAbDeps:
    """Deps whose only fake is the LLM; no tools, no OpenHands runtime."""

    def _build_provider(tier: TierEntry) -> ScriptedProvider:
        return ScriptedProvider(
            response=CompletionResponse(
                content=_PLAN_JSON,
                finish_reason=FinishReason.STOP,
                usage=TokenUsage(input_tokens=120, output_tokens=40, cost=0.002),
                model=tier.model_id,
            )
        )

    return LoopAbDeps(
        build_provider=_build_provider,
        build_tool_registry=lambda _work_dir: None,
        openhands_loop_deps=None,
    )


def _simple_brief() -> tuple[Brief, ...]:
    """Just the simple brief, to keep the matrix small in tests."""
    return tuple(b for b in load_brief_suite(_SUITE) if b.brief_id == "loop-ab-simple")


async def test_every_registered_loop_gets_a_row(tmp_path: Path) -> None:
    """The comparison must cover the whole field, discovered not hardcoded."""
    scoreboard = await run_matrix(
        manifest=_manifest(),
        briefs=_simple_brief(),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=_scripted_deps(),
        provenance=_provenance(),
    )

    assert {row.loop_type for row in scoreboard.rows} == set(registered_loop_types())


async def test_an_unwired_loop_is_reported_not_dropped(tmp_path: Path) -> None:
    """An unavailable loop must be visible in the artifact, with its reason."""
    scoreboard = await run_matrix(
        manifest=_manifest(),
        briefs=_simple_brief(),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=_scripted_deps(),
        provenance=_provenance(),
    )

    unavailable = {row.loop_type for row in scoreboard.unavailable_rows}
    assert unavailable == {"openhands"}
    reason = scoreboard.unavailable_rows[0].unavailable_reason
    assert reason is not None
    assert "OpenHands" in reason


async def test_the_native_loops_are_measured_and_scored(tmp_path: Path) -> None:
    """Every loop that could run carries a real measurement and a score."""
    scoreboard = await run_matrix(
        manifest=_manifest(),
        briefs=_simple_brief(),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=_scripted_deps(),
        provenance=_provenance(),
    )

    measured = {row.loop_type for row in scoreboard.measured_rows}
    assert measured == {"react", "plan_execute", "hybrid"}
    for row in scoreboard.measured_rows:
        assert row.score is not None
        assert row.measurement is not None
        assert row.measurement.repetitions == 1


async def test_the_scoreboard_separates_loops_on_measured_behaviour(
    tmp_path: Path,
) -> None:
    """The discriminating property the whole harness exists to provide.

    The scripted LLM is identical for every loop, so any difference in turns or
    tokens comes from the loops themselves: a planning loop spends a turn
    planning before it executes, a reactive one does not. If the scoreboard
    cannot separate them here it cannot separate them on real work either.
    """
    scoreboard = await run_matrix(
        manifest=_manifest(),
        briefs=_simple_brief(),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=_scripted_deps(),
        provenance=_provenance(),
    )

    turns = {
        row.loop_type: row.measurement.aggregate.total_turns
        for row in scoreboard.measured_rows
        if row.measurement is not None
    }
    composites = {
        row.loop_type: row.score.composite
        for row in scoreboard.measured_rows
        if row.score is not None
    }

    assert len(set(turns.values())) > 1, (
        f"the loops must differ in measured turns to be comparable, got {turns}"
    )
    # A whole-turn gap, not merely any difference: the planning loops execute
    # both scripted steps while the reactive loop stops at the plan, so the
    # separation is a real measured margin rather than rounding noise.
    assert max(turns.values()) - min(turns.values()) >= 1, turns
    assert len(set(composites.values())) > 1, (
        f"differing measurements must produce differing scores, got {composites}"
    )


async def test_a_cheaper_loop_outscores_a_more_expensive_one(
    tmp_path: Path,
) -> None:
    """With correctness tied, the efficiency dimensions decide the ranking."""
    scoreboard = await run_matrix(
        manifest=_manifest(),
        briefs=_simple_brief(),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=_scripted_deps(),
        provenance=_provenance(),
    )
    rows = {
        row.loop_type: row
        for row in scoreboard.measured_rows
        if row.score is not None and row.measurement is not None
    }
    cheapest = min(
        rows.values(),
        key=lambda r: r.measurement.aggregate.total_tokens,  # type: ignore[union-attr]
    )
    dearest = max(
        rows.values(),
        key=lambda r: r.measurement.aggregate.total_tokens,  # type: ignore[union-attr]
    )

    # Unconditional: if only one loop were measured, cheapest is dearest and
    # this is a trivial ``x >= x``; a real token spread must never leave the
    # ranking silently unchecked, which a guarded assertion would allow.
    assert cheapest.score.composite >= dearest.score.composite  # type: ignore[union-attr]


async def test_each_repetition_starts_from_a_freshly_seeded_workspace(
    tmp_path: Path,
) -> None:
    """Repetitions must be independent, or the median measures run order."""
    scoreboard = await run_matrix(
        manifest=_manifest(repetitions=3),
        briefs=_simple_brief(),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=_scripted_deps(),
        provenance=_provenance(),
    )
    react = next(row for row in scoreboard.measured_rows if row.loop_type == "react")

    assert react.measurement is not None
    assert react.measurement.repetitions == 3
    # Identical scripted input across independent repetitions must produce an
    # identical result; any spread would mean state leaked between runs.
    spread = react.measurement.correctness_spread
    assert spread.minimum == spread.maximum


async def test_the_scoreboard_carries_its_promotion_recommendation(
    tmp_path: Path,
) -> None:
    """The artifact is only actionable if it ends in settings values."""
    scoreboard = await run_matrix(
        manifest=_manifest(),
        briefs=_simple_brief(),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=_scripted_deps(),
        provenance=_provenance(),
    )

    # Every scripted loop runs tool-less, writes nothing, and grades below the
    # correctness gate, so none clears it and the recommendation degrades to
    # "promote nothing": a real assertion, not a tautological not-None check on a
    # required field.
    assert scoreboard.recommendation.default_loop_type is None
    assert scoreboard.provenance.git_commit == "b" * 40


async def test_measured_rows_carry_their_ledger_spend(tmp_path: Path) -> None:
    """Cost is read back from the run's own ledger, not re-derived from tokens."""
    scoreboard = await run_matrix(
        manifest=_manifest(),
        briefs=_simple_brief(),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=_scripted_deps(),
        provenance=_provenance(),
    )

    for row in scoreboard.measured_rows:
        assert row.spend, f"{row.loop_type} recorded no spend"
        for item in row.spend:
            assert item.provider == "example-provider"
            assert item.model_id == "example-large-001"
            assert item.cost > 0.0


async def test_a_failed_later_repetition_keeps_the_earlier_spend(
    tmp_path: Path,
) -> None:
    """A cell that dies on a later repetition still reports what it already paid.

    Drive a two-repetition cell whose provider works for the first repetition
    (booking real spend against the ledger) and then becomes unavailable for the
    second. The emitted unavailable row must carry the first repetition's spend
    forward, because that money was charged and the scoreboard's dollar total
    must not silently lose it just because a later repetition failed.
    """
    calls = {"count": 0}

    def _build_provider(tier: TierEntry) -> ScriptedProvider:
        calls["count"] += 1
        if calls["count"] >= 2:
            msg = "provider unavailable on the second repetition"
            raise RuntimeError(msg)
        return ScriptedProvider(
            response=CompletionResponse(
                content=_PLAN_JSON,
                finish_reason=FinishReason.STOP,
                usage=TokenUsage(input_tokens=120, output_tokens=40, cost=0.002),
                model=tier.model_id,
            )
        )

    deps = LoopAbDeps(
        build_provider=_build_provider,
        build_tool_registry=lambda _work_dir: None,
        openhands_loop_deps=None,
    )
    coord = _CellCoordinates(loop_type="react", tier=_tier(), brief=_simple_brief()[0])

    row = await _run_cell(
        coord=coord,
        manifest=_manifest(repetitions=2),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=deps,
    )

    assert row.unavailable_reason is not None
    assert row.measurement is None
    assert row.spend, "the failed cell dropped the first repetition's spend"
    assert sum(item.cost for item in row.spend) > 0.0


async def test_a_tool_less_run_disqualifies_every_measured_loop(
    tmp_path: Path,
) -> None:
    """With no tools the loops write nothing, so the correctness gate fires."""
    scoreboard = await run_matrix(
        manifest=_manifest(),
        briefs=_simple_brief(),
        suite_root=_SUITE,
        work_root=tmp_path / "work",
        deps=_scripted_deps(),
        provenance=_provenance(),
    )

    measured = scoreboard.measured_rows
    assert measured
    for row in measured:
        assert row.score is not None
        assert row.score.disqualified is True
