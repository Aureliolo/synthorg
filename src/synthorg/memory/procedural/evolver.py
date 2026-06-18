"""Autonomous skill evolver service.

Aggregates trajectory patterns across agents and proposes org-scope
skills for human review. **Proposal-only**: the evolver has no write
access to org memory. All proposals are emitted as ``ApprovalItem``
entries for human approval.
"""

import copy
import json
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from typing import Final
from uuid import uuid4

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.memory.procedural.evolver_config import EvolverConfig
from synthorg.memory.procedural.evolver_report import EvolverReport
from synthorg.memory.procedural.models import (
    ProceduralMemoryProposal,
    ProceduralMemoryScope,
)
from synthorg.memory.procedural.supersession import (
    SupersessionResult,
    SupersessionVerdict,
    evaluate_supersession,
)
from synthorg.memory.procedural.trajectory_aggregator import (
    AggregatedTrajectory,
    TrajectoryAggregator,
    TrajectoryPattern,
)
from synthorg.memory.protocol import MemoryBackend
from synthorg.observability import get_logger
from synthorg.observability.events.skill_evolver import (
    SKILL_EVOLVER_CONFLICT_DETECTED,
    SKILL_EVOLVER_CYCLE_COMPLETE,
    SKILL_EVOLVER_CYCLE_FAILED,
    SKILL_EVOLVER_CYCLE_START,
    SKILL_EVOLVER_DISABLED,
    SKILL_EVOLVER_PROPOSAL_EMITTED,
)

logger = get_logger(__name__)

# Systemic-failure pattern confidence: baseline plus failure-rate weight.
_CONFIDENCE_BASELINE: Final[float] = 0.5
_CONFIDENCE_FAILURE_WEIGHT: Final[float] = 0.5


