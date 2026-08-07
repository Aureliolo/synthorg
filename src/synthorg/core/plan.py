# module-kind: code
"""Durable Plan entity: the reviewable, revisable breakdown of an objective.

A ``Plan`` is the first-class, persisted, versioned form of an objective's
decomposition: the operator can review and edit it before approving, and it
outlives the approval decision (the approval references only ``plan_id``). It
is distinct from the transient ``DecompositionResult`` the engine dispatches;
the two are projected onto each other by ``engine.decomposition.plan_mapping``.
"""

from collections import Counter
from datetime import datetime
from typing import Final, Self
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.plan_enums import ITEMLESS_STATUSES, PlanItemKind, PlanStatus
from synthorg.core.plan_review import PlanReview
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


class PlanOption(BaseModel):
    """One option a ``DECISION`` plan item offers a reviewer to choose among.

    Attributes:
        id: Stable option identifier within the decision item.
        title: Short option title.
        summary: The option's tradeoffs and rationale, so a reviewer can choose.
        recommended: Whether the owner recommends this option.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Stable option identifier within the item")
    title: NotBlankStr = Field(description="Short option title")
    summary: NotBlankStr = Field(description="The option's tradeoffs and rationale")
    recommended: bool = Field(
        default=False,
        description="Whether the owner recommends this option",
    )


_MAX_DECISION_OPTIONS: Final[int] = 50
MAX_PLAN_VERSION_HISTORY: Final[int] = 20

#: Ceiling on an objective's success criteria. Matched to the evaluate stage's
#: per-verdict cap: an objective carrying more criteria than one verdict may
#: judge would be permanently unevaluatable, burning a paid session per rollup
#: to produce a submission the coverage check must reject. Refusing it at
#: write time says so once, at the point an operator can fix it.
MAX_OBJECTIVE_CRITERIA: Final[int] = 100


class PlanItem(BaseModel):
    """A single unit of work within a plan: reviewable, ownable, verifiable.

    Attributes:
        id: Unique item identifier within the plan.
        title: Short item title.
        description: Detailed item description.
        dependencies: IDs of items this one depends on (the plan DAG).
        owner: Role or agent that owns this item, or ``None`` when unassigned.
        acceptance_criteria: Per-item criteria that define "done" for it.
        expected_artifacts: Deliverables this item must produce; non-empty for
            a WORK item (it arms the fail-loud zero-artifact guard once the
            item runs) and empty for a DECISION, which never dispatches.
        required_skills: Skill IDs the routing scorer matches against.
        required_tags: Tags for multi-faceted routing match.
        estimated_complexity: Complexity estimate for routing.
        stakes: Stakes level for stakes-aware model routing.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique item identifier within the plan")
    title: NotBlankStr = Field(description="Short item title")
    description: NotBlankStr = Field(description="Detailed item description")
    dependencies: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="IDs of items this one depends on",
    )
    owner: NotBlankStr | None = Field(
        default=None,
        description="Role or agent that owns this item",
    )
    acceptance_criteria: tuple[NotBlankStr, ...] = Field(
        min_length=1,
        description="Per-item criteria that define done (never empty)",
    )
    expected_artifacts: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Deliverables this item must produce (non-empty for WORK)",
    )
    required_skills: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Skill IDs the routing scorer matches against",
    )
    required_tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Tags for multi-faceted routing match",
    )
    estimated_complexity: Complexity = Field(
        default=Complexity.MEDIUM,
        description="Complexity estimate for routing",
    )
    stakes: Stakes = Field(
        default=Stakes.NORMAL,
        description="Stakes level for stakes-aware model routing",
    )
    kind: PlanItemKind = Field(
        default=PlanItemKind.WORK,
        description="Whether this item is work to execute or a decision point",
    )
    options: tuple[PlanOption, ...] = Field(
        default=(),
        max_length=_MAX_DECISION_OPTIONS,
        description="For a DECISION item, the options to choose among",
    )
    chosen_option_id: NotBlankStr | None = Field(
        default=None,
        description="The option a reviewer chose (DECISION items only)",
    )
    satisfies: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Advisory tags naming the objective criteria this item "
        "advances; matched leniently for the coverage map, not enforced to "
        "name an entry of the plan's objective_criteria",
    )

    @model_validator(mode="after")
    def _validate_item(self) -> Self:
        """Reject an id that is not a canonical UUID, or a bad dependency list.

        The id must be a canonical UUID string: the dispatch path rebuilds each
        child task via ``subtask_uuid(item.id)``, which rejects a non-UUID id,
        so an operator edit adding ``"my-item"`` would otherwise pass here and
        only fail (silently, into a FAILED task) at approval-dispatch time.
        Dependencies must be unique and must not include the item itself.

        Returns:
            ``self`` unchanged when the id is a canonical UUID, the dependency
            list is free of self-references and duplicates, and the option /
            deliverable shape matches the item kind.

        Raises:
            ValueError: When the id is not a canonical UUID, the item depends
                on itself, a dependency is listed more than once, or the
                option / expected-artifact shape contradicts the kind.
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
        self._validate_decision()
        validate_expected_artifacts(
            entity_id=self.id,
            kind=self.kind,
            expected_artifacts=self.expected_artifacts,
        )
        return self

    def _validate_decision(self) -> None:
        """Enforce the decision-vs-work option invariants for this item.

        Raises:
            ValueError: When the WORK/DECISION option shape is invalid (see
                :func:`validate_decision_options`).
        """
        validate_decision_options(
            entity_id=self.id,
            kind=self.kind,
            options=self.options,
            chosen_option_id=self.chosen_option_id,
        )

    def resolved_option(self) -> PlanOption | None:
        """The option this decision resolves to: the chosen one, else recommended.

        A reviewer's explicit ``chosen_option_id`` wins; absent a pick, the
        decision falls back to the owner's recommended option (the validator
        guarantees a DECISION always has exactly one), so an approved decision
        always resolves to a concrete outcome rather than dispatching unresolved.

        Returns:
            The resolved :class:`PlanOption`, or ``None`` for a WORK item (which
            carries no options).

        Raises:
            ValueError: The decision has no resolvable option (its construction
                invariant was bypassed, e.g. a raw-SQL backfill).
        """
        if self.kind is not PlanItemKind.DECISION:
            return None
        if self.chosen_option_id is not None:
            match = next(
                (o for o in self.options if o.id == self.chosen_option_id), None
            )
        else:
            match = next((o for o in self.options if o.recommended), None)
        if match is None:
            msg = f"Decision item {self.id!r} has no resolvable option"
            raise ValueError(msg)
        return match


class PlanVersionSnapshot(BaseModel):
    """A frozen snapshot of a plan's items at a prior version, for diffing.

    Attributes:
        version: The plan version this snapshot captures.
        items: The plan items as they stood at that version.
        task_structure: The classified structure at that version.
        captured_at: When the snapshot was taken (tz-aware UTC).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    version: int = Field(ge=1, description="The plan version this snapshot captures")
    items: tuple[PlanItem, ...] = Field(description="Plan items at that version")
    task_structure: TaskStructure = Field(
        description="Classified structure at that version",
    )
    captured_at: AwareDatetime = Field(
        description="When the snapshot was taken (tz-aware UTC)",
    )


