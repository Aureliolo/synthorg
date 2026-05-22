"""Domain models for stakes-aware model routing."""

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.agent import ModelConfig  # noqa: TC001
from synthorg.core.enums import Stakes  # noqa: TC001 -- runtime field type (Pydantic)
from synthorg.core.types import NotBlankStr  # noqa: TC001


class StakesRoutingDecision(BaseModel):
    """Outcome of a stakes-aware routing decision.

    Attributes:
        selected_model: The model config to run the subtask with. For a
            no-op (flat routing, or no adjustment warranted) this equals
            the agent's incoming model.
        red_team_required: Whether the deliverable must pass the
            adversarial red-team gate before completion. Set for stakes
            at or above the configured threshold.
        stakes: The stakes level that drove the decision.
        reason: Human-readable explanation for surfacing/audit.
        source: Machine-readable provenance, e.g. ``"stakes_aware:floor"``,
            ``"stakes_aware:nudge"``, ``"stakes_aware:noop"``, ``"flat"``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    selected_model: ModelConfig = Field(description="Model to execute with")
    red_team_required: bool = Field(
        description="Whether the red-team gate must run for this subtask",
    )
    stakes: Stakes = Field(description="Stakes level driving the decision")
    reason: NotBlankStr = Field(description="Human-readable explanation")
    source: NotBlankStr = Field(description="Machine-readable decision provenance")
