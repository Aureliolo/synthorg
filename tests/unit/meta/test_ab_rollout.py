"""Unit tests for A/B test rollout strategy."""

from uuid import UUID, uuid4

import pytest

from synthorg.meta.config import ABTestConfig
from synthorg.meta.models import (
    ApplyResult,
    ConfigChange,
    ImprovementProposal,
    OrgSignalSnapshot,
    ProposalAltitude,
    ProposalRationale,
    RegressionResult,
    RegressionThresholds,
    RegressionVerdict,
    RollbackOperation,
    RollbackPlan,
    RolloutOutcome,
    RolloutStrategyType,
)
from synthorg.meta.rollout.ab_comparator import ABTestComparator
from synthorg.meta.rollout.ab_models import (
    ABTestComparison,
    ABTestGroup,
    ABTestVerdict,
    GroupAssignment,
    GroupMetrics,
)
from synthorg.meta.rollout.ab_test import ABTestRollout
from tests.unit.meta.rollout._ramp import ramp as _ramp

pytestmark = pytest.mark.unit


# -- Helpers ---------------------------------------------------------------


# assign_groups hashes ``agent_id:proposal_id`` per agent, so the control/
# treatment split is a function of the proposal id. A random id leaves both
# group sizes binomial -- ~0.2% of ids send all 10 roster agents to one group,
# emptying the other and short-circuiting execute() to INCONCLUSIVE before the
# applier runs. Pinning the id makes the split a constant (a verified, balanced
# 5/5 over agent-0..agent-9 at control_fraction=0.5) so outcome-asserting tests
# are deterministic instead of flaking ~1-in-500 runs.
_FIXED_PROPOSAL_ID: UUID = UUID(int=1)


def _proposal(
    strategy: RolloutStrategyType = RolloutStrategyType.AB_TEST,
    proposal_id: UUID = _FIXED_PROPOSAL_ID,
) -> ImprovementProposal:
    return ImprovementProposal(
        id=proposal_id,
        altitude=ProposalAltitude.CONFIG_TUNING,
        title="test ab proposal",
        description="test ab",
        rationale=ProposalRationale(
            signal_summary="test",
            pattern_detected="test",
            expected_impact="test",
            confidence_reasoning="test",
        ),
        config_changes=(
            ConfigChange(
                path="a.b",
                old_value=1,
                new_value=2,
                description="d",
            ),
        ),
        rollback_plan=RollbackPlan(
            operations=(
                RollbackOperation(
                    operation_type="revert",
                    target="a.b",
                    previous_value=1,
                    description="revert a.b",
                ),
            ),
            validation_check="a.b equals 1",
        ),
        confidence=0.8,
        rollout_strategy=strategy,
    )


def _group_metrics(  # noqa: PLR0913
    group: ABTestGroup,
    *,
    quality: float = 7.5,
    success: float = 0.85,
    spend: float = 100.0,
    observations: int = 20,
    agents: int = 20,
) -> GroupMetrics:
    """Build sample-backed GroupMetrics matching the legacy aggregates.

    Expands scalar ``quality``/``success``/``spend`` into deterministic
    aligned sample tuples. The sample mean equals the requested
    ``quality``/``success`` and the sum of ``spend_samples`` equals
    the requested ``spend`` (both exactly when ``observations`` is
    even). Samples ramp symmetrically so Welch's t-test sees non-zero
    variance.
    """
    per_agent_spend = spend / observations if observations > 0 else 0.0
    return GroupMetrics(
        group=group,
        agent_count=agents,
        quality_samples=_ramp(quality, observations, 0.2),
        success_samples=_ramp(success, observations, 0.02),
        spend_samples=_ramp(per_agent_spend, observations, 0.0),
    )


def _thresholds() -> RegressionThresholds:
    return RegressionThresholds()


class _StaticRoster:
    """OrgRoster yielding a fixed agent tuple."""

    def __init__(self, count: int = 10) -> None:
        from synthorg.core.types import NotBlankStr

        self._agents = tuple(NotBlankStr(f"agent-{i}") for i in range(count))

    async def list_agent_ids(self) -> tuple[str, ...]:
        return self._agents


