"""Request DTOs for the plan-review API.

The response body is the durable :class:`~synthorg.core.plan.Plan` domain
model itself (wrapped in ``ApiResponse`` / ``PaginatedResponse``), mirroring
how the projects controller returns ``Project`` directly. Only the mutation
payloads need their own request models, and they live here.
"""

from collections import Counter
from typing import Final, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.plan import PlanOption, validate_decision_options
from synthorg.core.plan_enums import PlanItemKind
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
        min_length=1,
        max_length=_MAX_CRITERIA,
        description="Per-item criteria that define done (never empty)",
    )
    expected_artifacts: tuple[NotBlankStr, ...] = Field(
        default=(),
        max_length=_MAX_CRITERIA,
        description="Deliverables this item must produce",
    )
    required_skills: tuple[NotBlankStr, ...] = Field(
        default=(),
        max_length=_MAX_CRITERIA,
        description="Skill IDs the routing scorer matches against",
    )
    required_tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        max_length=_MAX_CRITERIA,
        description="Tags for multi-faceted routing match",
    )
    estimated_complexity: Complexity = Field(
        default=Complexity.MEDIUM, description="Complexity estimate for routing"
    )
    stakes: Stakes = Field(
        default=Stakes.NORMAL, description="Stakes level for routing"
    )
    kind: PlanItemKind = Field(
        default=PlanItemKind.WORK,
        description="Whether this item is work to execute or a decision point",
    )
    options: tuple[PlanOption, ...] = Field(
        default=(),
        max_length=_MAX_CRITERIA,
        description="For a DECISION item, the options to choose among",
    )
    chosen_option_id: NotBlankStr | None = Field(
        default=None, description="The option a reviewer chose (DECISION items)"
    )

    @model_validator(mode="after")
    def _validate_item(self) -> Self:
        """Reject a non-UUID id or a self-referential / duplicated dependency.

        Mirrors :class:`~synthorg.core.plan.PlanItem` so a malformed edit is
        rejected at the request boundary (with field detail) rather than
        surfacing later as a dispatch failure.

        Returns:
            ``self`` when the id is a canonical UUID and the dependency list is
            free of self-references and duplicates.

        Raises:
            ValueError: When the id is not a canonical UUID, the item depends
                on itself, or a dependency is listed more than once.
        """
        try:
            canonical = str(UUID(self.id))
        except ValueError as exc:
            msg = f"Plan item id {self.id!r} must be a canonical UUID string"
            raise ValueError(msg) from exc
        if self.id != canonical:
            msg = f"Plan item id {self.id!r} is not in canonical UUID form"
            raise ValueError(msg)
        if self.id in self.dependencies:
            msg = f"Plan item {self.id!r} cannot depend on itself"
            raise ValueError(msg)
        if len(self.dependencies) != len(set(self.dependencies)):
            dupes = sorted(d for d, c in Counter(self.dependencies).items() if c > 1)
            msg = f"Plan item {self.id!r} has duplicate dependencies: {dupes}"
            raise ValueError(msg)
        validate_decision_options(
            entity_id=self.id,
            kind=self.kind,
            options=self.options,
            chosen_option_id=self.chosen_option_id,
        )
        return self


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
        note: What the operator wants changed. Surfaced to WS subscribers and
            the audit log; sending the plan back to draft is immediate, but
            auto-routing the note into a concrete replan is not yet wired.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    note: NotBlankStr = Field(
        max_length=8192, description="What the operator wants changed"
    )
