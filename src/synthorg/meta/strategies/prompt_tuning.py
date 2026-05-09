"""Prompt tuning improvement strategy.

Generates proposals for org-wide prompt policies that get injected
as constitutional principles. Supports three evolution modes:
org-wide (default), override, and advisory.
"""

from typing import TYPE_CHECKING
from uuid import uuid4

from pydantic import JsonValue  # noqa: TC002

from synthorg.meta.models import (
    EvolutionMode,
    ImprovementProposal,
    OrgSignalSnapshot,
    PromptChange,
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


class PromptTuningStrategy:
    """Generates prompt tuning proposals from signal patterns.

    Proposes org-wide prompt policies as constitutional principles
    that get injected into agent prompts. The evolution mode
    determines how these interact with per-agent evolution:
    ORG_WIDE applies to all agents, OVERRIDE replaces per-agent
    settings, ADVISORY suggests but lets per-agent evolution decide.

    Args:
        config: Self-improvement configuration.
    """

    def __init__(self, *, config: SelfImprovementConfig) -> None:
        self._config = config

    @property
    def altitude(self) -> ProposalAltitude:
        """This strategy produces prompt tuning proposals."""
        return ProposalAltitude.PROMPT_TUNING

    async def propose(
        self,
        *,
        snapshot: OrgSignalSnapshot,
        triggered_rules: tuple[RuleMatch, ...],
    ) -> tuple[ImprovementProposal, ...]:
        """Generate prompt tuning proposals from triggered rules.

        Args:
            snapshot: Current org-wide signal snapshot.
            triggered_rules: Rules targeting prompt tuning.

        Returns:
            Tuple of prompt tuning proposals.
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
                    altitude="prompt_tuning",
                    rule=rule_match.rule_name,
                    title=proposal.title,
                )

        return tuple(proposals)

    def _build_proposal(
        self,
        rule_match: RuleMatch,
        snapshot: OrgSignalSnapshot,
    ) -> ImprovementProposal | None:
        """Build a proposal from a rule match."""
        _ = snapshot
        name = rule_match.rule_name
        mode = self._config.prompt_tuning.default_evolution_mode

        if name == "quality_declining":
            return self._propose_quality_principle(mode)
        if name == "error_spike":
            return self._propose_error_awareness(rule_match.signal_context, mode)
        return None

    def _propose_quality_principle(
        self,
        mode: EvolutionMode,
    ) -> ImprovementProposal:
        return ImprovementProposal(
            id=uuid4(),
            altitude=ProposalAltitude.PROMPT_TUNING,
            title="Add quality focus principle",
            description=(
                "Quality is declining. Inject a constitutional "
                "principle reminding all agents to prioritize "
                "output quality over speed."
            ),
            rationale=ProposalRationale(
                signal_summary="Quality scores declining org-wide",
                pattern_detected="Sustained quality decline",
                expected_impact=("Agents prioritize quality in task execution"),
                confidence_reasoning=(
                    "Constitutional principles influence agent behavior at prompt level"
                ),
            ),
            prompt_changes=(
                PromptChange(
                    principle_text=(
                        "Prioritize output quality over speed. "
                        "Verify your work before submitting. "
                        "If unsure, ask for clarification."
                    ),
                    target_scope="all",
                    evolution_mode=mode,
                    description="Quality focus principle for all agents",
                ),
            ),
            rollback_plan=RollbackPlan(
                operations=(
                    RollbackOperation(
                        operation_type="remove_principle",
                        target="quality_focus_principle",
                        description="Remove quality focus principle",
                    ),
                ),
                validation_check=("Quality focus principle is not in any agent prompt"),
            ),
            confidence=0.6,
            source_rule="quality_declining",
        )

    def _propose_error_awareness(
        self,
        ctx: dict[str, JsonValue],
        mode: EvolutionMode,
    ) -> ImprovementProposal:
        return ImprovementProposal(
            id=uuid4(),
            altitude=ProposalAltitude.PROMPT_TUNING,
            title="Add error awareness principle",
            description=(
                f"Error findings ({ctx.get('total_findings', '?')}) "
                f"exceed threshold. Inject a principle for "
                f"careful error checking."
            ),
            rationale=ProposalRationale(
                signal_summary=(
                    f"Total findings: "
                    f"{ctx.get('total_findings', 'N/A')}, "
                    f"most severe: "
                    f"{ctx.get('most_severe', 'N/A')}"
                ),
                pattern_detected="Error spike detected",
                expected_impact=("Agents check for common errors before submitting"),
                confidence_reasoning=("Error-checking prompts reduce repeat mistakes"),
            ),
            prompt_changes=(
                PromptChange(
                    principle_text=(
                        "Before completing any task, review your "
                        "output for common errors: contradictions, "
                        "incomplete reasoning, and coordination "
                        "mismatches with delegated work."
                    ),
                    target_scope="all",
                    evolution_mode=mode,
                    description="Error awareness principle",
                ),
            ),
            rollback_plan=RollbackPlan(
                operations=(
                    RollbackOperation(
                        operation_type="remove_principle",
                        target="error_awareness_principle",
                        description=("Remove error awareness principle"),
                    ),
                ),
                validation_check=(
                    "Error awareness principle is not in any agent prompt"
                ),
            ),
            confidence=0.55,
            source_rule="error_spike",
        )
