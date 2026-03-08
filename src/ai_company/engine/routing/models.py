"""Task routing domain models.

Frozen Pydantic models for routing candidates, decisions,
results, and topology configuration.
"""

from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_company.core.agent import AgentIdentity  # noqa: TC001
from ai_company.core.enums import CoordinationTopology
from ai_company.core.types import NotBlankStr  # noqa: TC001


class RoutingCandidate(BaseModel):
    """A candidate agent for a subtask with scoring details.

    Attributes:
        agent_identity: The candidate agent.
        score: Match score between 0.0 and 1.0.
        matched_skills: Skills that matched the subtask requirements.
        reason: Human-readable explanation of the score.
    """

    model_config = ConfigDict(frozen=True)

    agent_identity: AgentIdentity = Field(description="Candidate agent")
    score: float = Field(
        ge=0.0,
        le=1.0,
        description="Match score (0.0-1.0)",
    )
    matched_skills: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Skills that matched subtask requirements",
    )
    reason: NotBlankStr = Field(description="Explanation of score")


class RoutingDecision(BaseModel):
    """Routing decision for a single subtask.

    Attributes:
        subtask_id: ID of the subtask being routed.
        selected_candidate: The chosen agent candidate.
        alternatives: Other candidates considered (sorted by score).
        topology: Coordination topology for this subtask.
    """

    model_config = ConfigDict(frozen=True)

    subtask_id: NotBlankStr = Field(description="Subtask being routed")
    selected_candidate: RoutingCandidate = Field(
        description="Chosen agent candidate",
    )
    alternatives: tuple[RoutingCandidate, ...] = Field(
        default=(),
        description="Other candidates considered",
    )
    topology: CoordinationTopology = Field(
        description="Coordination topology for this subtask",
    )

    @model_validator(mode="after")
    def _validate_selected_not_in_alternatives(self) -> Self:
        """Ensure selected candidate is not duplicated in alternatives."""
        selected_name = self.selected_candidate.agent_identity.name
        for alt in self.alternatives:
            if alt.agent_identity.name == selected_name:
                msg = (
                    f"Selected candidate {selected_name!r} also appears in alternatives"
                )
                raise ValueError(msg)
        return self


class RoutingResult(BaseModel):
    """Result of routing all subtasks in a decomposition.

    Attributes:
        parent_task_id: ID of the parent task.
        decisions: Routing decisions for routable subtasks.
        unroutable: IDs of subtasks with no matching agent.
    """

    model_config = ConfigDict(frozen=True)

    parent_task_id: NotBlankStr = Field(description="Parent task ID")
    decisions: tuple[RoutingDecision, ...] = Field(
        default=(),
        description="Routing decisions",
    )
    unroutable: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Subtask IDs with no matching agent",
    )

    @model_validator(mode="after")
    def _validate_unique_subtask_ids(self) -> Self:
        """Ensure no subtask appears in both decisions and unroutable."""
        decision_ids = {d.subtask_id for d in self.decisions}
        unroutable_set = set(self.unroutable)
        overlap = decision_ids & unroutable_set
        if overlap:
            msg = (
                f"Subtask IDs appear in both decisions and "
                f"unroutable: {sorted(overlap)}"
            )
            raise ValueError(msg)
        return self


class AutoTopologyConfig(BaseModel):
    """Configuration for automatic topology selection.

    Attributes:
        sequential_override: Topology for sequential structures.
        parallel_default: Topology for parallel structures.
        mixed_default: Topology for mixed structures.
        parallel_artifact_threshold: Artifact count above which
            parallel tasks use decentralized topology.
    """

    model_config = ConfigDict(frozen=True)

    sequential_override: CoordinationTopology = Field(
        default=CoordinationTopology.SAS,
        description="Topology for sequential structures",
    )
    parallel_default: CoordinationTopology = Field(
        default=CoordinationTopology.CENTRALIZED,
        description="Topology for parallel structures",
    )
    mixed_default: CoordinationTopology = Field(
        default=CoordinationTopology.CONTEXT_DEPENDENT,
        description="Topology for mixed structures",
    )
    parallel_artifact_threshold: int = Field(
        default=4,
        ge=1,
        description="Artifact count threshold for decentralized topology",
    )

    @model_validator(mode="after")
    def _validate_no_auto_defaults(self) -> Self:
        """Ensure topology defaults are concrete, not AUTO."""
        for field_name in (
            "sequential_override",
            "parallel_default",
            "mixed_default",
        ):
            value = getattr(self, field_name)
            if value == CoordinationTopology.AUTO:
                msg = f"{field_name} cannot be AUTO — would cause infinite resolution"
                raise ValueError(msg)
        return self
