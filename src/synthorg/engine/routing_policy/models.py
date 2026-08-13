"""Domain models for stakes-aware capability gating."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.task_enums import Stakes
from synthorg.core.types import CapabilityLevel, NotBlankStr


class StakesRoutingDecision(BaseModel):
    """Outcome of gating a run on the task's stakes.

    The decision never names a model. An agent is a fixed
    ``(role, personality, model)`` unit, so stakes decide what the work
    needs and which agent may take it, never what horsepower runs behind
    one agent's name. A decision that is returned at all is a decision the
    bound agent cleared; falling short raises instead.

    Attributes:
        required_capability: The rung this task's stakes demand, after any
            coordination-health nudge. ``None`` when the strategy imposes no
            requirement at all (flat routing).
        agent_capability: The rung the bound agent actually runs at, as the
            capability registry reports it. ``None`` when nothing grades the
            pair, which is only ever returned alongside a ``None``
            requirement.
        red_team_required: Whether the deliverable must pass the
            adversarial red-team gate before completion. Set for stakes
            at or above the configured threshold.
        stakes: The stakes level that drove the decision.
        reasoning_effort: Reasoning depth to request for this subtask, or
            ``None`` to leave it at the provider default. Driven by stakes,
            so higher-stakes work asks the model to think harder. This is the
            one dial stakes still turns on the call itself, because it tunes
            how the bound model works rather than which model runs.
        reason: Human-readable explanation for surfacing/audit.
        source: Machine-readable provenance, e.g. ``"stakes_aware:cleared"``,
            ``"stakes_aware:nudge"``, ``"flat"``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    required_capability: CapabilityLevel | None = Field(
        default=None,
        description="Rung the stakes demand (None = no requirement)",
    )
    agent_capability: CapabilityLevel | None = Field(
        default=None,
        description="Rung the bound agent runs at",
    )
    red_team_required: bool = Field(
        description="Whether the red-team gate must run for this subtask",
    )
    stakes: Stakes = Field(description="Stakes level driving the decision")
    reasoning_effort: ReasoningEffort | None = Field(
        default=None,
        description="Reasoning depth to request (None = provider default)",
    )
    reason: NotBlankStr = Field(description="Human-readable explanation")
    source: NotBlankStr = Field(description="Machine-readable decision provenance")
