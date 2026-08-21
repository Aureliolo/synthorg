# module-kind: tests
"""The entry point: plan mode spends nothing, and staging narrows honestly."""

from pathlib import Path

import pytest
from scripts.record_recursion_depth import describe_plan, main, narrow

from evals.recursion_depth.manifest import Independence, load_manifest
from evals.recursion_depth.tree import SpecBrief, load_spec_brief

pytestmark = pytest.mark.unit

_MANIFEST = (
    Path(__file__).resolve().parents[3] / "evals" / "recursion_depth" / "manifest.yaml"
)


def _spec() -> SpecBrief:
    """Load the committed specification.

    Returns:
        The brief.
    """
    manifest = load_manifest(_MANIFEST)
    return load_spec_brief(Path(manifest.spec_dir))


class TestPlanMode:
    """The default path boots nothing and states the bill."""

    def test_it_exits_clean_without_recording(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        # No --record, so no gateway, no port, no container and no spend.
        assert main([]) == 0
        assert "Recursion-depth recording plan" in capsys.readouterr().out

    def test_it_states_the_session_floor_and_the_ceiling(self) -> None:
        # A depth sweep's session count is a product of branching factors the
        # manifest cannot predict, so the figure is a floor and the ceiling is
        # what actually bounds the spend.
        manifest = load_manifest(_MANIFEST)

        plan = describe_plan(manifest, _spec())

        assert "at least" in plan
        assert str(manifest.max_sessions) in plan

    def test_it_states_the_equal_attempt_budget(self) -> None:
        # Repair only in the gated arm would let it win by spending more, so
        # the operator reading the plan is told the budget is shared.
        plan = describe_plan(load_manifest(_MANIFEST), _spec())

        assert "the SAME in both arms" in plan

    def test_the_shipped_manifest_needs_no_independence_caveat(self) -> None:
        plan = describe_plan(load_manifest(_MANIFEST), _spec())

        assert "CAVEAT" not in plan

    def test_a_weakened_judge_puts_its_caveat_on_the_plan(self) -> None:
        # The operator is told before spending, not after reading the chart.
        shipped = load_manifest(_MANIFEST)
        weakened = shipped.model_copy(
            update={
                "reviewer": shipped.reviewer.model_copy(
                    update={"family": shipped.executor.family}
                ),
                "independence": Independence.SAME_FAMILY,
            }
        )

        plan = describe_plan(weakened, _spec())

        assert "CAVEAT" in plan
        assert "share a model family" in plan


class TestStaging:
    """A large bill is paid in instalments, and never for a cap nobody asked."""

    def test_depths_narrows_to_the_named_caps(self) -> None:
        narrowed = narrow(load_manifest(_MANIFEST), "1,2")

        assert narrowed.depths == (1, 2)

    def test_no_depths_keeps_the_manifest(self) -> None:
        manifest = load_manifest(_MANIFEST)

        assert narrow(manifest, None).depths == manifest.depths

    def test_a_cap_the_manifest_does_not_carry_is_refused(self) -> None:
        # Silently recording nothing for it would leave a gap in the curve that
        # reads as a measured zero.
        with pytest.raises(ValueError, match="does not carry"):
            narrow(load_manifest(_MANIFEST), "1,4,9")
