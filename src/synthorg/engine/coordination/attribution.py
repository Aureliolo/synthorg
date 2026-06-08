"""Structural credit assignment for coordinated multi-agent execution.

Provides per-agent contribution scoring and failure attribution
without modifying the frozen ``CoordinationResult``. The wrapper
``CoordinationResultWithAttribution`` pairs the original result
with attribution data built from routing decisions and wave outcomes.
"""

from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.models import (
    CoordinationResult,
    CoordinationWave,
)
from synthorg.engine.loop_protocol import TerminationReason
from synthorg.engine.recovery import FailureCategory, infer_failure_category
from synthorg.engine.routing.models import RoutingResult
from synthorg.observability import get_logger
from synthorg.observability.events.coordination import (
    COORDINATION_ATTRIBUTION_BUILT,
)

logger = get_logger(__name__)


FailureAttribution = Literal[
    "direct",
    "upstream_contamination",
    "coordination_overhead",
    "quality_gate",
]

# Internal constant by design: defensive truncation of evidence text
# attached to failure attributions; prevents oversized error-evidence
# payloads.  Not exposed to the settings registry.
_MAX_EVIDENCE_LENGTH: Final[int] = 500

# Map FailureCategory -> FailureAttribution for error-based outcomes.
_CATEGORY_TO_ATTRIBUTION: dict[FailureCategory, FailureAttribution] = {
    FailureCategory.TOOL_FAILURE: "direct",
    FailureCategory.STAGNATION: "direct",
    FailureCategory.TIMEOUT: "direct",
    FailureCategory.DELEGATION_FAILED: "direct",
    FailureCategory.BUDGET_EXCEEDED: "coordination_overhead",
    FailureCategory.QUALITY_GATE_FAILED: "quality_gate",
    FailureCategory.UNKNOWN: "direct",
}

# Map TerminationReason -> FailureAttribution for non-success runs.
_TERMINATION_TO_ATTRIBUTION: dict[TerminationReason, FailureAttribution] = {
    TerminationReason.STAGNATION: "direct",
    TerminationReason.BUDGET_EXHAUSTED: "coordination_overhead",
    TerminationReason.MAX_TURNS: "coordination_overhead",
    TerminationReason.ERROR: "direct",
    TerminationReason.SHUTDOWN: "coordination_overhead",
    TerminationReason.PARKED: "coordination_overhead",
}


class AgentContribution(BaseModel):
    """Per-agent contribution to a coordinated task execution.

    Attributes:
        agent_id: Identifier of the contributing agent.
        subtask_id: Identifier of the subtask this agent executed.
        contribution_score: Normalized score (0.0-1.0) reflecting
            the agent's contribution quality.
        failure_attribution: Classification of why the agent failed
            (``None`` when the agent succeeded with score 1.0).
        evidence: Truncated error message or evidence pointer
            (``None`` when the agent succeeded).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Contributing agent")
    subtask_id: NotBlankStr = Field(description="Subtask executed")
    contribution_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Contribution quality (0.0-1.0)",
    )
    failure_attribution: FailureAttribution | None = Field(
        default=None,
        description="Why the agent failed (None on success)",
    )
    evidence: str | None = Field(
        default=None,
        max_length=_MAX_EVIDENCE_LENGTH,
        description="Truncated error or evidence pointer",
    )

    @model_validator(mode="after")
    def _validate_score_attribution_consistency(self) -> Self:
        """Score < 1.0 requires failure_attribution; 1.0 forbids it.

        Returns:
            ``self`` unchanged when score / attribution are
            consistent.

        Raises:
            ValueError: When score < 1.0 carries no
                ``failure_attribution`` or score == 1.0 carries
                one.
        """
        if self.contribution_score < 1.0 and self.failure_attribution is None:
            msg = (
                "failure_attribution must be set when "
                f"contribution_score ({self.contribution_score}) < 1.0"
            )
            raise ValueError(msg)
        if self.contribution_score == 1.0 and self.failure_attribution is not None:
            msg = "failure_attribution must be None when contribution_score is 1.0"
            raise ValueError(msg)
        return self


class CoordinationResultWithAttribution(BaseModel):
    """Immutable wrapper pairing a CoordinationResult with attribution.

    Preserves the frozen ``CoordinationResult`` contract while adding
    per-agent contribution data for structural credit assignment.

    Attributes:
        result: The original coordination result (unmodified).
        agent_contributions: Per-agent contribution records.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    result: CoordinationResult = Field(
        description="Original coordination result",
    )
    agent_contributions: tuple[AgentContribution, ...] = Field(
        default=(),
        description="Per-agent contributions",
    )

    @computed_field(  # type: ignore[prop-decorator]
        description="Whether all phases succeeded",
    )
    @property
    def is_success(self) -> bool:
        """Delegate to the wrapped result."""
        return self.result.is_success

    @computed_field(  # type: ignore[prop-decorator]
        description="Average contribution score across agents",
    )
    @property
    def avg_contribution_score(self) -> float:
        """Average of contribution scores, 0.0 when empty."""
        if not self.agent_contributions:
            return 0.0
        total = sum(c.contribution_score for c in self.agent_contributions)
        return total / len(self.agent_contributions)


