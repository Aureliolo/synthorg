"""Architecture proposal improvement strategy.

Generates proposals for structural changes to the organization:
new roles, department restructuring, workflow modifications.
"""

from typing import Final
from uuid import uuid4

from pydantic import JsonValue

from synthorg.meta.config import SelfImprovementConfig
from synthorg.meta.models import (
    ArchitectureChange,
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

logger = get_logger(__name__)

# Per-rule proposal confidences.
_CONFIDENCE_REVIEW_PIPELINE: Final[float] = 0.55
_CONFIDENCE_SPECIALIST_ROLE: Final[float] = 0.5


class ArchitectureProposalStrategy:
    """Generates architecture proposals from signal patterns.

    Proposes structural changes like new roles, department
    restructuring, or workflow modifications when coordination
    or scaling patterns suggest the current structure is suboptimal.

    Args:
        config: Self-improvement configuration.
    """

    def __init__(self, *, config: SelfImprovementConfig) -> None:
        self._config = config

    @property
    def altitude(self) -> ProposalAltitude:
        """This strategy produces architecture proposals.

        Returns:
            ``ProposalAltitude`` instance.
        """
        return ProposalAltitude.ARCHITECTURE

    async def propose(
        self,
        *,
        snapshot: OrgSignalSnapshot,
        triggered_rules: tuple[RuleMatch, ...],
    ) -> tuple[ImprovementProposal, ...]:
        """Generate architecture proposals from triggered rules.

        Args:
            snapshot: Current org-wide signal snapshot.
            triggered_rules: Rules targeting architecture changes.

        Returns:
            Tuple of architecture proposals.
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
                    altitude="architecture",
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

        Returns:
            The ``ImprovementProposal`` value when present, ``None`` otherwise.
        """
        _ = snapshot
        name = rule_match.rule_name

        if name == "coordination_cost_ratio":
            return self._propose_team_restructure(rule_match.signal_context)
        if name == "straggler_bottleneck":
            return self._propose_specialist_role(rule_match.signal_context)
        return None

    def _propose_team_restructure(
        self,
        ctx: dict[str, JsonValue],
    ) -> ImprovementProposal:
        """Return propose team restructure."""
        return ImprovementProposal(
            id=uuid4(),
            altitude=ProposalAltitude.ARCHITECTURE,
            title="Restructure teams to reduce coordination",
            description=(
                "High coordination costs suggest teams are too "
                "interconnected. Propose splitting into more "
                "autonomous sub-teams."
            ),
            rationale=ProposalRationale(
                signal_summary=(
                    f"Coordination ratio: {ctx.get('coordination_ratio', 'N/A')}"
                ),
                pattern_detected=("High coordination cost ratio"),
                expected_impact=("Reduce inter-team dependencies"),
                confidence_reasoning=("Team autonomy reduces coordination overhead"),
            ),
            architecture_changes=(
                ArchitectureChange(
                    operation="modify_workflow",
                    target_name="default_review_pipeline",
                    payload={
                        "change": "reduce_cross_team_reviews",
                    },
                    description=("Reduce cross-team review requirements"),
                ),
            ),
            rollback_plan=RollbackPlan(
                operations=(
                    RollbackOperation(
                        operation_type="revert_architecture",
                        target="workflow:default_review_pipeline",
                        description=("Revert review pipeline to original"),
                    ),
                ),
                validation_check=("Review pipeline matches original config"),
            ),
            confidence=_CONFIDENCE_REVIEW_PIPELINE,
            source_rule="coordination_cost_ratio",
        )

    def _propose_specialist_role(
        self,
        ctx: dict[str, JsonValue],
    ) -> ImprovementProposal:
        """Return propose specialist role."""
        return ImprovementProposal(
            id=uuid4(),
            altitude=ProposalAltitude.ARCHITECTURE,
            title="Add specialist role to reduce bottleneck",
            description=(
                f"Straggler gap ratio at "
                f"{ctx.get('straggler_gap_ratio', '?')}. "
                f"Add a specialist role for the bottleneck area."
            ),
            rationale=ProposalRationale(
                signal_summary=(
                    f"Straggler ratio: {ctx.get('straggler_gap_ratio', 'N/A')}"
                ),
                pattern_detected="Persistent straggler bottleneck",
                expected_impact=("Reduce completion time variance"),
                confidence_reasoning=(
                    "Specialist roles address skill gaps that cause stragglers"
                ),
            ),
            architecture_changes=(
                ArchitectureChange(
                    operation="create_role",
                    target_name="bottleneck_specialist",
                    payload={
                        "department": "engineering",
                        "skills": ["performance_optimization"],
                    },
                    description="Create specialist role",
                ),
            ),
            rollback_plan=RollbackPlan(
                operations=(
                    RollbackOperation(
                        operation_type="revert_architecture",
                        target="role:bottleneck_specialist",
                        description="Remove specialist role",
                    ),
                ),
                validation_check=("bottleneck_specialist role does not exist"),
            ),
            confidence=_CONFIDENCE_SPECIALIST_ROLE,
            source_rule="straggler_bottleneck",
        )
