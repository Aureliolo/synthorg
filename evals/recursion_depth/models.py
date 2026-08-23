# module-kind: declarative
"""The committed report: what was swept, what survived, and what it cost.

Three reporting rules the models enforce structurally, each answering a way the
chart could otherwise mislead.

* A cell that could not be measured is recorded as unavailable with its reason.
  It is never silently omitted and never fabricated as a zero, because a curve
  of zeros and a curve nobody ran look identical once the reasons are gone.
* Every unit carries the depth it sat at, so the curve can be plotted against
  the depth a tree ACHIEVED rather than the cap it was allowed. Sweeping the cap
  does not sweep depth: a planner that stops splitting at 3 produces identical
  trees at caps 4, 5 and 6, and a flat right half would then read as "gating
  holds at depth" when it means "nothing went there".
* Attempts, turns, tokens and cost are recorded per arm, because the gated arm
  does more work per merge and a survival gap it bought with spend rather than
  with judgement is not the finding.
"""

from datetime import datetime
from typing import Final, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from evals.recursion_depth.claims import RequirementId
from evals.recursion_depth.manifest import Arm, Independence, ModelPair
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import DecompositionResult

#: Bumping this is a deliberate, breaking change for downstream readers.
RECURSION_DEPTH_SCHEMA_VERSION: Final[int] = 1


#: What a recorded unit may be. Declared as a closed set rather than free text
#: because two consumers filter on it and neither can tell a typo from a unit
#: that genuinely is not what they wanted: ``CellRecord.leaves`` would drop a
#: misspelled leaf out of the survival denominator, and ``_gate_table`` in
#: :mod:`evals.recursion_depth.emit` would drop it out of the gate table, both
#: silently. A closed set moves that to load time.
type UnitKind = Literal["leaf", "merge", "plan"]

#: A unit an agent built end to end, its own tests included.
LEAF: Final[Literal["leaf"]] = "leaf"

#: A unit that assembled the units below it.
MERGE: Final[Literal["merge"]] = "merge"

#: The planning sessions that wrote the tree. Not work and not an assembly, so
#: it claims nothing and delivers nothing, but a deep sweep pays for one of
#: these per node and a cost panel that omitted them would understate the deep
#: end exactly where the question is.
PLAN: Final[Literal["plan"]] = "plan"

#: What the harness measures under, stated wherever the number is. Held beside
#: the field they populate rather than beside the renderer, because the writer
#: seeds them and the renderer only draws them, and a caveat owned by the
#: renderer is one the JSON can be emitted without.
SIZING_CAVEAT: Final[str] = (
    "Unit sizing is the planner's own: the size signal reads the declaration a "
    "planner made, so this measures gated recursion UNDER PLANNER-DECLARED "
    "SIZING and cannot separate 'recursion fails' from 'the planner sized "
    "badly'. Separating them needs an agent that has read the code deciding its "
    "own split, which no published system has."
)

#: What the held-out oracle buys, stated for the same reason.
ORACLE_CAVEAT: Final[str] = (
    "The oracle is held out: it never enters a workspace and is named in no "
    "brief, so a delivery cannot be built to it."
)


