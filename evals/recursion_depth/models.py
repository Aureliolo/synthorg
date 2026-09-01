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

from collections.abc import Iterable
from datetime import datetime
from enum import StrEnum
from typing import Final, Literal, Self

from pydantic import (
    AliasChoices,
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from evals.recursion_depth.claims import RequirementId
from evals.recursion_depth.manifest import (
    SHARED_FAMILY_CAVEAT,
    Arm,
    Independence,
    ModelPair,
)
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import DecompositionResult

#: Bumping this is a deliberate, breaking change for downstream readers, and
#: only that: a version is a claim that an artifact written under an earlier one
#: has NO reading under this model. :class:`DepthSpread` is therefore not a bump,
#: because its two fields default and every version-3 field means what it did,
#: so a version-3 report still reads correctly here and a reader written for
#: version 3 still reads a report written now.
#:
#: Version 3 carries BOTH questions rather than one: :class:`DepthPoint` keeps
#: the fraction of the SPECIFICATION a merged tree satisfies, and
#: :class:`SurvivalPoint` reports beside it the fraction of the leaves' own
#: claims that survived. A unit no longer counts claims that resolved to
#: nothing, because such a claim now ends the cell before it is paid for. An
#: artifact written under an earlier version therefore has no reading under
#: this model at all, and the version check is what says so: left where it
#: was, such a file would pass the version field and then fail on the shape,
#: reporting a field error for what is a whole-artifact mismatch.
RECURSION_DEPTH_SCHEMA_VERSION: Final[int] = 3


#: What a recorded unit may be. Declared as a closed set rather than free text
#: because two consumers filter on it and neither can tell a typo from a unit
#: that genuinely is not what they wanted: ``CellRecord.leaves`` would drop a
#: misspelled leaf out of the survival denominator, and ``_gate_table`` in
#: :mod:`evals.recursion_depth.emit` would drop it out of the gate table, both
#: silently. A closed set moves that to load time.
type UnitKind = Literal["leaf", "merge", "plan", "contract"]

#: A unit an agent built end to end, its own tests included.
LEAF: Final[Literal["leaf"]] = "leaf"

#: The one session per cell that fixes what the units build against. Neither
#: work nor an assembly: it claims no requirement and is graded against none,
#: and its own tests are supposed to FAIL. Carried as its own kind rather than
#: folded into ``LEAF`` because every consumer that reads a leaf reads it to
#: ask about delivery, and a stage whose success looks exactly like a leaf's
#: failure would be counted as one in the survival denominator.
CONTRACT: Final[Literal["contract"]] = "contract"

#: A unit that assembled the units below it.
MERGE: Final[Literal["merge"]] = "merge"

#: The planning sessions that wrote the tree. Not work and not an assembly, so
#: it claims nothing and delivers nothing, but a deep sweep pays for one of
#: these per node and a cost panel that omitted them would understate the deep
#: end exactly where the question is.
PLAN: Final[Literal["plan"]] = "plan"

#: What a plan unit's id ends in. Planning is the one unit whose id is minted
#: rather than taken from the task it ran, because there is no task: the tree
#: does not exist yet. Anything reading a journalled id back has to recognise
#: the plan row by its shape, so the shape is declared once here.
PLAN_UNIT_SUFFIX: Final[str] = "-plan"


def sum_costs(values: Iterable[float | None]) -> float | None:
    """Fold cost components with ``None`` absorbing rather than zero.

    A component is ``None`` when the session it came from ran on a connection
    that does not price its calls, which is a fact about the WHOLE figure, not
    a zero contribution to it: a merge whose assembling session was priced and
    whose review was not has no honest total, only a partial sum wearing the
    shape of one. Folding a single ``None`` into the running total therefore
    poisons it for good, exactly as a genuinely unknown quantity should.

    Returns:
        The sum of the known components, or ``None`` if any component is
        ``None``, or ``0.0`` if *values* is empty.
    """
    total = 0.0
    for value in values:
        if value is None:
            return None
        total += value
    return total


def reject_negative_deltas(what: str, **deltas: float | None) -> None:
    """Refuse a negative spend delta at the call that computed it.

    Every accumulator books into a ``UnitRecord`` in the end, and that
    model's ``ge=0`` fields catch a negative delta modules and one exception
    boundary away from its origin, by which point the running total is
    already wrong and nothing names which session corrupted it. Refusing at
    the booking keeps the two together.

    Args:
        what: What is being booked, naming the ledger in the message.
        deltas: The figures this booking adds, by field name. A ``None``
            cost is unpriced rather than negative and passes.

    Raises:
        ValueError: Any supplied delta is negative.
    """
    if any(value is not None and value < 0 for value in deltas.values()):
        rendered = ", ".join(f"{name}={value}" for name, value in deltas.items())
        msg = f"{what} spend cannot decrease: {rendered}"
        raise ValueError(msg)


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

#: What each curve is, stated because two of them travel together and only one
#: answers the question the sweep was built around. The artefacts travel
#: without the design page, so a reader holding only the chart and the JSON
#: would otherwise read either axis as the other.
METRIC_CAVEAT: Final[str] = (
    "Two curves, and they answer different questions. SPECIFICATION is the "
    "share of the specification's own requirements the merged tree satisfies: "
    "a denominator every cell shares, which cannot empty, and which says "
    "nothing about where the work came from, so a tree scoring well because "
    "the merging agent rebuilt it reads there exactly like one whose leaves "
    "survived. SURVIVAL is the share of the requirements DELIVERED leaves "
    "claimed that the merged tree still satisfies: the question this sweep "
    "was built around, on a denominator that is leaf work and can be empty, "
    "in which case the point is absent rather than zero. The two coming apart "
    "IS the finding."
)

#: What the held-out oracle buys, stated for the same reason.
ORACLE_CAVEAT: Final[str] = (
    "The oracle is held out: it never enters a workspace and is named in no "
    "brief, so a delivery cannot be built to it."
)

#: What a sweep that stopped on its own ceiling says about itself.
CEILING_CAVEAT: Final[str] = (
    "The sweep stopped early on its session ceiling, so the depths and "
    "repetitions the manifest asked for are not all present. Read the cell "
    "list, not the manifest, for what was actually measured."
)

#: The same, for a sweep the provider account stopped.
QUOTA_CAVEAT: Final[str] = (
    "The sweep stopped early because the provider account ran out of quota, "
    "so the depths and repetitions the manifest asked for are not all "
    "present, and the cell it stopped on was cut off part-way rather than "
    "measured. Read the cell list, not the manifest, for what was actually "
    "measured, and re-run the remainder once the account's window resets."
)

#: The caveats a re-score CARRIES rather than rebuilds, and the whole set of
#: them. Every other caveat either stands for every sweep or is a function of
#: the cells, so a re-score derives it and gets this release's wording; these
#: three are facts about how one run went that the journal does not hold, so
#: they survive only by being copied. Declared as a set because recognising
#: them is what lets everything else be rebuilt: carrying an unrecognised
#: sentence forward instead is how a report comes to hold two wordings of one
#: caveat, and re-deriving one of these would silently drop it.
RUN_STATE_CAVEATS: Final[frozenset[str]] = frozenset(
    {CEILING_CAVEAT, QUOTA_CAVEAT, SHARED_FAMILY_CAVEAT}
)

#: Derived from the cells rather than standing for every sweep, but here with
#: its siblings because a report's caveats are one vocabulary and splitting them
#: across the modules that happen to raise each one is how two of them come to
#: contradict each other.
UNATTRIBUTED_LEAVES_CAVEAT: Final[str] = (
    "{buckets} bucket(s) carry no survival point: their delivered leaves "
    "claimed no requirement between them, so the ratio has an empty "
    "denominator and is reported as absent rather than as zero. A planner may "
    "legitimately leave a subtree claiming nothing; a whole arm reading this "
    "way means the plans stopped tagging what they advance."
)

#: The same, for a recording taken before an unresolvable claim stopped its own
#: cell. Nothing this harness records can produce it now, and it is kept
#: because the recording that did is still on disk, still readable, and still
#: the evidence of what the drift cost.
UNRESOLVED_CLAIMS_CAVEAT: Final[str] = (
    "{dropped} planner claim(s) named no requirement this specification "
    "defines and were dropped before scoring, so the per-unit attribution in "
    "this recording is not trustworthy. A claim naming nothing is now refused "
    "where the planner writes it, and one reaching the harness ends its cell "
    "before any leaf is paid for, so a later recording carrying this line is "
    "reporting a regression rather than a known gap."
)

#: The complement of the line above, for a cell this harness stopped rather
#: than deflated. It rides the caption because an unmeasured cell is otherwise
#: invisible on the chart: it lowers a histogram bar and says nothing, and this
#: particular reason means the product's own write boundary let a claim through
#: rather than that a sample was unlucky.
UNRESOLVABLE_CLAIM_CELLS_CAVEAT: Final[str] = (
    "{cells} cell(s) were stopped before any leaf ran because a planner claim "
    "named no requirement this specification defines. That claim should have "
    "been refused where it was written, so these cells are evidence of a "
    "regression in the product rather than of a difficult sample; the depths "
    "they would have reached are missing from every curve below."
)

#: Fires whenever ``Provenance.cost_basis`` is ``UNPRICED``, so a reader of a
#: recording where every cost figure is absent is told why in the same place
#: the repaired-tokens caveat lives, rather than left to guess whether nobody
#: filled the column in.
UNPRICED_COST_CAVEAT: Final[str] = (
    "At least one connection this sweep dispatched through does not price its "
    "calls (its billing model is not in MEASURABLE_BILLING_MODELS), or could "
    "not be resolved at all, so every cost figure in this recording is absent "
    "rather than zero: an unpriced call and a free one are not the same claim. "
    "Token counts are unaffected and remain the figure the equal-budget check "
    "is stated in."
)


class UnitRecord(BaseModel):
    """One unit of one run: what it was asked for and what it did.

    Attributes:
        unit_id: The plan subtask id, which is also the workspace key.
        title: What the planner called it.
        kind: :data:`LEAF`, :data:`MERGE` or :data:`PLAN`.
        depth: Its level in the decomposition tree, ``0`` at the root.
        claimed: The spec requirement ids the planner said this unit advances.
            Empty only where the unit genuinely claimed nothing: a claim naming
            no requirement this specification defines ends its cell before any
            leaf runs, rather than being dropped into this field's silence.
        unresolved_claims: How many of the planner's claims named no
            requirement this specification defines. Structurally zero on
            anything this harness records: such a claim is refused where the
            planner writes it, and one that reaches the harness anyway ends
            the cell at ``claimed_requirements`` before the first leaf session
            opens. It is carried because a recording taken while that was not
            true is still on disk and still readable, and because the count it
            holds is the whole evidence of what went wrong: dropped instead,
            an unresolvable claim deflates both halves of the survival ratio
            and reads on the chart exactly like a gate that does not help.
        delivered: Whether it changed something it declared and its own tests
            passed in its own tree. Only a delivered leaf's claims enter the
            survival denominator: work that never worked cannot be work the
            merge lost. The declared list does not decide this; see
            ``missing_declared_paths``.
        produced: Whether its own tree changed at all. Recorded beside
            ``delivered`` rather than derived from it because the two answer
            different questions and a resume has to reconstruct both: this is
            the half a parent's brief renders, and collapsing them told a live
            root merge that four subtrees holding 169 modules had delivered
            nothing. A journal written before this field existed reads it as
            false, which is why a resume of one re-runs rather than replaying
            a merge whose inputs it cannot describe.
        attempts: How many sessions this unit consumed, repair and review
            included, which is the figure the equal-budget check reads.
        turns: Agent turns across the sessions that BUILT. A review's turns are
            not observable through the gate's dispatch seam, which answers with
            the pair it ran on and nothing else; its spend is, and spend is
            what the confound is about.
        cost: Total spend across those sessions, ``None`` when the connection
            those sessions ran on does not price its calls (a flat-rate or
            subscription connection reports every call at zero), which is a
            different claim from a genuinely free session: a stored ``0.0``
            says "measured and free", and a recording made before this
            distinction existed reads every unit that way. The whole-sweep
            resolution is ``Provenance.cost_basis``, and this field's ``None``
            never means "unmeasured for this unit alone".
        tokens: Input plus output tokens across the same sessions. The arm
            comparison that does not move with a price change, and the figure
            the equal-budget check is stated in.
        input_tokens: The input half of ``tokens``. ``None`` on a recording
            made before the split existed, and also on a PLAN unit: the
            planner's own ledger (``PlanningSpend``) only ever accumulates a
            total, so a plan row has no split to report even in a fresh
            recording. ``tokens`` stays the total rather than becoming a
            computed sum of this and ``output_tokens``, because a recording
            that never carried the split still carries a real ``tokens``
            figure and a computed field would demand both halves to answer
            it.
        output_tokens: The output half of ``tokens``, on the same terms.
        review_tokens: For a MERGE, the share of ``tokens`` its reviewing
            sessions spent; zero for every other kind. Held apart because
            assembling and judging are different work and the judging half is
            the one that floats between otherwise identical cells: three
            recorded gate sessions made 85, 159 and 257 shell calls on
            byte-identical configuration, and folded into one figure that
            variance is invisible to anything reading the artifact.
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
        parked: Whether the LAST review escalated with no human to decide, so
            the merge stood unreviewed. Counted and reported: a gated line
            resting on unresolved escalations is a different claim.
        parked_attempts: How many repair ROUNDS parked (not sessions, unlike
            ``attempts``), empty (``0``) on a leaf. ``parked_attempts ==
            len(terminations)`` and both non-zero means every round that ran
            asked for a verdict and got none: the unit is UNJUDGED rather
            than gated-and-approved, and ``emit.py`` excludes it from the
            depth curve on that basis.
        amendments: How many times the merging agent recorded changing a
            child's interface to make the pieces fit. Contracts do not survive
            implementation, so this is expected to be non-zero; a run reporting
            none is reporting that nothing was integrated.
        missing_declared_paths: Declared paths ABSENT from the finished tree,
            recorded because a planner over-declaring is worth seeing. Named
            for what it holds after its previous name was read, in this
            project's own design page, as the paths the unit had WRITTEN, which
            inverted the meaning and turned a failed assembly into a merge that
            "wrote a report and touched no code". It still parses the old key,
            because the committed recordings under ``results/`` carry it and
            stay re-scorable in place.
            Diagnosis, and deliberately not a verdict, because the list is the
            PLANNER's guess rather than the agent's work: it is written per
            node at whatever granularity the planner chose, so deciding
            delivery on it makes one output a delivery under a parent's
            two-entry list and a non-delivery under the leaf's four-entry one.
            A live run booked 598,585 tokens as no delivery over an absent
            empty package marker its own passing suite proved it did not need.
            Recorded because an over-declaring planner is worth seeing, and
            separated because what it measures is the planner.
        terminations: How each of this unit's BUILDING sessions ended, in the
            order they ran, empty on a planning unit and on a recording made
            before the field existed. Computed per session all along and only
            logged, which left every other field unable to tell "the agent
            produced nothing" from "the loop stopped it before it could": a
            merge whose three attempts ended ``no_op``, ``budget_exhausted``
            and ``budget_exhausted`` reads here in one line, and reading it
            off the transcripts instead is what it cost before. A review's
            ending is absent for the reason its turns are, see ``turns``.
        workspace_files_changed: The symmetric difference between the unit's
            tree before its session(s) ran and after, so "spent turns and
            changed nothing" is a queryable fact rather than something a
            transcript has to be opened to see. ``None`` on a recording made
            before this field existed, which is a different claim from ``0``:
            the earlier journal never asked the question at all.
    """

    # populate_by_name so the field is settable by its own name despite
    # the alias below, which the committed recordings need on the read side.
    model_config = ConfigDict(
        frozen=True, extra="forbid", allow_inf_nan=False, populate_by_name=True
    )

    unit_id: NotBlankStr
    title: NotBlankStr
    kind: UnitKind
    depth: int = Field(ge=0)
    claimed: tuple[RequirementId, ...] = ()
    unresolved_claims: int = Field(default=0, ge=0)
    delivered: bool = False
    produced: bool = False
    attempts: int = Field(default=0, ge=0)
    turns: int = Field(default=0, ge=0)
    cost: float | None = Field(default=0.0, ge=0.0)
    tokens: int = Field(default=0, ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    review_tokens: int = Field(default=0, ge=0)
    executor: ModelPair | None = None
    reviewer: ModelPair | None = None
    detail: str = ""
    verdict: NotBlankStr | None = None
    parked: bool = False
    parked_attempts: int = Field(default=0, ge=0)
    amendments: int = Field(default=0, ge=0)
    missing_declared_paths: tuple[str, ...] = Field(
        default=(),
        validation_alias=AliasChoices("missing_declared_paths", "undeclared_paths"),
    )
    terminations: tuple[str, ...] = ()
    workspace_files_changed: int | None = Field(default=None, ge=0)

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

    @model_validator(mode="after")
    def _parked_attempts_bounded_by_rounds(self) -> Self:
        """A round parks at most once, so parks cannot outnumber rounds.

        ``CellRecord.is_unjudged`` keys on ``parked_attempts == len(terminations)``
        to decide whether a merge was judged at all; enforcing the bound here
        rather than trusting every future writer keeps that equality from
        silently going one-sided.

        Returns:
            ``self`` when the count is in range.

        Raises:
            ValueError: ``parked_attempts`` exceeds the number of rounds this
                unit actually ran.
        """
        if self.parked_attempts > len(self.terminations):
            msg = (
                f"unit {self.unit_id} reports {self.parked_attempts} parked "
                f"attempts across only {len(self.terminations)} rounds"
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
        shared_modules: How many module paths more than one leaf wrote. The
            denominator ``diverged_modules`` is read against, and it has to be
            reported beside it: a cell where nothing was shared has nothing to
            agree about, which is a different fact from perfect agreement and
            reads identically once the ratio is taken.
        diverged_modules: How many of those the leaves disagreed on, by public
            surface. Carried on the RECORD rather than left to a script,
            because it is what the contract stage exists to move and a
            measurement nobody remembers to take is one the next reader will
            not have. Measured off the trees, since no unit can see a sibling
            and so no unit can report it.
        unavailable_reason: Why this cell has no measurement.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    depth_cap: int = Field(ge=1)
    arm: Arm
    repetition: int = Field(ge=0)
    achieved_depth: int | None = None
    units: tuple[UnitRecord, ...] = ()
    merged_passing: tuple[RequirementId, ...] = ()
    shared_modules: int = Field(default=0, ge=0)
    diverged_modules: int = Field(default=0, ge=0)
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
    def total_cost(self) -> float | None:
        """What this run spent.

        Returns:
            The summed unit cost, or ``None`` if any unit's own cost is
            ``None``: a partial sum reported as the whole is the same defect
            a silent ``0.0`` was.
        """
        return sum_costs(unit.cost for unit in self.units)

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

    @computed_field
    @property
    def is_unjudged(self) -> bool:
        """Whether any merge in this run asked for a verdict and never got one.

        Keyed on PARK EXHAUSTION (every round a merge ran parked), never on
        verdict absence: ``BlindMergeReviewer`` returns no verdict on every
        attempt by design, so keying on absence would erase the ungated
        control arm from the curve along with the cells this is actually for.
        Structurally impossible in the ungated arm, where a merge never parks.

        Returns:
            ``True`` when any merge unit has ``parked_attempts`` equal to its
            own round count and both non-zero.
        """
        return any(
            unit.kind == MERGE
            and unit.parked_attempts > 0
            and unit.parked_attempts == len(unit.terminations)
            for unit in self.units
        )


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
    end, so without this row a cell killed part-way leaves nothing behind: not
    what it built, not what it spent, not the tree it was building against.
    It is the SPEND ledger as well as the progress log, because every session
    the sweep books is one of these and no session is anything else.

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
    """One point on the specification-satisfaction curve.

    The PRIMARY curve, and not the same question as
    :class:`SurvivalPoint`: this one asks how much of the specification the
    merged tree satisfies, against a denominator fixed by the specification
    that cannot empty.

    Attributes:
        depth: The depth this point bins, in levels rather than zero-based, so
            it reads the way the question is asked.
        arm: Which line the point belongs to.
        required: The specification's own requirement count, summed over the
            runs in this bucket. The denominator, and one that cannot empty.
        satisfied: How many of those the merged tree satisfies, per the
            held-out oracle. The numerator.
        cells: How many runs this bucket holds. One run contributes one point's
            worth of both the fraction and the spend, so this is the population
            behind every figure on the point.
        cost: What the runs booked here spent in total.
        tokens: What they spent in tokens. The equal-budget check reads this
            rather than cost, because a price change moves cost and leaves the
            work the two arms did unchanged.
        attempts: How many agent sessions those runs consumed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    depth: int = Field(ge=1)
    arm: Arm
    required: int = Field(ge=0)
    satisfied: int = Field(ge=0)
    cells: int = Field(ge=0)
    cost: float | None = Field(default=None, ge=0.0)
    tokens: int = Field(default=0, ge=0)
    attempts: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _survivors_are_a_subset(self) -> Self:
        """Reject a point satisfying more than the specification asks for.

        Both operands derive from the same requirement set, so a satisfied
        count exceeding the required one means the oracle and the provenance
        have come apart about WHICH specification was run.

        Returns:
            ``self`` when the fraction is in range.

        Raises:
            ValueError: The numerator exceeds the denominator.
        """
        if self.satisfied > self.required:
            msg = (
                f"depth {self.depth} {self.arm.value}: {self.satisfied} "
                f"satisfied against {self.required} required"
            )
            raise ValueError(msg)
        return self

    # Serialised: this is the number the whole sweep exists to produce, and a
    # report that carries its two operands but not the ratio makes every
    # reader recompute it and disagree about the empty case.
    @computed_field
    @property
    def fraction(self) -> float | None:
        """The fraction of the specification the merged tree satisfies.

        Deliberately NOT the fraction of leaf work that survived the merge:
        :class:`SurvivalPoint` answers that, on the same axis, and the two
        coming apart is what the pair exists to show. This denominator is the
        specification's own requirement count, which every cell shares and
        which cannot empty.

        Returns:
            The fraction, or ``None`` when the bucket holds no run at all.
        """
        if self.required == 0:
            return None
        return self.satisfied / self.required


class SurvivalPoint(BaseModel):
    """One point on the leaf-work survival curve.

    A model of its own rather than more fields on :class:`DepthPoint`, because
    the two have different denominators and different empty cases, and because
    spend belongs to a RUN and is booked once, there. Binned on the same axis
    so the pair reads off one chart.

    Attributes:
        depth: The depth this point bins, in levels, matching its
            :class:`DepthPoint` sibling.
        arm: Which line the point belongs to.
        delivered_claims: How many distinct requirements the DELIVERED leaves
            of these runs claimed. The denominator, and the one that can empty:
            a leaf must have changed something it declared and passed its own
            suite to count at all, so a bucket whose leaves all failed has no
            leaf work to have survived anything.
        surviving_claims: How many of those the merged tree still satisfies,
            per the held-out oracle. The numerator.
        cells: How many runs this bucket holds.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    depth: int = Field(ge=1)
    arm: Arm
    delivered_claims: int = Field(ge=0)
    surviving_claims: int = Field(ge=0)
    cells: int = Field(ge=0)

    @model_validator(mode="after")
    def _survivors_are_a_subset(self) -> Self:
        """Reject a point where more survived than was ever delivered.

        Both operands are derived by intersecting one set with another, so a
        numerator exceeding the denominator means the two were taken from
        different populations.

        Returns:
            ``self`` when the fraction is in range.

        Raises:
            ValueError: The numerator exceeds the denominator.
        """
        if self.surviving_claims > self.delivered_claims:
            msg = (
                f"depth {self.depth} {self.arm.value}: "
                f"{self.surviving_claims} surviving against "
                f"{self.delivered_claims} delivered"
            )
            raise ValueError(msg)
        return self

    # Serialised for the same reason its sibling's is: a report carrying two
    # operands and not the ratio makes every reader recompute it and disagree
    # about the empty case, which here is the case that matters most.
    @computed_field
    @property
    def fraction(self) -> float | None:
        """The fraction of claimed leaf work the merge kept.

        Returns:
            The fraction, or ``None`` when the delivered leaves of this bucket
            claimed nothing between them. ``None`` rather than zero: nothing
            was measured there, and a zero reads as everything having been
            lost, which is the opposite conclusion.
        """
        if self.delivered_claims == 0:
            return None
        return self.surviving_claims / self.delivered_claims


class DepthSpread(BaseModel):
    """How much one bucket's repetitions disagreed with each other.

    A model beside the two curves rather than more fields on them, because it
    answers a question neither can. A curve POOLS a bucket's runs into one
    fraction, which is the right shape for a rate over work and cannot say
    whether a low point is one bad draw or three consistent ones. A cap is
    recorded more than once precisely to answer that, so hiding it defeats the
    repetitions.

    Both metrics are ranged over the RUNS rather than recomputed from pooled
    operands, and the figures are stored rather than derived, so a reader
    cannot take a median over a population the recorder did not use.

    Attributes:
        depth: The depth this bucket bins, on the same axis its curve uses.
        arm: Which line the bucket belongs to.
        cells: How many runs the bucket holds, which is the population behind
            every figure here.
        required: The specification's own requirement count, which every run
            shares. Carried on the row rather than left to the reader so a
            range renders as what it is without reaching for the provenance.
        satisfied_min: The fewest specification requirements any run in the
            bucket satisfied, out of ``required``.
        satisfied_median: The middle run's count.
        satisfied_max: The most any run satisfied.
        survival_min: The lowest leaf-work survival rate among the runs that
            HAVE one. ``None`` when no run in the bucket does, which is the
            same absent-point rule :class:`SurvivalPoint` states, applied per
            run: a run whose delivered leaves claimed nothing has no rate, and
            folding it in as a zero reports a collapse nobody measured.
        survival_median: The middle such run's rate, or ``None``.
        survival_max: The highest, or ``None``.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    depth: int = Field(ge=1)
    arm: Arm
    cells: int = Field(ge=1)
    required: int = Field(ge=1)
    satisfied_min: int = Field(ge=0)
    satisfied_median: int = Field(ge=0)
    satisfied_max: int = Field(ge=0)
    survival_min: float | None = Field(default=None, ge=0.0, le=1.0)
    survival_median: float | None = Field(default=None, ge=0.0, le=1.0)
    survival_max: float | None = Field(default=None, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _the_range_is_ordered(self) -> Self:
        """Reject a range whose ends are the wrong way round.

        Three figures summarising one population can only disagree by being
        computed from different ones, which is the failure a stored summary has
        and a derived one does not. Caught here it names the bucket; left
        alone it reads as a real result.

        Returns:
            ``self`` when both ranges are ordered.

        Raises:
            ValueError: A minimum exceeds its median or maximum, one metric
                reports a rate for some ends of its range and not others, or a
                run satisfied more than the specification asks for.
        """
        if not self.satisfied_min <= self.satisfied_median <= self.satisfied_max:
            msg = (
                f"depth {self.depth} {self.arm.value}: satisfied range "
                f"{self.satisfied_min}..{self.satisfied_median}.."
                f"{self.satisfied_max} is not ordered"
            )
            raise ValueError(msg)
        if self.satisfied_max > self.required:
            # The same reading `DepthPoint` refuses, and for the same reason:
            # both operands derive from one requirement set, so a count past
            # the denominator means the oracle and the provenance have come
            # apart about WHICH specification was run.
            msg = (
                f"depth {self.depth} {self.arm.value}: a run satisfied "
                f"{self.satisfied_max} against {self.required} required"
            )
            raise ValueError(msg)
        survival = (self.survival_min, self.survival_median, self.survival_max)
        present = [value for value in survival if value is not None]
        if present and len(present) != len(survival):
            msg = (
                f"depth {self.depth} {self.arm.value}: survival range "
                f"{survival} is part-absent; a bucket either holds a run with "
                f"an attributable rate or it does not"
            )
            raise ValueError(msg)
        if present != sorted(present):
            msg = (
                f"depth {self.depth} {self.arm.value}: survival range "
                f"{survival} is not ordered"
            )
            raise ValueError(msg)
        return self


class SpendSource(StrEnum):
    """Where a recording's token column came from.

    A property of the DATA rather than of the invocation that scored it. Read
    off the run alone, "were these figures repaired" is answerable only by
    whoever typed the command, so a re-score of the same journal by anyone else
    either states the opposite of what it holds or says nothing at all.

    Members:
        JOURNALLED: What each session recorded for itself.
        REPAIRED: Rebuilt per call, because the sessions shared one
            process-wide cost sink that a concurrent run scrambles.
    """

    JOURNALLED = "journalled"
    REPAIRED = "repaired"


class CostBasis(StrEnum):
    """Whether a sweep's spend figures are money or an honest absence of it.

    A sweep-wide verdict rather than a per-unit one, because the claim it
    carries is about the WHOLE artifact: a merge whose assembling session ran
    on a priced connection and whose review ran on an unpriced one has no
    partial answer, only a partial sum wearing the shape of a total. Resolved
    once, from both of the manifest's pairs, at the point provenance is
    captured.

    Members:
        PRICED: Every connection this sweep dispatched through prices its
            calls (``ProviderConfig.billing_model`` is a member of
            ``MEASURABLE_BILLING_MODELS`` for both pairs), so a stored cost
            figure is money.
        UNPRICED: At least one connection does not, or could not be resolved
            at all, so every cost figure this sweep records is ``None`` and a
            reader must not treat a stored ``0.0`` from an earlier recording
            as this sweep's claim.
    """

    PRICED = "priced"
    UNPRICED = "unpriced"


class LoopTreatments(BaseModel):
    """The settings that change what the loop DOES, as this run resolved them.

    Separate from the manifest's digest because that digests the FILE, and a
    per-run override deliberately leaves the file alone: two cells recorded an
    hour apart with opposite `--contract-stage` flags produced byte-identical
    journal headers, so the identity check would accept a resume of one arm
    inside the other's directory and splice two loops into one curve. That is
    exactly what pinning an identity exists to refuse.

    A treatment, never a SCHEDULE lever. ``max_sessions`` and ``repetitions``
    also override the file and deliberately stay out: they decide how much of
    the matrix runs, not what running it means, so an operator trading one of
    them for an evening must still be able to resume.

    Resolved from the NARROWED manifest, which is the one that drove the run,
    so a field here cannot report something other than what ran.

    Attributes:
        contract_stage: Whether one contract session ran between the plan and
            the units, fixing the shape every unit is then recreated from.
        merge_attempts: How many attempts each merge got.
        leaf_reasoning_effort: The depth units BUILT at, or ``None`` when they
            built at the executor's own. The published ablation puts the win in
            the schedule rather than the level, so which phases reasoned how
            deeply is a treatment and belongs in the identity beside the others.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    contract_stage: bool
    merge_attempts: int = Field(ge=1)
    leaf_reasoning_effort: str | None = None


class Provenance(BaseModel):
    """What this sweep was measured against.

    Attributes:
        generated_at: When the report was written.
        git_commit: The commit the recursion point and the gate were built at.
        git_dirty: Whether the tree carried uncommitted changes.
        manifest_sha256: Digest of the matrix FILE. Not of what drove the
            sweep, because a per-run override never touches the file: see
            ``loop``, which carries the treatments it can change.
        loop: What this run made the loop do, as opposed to what the file on
            disk says. ``None`` on a recording made before this field existed.
        spec_id: Which specification was built.
        requirement_count: How many requirements it declares.
        executor: The pair every unit was built on.
        reviewer: The pair every review ran on.
        independence: How far apart those two are.
        spend_source: Whether the token column is what the sessions journalled
            or what a per-call repair rebuilt. Carried here rather than
            appended as a sentence at scoring time, because it is a claim about
            the figures a reader is holding: stated only by the run that typed
            the flag, a later re-score of the same journal reports the opposite
            of what it holds.
        executor_connection_sha256: What SYSTEM ``executor`` actually dispatches
            through, as opposed to the placeholder ``(provider, model_id)`` name
            it carries: the real endpoint, model and serving stack, which can
            differ behind one name and which the placeholder cannot see.
            ``None`` on a recording made before this field existed, and on any
            recording where the connection could not be resolved; comparable
            like every other identity field, so a provider swap mid-matrix
            refuses a resume rather than silently splicing two systems into
            one curve.
        reviewer_connection_sha256: The same fact for ``reviewer``. Digested
            separately because the two pairs can sit on different connections,
            and a swap of either one contaminates the curve.
        sandbox_image: The image every unit built in and every grading ran in,
            as resolved rather than as requested. A sweep executes
            agent-authored code and grades it by importing it, so which build
            of that image was in force is part of what the curve was measured
            against; it was previously recoverable only from the recorder's
            own boot log, which is not something a published artifact carries.
            ``None`` on a recording made before this field existed. Pinned into
            the matrix identity like every other field here, so a run that
            resumes against a different image is refused rather than splicing
            two toolchains into one curve.
        cost_basis: Whether this sweep's cost figures are money or an honest
            absence of it, resolved once from both pairs' connections.
            ``PRICED`` on a recording made before this field existed: those
            recordings stored a real ``cost`` throughout, on a connection this
            field did not yet know how to doubt, and defaulting the historical
            claim to the honest reading it always was is not the same as a
            fresh recording resolving unpriced today.
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
    spend_source: SpendSource = SpendSource.JOURNALLED
    executor_connection_sha256: NotBlankStr | None = None
    reviewer_connection_sha256: NotBlankStr | None = None
    cost_basis: CostBasis = CostBasis.PRICED
    sandbox_image: NotBlankStr | None = None
    loop: LoopTreatments | None = None

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
        by_achieved_depth: The primary curve, binned on the depth each tree
            actually reached.
        by_depth_cap: The secondary curve, binned on the cap the run was
            allowed. Kept because the cap is the manipulated variable and a
            reader needs to see how much of the sweep the planner used.
        survival_by_achieved_depth: What share of the DELIVERED leaves' own
            claims the merge kept, on the same axis as ``by_achieved_depth``.
            The question the sweep was built around; its sibling answers the
            adjacent one, and the pair coming apart is the finding.
        survival_by_depth_cap: The same, binned on the cap, beside
            ``by_depth_cap`` for the same reason that one exists.
        spread_by_achieved_depth: How much each bucket's repetitions disagreed
            with each other, on the axis ``by_achieved_depth`` uses. The curves
            pool a bucket's runs, so without this a reader cannot tell one bad
            draw from a real drop, which is what recording a cap more than once
            is for.
        spread_by_depth_cap: The same, binned on the cap.
        achieved_depth_histogram: How many runs reached each depth, per cap.
            Without it a flat right half of the primary curve is unreadable.
        unjudged_by_depth: How many measured cells asked a gate for a verdict
            and never got one (``CellRecord.is_unjudged``), by achieved depth.
            Those cells are excluded from ``by_achieved_depth`` and
            ``by_depth_cap`` -- a cell whose gate rendered no verdict is a
            missing observation, not a gated one -- but stay in ``cells``
            with their spend intact, and this field is what makes the
            exclusion a fact of the artifact rather than prose that a later
            re-score of the same journal could report differently.
        caveats: What a reader must hold in mind, in the report's own words.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    schema_version: int = Field(default=RECURSION_DEPTH_SCHEMA_VERSION)
    provenance: Provenance
    cells: tuple[CellRecord, ...] = Field(min_length=1)
    by_achieved_depth: tuple[DepthPoint, ...] = ()
    by_depth_cap: tuple[DepthPoint, ...] = ()
    survival_by_achieved_depth: tuple[SurvivalPoint, ...] = ()
    survival_by_depth_cap: tuple[SurvivalPoint, ...] = ()
    spread_by_achieved_depth: tuple[DepthSpread, ...] = ()
    spread_by_depth_cap: tuple[DepthSpread, ...] = ()
    achieved_depth_histogram: dict[str, int] = Field(default_factory=dict)
    unjudged_by_depth: dict[str, int] = Field(default_factory=dict)
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
    def total_cost(self) -> float | None:
        """What the whole sweep spent.

        Returns:
            The summed cell cost, or ``None`` when any cell's own total is.
        """
        return sum_costs(cell.total_cost for cell in self.cells)

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
    "PLAN_UNIT_SUFFIX",
    "RECURSION_DEPTH_SCHEMA_VERSION",
    "UNPRICED_COST_CAVEAT",
    "CellRecord",
    "CostBasis",
    "DepthPoint",
    "DepthSpread",
    "LoopTreatments",
    "Provenance",
    "RecursionDepthReport",
    "SpendSource",
    "SurvivalPoint",
    "UnitKind",
    "UnitRecord",
    "sum_costs",
]