def _ab_rollout(
    *,
    control_fraction: float = 0.5,
    min_agents_per_group: int = 1,
    min_observations_per_group: int = 10,
    improvement_threshold: float = 0.15,
    roster_size: int = 10,
) -> ABTestRollout:
    """Construct an ABTestRollout wired with FakeClock + static roster."""
    from tests._shared.fake_clock import FakeClock

    return ABTestRollout(
        control_fraction=control_fraction,
        min_agents_per_group=min_agents_per_group,
        min_observations_per_group=min_observations_per_group,
        improvement_threshold=improvement_threshold,
        clock=FakeClock(),
        roster=_StaticRoster(roster_size),
        check_interval_hours=4.0,
    )


class _StubApplier:
    @property
    def altitude(self) -> ProposalAltitude:
        return ProposalAltitude.CONFIG_TUNING

    async def apply(self, proposal: ImprovementProposal) -> ApplyResult:
        return ApplyResult(
            success=True,
            changes_applied=len(proposal.config_changes),
        )

    async def dry_run(self, proposal: ImprovementProposal) -> ApplyResult:
        _ = proposal
        return ApplyResult(success=True, changes_applied=0)


class _FailApplier:
    @property
    def altitude(self) -> ProposalAltitude:
        return ProposalAltitude.CONFIG_TUNING

    async def apply(self, proposal: ImprovementProposal) -> ApplyResult:
        _ = proposal
        return ApplyResult(
            success=False,
            error_message="apply failed",
            changes_applied=0,
        )

    async def dry_run(self, proposal: ImprovementProposal) -> ApplyResult:
        _ = proposal
        return ApplyResult(success=True, changes_applied=0)


class _StubDetector:
    @property
    def name(self) -> str:
        return "stub"

    async def check(
        self,
        *,
        baseline: OrgSignalSnapshot,
        current: OrgSignalSnapshot,
        thresholds: RegressionThresholds,
    ) -> RegressionResult:
        _ = baseline, current, thresholds
        return RegressionResult(
            verdict=RegressionVerdict.NO_REGRESSION,
        )


# -- ABTestGroup & ABTestVerdict enums -------------------------------------


class TestABTestEnums:
    """Verify enum values exist."""

    def test_group_values(self) -> None:
        assert ABTestGroup.CONTROL.value == "control"
        assert ABTestGroup.TREATMENT.value == "treatment"

    def test_verdict_values(self) -> None:
        assert ABTestVerdict.TREATMENT_WINS.value == "treatment_wins"
        assert ABTestVerdict.CONTROL_WINS.value == "control_wins"
        assert ABTestVerdict.INCONCLUSIVE.value == "inconclusive"
        assert ABTestVerdict.TREATMENT_REGRESSED.value == "treatment_regressed"


# -- RolloutStrategyType.AB_TEST ------------------------------------------


class TestRolloutStrategyTypeABTest:
    """Verify AB_TEST enum added to RolloutStrategyType."""

    def test_ab_test_value(self) -> None:
        assert RolloutStrategyType.AB_TEST.value == "ab_test"

    def test_ab_test_in_members(self) -> None:
        assert "AB_TEST" in RolloutStrategyType.__members__


# -- RolloutOutcome.INCONCLUSIVE ------------------------------------------


class TestRolloutOutcomeInconclusive:
    """Verify INCONCLUSIVE enum added to RolloutOutcome."""

    def test_inconclusive_value(self) -> None:
        assert RolloutOutcome.INCONCLUSIVE.value == "inconclusive"


# -- GroupAssignment model -------------------------------------------------