class UnitRecord(BaseModel):
    """One unit of one run: what it was asked for and what it did.

    Attributes:
        unit_id: The plan subtask id, which is also the workspace key.
        title: What the planner called it.
        kind: :data:`LEAF`, :data:`MERGE` or :data:`PLAN`.
        depth: Its level in the decomposition tree, ``0`` at the root.
        claimed: The spec requirement ids the planner said this unit advances.
        unresolved_claims: How many of the planner's claims named no
            requirement this specification defines, so they were dropped
            before scoring. Carried into the report rather than left in a
            warning log because the survival metric is a ratio over what
            survives here: a drift between the criterion template and the id
            pattern would deflate both halves toward zero and read on the
            chart exactly like a gate that does not help.
        delivered: Whether it changed something it declared and its own tests
            passed in its own tree. Only a delivered leaf's claims enter the
            survival denominator: work that never worked cannot be work the
            merge lost. The declared list does not decide this; see
            ``undeclared_paths``.
        attempts: How many sessions this unit consumed, repair and review
            included, which is the figure the equal-budget check reads.
        turns: Agent turns across the sessions that BUILT. A review's turns are
            not observable through the gate's dispatch seam, which answers with
            the pair it ran on and nothing else; its spend is, and spend is
            what the confound is about.
        cost: Total spend across those sessions.
        tokens: Input plus output tokens across the same sessions. The arm
            comparison that does not move with a price change, and the figure
            the equal-budget check is stated in.
        executor: The pair this unit was actually built on.
        reviewer: The pair that JUDGED it, on a gated merge. Recorded per unit
            rather than once per sweep because the gate is the treatment: a
            reviewer that silently ran on the executor's own pair is the one
            failure that would bias the result toward the null while every
            sweep-level field still read correctly.
        detail: Why this unit is not a delivery, empty when it is. The whole
            diagnostic surface for a sweep that cost thousands of sessions and
            produced a flat line.
        verdict: The gate's verdict on this merge, absent in the ungated arm
            and on every leaf.
        parked: Whether the gate escalated with no human to decide, so the
            merge stood unreviewed. Counted and reported: a gated line resting
            on unresolved escalations is a different claim.
        amendments: How many times the merging agent recorded changing a
            child's interface to make the pieces fit. Contracts do not survive
            implementation, so this is expected to be non-zero; a run reporting
            none is reporting that nothing was integrated.
        undeclared_paths: Declared paths absent from the finished tree.
            Diagnosis, and deliberately not a verdict, because the list is the
            PLANNER's guess rather than the agent's work: it is written per
            node at whatever granularity the planner chose, so deciding
            delivery on it makes one output a delivery under a parent's
            two-entry list and a non-delivery under the leaf's four-entry one.
            A live run booked 598,585 tokens as no delivery over an absent
            empty package marker its own passing suite proved it did not need.
            Recorded because an over-declaring planner is worth seeing, and
            separated because what it measures is the planner.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    unit_id: NotBlankStr
    title: NotBlankStr
    kind: UnitKind
    depth: int = Field(ge=0)
    claimed: tuple[RequirementId, ...] = ()
    unresolved_claims: int = Field(default=0, ge=0)
    delivered: bool = False
    attempts: int = Field(default=0, ge=0)
    turns: int = Field(default=0, ge=0)
    cost: float = Field(default=0.0, ge=0.0)
    tokens: int = Field(default=0, ge=0)
    executor: ModelPair | None = None
    reviewer: ModelPair | None = None
    detail: str = ""
    verdict: NotBlankStr | None = None
    parked: bool = False
    amendments: int = Field(default=0, ge=0)
    undeclared_paths: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _delivered_units_carry_no_reason(self) -> Self:
        """Reject a unit that both delivered and says why it did not.

        Returns:
            ``self`` when the pair agrees.

        Raises:
            ValueError: A delivered unit carries a failure reason.
        """
        if self.delivered and self.detail:
            msg = (
                f"unit {self.unit_id} delivered and still reports "
                f"{self.detail!r} as why it did not"
            )
            raise ValueError(msg)
        return self


class CellRecord(BaseModel):
    """One ``(depth cap, arm, repetition)`` run.

    Invariant: a cell is either measured (carrying units and an oracle result)
    or unavailable (carrying a reason), never both and never neither.

    Attributes:
        depth_cap: How many LEVELS of planning this run was allowed. The
            product's ``max_depth`` is that count, not an edge count:
            ``max_depth=3`` admits levels 0, 1 and 2.
        arm: Gated or ungated.
        repetition: Zero-based index within the cell.
        achieved_depth: How many levels the tree actually used, in the SAME
            unit as ``depth_cap``, so a cap spent in full reads equal to it
            (``tree.achieved_levels`` owns the conversion). The unit is
            load-bearing rather than cosmetic: this number IS the experiment's
            independent variable, and reporting the deepest level's INDEX
            beside a cap that counts levels makes a tree that used its whole
            cap of three read as one that stopped a level short.
        units: Every unit of the run, leaves and merges.
        merged_passing: The spec requirements the final merged tree satisfies.
        unavailable_reason: Why this cell has no measurement.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    depth_cap: int = Field(ge=1)
    arm: Arm
    repetition: int = Field(ge=0)
    achieved_depth: int | None = None
    units: tuple[UnitRecord, ...] = ()
    merged_passing: tuple[RequirementId, ...] = ()
    unavailable_reason: str | None = None

    @model_validator(mode="after")
    def _measured_xor_unavailable(self) -> Self:
        """Enforce that a cell is either measured or explicitly unavailable.

        Returns:
            ``self`` when the cell reports one or the other.

        Raises:
            ValueError: The cell reports both or neither.
        """
        measured = self.achieved_depth is not None
        unavailable = self.unavailable_reason is not None
        if measured == unavailable:
            msg = (
                f"cell depth={self.depth_cap} arm={self.arm.value} "
                f"rep={self.repetition} must be either measured or unavailable, "
                f"got measured={measured}, reason={self.unavailable_reason!r}"
            )
            raise ValueError(msg)
        return self

    @property
    def leaves(self) -> tuple[UnitRecord, ...]:
        """The units an agent built rather than assembled.

        Returns:
            The leaf units.
        """
        return tuple(unit for unit in self.units if unit.kind == LEAF)

    # The three scalars below are `computed_field` rather than plain
    # properties because `emit.py` persists this model with
    # `model_dump_json` and calls that file what a later analysis reads: a
    # plain property is invisible to serialisation, so the artifact would
    # carry every raw unit and none of the totals they add up to. The
    # record-returning helpers stay plain properties for the mirror-image
    # reason: serialising them would write `units` and `cells` out a second
    # time under another name.
    @computed_field
    @property
    def total_cost(self) -> float:
        """What this run spent.

        Returns:
            The summed unit cost.
        """
        return sum(unit.cost for unit in self.units)

    @computed_field
    @property
    def total_attempts(self) -> int:
        """How many agent sessions this run consumed.

        Returns:
            The summed attempts.
        """
        return sum(unit.attempts for unit in self.units)

    @computed_field
    @property
    def total_tokens(self) -> int:
        """What this run spent in tokens.

        Returns:
            The summed unit tokens.
        """
        return sum(unit.tokens for unit in self.units)


