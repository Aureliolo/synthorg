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
from typing import Final, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from evals.recursion_depth.manifest import Arm, Independence, ModelPair
from synthorg.core.types import NotBlankStr

#: Bumping this is a deliberate, breaking change for downstream readers.
RECURSION_DEPTH_SCHEMA_VERSION: Final[int] = 1


#: A unit an agent built end to end, its own tests included.
LEAF: Final[str] = "leaf"

#: A unit that assembled the units below it.
MERGE: Final[str] = "merge"


class UnitRecord(BaseModel):
    """One unit of one run: what it was asked for and what it did.

    Attributes:
        unit_id: The plan subtask id, which is also the workspace key.
        title: What the planner called it.
        kind: :data:`LEAF` or :data:`MERGE`.
        depth: Its level in the decomposition tree, ``0`` at the root.
        claimed: The spec requirement ids the planner said this unit advances.
        delivered: Whether it produced its declared artifacts and its own tests
            passed in its own tree. Only a delivered leaf's claims enter the
            survival denominator: work that never worked cannot be work the
            merge lost.
        attempts: How many sessions this unit consumed, repair included.
        turns: Total agent turns across those sessions.
        cost: Total spend across those sessions.
        verdict: The gate's verdict on this merge, absent in the ungated arm
            and on every leaf.
        parked: Whether the gate escalated with no human to decide, so the
            merge stood unreviewed. Counted and reported: a gated line resting
            on unresolved escalations is a different claim.
        amendments: How many times the merging agent recorded changing a
            child's interface to make the pieces fit. Contracts do not survive
            implementation, so this is expected to be non-zero; a run reporting
            none is reporting that nothing was integrated.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    unit_id: NotBlankStr
    title: NotBlankStr
    kind: NotBlankStr
    depth: int = Field(ge=0)
    claimed: tuple[NotBlankStr, ...] = ()
    delivered: bool = False
    attempts: int = Field(default=0, ge=0)
    turns: int = Field(default=0, ge=0)
    cost: float = Field(default=0.0, ge=0.0)
    verdict: NotBlankStr | None = None
    parked: bool = False
    amendments: int = Field(default=0, ge=0)


class CellRecord(BaseModel):
    """One ``(depth cap, arm, repetition)`` run.

    Invariant: a cell is either measured (carrying units and an oracle result)
    or unavailable (carrying a reason), never both and never neither.

    Attributes:
        depth_cap: The ``max_depth`` this run was allowed.
        arm: Gated or ungated.
        repetition: Zero-based index within the cell.
        achieved_depth: The deepest level the tree actually reached, which is
            what the primary curve is plotted against.
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
    merged_passing: tuple[NotBlankStr, ...] = ()
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

    @property
    def total_cost(self) -> float:
        """What this run spent.

        Returns:
            The summed unit cost.
        """
        return sum(unit.cost for unit in self.units)

    @property
    def total_attempts(self) -> int:
        """How many agent sessions this run consumed.

        Returns:
            The summed attempts.
        """
        return sum(unit.attempts for unit in self.units)


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
        cells: How many runs contributed.
        cost: What those runs spent in total.
        attempts: How many agent sessions they consumed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    depth: int = Field(ge=1)
    arm: Arm
    delivered_claims: int = Field(ge=0)
    surviving_claims: int = Field(ge=0)
    cells: int = Field(ge=0)
    cost: float = Field(default=0.0, ge=0.0)
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
        if value.tzinfo is None:
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

    @property
    def total_cost(self) -> float:
        """What the whole sweep spent.

        Returns:
            The summed cell cost.
        """
        return sum(cell.total_cost for cell in self.cells)


__all__ = [
    "LEAF",
    "MERGE",
    "RECURSION_DEPTH_SCHEMA_VERSION",
    "CellRecord",
    "DepthPoint",
    "Provenance",
    "RecursionDepthReport",
    "UnitRecord",
]
