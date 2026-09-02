# module-kind: tests
"""A merge must read every child before it can write; a leaf reads nothing.

Sizing both off one flat ceiling is what starved the recording stopped on
2026-08-30: four merges made 167 tool calls between them, all of them
``shell_command``, and wrote zero files, because reading alone exhausted the
budget before a writing tool was ever loaded. ``session_limits_for`` is the
one owner of the arithmetic that fixes it.
"""

import pytest

from evals.recursion_depth.manifest import RecursionDepthManifest, Role
from evals.recursion_depth.session import session_limits_for
from synthorg.core.completion_enums import ReasoningEffort

pytestmark = pytest.mark.unit


def _manifest(**overrides: object) -> RecursionDepthManifest:
    """Build a manifest with distinguishable merge and review sizing.

    The two roles are deliberately given DIFFERENT bases and increments, so a
    test asserting they diverge cannot pass by accident if the sizing
    function silently read one role's fields for the other.

    Returns:
        The manifest.
    """
    payload: dict[str, object] = {
        "spec_dir": "evals/recursion_depth/spec/sqlcsv",
        "depths": [1],
        "repetitions": {1: 1},
        "arms": ["gated"],
        "executor": {
            "provider": "example-provider",
            "model_id": "example-capable-001",
            "capability": "capable",
            "family": "example-family-a",
        },
        "reviewer": {
            "provider": "example-provider",
            "model_id": "example-expert-001",
            "capability": "expert",
            "family": "example-family-a",
        },
        "independence": "same_family",
        "embedder": {"provider": "example-provider", "model_id": "example-embed-001"},
        "stagnation": {"strategy": "tool_repetition"},
        "compaction": {"fill_threshold_percent": 80.0, "summariser": None},
        "leaf_deep_claims": 4,
        "merge_attempts": 3,
        "unit_max_turns": 40,
        "planner_max_turns": 40,
        "unit_cost_ceiling": 2.0,
        "unit_token_ceiling": 1_000_000,
        "unit_token_per_claim": 0,
        "unit_token_cap": 4_000_000,
        "contract_stage": True,
        "contract_max_turns": 60,
        "contract_token_ceiling": 2_500_000,
        "merge_token_base": 1_500_000,
        "merge_token_per_piece": 500_000,
        "merge_token_cap": 8_000_000,
        "merge_max_turns_base": 40,
        "merge_max_turns_per_piece": 5,
        "merge_max_turns_cap": 120,
        "review_token_base": 300_000,
        "review_token_per_piece": 200_000,
        "review_token_cap": 2_000_000,
        "review_max_turns_base": 20,
        "review_max_turns_per_piece": 2,
        "review_max_turns_cap": 60,
        "max_sessions": 100,
        "projected_branching": 4,
        "expected_sessions_per_cell": {1: 20},
    }
    payload.update(overrides)
    return RecursionDepthManifest.model_validate(payload)


class TestALeafAndAPlanTakeNoFanInScaling:
    """Neither reads a sibling's tree, so neither has one to scale against."""

    def test_a_leaf_ignores_fan_in(self) -> None:
        manifest = _manifest()

        flat = session_limits_for(manifest, Role.LEAF, fan_in=0)
        wide = session_limits_for(manifest, Role.LEAF, fan_in=50)

        assert flat == wide
        assert flat.token_ceiling == manifest.unit_token_ceiling
        assert flat.max_turns == manifest.unit_max_turns

    def test_a_plan_ignores_fan_in(self) -> None:
        manifest = _manifest()

        flat = session_limits_for(manifest, Role.PLAN, fan_in=0)
        wide = session_limits_for(manifest, Role.PLAN, fan_in=50)

        assert flat == wide
        assert flat.token_ceiling == manifest.unit_token_ceiling
        assert flat.max_turns == manifest.planner_max_turns


