"""Protocols for the agent evolution system.

Defines the four pluggable strategy interfaces (trigger, proposer,
guard, adapter) and the context model passed through the pipeline.
"""

from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.agent import AgentIdentity
from synthorg.core.types import NotBlankStr
from synthorg.engine.evolution.models import (
    AdaptationAxis,
    AdaptationDecision,
    AdaptationProposal,
)
from synthorg.hr.performance.models import (
    AgentPerformanceSnapshot,
    TaskMetricRecord,
)
from synthorg.memory.models import MemoryEntry


def _default_triggered_at() -> datetime:
    """Return current time in UTC."""
    return datetime.now(UTC)


class EvolutionContext(BaseModel):
    """Context bag passed through the evolution pipeline.

    Assembled by ``EvolutionService`` before invoking proposers.
    Contains the agent's current identity, performance snapshot,
    recent task results, and relevant procedural memories.

    Attributes:
        agent_id: Target agent identifier.
        identity: Current agent identity snapshot.
        performance_snapshot: Latest performance snapshot (or None
            if insufficient data).
        recent_task_results: Recent task metric records.
        recent_procedural_memories: Relevant procedural memory entries.
        triggered_at: When the evolution was triggered.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr
    identity: AgentIdentity
    performance_snapshot: AgentPerformanceSnapshot | None = None
    recent_task_results: tuple[TaskMetricRecord, ...] = ()
    recent_procedural_memories: tuple[MemoryEntry, ...] = ()
    triggered_at: AwareDatetime = Field(
        default_factory=_default_triggered_at,
    )


@runtime_checkable
class EvolutionTrigger(Protocol):
    """Determines when evolution should run for an agent.

    Implementations include per-task triggers, inflection-based
    triggers, batched/scheduled triggers, and composites.
    """

    @property
    def name(self) -> str:
        """Human-readable trigger name."""
        ...

    async def should_trigger(
        self,
        *,
        agent_id: NotBlankStr,
        context: EvolutionContext,
    ) -> bool:
        """Decide whether evolution should proceed.

        Args:
            agent_id: Agent to evaluate.
            context: Current evolution context.

        Returns:
            True if evolution should run for this agent.
        """
        ...


@runtime_checkable
class AdaptationProposer(Protocol):
    """Generates adaptation proposals from evolution context.

    Implementations include separate-analyzer (EvoSkill strict),
    self-report (lighter, success-path), and composite routing.
    """

    @property
    def name(self) -> str:
        """Human-readable proposer name."""
        ...

    async def propose(
        self,
        *,
        agent_id: NotBlankStr,
        context: EvolutionContext,
    ) -> tuple[AdaptationProposal, ...]:
        """Generate zero or more adaptation proposals.

        Args:
            agent_id: Agent to generate proposals for.
            context: Evolution context with identity, performance,
                and memory data.

        Returns:
            Tuple of proposals (empty if no adaptations suggested).
        """
        ...


@runtime_checkable
class AdaptationGuard(Protocol):
    """Validates an adaptation proposal before it is applied.

    Implementations include rate limiting, review gates,
    rollback monitoring, shadow evaluation, and composites.
    """

    @property
    def name(self) -> str:
        """Human-readable guard name."""
        ...

    async def evaluate(
        self,
        proposal: AdaptationProposal,
    ) -> AdaptationDecision:
        """Evaluate whether a proposal should be applied.

        Args:
            proposal: The adaptation proposal to evaluate.

        Returns:
            Decision with approval/rejection and rationale.
        """
        ...


@runtime_checkable
class AdaptationAdapter(Protocol):
    """Applies an approved adaptation proposal.

    Implementations include identity mutation, strategy selection
    updates, and prompt template injection.
    """

    @property
    def name(self) -> str:
        """Human-readable adapter name."""
        ...

    @property
    def axis(self) -> AdaptationAxis:
        """Which adaptation axis this adapter handles."""
        ...

    async def apply(
        self,
        proposal: AdaptationProposal,
        agent_id: NotBlankStr,
    ) -> None:
        """Apply the approved adaptation.

        Args:
            proposal: The approved proposal to apply.
            agent_id: Target agent.

        Raises:
            AdaptationError: If the adaptation cannot be applied.
        """
        ...


@runtime_checkable
class EvolutionOutcomeSink(Protocol):
    """Narrow write seam for recording terminal evolution outcomes.

    The engine evolution loop records each applied / not-applied event
    here after processing a proposal. Kept deliberately narrow (no
    persistence or meta imports) so the engine never depends on the
    meta-loop's durable store; the durable store satisfies it
    structurally.
    """

    async def record(
        self,
        *,
        agent_id: NotBlankStr,
        axis: NotBlankStr,
        applied: bool,
        proposed_at: datetime,
    ) -> None:
        """Record a terminal evolution outcome.

        Args:
            agent_id: Agent the proposal targeted.
            axis: Adaptation axis value.
            applied: Whether the adaptation was applied without rollback.
            proposed_at: When the proposal was generated.
        """
        ...


# Rebuild EvolutionContext model with forward references resolved at runtime.

EvolutionContext.model_rebuild()