class TestGroupAssignment:
    """GroupAssignment model validation."""

    def test_valid_assignment(self) -> None:
        pid = uuid4()
        assignment = GroupAssignment(
            proposal_id=pid,
            control_agent_ids=("agent-1", "agent-2"),
            treatment_agent_ids=("agent-3", "agent-4"),
            control_fraction=0.5,
        )
        assert assignment.proposal_id == pid
        assert len(assignment.control_agent_ids) == 2
        assert len(assignment.treatment_agent_ids) == 2

    def test_empty_groups_allowed(self) -> None:
        assignment = GroupAssignment(
            proposal_id=uuid4(),
            control_fraction=0.5,
        )
        assert assignment.control_agent_ids == ()
        assert assignment.treatment_agent_ids == ()

    def test_fraction_must_be_exclusive(self) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            GroupAssignment(
                proposal_id=uuid4(),
                control_fraction=0.0,
            )
        with pytest.raises(ValueError, match="less than 1"):
            GroupAssignment(
                proposal_id=uuid4(),
                control_fraction=1.0,
            )


# -- GroupMetrics model ----------------------------------------------------


class TestGroupMetrics:
    """GroupMetrics model validation."""

    def test_valid_metrics(self) -> None:
        m = _group_metrics(ABTestGroup.CONTROL)
        assert m.group == ABTestGroup.CONTROL
        assert m.agent_count == 20
        assert m.observation_count == 20

    def test_quality_bounds(self) -> None:
        # Inject samples directly: the _ramp helper rejects negative
        # centers so we can't use the _group_metrics fixture to force
        # out-of-range values. Using direct tuples isolates the
        # GroupMetrics validator from the ramp input guard.
        with pytest.raises(ValueError, match=r"quality_samples must be in"):
            GroupMetrics(
                group=ABTestGroup.CONTROL,
                agent_count=3,
                quality_samples=(-1.0, 0.0, 1.0),
                success_samples=(0.5, 0.5, 0.5),
                spend_samples=(1.0, 1.0, 1.0),
            )
        with pytest.raises(ValueError, match=r"quality_samples must be in"):
            GroupMetrics(
                group=ABTestGroup.CONTROL,
                agent_count=3,
                quality_samples=(11.0, 5.0, 5.0),
                success_samples=(0.5, 0.5, 0.5),
                spend_samples=(1.0, 1.0, 1.0),
            )

    def test_success_rate_bounds(self) -> None:
        with pytest.raises(ValueError, match=r"success_samples must be in"):
            GroupMetrics(
                group=ABTestGroup.CONTROL,
                agent_count=3,
                quality_samples=(5.0, 5.0, 5.0),
                success_samples=(-0.1, 0.5, 0.5),
                spend_samples=(1.0, 1.0, 1.0),
            )
        with pytest.raises(ValueError, match=r"success_samples must be in"):
            GroupMetrics(
                group=ABTestGroup.CONTROL,
                agent_count=3,
                quality_samples=(5.0, 5.0, 5.0),
                success_samples=(1.1, 0.5, 0.5),
                spend_samples=(1.0, 1.0, 1.0),
            )

    def test_observations_exceeding_agents_rejected(self) -> None:
        with pytest.raises(ValueError, match="observation_count"):
            GroupMetrics(
                group=ABTestGroup.CONTROL,
                agent_count=0,
                quality_samples=(5.0, 5.0),
                success_samples=(0.5, 0.5),
                spend_samples=(1.0, 1.0),
            )


# -- ABTestComparison model -----------------------------------------------