class TestMergeAndReviewScaleWithFanIn:
    """The fix, stated as a test: reading more children buys more budget."""

    def test_an_eight_child_merge_exceeds_a_leafs_flat_budget(self) -> None:
        manifest = _manifest()

        leaf = session_limits_for(manifest, Role.LEAF, fan_in=0)
        merge = session_limits_for(manifest, Role.MERGE, fan_in=8)

        assert merge.token_ceiling > leaf.token_ceiling
        assert merge.token_ceiling == manifest.merge_token_base + 8 * 500_000

    def test_a_wider_fan_in_buys_a_larger_review_budget(self) -> None:
        manifest = _manifest()

        narrow = session_limits_for(manifest, Role.REVIEW, fan_in=1)
        wide = session_limits_for(manifest, Role.REVIEW, fan_in=6)

        assert wide.token_ceiling > narrow.token_ceiling
        assert wide.max_turns > narrow.max_turns

    def test_merge_and_review_are_sized_from_different_fields(self) -> None:
        """The load-bearing case: same fan-in, different roles, different
        answers, which a sizing function reading the wrong role's fields for
        the other would fail."""
        manifest = _manifest()

        merge = session_limits_for(manifest, Role.MERGE, fan_in=4)
        review = session_limits_for(manifest, Role.REVIEW, fan_in=4)

        assert merge.token_ceiling != review.token_ceiling
        assert merge.max_turns != review.max_turns

    def test_the_declared_cap_binds_a_wide_fan_in(self) -> None:
        manifest = _manifest()

        swamped = session_limits_for(manifest, Role.MERGE, fan_in=1000)

        assert swamped.token_ceiling == manifest.merge_token_cap
        assert swamped.max_turns == manifest.merge_max_turns_cap

    def test_the_review_cap_also_binds_a_wide_fan_in(self) -> None:
        manifest = _manifest()

        swamped = session_limits_for(manifest, Role.REVIEW, fan_in=1000)

        assert swamped.token_ceiling == manifest.review_token_cap
        assert swamped.max_turns == manifest.review_max_turns_cap


class TestSizingBoundsAreValidatedAtLoad:
    """A cap below its own base refuses every session of that role, so the
    matrix cannot record at all, which is worth catching before spend."""

    def test_a_merge_cap_below_its_base_is_refused(self) -> None:
        with pytest.raises(ValueError, match="merge_token_base"):
            _manifest(merge_token_base=9_000_000, merge_token_cap=8_000_000)

    def test_a_review_turn_cap_below_its_base_is_refused(self) -> None:
        with pytest.raises(ValueError, match="review_max_turns_base"):
            _manifest(review_max_turns_base=100, review_max_turns_cap=60)

    def test_a_leaf_cap_below_its_ceiling_is_refused(self) -> None:
        """The leaf pair is the one where the clamp reaches every session.

        A merge or review cap sits above a base that is scaled by fan-in, so
        an undersized cap clamps the largest of them. The leaf ceiling is the
        floor its per-claim scaling starts from, so a cap below it sizes every
        leaf in the sweep to the smaller number, including the ones claiming
        nothing at all.
        """
        with pytest.raises(ValueError, match="unit_token_ceiling"):
            _manifest(unit_token_ceiling=4_000_000, unit_token_cap=1_500_000)


class TestReasoningDepthIsAllocatedPerUnit:
    """The published fixed allocation loses to a per-task one, so this is one.

    The same function that sizes a unit's budget names the pool it builds
    on: a unit claiming little takes the shallow pool the matrix declared, a
    unit answerable for a subsystem keeps the executor's own depth.
    """

    def test_a_small_claim_takes_the_shallow_pool(self) -> None:
        manifest = _manifest(leaf_reasoning_effort="low", leaf_deep_claims=4)
        limits = session_limits_for(manifest, Role.LEAF, fan_in=0, claims=2)
        assert limits.reasoning_effort is ReasoningEffort.LOW

    def test_a_claim_at_the_threshold_keeps_the_executors_depth(self) -> None:
        manifest = _manifest(leaf_reasoning_effort="low", leaf_deep_claims=4)
        limits = session_limits_for(manifest, Role.LEAF, fan_in=0, claims=4)
        assert limits.reasoning_effort is None

    def test_no_shallow_pool_means_every_unit_keeps_the_executors_depth(
        self,
    ) -> None:
        manifest = _manifest(leaf_deep_claims=4)
        for claims in (0, 1, 4, 18):
            limits = session_limits_for(manifest, Role.LEAF, fan_in=0, claims=claims)
            assert limits.reasoning_effort is None

    @pytest.mark.parametrize(
        "role", [Role.PLAN, Role.CONTRACT, Role.MERGE, Role.REVIEW]
    )
    def test_every_other_role_keeps_the_executors_depth(self, role: Role) -> None:
        # The sandwich is only a sandwich if the outer phases stay deep.
        manifest = _manifest(leaf_reasoning_effort="low", leaf_deep_claims=1)
        assert session_limits_for(manifest, role, fan_in=3).reasoning_effort is None