class PlannedTreeRecord(BaseModel):
    """The tree one run was executed from, written down before anything runs.

    Both halves, because neither is recoverable without the other. ``result``
    is what a resume walks; ``root`` is the objective its top level hangs off,
    and its id is minted per call, so re-deriving it would leave every
    ``parent_task_id`` in ``result`` naming a task that no longer exists.

    Attributes:
        root: The objective the tree decomposes.
        result: The decomposition tree.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    root: Task
    result: DecompositionResult


class CellProgressRecord(BaseModel):
    """One session of one run, on disk the moment it returns.

    A cell is hours of sessions and the cell record is written once, at the
    end, so a cell killed part-way used to leave nothing: not what it built,
    not what it spent, not the tree it was building against. This is the row
    that closes that window, and it is the SPEND ledger as well as the progress
    log, because every session the sweep books is one of these and no session
    is anything else.

    Attributes:
        depth_cap: The ``max_depth`` the run was allowed.
        arm: Gated or ungated.
        repetition: Zero-based index within the cell.
        unit: What that session produced, whatever kind of session it was.
        plan: The tree, carried by the planning row alone. A resume that has it
            executes the tree the earlier attempt built against; a resume
            without it has nothing the units on disk belong to and re-runs the
            cell whole.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    depth_cap: int = Field(ge=1)
    arm: Arm
    repetition: int = Field(ge=0)
    unit: UnitRecord
    plan: PlannedTreeRecord | None = None

    @model_validator(mode="after")
    def _only_the_plan_row_carries_a_tree(self) -> Self:
        """Reject a tree hung off a row that did no planning.

        Returns:
            ``self`` when the pair agrees.

        Raises:
            ValueError: A non-planning row carries a tree.
        """
        if self.plan is not None and self.unit.kind != PLAN:
            msg = (
                f"progress row for unit {self.unit.unit_id} is a "
                f"{self.unit.kind} and carries a tree; only the planning row "
                f"does, or a resume has two trees to choose between"
            )
            raise ValueError(msg)
        return self