class TestABTestComparison:
    """ABTestComparison model validation."""

    def test_treatment_wins_requires_stats(self) -> None:
        with pytest.raises(ValueError, match="effect_size and p_value"):
            ABTestComparison(
                verdict=ABTestVerdict.TREATMENT_WINS,
                control_metrics=_group_metrics(ABTestGroup.CONTROL),
                treatment_metrics=_group_metrics(ABTestGroup.TREATMENT),
            )

    def test_treatment_wins_with_stats(self) -> None:
        c = ABTestComparison(
            verdict=ABTestVerdict.TREATMENT_WINS,
            control_metrics=_group_metrics(ABTestGroup.CONTROL),
            treatment_metrics=_group_metrics(
                ABTestGroup.TREATMENT,
                quality=8.5,
            ),
            effect_size=0.6,
            p_value=0.02,
        )
        assert c.verdict == ABTestVerdict.TREATMENT_WINS
        assert c.effect_size == 0.6

    def test_treatment_regressed_requires_metrics(self) -> None:
        with pytest.raises(ValueError, match="regressed_metrics"):
            ABTestComparison(
                verdict=ABTestVerdict.TREATMENT_REGRESSED,
                control_metrics=_group_metrics(ABTestGroup.CONTROL),
                treatment_metrics=_group_metrics(
                    ABTestGroup.TREATMENT,
                    quality=5.0,
                ),
            )

    def test_inconclusive_no_requirements(self) -> None:
        c = ABTestComparison(
            verdict=ABTestVerdict.INCONCLUSIVE,
            control_metrics=_group_metrics(ABTestGroup.CONTROL),
            treatment_metrics=_group_metrics(ABTestGroup.TREATMENT),
        )
        assert c.verdict == ABTestVerdict.INCONCLUSIVE

    def test_control_wins_requires_stats(self) -> None:
        with pytest.raises(ValueError, match="winner verdicts"):
            ABTestComparison(
                verdict=ABTestVerdict.CONTROL_WINS,
                control_metrics=_group_metrics(ABTestGroup.CONTROL),
                treatment_metrics=_group_metrics(ABTestGroup.TREATMENT),
            )

    def test_control_wins_with_stats(self) -> None:
        c = ABTestComparison(
            verdict=ABTestVerdict.CONTROL_WINS,
            control_metrics=_group_metrics(ABTestGroup.CONTROL),
            treatment_metrics=_group_metrics(ABTestGroup.TREATMENT),
            effect_size=0.3,
            p_value=0.04,
        )
        assert c.verdict == ABTestVerdict.CONTROL_WINS


# -- ABTestConfig ----------------------------------------------------------