def build_agent_contributions(
    routing_result: RoutingResult,
    waves: tuple[CoordinationWave, ...],
) -> tuple[AgentContribution, ...]:
    """Build contribution records from routing and execution data.

    Walks routing decisions to establish agent-to-subtask bindings,
    then inspects wave outcomes to determine each agent's score and
    failure classification.

    Args:
        routing_result: Routing decisions mapping agents to subtasks.
        waves: Executed coordination waves with outcomes.

    Returns:
        Tuple of ``AgentContribution`` records, one per agent outcome.
    """
    # Build lookups from routing decisions.
    # Primary: task_id -> subtask_id (direct match from execution outcome).
    # Fallback: agent_id -> subtask_id (single-subtask agents only).
    routed_subtask_ids: set[str] = set()
    agent_to_subtask: dict[str, str] = {}
    for decision in routing_result.decisions:
        sid = str(decision.subtask_id)
        aid = str(decision.selected_candidate.agent_identity.id)
        routed_subtask_ids.add(sid)
        agent_to_subtask[aid] = sid  # last-write for multi-subtask agents

    contributions: list[AgentContribution] = []

    for wave in waves:
        if wave.execution_result is None:
            continue
        for outcome in wave.execution_result.outcomes:
            tid = str(outcome.task_id)
            aid = str(outcome.agent_id)
            # Prefer task_id when it matches a routed subtask (handles
            # multi-subtask agents correctly). Fall back to agent lookup.
            if tid in routed_subtask_ids:
                subtask_id = tid
            else:
                subtask_id = agent_to_subtask.get(aid, tid)
            contrib = _score_outcome(
                agent_id=str(outcome.agent_id),
                subtask_id=subtask_id,
                outcome_result=outcome.result,
                outcome_error=outcome.error,
            )
            contributions.append(contrib)

    result = tuple(contributions)

    if result:
        success_count = sum(1 for c in result if c.contribution_score == 1.0)
        avg_score = sum(c.contribution_score for c in result) / len(result)
        logger.info(
            COORDINATION_ATTRIBUTION_BUILT,
            agent_count=len(result),
            success_count=success_count,
            avg_score=round(avg_score, 3),
        )

    return result


def _score_outcome(
    *,
    agent_id: str,
    subtask_id: str,
    outcome_result: object | None,
    outcome_error: str | None,
) -> AgentContribution:
    """Score a single agent outcome.

    Args:
        agent_id: Agent identifier.
        subtask_id: Subtask identifier.
        outcome_result: AgentRunResult if execution completed, else None.
        outcome_error: Error string if agent failed before execution.

    Returns:
        An ``AgentContribution`` with score and attribution.
    """
    # Case 1: Agent failed with an error string (no execution at all).
    if outcome_error is not None:
        category = infer_failure_category(outcome_error)
        attribution = _CATEGORY_TO_ATTRIBUTION.get(category, "direct")
        return AgentContribution(
            agent_id=NotBlankStr(agent_id),
            subtask_id=NotBlankStr(subtask_id),
            contribution_score=0.0,
            failure_attribution=attribution,
            evidence=outcome_error[:_MAX_EVIDENCE_LENGTH],
        )

    # Case 2: Execution completed -- check if successful. The
    # ``outcome_result`` is duck-typed (an ``AgentRunResult`` in practice)
    # so this module avoids importing ``engine.run_result`` and the cycle
    # it would pull in; we expect ``.is_success: bool``,
    # ``.termination_reason: TerminationReason | None``, and
    # ``.execution_result: <obj with .error_message: str | None> | None``.
    if outcome_result is not None:
        is_success = getattr(outcome_result, "is_success", False)
        if is_success:
            return AgentContribution(
                agent_id=NotBlankStr(agent_id),
                subtask_id=NotBlankStr(subtask_id),
                contribution_score=1.0,
            )

        # Non-success termination.
        termination_reason: TerminationReason | None = getattr(
            outcome_result, "termination_reason", None
        )
        failure_attr: FailureAttribution = "direct"
        if termination_reason is not None:
            failure_attr = _TERMINATION_TO_ATTRIBUTION.get(termination_reason, "direct")
        exec_result = getattr(outcome_result, "execution_result", None)
        error_text = ""
        if exec_result is not None:
            error_text = getattr(exec_result, "error_message", "") or ""

        return AgentContribution(
            agent_id=NotBlankStr(agent_id),
            subtask_id=NotBlankStr(subtask_id),
            contribution_score=0.0,
            failure_attribution=failure_attr,
            evidence=error_text[:_MAX_EVIDENCE_LENGTH] or None,
        )

    # Should not reach here -- AgentOutcome requires result XOR error.
    return AgentContribution(
        agent_id=NotBlankStr(agent_id),
        subtask_id=NotBlankStr(subtask_id),
        contribution_score=0.0,
        failure_attribution="direct",
        evidence="No result or error in outcome",
    )