class DepthPoint(BaseModel):
    """One point on the survival curve.

    Attributes:
        depth: The depth this point bins, in levels rather than zero-based, so
            it reads the way the question is asked.
        arm: Which line the point belongs to.
        delivered_claims: Requirements claimed by leaves that delivered. The
            denominator.
        surviving_claims: Those still satisfied by the final merged tree. The
            numerator.
        cells: How many runs contributed CLAIMS here. On the achieved-depth
            curve a run contributes to every level its leaves sat at, so this
            is the population behind the fraction and nothing else.
        runs: How many runs this bucket IS. A run's spend belongs to the run,
            booked once at the bucket the curve puts the run itself in, so this
            is the population behind ``cost`` and ``attempts``. It differs from
            ``cells`` on the achieved-depth curve, and reporting one figure for
            both made spend-per-run a ratio of two different populations.
        cost: What the runs booked here spent in total.
        tokens: What they spent in tokens. The equal-budget check reads this
            rather than cost, because a price change moves cost and leaves the
            work the two arms did unchanged.
        attempts: How many agent sessions those runs consumed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    depth: int = Field(ge=1)
    arm: Arm
    delivered_claims: int = Field(ge=0)
    surviving_claims: int = Field(ge=0)
    cells: int = Field(ge=0)
    runs: int = Field(default=0, ge=0)
    cost: float = Field(default=0.0, ge=0.0)
    tokens: int = Field(default=0, ge=0)
    attempts: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _survivors_are_a_subset(self) -> Self:
        """Reject a point claiming more survivors than there was work.

        Returns:
            ``self`` when the fraction is in range.

        Raises:
            ValueError: The numerator exceeds the denominator.
        """
        if self.surviving_claims > self.delivered_claims:
            msg = (
                f"depth {self.depth} {self.arm.value}: {self.surviving_claims} "
                f"surviving claims against {self.delivered_claims} delivered"
            )
            raise ValueError(msg)
        return self

    # Serialised: this is the number the whole sweep exists to produce, and a
    # report that carries its two operands but not the ratio makes every
    # reader recompute it and disagree about the empty case.
    @computed_field
    @property
    def fraction(self) -> float | None:
        """The fraction of leaf work surviving to a correct merged result.

        Returns:
            The fraction, or ``None`` when no leaf at this depth delivered
            anything, which is an absence rather than a zero.
        """
        if self.delivered_claims == 0:
            return None
        return self.surviving_claims / self.delivered_claims


class Provenance(BaseModel):
    """What this sweep was measured against.

    Attributes:
        generated_at: When the report was written.
        git_commit: The commit the recursion point and the gate were built at.
        git_dirty: Whether the tree carried uncommitted changes.
        manifest_sha256: Digest of the matrix that drove the sweep.
        spec_id: Which specification was built.
        requirement_count: How many requirements it declares.
        executor: The pair every unit was built on.
        reviewer: The pair every review ran on.
        independence: How far apart those two are.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    generated_at: datetime
    git_commit: NotBlankStr
    git_dirty: bool
    manifest_sha256: NotBlankStr
    spec_id: NotBlankStr
    requirement_count: int = Field(ge=1)
    executor: ModelPair
    reviewer: ModelPair
    independence: Independence

    @field_validator("generated_at")
    @classmethod
    def _generated_at_must_be_aware(cls, value: datetime) -> datetime:
        """Reject naive timestamps so artifacts order unambiguously.

        Returns:
            The validated timestamp.

        Raises:
            ValueError: The timestamp carries no timezone.
        """
        # Both halves: a tzinfo whose utcoffset() answers None is attached but
        # carries no offset, so it passes an is-not-None check and still cannot
        # be ordered against another timestamp, which is the whole point.
        if value.tzinfo is None or value.utcoffset() is None:
            msg = "generated_at must be timezone-aware"
            raise ValueError(msg)
        return value


class RecursionDepthReport(BaseModel):
    """The committed report artifact.

    Attributes:
        schema_version: Bumped only as a deliberate breaking change.
        provenance: What the sweep ran against.
        cells: Every run, measured or unavailable.
        by_achieved_depth: The primary curve, binned on the depth leaves
            actually sat at.
        by_depth_cap: The secondary curve, binned on the cap the run was
            allowed. Kept because the cap is the manipulated variable and a
            reader needs to see how much of the sweep the planner used.
        achieved_depth_histogram: How many runs reached each depth, per cap.
            Without it a flat right half of the primary curve is unreadable.
        caveats: What a reader must hold in mind, in the report's own words.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    schema_version: int = Field(default=RECURSION_DEPTH_SCHEMA_VERSION)
    provenance: Provenance
    cells: tuple[CellRecord, ...] = Field(min_length=1)
    by_achieved_depth: tuple[DepthPoint, ...] = ()
    by_depth_cap: tuple[DepthPoint, ...] = ()
    achieved_depth_histogram: dict[str, int] = Field(default_factory=dict)
    caveats: tuple[str, ...] = ()

    @field_validator("schema_version")
    @classmethod
    def _schema_version_must_be_current(cls, value: int) -> int:
        """Reject a report built against a mismatched schema version.

        Returns:
            The validated version.

        Raises:
            ValueError: The version is not the current one.
        """
        if value != RECURSION_DEPTH_SCHEMA_VERSION:
            msg = (
                f"report schema version mismatch: got {value}, "
                f"expected {RECURSION_DEPTH_SCHEMA_VERSION}"
            )
            raise ValueError(msg)
        return value

    @property
    def measured_cells(self) -> tuple[CellRecord, ...]:
        """Cells that carry a real measurement.

        Returns:
            The measured cells.
        """
        return tuple(cell for cell in self.cells if cell.achieved_depth is not None)

    @property
    def unavailable_cells(self) -> tuple[CellRecord, ...]:
        """Cells that could not be measured, with their reasons.

        Returns:
            The unavailable cells.
        """
        return tuple(cell for cell in self.cells if cell.unavailable_reason is not None)

    @computed_field
    @property
    def total_cost(self) -> float:
        """What the whole sweep spent.

        Returns:
            The summed cell cost.
        """
        return sum(cell.total_cost for cell in self.cells)

    @computed_field
    @property
    def total_tokens(self) -> int:
        """What the whole sweep spent in tokens.

        Returns:
            The summed cell tokens.
        """
        return sum(cell.total_tokens for cell in self.cells)


__all__ = [
    "LEAF",
    "MERGE",
    "PLAN",
    "RECURSION_DEPTH_SCHEMA_VERSION",
    "CellRecord",
    "DepthPoint",
    "Provenance",
    "RecursionDepthReport",
    "UnitKind",
    "UnitRecord",
]