class TestABTestConfig:
    """ABTestConfig validation."""

    def test_defaults(self) -> None:
        cfg = ABTestConfig()
        assert cfg.control_fraction == 0.5
        assert cfg.min_agents_per_group == 5
        assert cfg.min_observations_per_group == 10

    def test_custom_values(self) -> None:
        cfg = ABTestConfig(
            control_fraction=0.3,
            min_agents_per_group=3,
            min_observations_per_group=5,
        )
        assert cfg.control_fraction == 0.3
        assert cfg.min_agents_per_group == 3

    def test_fraction_bounds(self) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            ABTestConfig(control_fraction=0.0)
        with pytest.raises(ValueError, match="less than 1"):
            ABTestConfig(control_fraction=1.0)
        with pytest.raises(ValueError, match="greater than 0"):
            ABTestConfig(control_fraction=-0.1)

    def test_min_agents_floor(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal"):
            ABTestConfig(min_agents_per_group=1)

    def test_min_observations_floor(self) -> None:
        with pytest.raises(ValueError, match="greater than or equal"):
            ABTestConfig(min_observations_per_group=1)

    def test_improvement_threshold_default(self) -> None:
        cfg = ABTestConfig()
        assert cfg.improvement_threshold == 0.15

    def test_improvement_threshold_bounds(self) -> None:
        with pytest.raises(ValueError, match="greater than 0"):
            ABTestConfig(improvement_threshold=0.0)
        with pytest.raises(ValueError, match="less than or equal"):
            ABTestConfig(improvement_threshold=1.5)


# -- ABTestRollout.assign_groups -------------------------------------------


class TestAssignGroups:
    """Deterministic group assignment."""

    def test_deterministic(self) -> None:
        agent_ids = tuple(f"agent-{i}" for i in range(20))
        pid = uuid4()
        a1 = ABTestRollout.assign_groups(agent_ids, pid, 0.5)
        a2 = ABTestRollout.assign_groups(agent_ids, pid, 0.5)
        assert a1.control_agent_ids == a2.control_agent_ids
        assert a1.treatment_agent_ids == a2.treatment_agent_ids

    def test_different_proposals_different_splits(self) -> None:
        agent_ids = tuple(f"agent-{i}" for i in range(100))
        a1 = ABTestRollout.assign_groups(agent_ids, uuid4(), 0.5)
        a2 = ABTestRollout.assign_groups(agent_ids, uuid4(), 0.5)
        # Very unlikely to be identical with 100 agents.
        assert a1.control_agent_ids != a2.control_agent_ids

    def test_approximate_split(self) -> None:
        agent_ids = tuple(f"agent-{i}" for i in range(1000))
        pid = uuid4()
        assignment = ABTestRollout.assign_groups(agent_ids, pid, 0.5)
        control_count = len(assignment.control_agent_ids)
        # Within 10% of expected 500.
        assert 400 <= control_count <= 600

    def test_empty_agents(self) -> None:
        assignment = ABTestRollout.assign_groups((), uuid4(), 0.5)
        assert assignment.control_agent_ids == ()
        assert assignment.treatment_agent_ids == ()

    def test_single_agent(self) -> None:
        assignment = ABTestRollout.assign_groups(("solo",), uuid4(), 0.5)
        total = len(assignment.control_agent_ids) + len(assignment.treatment_agent_ids)
        assert total == 1

    def test_all_agents_accounted_for(self) -> None:
        agent_ids = tuple(f"agent-{i}" for i in range(50))
        pid = uuid4()
        assignment = ABTestRollout.assign_groups(agent_ids, pid, 0.5)
        all_assigned = set(assignment.control_agent_ids) | set(
            assignment.treatment_agent_ids,
        )
        assert all_assigned == set(agent_ids)

    def test_no_overlap(self) -> None:
        agent_ids = tuple(f"agent-{i}" for i in range(50))
        pid = uuid4()
        assignment = ABTestRollout.assign_groups(agent_ids, pid, 0.5)
        overlap = set(assignment.control_agent_ids) & set(
            assignment.treatment_agent_ids,
        )
        assert overlap == set()


# -- ABTestComparator ------------------------------------------------------


class TestABTestComparator:
    """Group metric comparison logic."""

    async def test_zero_observations_both_groups(self) -> None:
        comparator = ABTestComparator(min_observations=10)
        result = await comparator.compare(
            control=_group_metrics(
                ABTestGroup.CONTROL,
                observations=0,
            ),
            treatment=_group_metrics(
                ABTestGroup.TREATMENT,
                observations=0,
            ),
            thresholds=_thresholds(),
        )
        assert result.verdict == ABTestVerdict.INCONCLUSIVE

    async def test_multiple_regressions(self) -> None:
        comparator = ABTestComparator(min_observations=5)
        result = await comparator.compare(
            control=_group_metrics(
                ABTestGroup.CONTROL,
                quality=8.0,
                success=0.90,
                spend=100.0,
                observations=20,
            ),
            treatment=_group_metrics(
                ABTestGroup.TREATMENT,
                quality=5.0,
                success=0.65,
                spend=150.0,
                observations=20,
            ),
            thresholds=RegressionThresholds(
                quality_drop=0.10,
                success_rate_drop=0.10,
                cost_increase=0.20,
            ),
        )
        assert result.verdict == ABTestVerdict.TREATMENT_REGRESSED
        assert len(result.regressed_metrics) == 3
        assert "quality" in result.regressed_metrics
        assert "success_rate" in result.regressed_metrics
        assert "cost" in result.regressed_metrics

    async def test_insufficient_observations(self) -> None:
        comparator = ABTestComparator(min_observations=10)
        result = await comparator.compare(
            control=_group_metrics(
                ABTestGroup.CONTROL,
                observations=5,
            ),
            treatment=_group_metrics(
                ABTestGroup.TREATMENT,
                observations=5,
            ),
            thresholds=_thresholds(),
        )
        assert result.verdict == ABTestVerdict.INCONCLUSIVE

    @pytest.mark.parametrize(
        ("ctrl", "treat", "thresholds", "metric_key"),
        [
            (
                _group_metrics(ABTestGroup.CONTROL, quality=8.0, observations=20),
                _group_metrics(ABTestGroup.TREATMENT, quality=5.0, observations=20),
                RegressionThresholds(quality_drop=0.10),
                "quality",
            ),
            (
                _group_metrics(ABTestGroup.CONTROL, success=0.90, observations=20),
                _group_metrics(ABTestGroup.TREATMENT, success=0.70, observations=20),
                RegressionThresholds(success_rate_drop=0.10),
                "success_rate",
            ),
            (
                _group_metrics(ABTestGroup.CONTROL, spend=100.0, observations=20),
                _group_metrics(ABTestGroup.TREATMENT, spend=130.0, observations=20),
                RegressionThresholds(cost_increase=0.20),
                "cost",
            ),
        ],
    )
    async def test_treatment_regressed_single_metric(
        self,
        ctrl: GroupMetrics,
        treat: GroupMetrics,
        thresholds: RegressionThresholds,
        metric_key: str,
    ) -> None:
        comparator = ABTestComparator(min_observations=5)
        result = await comparator.compare(
            control=ctrl,
            treatment=treat,
            thresholds=thresholds,
        )
        assert result.verdict == ABTestVerdict.TREATMENT_REGRESSED
        assert metric_key in result.regressed_metrics

    async def test_treatment_wins(self) -> None:
        comparator = ABTestComparator(min_observations=5)
        result = await comparator.compare(
            control=_group_metrics(
                ABTestGroup.CONTROL,
                quality=7.0,
                success=0.80,
                observations=20,
            ),
            treatment=_group_metrics(
                ABTestGroup.TREATMENT,
                quality=9.0,
                success=0.95,
                observations=20,
            ),
            thresholds=_thresholds(),
        )
        assert result.verdict == ABTestVerdict.TREATMENT_WINS
        assert result.effect_size is not None
        assert result.p_value is not None

    async def test_zero_control_equals_zero_treatment(self) -> None:
        comparator = ABTestComparator(min_observations=5)
        result = await comparator.compare(
            control=_group_metrics(
                ABTestGroup.CONTROL,
                quality=0.0,
                success=0.0,
                observations=20,
            ),
            treatment=_group_metrics(
                ABTestGroup.TREATMENT,
                quality=0.0,
                success=0.0,
                observations=20,
            ),
            thresholds=_thresholds(),
        )
        # Zero variance in both arms -> Welch unavailable -> inconclusive.
        assert result.verdict == ABTestVerdict.INCONCLUSIVE

    async def test_near_zero_baseline_small_treatment_difference(self) -> None:
        comparator = ABTestComparator(
            min_observations=5,
            improvement_threshold=0.15,
        )
        result = await comparator.compare(
            control=_group_metrics(
                ABTestGroup.CONTROL,
                quality=0.5,
                success=0.5,
                observations=20,
            ),
            treatment=_group_metrics(
                ABTestGroup.TREATMENT,
                quality=0.52,
                success=0.51,
                observations=20,
            ),
            thresholds=_thresholds(),
        )
        # 0.02/0.5 = 4% improvement, below 15% threshold -> inconclusive.
        assert result.verdict == ABTestVerdict.INCONCLUSIVE

    async def test_no_significant_difference(self) -> None:
        comparator = ABTestComparator(min_observations=5)
        result = await comparator.compare(
            control=_group_metrics(
                ABTestGroup.CONTROL,
                quality=7.5,
                success=0.85,
                observations=20,
            ),
            treatment=_group_metrics(
                ABTestGroup.TREATMENT,
                quality=7.6,
                success=0.86,
                observations=20,
            ),
            thresholds=_thresholds(),
        )
        assert result.verdict == ABTestVerdict.INCONCLUSIVE

    async def test_zero_control_quality_safe(self) -> None:
        comparator = ABTestComparator(min_observations=5)
        result = await comparator.compare(
            control=_group_metrics(
                ABTestGroup.CONTROL,
                quality=0.0,
                observations=20,
            ),
            treatment=_group_metrics(
                ABTestGroup.TREATMENT,
                quality=5.0,
                observations=20,
            ),
            thresholds=_thresholds(),
        )
        # Zero baseline skips ratio check; effect is 0.0 (below
        # threshold), so verdict must be INCONCLUSIVE.
        assert result.verdict == ABTestVerdict.INCONCLUSIVE


# -- ABTestRollout ---------------------------------------------------------


class TestABTestRollout:
    """A/B test rollout strategy."""

    def test_name(self) -> None:
        rollout = ABTestRollout()
        assert rollout.name == "ab_test"

    def test_invalid_control_fraction_zero(self) -> None:
        with pytest.raises(ValueError, match="control_fraction"):
            ABTestRollout(control_fraction=0.0)

    def test_invalid_control_fraction_one(self) -> None:
        with pytest.raises(ValueError, match="control_fraction"):
            ABTestRollout(control_fraction=1.0)

    def test_invalid_control_fraction_negative(self) -> None:
        with pytest.raises(ValueError, match="control_fraction"):
            ABTestRollout(control_fraction=-0.5)

    def test_invalid_min_agents_zero(self) -> None:
        with pytest.raises(ValueError, match="min_agents_per_group"):
            ABTestRollout(min_agents_per_group=0)

    async def test_successful_execute(self) -> None:
        rollout = _ab_rollout()
        result = await rollout.execute(
            proposal=_proposal(),
            applier=_StubApplier(),
            detector=_StubDetector(),
        )
        assert result.outcome in (
            RolloutOutcome.SUCCESS,
            RolloutOutcome.INCONCLUSIVE,
        )

    async def test_failed_apply(self) -> None:
        rollout = _ab_rollout()
        result = await rollout.execute(
            proposal=_proposal(),
            applier=_FailApplier(),
            detector=_StubDetector(),
        )
        assert result.outcome == RolloutOutcome.FAILED

    async def test_too_few_agents_inconclusive(self) -> None:
        rollout = _ab_rollout(min_agents_per_group=100)
        result = await rollout.execute(
            proposal=_proposal(),
            applier=_StubApplier(),
            detector=_StubDetector(),
        )
        assert result.outcome == RolloutOutcome.INCONCLUSIVE

    async def test_configurable_fraction(self) -> None:
        rollout = ABTestRollout(control_fraction=0.3)
        assert rollout._control_fraction == 0.3


# -- GroupAssignment disjoint validator ------------------------------------


class TestGroupAssignmentDisjoint:
    """Disjoint partition validation."""

    def test_overlapping_groups_rejected(self) -> None:
        with pytest.raises(ValueError, match="disjoint"):
            GroupAssignment(
                proposal_id=uuid4(),
                control_agent_ids=("agent-1", "agent-2"),
                treatment_agent_ids=("agent-2", "agent-3"),
                control_fraction=0.5,
            )

    def test_disjoint_groups_accepted(self) -> None:
        assignment = GroupAssignment(
            proposal_id=uuid4(),
            control_agent_ids=("agent-1", "agent-2"),
            treatment_agent_ids=("agent-3", "agent-4"),
            control_fraction=0.5,
        )
        assert len(assignment.control_agent_ids) == 2


# -- ABTestComparison statistic bounds ------------------------------------


class TestABTestComparisonBounds:
    """Statistical field bounds validation."""

    def test_p_value_out_of_range_rejected(self) -> None:
        with pytest.raises(ValueError, match="p_value"):
            ABTestComparison(
                verdict=ABTestVerdict.TREATMENT_WINS,
                control_metrics=_group_metrics(ABTestGroup.CONTROL),
                treatment_metrics=_group_metrics(ABTestGroup.TREATMENT),
                effect_size=0.5,
                p_value=1.5,
            )

    def test_negative_effect_size_rejected(self) -> None:
        with pytest.raises(ValueError, match="effect_size"):
            ABTestComparison(
                verdict=ABTestVerdict.INCONCLUSIVE,
                control_metrics=_group_metrics(ABTestGroup.CONTROL),
                treatment_metrics=_group_metrics(ABTestGroup.TREATMENT),
                effect_size=-0.1,
            )
