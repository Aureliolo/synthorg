"""Request DTOs for the plan-review API.

The response body is the durable :class:`~synthorg.core.plan.Plan` domain
model itself (wrapped in ``ApiResponse`` / ``PaginatedResponse``), mirroring
how the projects controller returns ``Project`` directly. Only the mutation
payloads need their own request models, and they live here.
"""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.task_enums import (
    Complexity,
    CoordinationTopology,
    Stakes,
    TaskStructure,
)
from synthorg.core.types import NotBlankStr

_MAX_ITEMS: Final[int] = 50
_MAX_DEPS: Final[int] = 50
_MAX_CRITERIA: Final[int] = 50


class PlanItemPayload(BaseModel):
    """Editable form of a single plan item.

    Projected onto a :class:`~synthorg.core.plan.PlanItem` by the service;
    the same dependency-graph invariants (unique ids, resolvable deps, no
    self-cycle) are enforced when the resulting ``Plan`` is validated.

    Attributes:
        id: Stable item identifier within the plan.
        title: Short item title.
        description: Detailed item description.
        dependencies: IDs of items this one depends on.
        owner: Role or agent that owns this item, or ``None``.
        acceptance_criteria: Per-item done criteria.
        expected_artifacts: Deliverables this item must produce.
        estimated_complexity: Complexity estimate for routing.
        stakes: Stakes level for stakes-aware routing.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Stable item identifier within the plan")
    title: NotBlankStr = Field(max_length=256, description="Short item title")
    description: NotBlankStr = Field(
        max_length=8192, description="Detailed item description"
    )
    dependencies: tuple[NotBlankStr, ...] = Field(
        default=(),
        max_length=_MAX_DEPS,
        description="IDs of items this one depends on",
    )
    owner: NotBlankStr | None = Field(
        default=None, description="Role or agent that owns this item"
    )
    acceptance_criteria: tuple[NotBlankStr, ...] = Field(
        default=(),
        max_length=_MAX_CRITERIA,
        description="Per-item criteria that define done",
    )
    expected_artifacts: tuple[NotBlankStr, ...] = Field(
        default=(),
        max_length=_MAX_CRITERIA,
        description="Deliverables this item must produce",
    )
    estimated_complexity: Complexity = Field(
        default=Complexity.MEDIUM, description="Complexity estimate for routing"
    )
    stakes: Stakes = Field(
        default=Stakes.NORMAL, description="Stakes level for routing"
    )


class EditPlanRequest(BaseModel):
    """Payload for an operator rework of a plan under review.

    Replaces the plan's items wholesale (add, remove, retitle, re-own,
    re-scope) and optionally overrides the classified structure/topology.
    The service bumps the plan version and returns it to pending review.

    Attributes:
        items: The full revised item list (non-empty).
        task_structure: Optional override of the classified structure.
        coordination_topology: Optional override of the topology.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    items: tuple[PlanItemPayload, ...] = Field(
        min_length=1,
        max_length=_MAX_ITEMS,
        description="The full revised plan item list",
    )
    task_structure: TaskStructure | None = Field(
        default=None, description="Optional override of the classified structure"
    )
    coordination_topology: CoordinationTopology | None = Field(
        default=None, description="Optional override of the coordination topology"
    )


class RequestPlanChangesRequest(BaseModel):
    """Payload asking the org to revise a plan before approval.

    Attributes:
        note: What the operator wants changed (routed to the org on replan).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    note: NotBlankStr = Field(
        max_length=8192, description="What the operator wants changed"
    )
