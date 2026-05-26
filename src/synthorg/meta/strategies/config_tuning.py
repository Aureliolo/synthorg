"""Config tuning improvement strategy.

Generates proposals to tune existing company configuration values
based on detected signal patterns. The current implementation
synthesizes template-based proposals from rule-triggered signal
context; once the service orchestrator owns providers, the same
entry point will use LLM analysis to author the proposal bodies.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import JsonValue  # noqa: TC002

from synthorg.meta.models import (
    ConfigChange,
    ImprovementProposal,
    OrgSignalSnapshot,
    ProposalAltitude,
    ProposalRationale,
    RollbackOperation,
    RollbackPlan,
    RuleMatch,
)
from synthorg.observability import get_logger
from synthorg.observability.events.meta import META_PROPOSAL_GENERATED

if TYPE_CHECKING:
    from synthorg.meta.config import SelfImprovementConfig

logger = get_logger(__name__)


class ConfigTuningStrategy:
    """Generates config tuning proposals from signal patterns.

    The current implementation generates template-based proposals
    from rule-triggered signal context without making any LLM
    calls; for each triggered rule it picks a hand-authored
    proposal body keyed on the rule name. Once the service
    orchestrator owns providers, the same entry point will build
    a prompt with signal context and ask the LLM to author the
    proposal bodies instead of selecting from the template map.

    Args:
        config: Self-improvement configuration.
    """

    def __init__(self, *, config: SelfImprovementConfig) -> None:
        self._config = config

    @property
    def altitude(self) -> ProposalAltitude:
        """This strategy produces config tuning proposals.

        Returns:
            ``ProposalAltitude`` instance.
        """
        return ProposalAltitude.CONFIG_TUNING

    async def propose(
        self,
        *,
        snapshot: OrgSignalSnapshot,
        triggered_rules: tuple[RuleMatch, ...],
    ) -> tuple[ImprovementProposal, ...]:
        """Generate config tuning proposals from triggered rules.

        Args:
            snapshot: Current org-wide signal snapshot.
            triggered_rules: Rules that fired targeting config tuning.

        Returns:
            Tuple of config tuning proposals.
        """
        proposals: list[ImprovementProposal] = []

        for rule_match in triggered_rules:
            if self.altitude not in rule_match.suggested_altitudes:
                continue

            proposal = self._build_proposal(rule_match, snapshot)
            if proposal is not None:
                proposals.append(proposal)
                logger.info(
                    META_PROPOSAL_GENERATED,
                    altitude="config_tuning",
                    rule=rule_match.rule_name,
                    title=proposal.title,
                )

        return tuple(proposals)

    def _build_proposal(
        self,
        rule_match: RuleMatch,
        snapshot: OrgSignalSnapshot,
    ) -> ImprovementProposal | None:
        """Build a proposal from a rule match.

        Args:
            rule_match: The triggered rule match.
            snapshot: Current org signal snapshot.

        Returns:
            A config tuning proposal, or None if no change is warranted.
        """
        ctx = rule_match.signal_context
        builders = {
            "quality_declining": lambda: self._propose_quality_fix(ctx, snapshot),
            "success_rate_drop": lambda: self._propose_success_rate_fix(ctx, snapshot),
            "budget_overrun": lambda: self._propose_budget_fix(ctx, snapshot),
            "coordination_cost_ratio": lambda: self._propose_coordination_cost_fix(ctx),
            "coordination_overhead": lambda: self._propose_overhead_fix(ctx),
            "scaling_failure": lambda: self._propose_scaling_fix(ctx),
        }
        builder = builders.get(rule_match.rule_name)
        return builder() if builder else None

    def _propose_quality_fix(
        self,
        ctx: dict[str, JsonValue],
        snapshot: OrgSignalSnapshot,
    ) -> ImprovementProposal:
        """Return propose quality fix."""
        _ = snapshot
        return ImprovementProposal(
            id=uuid4(),
            altitude=ProposalAltitude.CONFIG_TUNING,
            title="Improve task quality scoring thresholds",
            description=(
                "Quality is declining. Adjust quality scoring "
                "weights and task complexity routing to improve "
                "output quality."
            ),
            rationale=ProposalRationale(
                signal_summary=(
                    f"Avg quality: {ctx.get('avg_quality', 'N/A')}, "
                    f"agents: {ctx.get('agent_count', 'N/A')}"
                ),
                pattern_detected="Quality below threshold",
                expected_impact="Stabilize quality scores",
                confidence_reasoning=(
                    "Direct correlation between quality threshold and scoring outcomes"
                ),
            ),
            config_changes=(
                ConfigChange(
                    path="performance.quality_weight_ci",
                    old_value=0.5,
                    new_value=0.6,
                    description="Increase CI quality weight",
                ),
            ),
            rollback_plan=RollbackPlan(
                operations=(
                    RollbackOperation(
                        operation_type="revert_config",
                        target="performance.quality_weight_ci",
                        previous_value=0.5,
                        description="Revert CI quality weight",
                    ),
                ),
                validation_check="quality_weight_ci equals 0.5",
            ),
            confidence=0.7,
            source_rule="quality_declining",
        )

    def _propose_success_rate_fix(
        self,
        ctx: dict[str, JsonValue],
        snapshot: OrgSignalSnapshot,
    ) -> ImprovementProposal:
        """Return propose success rate fix."""
        _ = snapshot
        return ImprovementProposal(
            id=uuid4(),
            altitude=ProposalAltitude.CONFIG_TUNING,
            title="Adjust task routing for higher success rate",
            description=(
                "Success rate dropped. Consider adjusting task "
                "complexity routing to better match agent capabilities."
            ),
            rationale=ProposalRationale(
                signal_summary=(f"Success rate: {ctx.get('avg_success_rate', 'N/A')}"),
                pattern_detected="Success rate below threshold",
                expected_impact="Improve task completion rates",
                confidence_reasoning=(
                    "Task-capability mismatch is a common cause of low success rates"
                ),
            ),
            config_changes=(
                ConfigChange(
                    path="task_engine.auto_loop.budget_downgrade_threshold",
                    old_value=0.85,
                    new_value=0.75,
                    description="Lower budget downgrade threshold",
                ),
            ),
            rollback_plan=RollbackPlan(
                operations=(
                    RollbackOperation(
                        operation_type="revert_config",
                        target="task_engine.auto_loop.budget_downgrade_threshold",
                        previous_value=0.85,
                        description="Revert downgrade threshold",
                    ),
                ),
                validation_check="budget_downgrade_threshold equals 0.85",
            ),
            confidence=0.65,
            source_rule="success_rate_drop",
        )

    def _propose_budget_fix(
        self,
        ctx: dict[str, JsonValue],
        snapshot: OrgSignalSnapshot,
    ) -> ImprovementProposal:
        """Return propose budget fix."""
        _ = snapshot
        return ImprovementProposal(
            id=uuid4(),
            altitude=ProposalAltitude.CONFIG_TUNING,
            title="Reduce spend to extend budget runway",
            description=(
                f"Budget exhaustion in "
                f"{ctx.get('days_until_exhausted', '?')} days. "
                f"Reduce model tier defaults to extend runway."
            ),
            rationale=ProposalRationale(
                signal_summary=(
                    f"Days until exhausted: "
                    f"{ctx.get('days_until_exhausted', 'N/A')}, "
                    f"total spend: {ctx.get('total_spend', 'N/A')}"
                ),
                pattern_detected="Budget exhaustion imminent",
                expected_impact="Extend budget runway",
                confidence_reasoning=(
                    "Model tier downgrade directly reduces per-task cost"
                ),
            ),
            config_changes=(
                ConfigChange(
                    path="routing.default_tier",
                    old_value="large",
                    new_value="medium",
                    description=("Downgrade default model tier to medium"),
                ),
            ),
            rollback_plan=RollbackPlan(
                operations=(
                    RollbackOperation(
                        operation_type="revert_config",
                        target="routing.default_tier",
                        previous_value="large",
                        description="Revert default tier to large",
                    ),
                ),
                validation_check="default_tier equals large",
            ),
            confidence=0.8,
            source_rule="budget_overrun",
        )

    def _propose_coordination_cost_fix(
        self,
        ctx: dict[str, JsonValue],
    ) -> ImprovementProposal:
        """Return propose coordination cost fix."""
        return ImprovementProposal(
            id=uuid4(),
            altitude=ProposalAltitude.CONFIG_TUNING,
            title="Reduce coordination overhead spend",
            description=(
                f"Coordination cost ratio at "
                f"{ctx.get('coordination_ratio', 0.0):.1%}. "
                f"Reduce max parallel tasks to lower overhead."
            ),
            rationale=ProposalRationale(
                signal_summary=(
                    f"Coordination ratio: {ctx.get('coordination_ratio', 'N/A')}"
                ),
                pattern_detected="High coordination cost ratio",
                expected_impact=("Lower coordination spend as fraction of total"),
                confidence_reasoning=(
                    "Fewer parallel tasks reduce inter-agent messages"
                ),
            ),
            config_changes=(
                ConfigChange(
                    path="coordination.max_parallel_tasks",
                    old_value=5,
                    new_value=3,
                    description="Reduce max parallel tasks",
                ),
            ),
            rollback_plan=RollbackPlan(
                operations=(
                    RollbackOperation(
                        operation_type="revert_config",
                        target="coordination.max_parallel_tasks",
                        previous_value=5,
                        description="Revert max parallel tasks to 5",
                    ),
                ),
                validation_check="max_parallel_tasks equals 5",
            ),
            confidence=0.75,
            source_rule="coordination_cost_ratio",
        )

    def _propose_overhead_fix(
        self,
        ctx: dict[str, JsonValue],
    ) -> ImprovementProposal:
        """Return propose overhead fix."""
        return ImprovementProposal(
            id=uuid4(),
            altitude=ProposalAltitude.CONFIG_TUNING,
            title="Reduce coordination overhead percentage",
            description=(
                f"Coordination overhead at "
                f"{ctx.get('overhead_pct', 0.0)}%. "
                f"Switch more tasks to single-agent execution."
            ),
            rationale=ProposalRationale(
                signal_summary=(f"Overhead: {ctx.get('overhead_pct', 'N/A')}%"),
                pattern_detected="High coordination overhead",
                expected_impact="Lower overhead percentage",
                confidence_reasoning=("SAS execution eliminates coordination overhead"),
            ),
            config_changes=(
                ConfigChange(
                    path="coordination.auto_select.sas_success_rate_threshold",
                    old_value=0.7,
                    new_value=0.8,
                    description=(
                        "Raise SAS threshold to prefer single-agent execution"
                    ),
                ),
            ),
            rollback_plan=RollbackPlan(
                operations=(
                    RollbackOperation(
                        operation_type="revert_config",
                        target="coordination.auto_select.sas_success_rate_threshold",
                        previous_value=0.7,
                        description="Revert SAS threshold to 0.7",
                    ),
                ),
                validation_check="sas_success_rate_threshold equals 0.7",
            ),
            confidence=0.7,
            source_rule="coordination_overhead",
        )

    def _propose_scaling_fix(
        self,
        ctx: dict[str, JsonValue],
    ) -> ImprovementProposal:
        """Return propose scaling fix."""
        return ImprovementProposal(
            id=uuid4(),
            altitude=ProposalAltitude.CONFIG_TUNING,
            title="Adjust scaling thresholds",
            description=(
                f"Scaling failure rate at "
                f"{ctx.get('failure_rate', 0):.1%}. "
                f"Widen scaling cooldown to reduce churn."
            ),
            rationale=ProposalRationale(
                signal_summary=(
                    f"Failure rate: "
                    f"{ctx.get('failure_rate', 'N/A')}, "
                    f"decisions: "
                    f"{ctx.get('total_decisions', 'N/A')}"
                ),
                pattern_detected="High scaling failure rate",
                expected_impact="Reduce failed scaling actions",
                confidence_reasoning=(
                    "Longer cooldown prevents rapid contradictory scaling decisions"
                ),
            ),
            config_changes=(
                ConfigChange(
                    path="hr.scaling.cooldown_seconds",
                    old_value=3600,
                    new_value=7200,
                    description="Double scaling cooldown period",
                ),
            ),
            rollback_plan=RollbackPlan(
                operations=(
                    RollbackOperation(
                        operation_type="revert_config",
                        target="hr.scaling.cooldown_seconds",
                        previous_value=3600,
                        description="Revert cooldown to 1 hour",
                    ),
                ),
                validation_check="cooldown_seconds equals 3600",
            ),
            confidence=0.6,
            source_rule="scaling_failure",
        )
