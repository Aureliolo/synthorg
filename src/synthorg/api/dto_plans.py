"""DTOs for the plan-review API.

The response body is usually the durable :class:`~synthorg.core.plan.Plan`
domain model itself (wrapped in ``ApiResponse`` / ``PaginatedResponse``),
mirroring how the projects controller returns ``Project`` directly. The
mutation payloads need their own request models, and so does the evaluation
history, whose stored record carries storage keys the wire contract should
not inherit.
"""

from collections import Counter
from typing import Final, Self
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.evaluation_verdict import CriterionVerdict
from synthorg.core.plan import PlanOption
from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.plan_validation import (
    validate_decision_options,
    validate_expected_artifacts,
)
from synthorg.core.task_enums import (
    Complexity,
    CoordinationTopology,
    Stakes,
    TaskStructure,
)
from synthorg.core.types import NotBlankStr

#: Ceiling on a submitted item list, and a request-boundary bound rather than
#: a model of the tree. A plan is a TREE, so this covers every level together:
#: the old 50 was one level's worth and would have refused an operator's own
#: edit of any plan that recursed. Sized above what the decomposition bounds
#: can actually produce (a whole-tree planning budget of 40 nodes, each
#: planning at most its width bound of 25), so a legitimate plan is never
#: unsubmittable while a hand-rolled payload still meets a ceiling.
#:
#: The graph validators this list is checked against run synchronously inside
#: the request, so the ceiling is also a bound on how long one edit holds the
#: worker: ~0.5s at this value, measured, and only because each validator
#: indexes the plan once rather than per pair. Re-measure before raising it.
_MAX_ITEMS: Final[int] = 1000
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
        parent_id: The item this one was split out of, or ``None`` when
            nothing contains it. Structure only: it never decides when an
            item runs.
        dependencies: IDs of items this one depends on.
        owner: Role or agent that owns this item, or ``None``.
        acceptance_criteria: Per-item done criteria.
        expected_artifacts: Deliverables this item must produce.
        estimated_complexity: Complexity estimate for routing.
        stakes: Stakes level for capability-based agent selection.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Stable item identifier within the plan")
    title: NotBlankStr = Field(max_length=256, description="Short item title")
    description: NotBlankStr = Field(
        max_length=8192, description="Detailed item description"
    )
    parent_id: NotBlankStr | None = Field(
        default=None,
        description="The item this one was split out of; None for a workstream",
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
        default=Stakes.NORMAL,
        description="Stakes level for capability-based agent selection",
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
    satisfies: tuple[NotBlankStr, ...] = Field(
        default=(),
        max_length=_MAX_CRITERIA,
        description="Which of the plan's objective criteria this item "
        "advances, copied from them and matched ignoring case and surrounding "
        "or repeated whitespace; an entry naming none of them is refused",
    )

    @model_validator(mode="after")
    def _validate_item(self) -> Self:
        """Reject a malformed item at the request boundary.

        Mirrors :class:`~synthorg.core.plan.PlanItem` so a malformed edit is
        rejected here (with field detail) rather than surfacing later as a
        dispatch failure.

        Returns:
            ``self`` when the id is a canonical UUID, the dependency list is
            free of self-references and duplicates, and the declared
            deliverables match the item's kind.

        Raises:
            ValueError: When the id is not a canonical UUID, the item depends
                on itself, a dependency is listed more than once, or a WORK
                item declares no deliverable (or a DECISION item declares one).
        """
        try:
            canonical = str(UUID(self.id))
        except ValueError as exc:
            msg = f"Plan item id {self.id!r} must be a canonical UUID string"
            raise ValueError(msg) from exc
        if self.id != canonical:
            msg = f"Plan item id {self.id!r} is not in canonical UUID form"
            raise ValueError(msg)
        if self.id == self.parent_id:
            msg = f"Plan item {self.id!r} cannot be its own parent"
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
        validate_expected_artifacts(
            entity_id=self.id,
            kind=self.kind,
            expected_artifacts=self.expected_artifacts,
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


class ReplanRequest(BaseModel):
    """Payload revising a plan that is already dispatched.

    A dispatched plan's items are building, so they cannot be rewritten in
    place. This retires the current revision, cancels the work it started, and
    opens a successor under review.

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


class PlanCommentPayload(BaseModel):
    """Payload posting a comment on a plan item's discussion thread.

    Attributes:
        body: The comment text. The author is taken from the authenticated
            user, never the request body.
        reply_to_id: The comment this one answers, when the operator replies
            within the item's thread; ``None`` for a top-level comment.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    body: NotBlankStr = Field(max_length=8192, description="The comment text")
    reply_to_id: UUID | None = Field(
        default=None, description="The comment this one answers, when a reply"
    )


class PlanEvaluationAttempt(BaseModel):
    """One recorded judgement of a plan's objective.

    Attributes:
        attempt: Which judgement of this plan this is, counting from 1.
        summary: The judge's narrative of what it checked.
        verdicts: One verdict per objective criterion.
        objective_met: Whether every criterion was met.
        evaluated_at: When the judgement landed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    attempt: int = Field(ge=1, description="Which judgement of this plan")
    summary: NotBlankStr = Field(description="The judge's narrative")
    verdicts: tuple[CriterionVerdict, ...] = Field(
        description="One verdict per objective criterion"
    )
    objective_met: bool = Field(description="True iff every criterion was met")
    evaluated_at: AwareDatetime = Field(description="When the judgement landed")


class PlanEvaluationResponse(BaseModel):
    """The evaluate stage's judgement history for one plan.

    Empty ``attempts`` is the honest answer for a plan that has not been
    judged, and is also what an operator sees for one parked at EVALUATING
    because no verdict ever landed. The two are told apart by the plan's own
    status, not by inventing a placeholder verdict here.

    Attributes:
        plan_id: The plan the judgements belong to.
        attempts: Every recorded judgement, newest first.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    plan_id: NotBlankStr = Field(description="The judged plan")
    attempts: tuple[PlanEvaluationAttempt, ...] = Field(
        description="Recorded judgements, newest first"
    )