class Plan(BaseModel):
    """A durable, versioned, revisable plan for an objective.

    Attributes:
        id: Plan identifier (entity primary key).
        project: Project the plan belongs to.
        objective_id: Charter/objective this plan serves.
        objective_title: Human title of the objective, denormalised at creation
            so the review surface never has to resolve (and never falls back to)
            a raw id.
        parent_task_id: Objective task the plan decomposes.
        items: Ordered plan items forming a validated dependency DAG.
        task_structure: Classified structure of the item graph.
        coordination_topology: Selected coordination topology.
        status: Plan lifecycle status.
        forecast_id: Cost forecast released alongside the plan, if any.
        review: The consolidated stakeholder-panel review, once reviewed.
        open_questions: Unresolved questions the owner surfaced for the human.
        assumptions: Assumptions the plan rests on.
        version_history: Snapshots of prior submitted versions, for diffing.
        replan_generation: How many auto-replans deep this plan's lineage is.
        version: Revision number, bumped on each operator edit / re-plan.
        created_at: Creation timestamp (tz-aware UTC).
        updated_at: Last-revision timestamp (tz-aware UTC).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(default_factory=uuid4, description="Plan identifier")
    project: NotBlankStr = Field(description="Project the plan belongs to")
    objective_id: NotBlankStr = Field(description="Charter/objective the plan serves")
    objective_title: NotBlankStr = Field(
        description="Human title of the objective this plan serves",
    )
    parent_task_id: NotBlankStr = Field(
        description="Objective task the plan decomposes",
    )
    items: tuple[PlanItem, ...] = Field(description="Ordered plan items")
    task_structure: TaskStructure = Field(
        default=TaskStructure.SEQUENTIAL,
        description="Classified task structure",
    )
    coordination_topology: CoordinationTopology = Field(
        default=CoordinationTopology.AUTO,
        description="Selected coordination topology",
    )
    status: PlanStatus = Field(
        default=PlanStatus.DRAFT,
        description="Plan lifecycle status",
    )
    failure_reason: NotBlankStr | None = Field(
        default=None,
        description="Why decomposition failed, set when status is FAILED so the "
        "review surface shows a visible reason instead of an empty plan",
    )
    forecast_id: UUID | None = Field(
        default=None,
        description="Cost forecast released alongside the plan",
    )
    review: PlanReview | None = Field(
        default=None,
        description="The consolidated stakeholder-panel review, once reviewed",
    )
    open_questions: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Unresolved questions the owner surfaced for the human",
    )
    assumptions: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Assumptions the plan rests on",
    )
    objective_criteria: tuple[NotBlankStr, ...] = Field(
        default=(),
        max_length=MAX_OBJECTIVE_CRITERIA,
        description="The objective's acceptance criteria, denormalised so the "
        "coverage map can flag any criterion no item advances",
    )
    version_history: tuple[PlanVersionSnapshot, ...] = Field(
        default=(),
        max_length=MAX_PLAN_VERSION_HISTORY,
        description="Snapshots of prior submitted versions, for diffing "
        f"(oldest dropped past {MAX_PLAN_VERSION_HISTORY})",
    )
    replan_generation: int = Field(
        default=0,
        ge=0,
        description="How many auto-replans deep this plan's lineage is; a human "
        "replan resets it, so only an unattended chain is capped",
    )
    version: int = Field(
        default=1,
        ge=1,
        description="Revision number, bumped on each edit / re-plan",
    )
    created_at: AwareDatetime = Field(description="Creation timestamp (tz-aware UTC)")
    updated_at: AwareDatetime = Field(
        description="Last-revision timestamp (tz-aware UTC)"
    )

    @model_validator(mode="after")
    def _validate_items(self) -> Self:
        """Validate the item DAG (non-empty, unique, resolvable, acyclic).

        The items form a dependency DAG that dispatch walks in topological
        order, so a cycle is an entity invariant, not a downstream concern:
        it is rejected here (via a topological sort) rather than surfacing as
        a generic dispatch failure at approval time.

        Returns:
            ``self`` unchanged when items are non-empty, ids are unique, every
            dependency points to a known item, and the graph is acyclic.

        Raises:
            ValueError: When ``items`` is empty for a status that requires a
                filled plan, ids duplicate, a dependency references an unknown
                item id, or the graph contains a cycle.
        """
        if not self.items:
            if self.status in ITEMLESS_STATUSES:
                # A PLANNING shell (persisted at greenlight, not yet decomposed)
                # or a FAILED plan (which may fail before any items were produced)
                # is permitted to carry no items; there is no DAG to validate.
                return self
            msg = "a plan must contain at least one item"
            raise ValueError(msg)
        ids = [item.id for item in self.items]
        if len(ids) != len(set(ids)):
            dupes = sorted(i for i, c in Counter(ids).items() if c > 1)
            msg = f"duplicate plan item ids: {dupes}"
            raise ValueError(msg)
        id_set = set(ids)
        for item in self.items:
            missing = [d for d in item.dependencies if d not in id_set]
            if missing:
                msg = f"plan item {item.id!r} references unknown items: {missing}"
                raise ValueError(msg)
        self._reject_dependency_cycle()
        return self

    def fail(self, reason: NotBlankStr, *, now: datetime) -> Self:
        """Return this plan failed, carrying the reason Plan Review shows.

        The only sanctioned way to produce a FAILED plan. ``model_copy`` does
        not re-run validators, so the present-iff-FAILED rule below cannot
        police a production write; pairing the two fields in one method is
        what makes an unpaired write impossible to express, rather than
        merely absent from the current call sites.

        Args:
            reason: Why the plan could not be delivered.
            now: The write's timestamp.

        Returns:
            A new FAILED revision, version bumped.
        """
        return self.model_copy(
            update={
                "status": PlanStatus.FAILED,
                "failure_reason": reason,
                "version": self.version + 1,
                "updated_at": now,
            }
        )

    @model_validator(mode="after")
    def _validate_failure_reason(self) -> Self:
        """Tie ``failure_reason`` to the FAILED status, both directions.

        A FAILED plan must carry a reason (so Plan Review always shows why it
        failed), and only a FAILED plan may carry one (a reason on any live
        status would be a stale leftover). The ``fail_plan`` compensation always
        sets both together; this is the entity-level backstop for every other
        caller of ``save``/``update``.

        Returns:
            ``self`` when ``failure_reason`` is present iff the status is FAILED.

        Raises:
            ValueError: When a FAILED plan has no reason, or a non-FAILED plan
                carries one.
        """
        has_reason = self.failure_reason is not None
        is_failed = self.status is PlanStatus.FAILED
        if is_failed and not has_reason:
            msg = "a FAILED plan must carry a failure_reason"
            raise ValueError(msg)
        if has_reason and not is_failed:
            msg = (
                f"failure_reason is only valid for a FAILED plan, "
                f"not status={self.status.value}"
            )
            raise ValueError(msg)
        return self

    def _reject_dependency_cycle(self) -> None:
        """Reject a plan whose item dependency graph contains a cycle.

        Raises:
            ValueError: When the items cannot be ordered topologically (Kahn's
                algorithm leaves at least one item with unresolved
                dependencies), naming the items caught in the cycle.
        """
        pending = {item.id: set(item.dependencies) for item in self.items}
        while True:
            ready = {item_id for item_id, deps in pending.items() if not deps}
            if not ready:
                break
            for item_id in ready:
                del pending[item_id]
            for deps in pending.values():
                deps.difference_update(ready)
        if pending:
            cyclic = sorted(pending)
            msg = f"plan items form a dependency cycle: {cyclic}"
            raise ValueError(msg)
