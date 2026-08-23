"""Shared fixtures for cross-deployment analytics tests."""

from datetime import UTC, datetime

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.chief_of_staff.models import ProposalOutcome
from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.models import (
    ConfigChange,
    ImprovementProposal,
    ProposalAltitude,
    ProposalRationale,
    RegressionVerdict,
    RollbackOperation,
    RollbackPlan,
    RolloutOutcome,
    RolloutResult,
    RolloutStrategyType,
)
from synthorg.meta.telemetry.config import CrossDeploymentAnalyticsConfig

_NOW = datetime(2026, 4, 16, 12, 0, 0, tzinfo=UTC)

BUILTIN_RULE_NAMES: frozenset[str] = frozenset(
    {
        "quality_declining",
        "success_rate_drop",
        "budget_overrun",
        "coordination_cost_ratio",
        "coordination_overhead",
        "straggler_bottleneck",
        "redundancy",
        "error_spike",
    }
)


@pytest.fixture
def analytics_config() -> CrossDeploymentAnalyticsConfig:
    """Enabled analytics config with test salt."""
    return CrossDeploymentAnalyticsConfig(
        enabled=True,
        collector_url=NotBlankStr("https://collector.test/api/meta/analytics"),
        deployment_id_salt=NotBlankStr("test-salt-value"),
        industry_tag=NotBlankStr("technology"),
        batch_size=10,
        flush_interval_seconds=5.0,
    )


@pytest.fixture
def self_improvement_config(
    analytics_config: CrossDeploymentAnalyticsConfig,
) -> SelfImprovementConfig:
    """SelfImprovementConfig with analytics enabled."""
    return SelfImprovementConfig(
        enabled=True,
        config_tuning_enabled=True,
        architecture_proposals_enabled=True,
        cross_deployment_analytics=analytics_config,
    )


@pytest.fixture
def sample_proposal() -> ImprovementProposal:
    """Sample approved proposal for testing."""
    return ImprovementProposal(
        altitude=ProposalAltitude.CONFIG_TUNING,
        title=NotBlankStr("Reduce coordination overhead threshold"),
        description=NotBlankStr("Lower the coordination overhead threshold"),
        rationale=ProposalRationale(
            signal_summary=NotBlankStr("Coordination overhead is 42%"),
            pattern_detected=NotBlankStr("coordination_overhead_high"),
            expected_impact=NotBlankStr("Reduce overhead to 35%"),
            confidence_reasoning=NotBlankStr("Historical data supports this"),
        ),
        config_changes=(
            ConfigChange(
                path=NotBlankStr("coordination.overhead_threshold"),
                old_value="0.35",
                new_value="0.30",
                description=NotBlankStr("Lower threshold"),
            ),
        ),
        rollback_plan=RollbackPlan(
            operations=(
                RollbackOperation(
                    operation_type=NotBlankStr("revert_config"),
                    target=NotBlankStr("coordination.overhead_threshold"),
                    previous_value="0.35",
                    description=NotBlankStr("Revert threshold to 0.35"),
                ),
            ),
            validation_check=NotBlankStr(
                "Verify coordination overhead threshold is 0.35",
            ),
        ),
        confidence=0.72,
        source_rule=NotBlankStr("coordination_overhead"),
        rollout_strategy=RolloutStrategyType.BEFORE_AFTER,
    )


@pytest.fixture
def sample_outcome(sample_proposal: ImprovementProposal) -> ProposalOutcome:
    """Sample proposal outcome for testing."""
    return ProposalOutcome(
        proposal_id=sample_proposal.id,
        title=NotBlankStr("Reduce coordination overhead threshold"),
        altitude=ProposalAltitude.CONFIG_TUNING,
        source_rule=NotBlankStr("coordination_overhead"),
        decision="approved",
        confidence_at_decision=0.72,
        decided_at=_NOW,
        decided_by=NotBlankStr("admin-user"),
        decision_reason=NotBlankStr("Looks good, proceed with rollout"),
    )


@pytest.fixture
def sample_rollout_result(
    sample_proposal: ImprovementProposal,
) -> RolloutResult:
    """Sample rollout result for testing."""
    return RolloutResult(
        proposal_id=sample_proposal.id,
        outcome=RolloutOutcome.SUCCESS,
        regression_verdict=RegressionVerdict.NO_REGRESSION,
        observation_hours_elapsed=48.0,
        details=NotBlankStr("Observation window completed without regression"),
        completed_at=_NOW,
    )