class AutonomousSkillEvolver:
    """Aggregates trajectory patterns and proposes org-scope skills.

    **Proposal-only**: emits ``ApprovalItem`` entries for human review.
    Has NO write access to org memory -- structurally enforced by
    config (``requires_human_approval: Literal[True]``) and by not
    calling ``org_memory.store()`` anywhere in this service.

    Args:
        memory_backend: Per-agent memory backend (read-only for
            trajectory collection).
        trajectory_aggregator: Pattern identification service.
        proposer: LLM-based proposal generator.
        config: Evolver configuration with safety rails.
        existing_org_proposals: Existing org-scope proposals for
            supersession checks (read-only).
    """

    __slots__ = (
        "_aggregator",
        "_config",
        "_existing_org_proposals",
        "_memory_backend",
        "_proposer",
    )

    def __init__(
        self,
        *,
        memory_backend: MemoryBackend,
        trajectory_aggregator: TrajectoryAggregator,
        proposer: object,
        config: EvolverConfig,
        existing_org_proposals: (dict[str, ProceduralMemoryProposal] | None) = None,
    ) -> None:
        self._memory_backend = memory_backend
        self._aggregator = trajectory_aggregator
        self._proposer = proposer
        self._config = config
        self._existing_org_proposals: MappingProxyType[
            str, ProceduralMemoryProposal
        ] = MappingProxyType(copy.deepcopy(existing_org_proposals or {}))

    async def evolve_cycle(
        self,
        window: timedelta,
        trajectories: tuple[AggregatedTrajectory, ...] = (),
    ) -> EvolverReport:
        """Run one evolution cycle.

        Steps:
            1. Check if evolver is enabled.
            2. Aggregate trajectories into patterns.
            3. For each qualifying pattern, build org-scope proposal.
            4. Check supersession against existing org entries.
            5. Emit proposals as ``ApprovalItem`` (NOT direct write).
            6. Return ``EvolverReport``.

        Args:
            window: Analysis time window.
            trajectories: Pre-collected trajectories to analyze.

        Returns:
            Report summarizing the cycle results.

        Raises:
            Exception: Raised when the relevant invariant fails.
        """
        now = datetime.now(UTC)
        cycle_id = str(uuid4())

        if not self._config.enabled:
            logger.info(SKILL_EVOLVER_DISABLED, cycle_id=cycle_id)
            return EvolverReport(
                cycle_id=cycle_id,
                window_start=now - window,
                window_end=now,
                trajectories_analyzed=0,
                patterns_found=0,
            )

        logger.info(
            SKILL_EVOLVER_CYCLE_START,
            cycle_id=cycle_id,
            window_seconds=window.total_seconds(),
            trajectory_count=len(trajectories),
        )

        try:
            return await self._run_cycle(
                cycle_id=cycle_id,
                window=window,
                now=now,
                trajectories=trajectories,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.error(
                SKILL_EVOLVER_CYCLE_FAILED,
                cycle_id=cycle_id,
            )
            raise

    async def _run_cycle(
        self,
        *,
        cycle_id: str,
        window: timedelta,
        now: datetime,
        trajectories: tuple[AggregatedTrajectory, ...],
    ) -> EvolverReport:
        """Internal cycle logic.

        Returns:
            Result of type ``EvolverReport``.
        """
        patterns = self._aggregator.aggregate(trajectories)
        proposals, conflicts, supersessions, skipped = self._collect_proposals(
            patterns, cycle_id
        )
        return self._assemble_report(
            cycle_id,
            window,
            now,
            trajectories,
            patterns,
            proposals,
            conflicts,
            supersessions,
            skipped,
        )

    def _collect_proposals(
        self,
        patterns: tuple[TrajectoryPattern, ...],
        cycle_id: str,
    ) -> tuple[
        list[ApprovalItem],
        list[SupersessionResult],
        list[SupersessionResult],
        int,
    ]:
        """Evaluate patterns and build approval proposals.

        Returns:
            Tuple ``(list[ApprovalItem], list[SupersessionResult],
            list[SupersessionResult], int)``.
        """
        proposals: list[ApprovalItem] = []
        conflicts: list[SupersessionResult] = []
        supersessions: list[SupersessionResult] = []
        skipped_low_confidence = 0

        for pattern in patterns:
            if len(proposals) >= self._config.max_proposals_per_cycle:
                break
            proposal = self._build_proposal_from_pattern(pattern)
            if proposal.confidence < self._config.min_confidence_for_org_promotion:
                skipped_low_confidence += 1
                continue

            candidate_id = f"{cycle_id}:{pattern.pattern_id}"
            skip_pattern = False
            supersedes_ids: list[str] = []
            for eid, existing in self._existing_org_proposals.items():
                result = evaluate_supersession(
                    candidate=proposal,
                    existing=existing,
                    candidate_id=candidate_id,
                    existing_id=eid,
                )
                if result.verdict == SupersessionVerdict.CONFLICT:
                    conflicts.append(result)
                    logger.warning(
                        SKILL_EVOLVER_CONFLICT_DETECTED,
                        cycle_id=cycle_id,
                        candidate_id=result.candidate_id,
                        existing_id=result.existing_id,
                    )
                    skip_pattern = True
                    break
                if result.verdict == SupersessionVerdict.FULL:
                    supersessions.append(result)
                    supersedes_ids.append(eid)

            if skip_pattern:
                continue

            if supersedes_ids:
                proposal = proposal.model_copy(
                    update={"supersedes": tuple(supersedes_ids)},
                )

            approval = self._build_approval_item(proposal, pattern, cycle_id)
            proposals.append(approval)
            logger.info(
                SKILL_EVOLVER_PROPOSAL_EMITTED,
                cycle_id=cycle_id,
                approval_id=approval.id,
                pattern_description=pattern.description,
            )

        return proposals, conflicts, supersessions, skipped_low_confidence

    def _assemble_report(  # noqa: PLR0913
        self,
        cycle_id: str,
        window: timedelta,
        now: datetime,
        trajectories: tuple[AggregatedTrajectory, ...],
        patterns: tuple[TrajectoryPattern, ...],
        proposals: list[ApprovalItem],
        conflicts: list[SupersessionResult],
        supersessions: list[SupersessionResult],
        skipped_low_confidence: int,
    ) -> EvolverReport:
        """Build the cycle report and log completion.

        Returns:
            Result of type ``EvolverReport``.
        """
        report = EvolverReport(
            cycle_id=cycle_id,
            window_start=now - window,
            window_end=now,
            trajectories_analyzed=len(trajectories),
            patterns_found=len(patterns),
            proposals_emitted=tuple(proposals),
            conflicts=tuple(conflicts),
            supersessions=tuple(supersessions),
            skipped_low_confidence=skipped_low_confidence,
            skipped_below_agent_threshold=self._aggregator.last_skipped_count,
        )
        logger.info(
            SKILL_EVOLVER_CYCLE_COMPLETE,
            cycle_id=cycle_id,
            proposals=len(proposals),
            conflicts=len(conflicts),
            supersessions=len(supersessions),
        )
        return report

    def _build_proposal_from_pattern(
        self,
        pattern: TrajectoryPattern,
    ) -> ProceduralMemoryProposal:
        """Build a proposal from an identified pattern.

        Returns:
            Result of type ``ProceduralMemoryProposal``.
        """
        return ProceduralMemoryProposal(
            discovery=f"Cross-agent pattern: {pattern.description[:550]}",
            condition=(
                f"When {len(pattern.agent_ids)} agents encounter "
                f"the same pattern with failure rate "
                f"{pattern.failure_rate:.0%}"
            ),
            action=(
                f"Apply mitigation for pattern observed across "
                f"{pattern.occurrence_count} executions"
            ),
            rationale=(
                f"Pattern seen by {len(pattern.agent_ids)} distinct "
                f"agents, indicating a systemic issue"
            ),
            confidence=_CONFIDENCE_BASELINE
            + _CONFIDENCE_FAILURE_WEIGHT * pattern.failure_rate,
            tags=("evolver-generated", "org-scope"),
            scope=ProceduralMemoryScope.ORG,
        )

    def _build_approval_item(
        self,
        proposal: ProceduralMemoryProposal,
        pattern: TrajectoryPattern,
        cycle_id: str,
    ) -> ApprovalItem:
        """Build an ApprovalItem for human review.

        Returns:
            Result of type ``ApprovalItem``.
        """
        return ApprovalItem(
            id=uuid4(),
            action_type="skill_evolver:org_promotion",
            title=f"Org skill proposal: {proposal.discovery[:80]}",
            description=(
                f"Condition: {proposal.condition}\n"
                f"Action: {proposal.action}\n"
                f"Rationale: {proposal.rationale}\n"
                f"Confidence: {proposal.confidence:.2f}\n"
                f"Agents: {len(pattern.agent_ids)}, "
                f"Occurrences: {pattern.occurrence_count}"
            ),
            requested_by="AutonomousSkillEvolver",
            risk_level=ApprovalRiskLevel.MEDIUM,
            status=ApprovalStatus.PENDING,
            created_at=datetime.now(UTC),
            metadata={
                "cycle_id": cycle_id,
                "pattern_id": pattern.pattern_id,
                "scope": ProceduralMemoryScope.ORG.value,
                "supersedes": json.dumps(list(proposal.supersedes))
                if proposal.supersedes
                else "[]",
            },
        )
