# module-kind: tests
"""The recording manifest and the complexity rollup that feeds promotion.

The manifest is the A/B's declaration of what gets measured, so its validation
carries an acceptance criterion directly: the comparison must cover every loop
that ships. A manifest that quietly omits one would publish a scoreboard that
looks complete while leaving a shipped loop unmeasured.
"""

from pathlib import Path
from typing import Final

import pytest

from evals.loop_ab.manifest import (
    DEFAULT_REPETITIONS,
    CapabilityEntry,
    LoopAbManifest,
    load_manifest,
)
from evals.loop_ab.rollup import complexity_for_estimate, rollup_by_complexity
from evals.loop_ab.rubric import DimensionScores, LoopCellScore
from synthorg.core.task_enums import Complexity
from synthorg.core.types import NotBlankStr
from synthorg.engine.loop_selector import registered_loop_types

pytestmark = pytest.mark.unit

_MANIFEST: Final[Path] = (
    Path(__file__).resolve().parents[3] / "evals" / "loop_ab" / "manifest.yaml"
)


def _tier(label: str) -> CapabilityEntry:
    """A capability bound to an explicit vendor-agnostic provider and model."""
    return CapabilityEntry(
        capability=NotBlankStr(label),
        provider=NotBlankStr("example-provider"),
        model_id=NotBlankStr(f"example-{label}-001"),
    )


def _manifest(**overrides: object) -> LoopAbManifest:
    """Build a manifest covering every registered loop unless overridden."""
    fields: dict[str, object] = {
        "brief_suite": "evals/loop_ab/briefs",
        "loops": registered_loop_types(),
        "capabilities": (_tier("expert"),),
    }
    fields.update(overrides)
    return LoopAbManifest.model_validate(fields)


def _score(
    loop_type: str, *, composite: float, disqualified: bool = False
) -> LoopCellScore:
    """A scored row with uniform dimensions; only the composite matters here."""
    return LoopCellScore(
        loop_type=NotBlankStr(loop_type),
        dimensions=DimensionScores(
            correctness=1.0, tokens=1.0, latency=1.0, turns=1.0, resilience=1.0
        ),
        composite=composite,
        disqualified=disqualified,
        disqualification_reason="below the gate" if disqualified else None,
    )


def test_the_committed_manifest_covers_every_registered_loop() -> None:
    """The shipped matrix must compare the whole field, not a subset."""
    manifest = load_manifest(_MANIFEST)

    assert set(manifest.loops) == set(registered_loop_types())


def test_the_committed_manifest_measures_several_capability_rungs() -> None:
    """Per-complexity advice needs evidence holding across capability rungs."""
    manifest = load_manifest(_MANIFEST)

    assert len(manifest.capabilities) > 1
    assert manifest.repetitions == DEFAULT_REPETITIONS


def test_a_manifest_omitting_a_registered_loop_is_refused() -> None:
    """Silently dropping a loop would understate the comparison."""
    # Dropping the first registered name rather than a hardcoded one: a filter
    # naming a loop that no longer ships silently becomes a no-op, and the
    # manifest it builds is complete, so the test stops testing anything.
    partial = registered_loop_types()[1:]
    assert partial != registered_loop_types()

    with pytest.raises(ValueError, match="omits registered loop"):
        _manifest(loops=partial)


def test_a_manifest_naming_an_unknown_loop_is_refused() -> None:
    """A typo must fail loudly rather than shrink the matrix."""
    with pytest.raises(ValueError, match="unregistered loop"):
        _manifest(loops=(*registered_loop_types(), "reakt"))


def test_duplicate_tier_labels_are_refused() -> None:
    """Two capabilities with one label would collide in the scoreboard."""
    with pytest.raises(ValueError, match="duplicate capability labels"):
        _manifest(capabilities=(_tier("expert"), _tier("expert")))


def test_duplicate_loop_names_are_refused() -> None:
    """A duplicated loop still covers the registry (the set collapses it) but
    multiplies planned_runs by len(loops), doubling that loop's real spend."""
    with pytest.raises(ValueError, match="duplicate loop names"):
        _manifest(loops=(*registered_loop_types(), "react"))


def test_the_planned_run_count_is_reported() -> None:
    """A maintainer must be able to see the size of the bill before paying it."""
    manifest = _manifest(capabilities=(_tier("expert"), _tier("basic")), repetitions=3)

    assert manifest.planned_runs == len(registered_loop_types()) * 2 * 3


@pytest.mark.parametrize(
    ("estimate", "expected"),
    [
        (1, Complexity.SIMPLE),
        (2, Complexity.MEDIUM),
        (3, Complexity.COMPLEX),
        (4, Complexity.EPIC),
        (5, Complexity.EPIC),
    ],
)
def test_every_brief_complexity_maps_to_a_routing_bucket(
    estimate: int, expected: Complexity
) -> None:
    """The setting routes on complexity, so each estimate needs a bucket."""
    assert complexity_for_estimate(estimate) == expected


def test_an_out_of_range_complexity_is_refused() -> None:
    """An unmappable estimate must fail rather than route somewhere arbitrary."""
    with pytest.raises(ValueError, match="no routing bucket"):
        complexity_for_estimate(9)


def test_a_loops_bucket_standing_averages_its_tiers() -> None:
    """One flattering capability must not carry a loop into a promotion."""
    buckets = rollup_by_complexity(
        (
            (1, (_score("react", composite=100.0),)),
            (1, (_score("react", composite=50.0),)),
        )
    )

    assert buckets[Complexity.SIMPLE][0].composite == pytest.approx(75.0)


def test_a_loop_disqualified_on_any_rung_is_disqualified_for_the_bucket() -> None:
    """Promoting a loop that fails on the basic rung would break that rung.

    ``loop_complexity_overrides`` routes on complexity alone and applies
    whatever model the agent is pinned to, so a loop is only promotable if it
    cleared the gate everywhere it was measured.
    """
    buckets = rollup_by_complexity(
        (
            (2, (_score("openhands", composite=95.0),)),
            (2, (_score("openhands", composite=20.0, disqualified=True),)),
        )
    )
    merged = buckets[Complexity.MEDIUM][0]

    assert merged.disqualified is True
    assert merged.disqualification_reason is not None
    assert "1 of 2" in merged.disqualification_reason


def test_briefs_of_different_complexity_land_in_different_buckets() -> None:
    """Per-complexity advice depends on the buckets staying separate."""
    buckets = rollup_by_complexity(
        (
            (1, (_score("react", composite=90.0),)),
            (3, (_score("openhands", composite=90.0),)),
        )
    )

    assert set(buckets) == {Complexity.SIMPLE, Complexity.COMPLEX}
