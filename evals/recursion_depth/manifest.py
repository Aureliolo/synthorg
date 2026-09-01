# module-kind: declarative
"""The recording matrix: what is swept, how often, and by whom.

Two things here are load-bearing beyond configuration.

The reviewer's binding is checked, not assumed. The gate is the treatment in
this experiment, so a judge sharing the executor's ``(provider, model)`` pair
biases straight toward the null: self-preference runs 75-84% toward a model's
own family, and an identical pair is that effect at its maximum. The manifest
therefore declares an independence class and the loader refuses a manifest whose
pairs do not match what it claims, so a weakened judge cannot enter a recording
silently.

That claim is checked against a declared ``family``, never against the provider.
Self-preference attaches to the organisation that trained a model, and an
aggregating connection serves many of those through one endpoint, so a shared
provider says nothing either way: two models reached through the same aggregator
are as decorrelated as their families are, and two connections to the same
vendor are not decorrelated at all. Family is undiscoverable from a placeholder
id for the same reason the capability rung is, so it is declared beside it.

And the repetitions are per depth rather than uniform. Depths 1 and 2 are
expected flat and are cheap; the transition ARIES reports sits at 3 to 4, which
is where samples are worth paying for.
"""

from collections.abc import Sequence
from enum import StrEnum
from pathlib import Path
from typing import Final, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.errors import (
    RecursionDepthCapabilityUnresolvedError,
    RecursionDepthJudgeNotIndependentError,
)
from synthorg.core.agent import AgentIdentity
from synthorg.core.completion_enums import ReasoningEffort
from synthorg.core.types import CapabilityLevel, NotBlankStr

#: The shallowest cap worth recording: one level of planning, every unit
#: dispatched whole, which is what the product did before recursion existed.
MIN_DEPTH: Final[int] = 1

#: The deepest cap the sweep records.
MAX_DEPTH: Final[int] = 6

#: What a same-family judge costs the result. A module constant rather than a
#: literal inside the accessor because a re-score has to RECOGNISE it: the
#: report is rebuilt from the journal, which does not hold the manifest, so
#: this sentence is one of the few a re-score carries forward rather than
#: derives, and matching it means naming it.
SHARED_FAMILY_CAVEAT: Final[str] = (
    "The reviewer and the executor share a model family, so judge "
    "independence here is by model rather than by family. Self-preference "
    "runs 75-84% toward a model's own family, which biases the gated arm "
    "toward the null: a gap in its favour survives this, a null result is "
    "not interpretable under it."
)

#: An assembly costs a merge session and the review that follows it. Two in
#: BOTH arms by construction, since the ungated arm spends the identical budget
#: blindly so repair cannot win by spending more.
_SESSIONS_PER_ASSEMBLY: Final[int] = 2

#: What the shipped planning session accepts as its turn cap
#: (``AgentSessionDecompositionConfig.max_turns``). Mirrored here so a value the
#: product would refuse is refused at manifest load, rather than after a host
#: has booted and against a matrix nobody can record until it is corrected.
_PLANNER_TURN_CAP: Final[int] = 50

#: A sanity bound on a unit's turns. The unit loop takes its cap as a plain
#: argument with no ceiling of its own, so nothing but this refuses a typo, and
#: a unit is bounded in practice by ``unit_token_ceiling`` instead: turns are a
#: BASE budget that re-earns itself, which is why they cannot hold a runaway.
_UNIT_TURN_CAP: Final[int] = 200


class Arm(StrEnum):
    """Which arm of the experiment a cell belongs to.

    Members:
        GATED: The completion-oracle gate runs at every merge.
        UNGATED: No merge is reviewed. Leaf-level verification is unchanged, so
            the difference between the arms is attributable to gating the
            aggregation rather than to leaf quality.
    """

    GATED = "gated"
    UNGATED = "ungated"


class Role(StrEnum):
    """Which kind of session a sizing question is being asked about.

    A merge must read every child before it can write one line; a leaf reads
    nothing. Sizing both off one flat ceiling is what starved the merge and,
    downstream of it, the review that judges the merge's output: both scale
    with how many pieces the session has to read, and neither is a leaf.

    Members:
        PLAN: The session that decomposes a node. Sized separately already
            (``planner_max_turns``), because it writes a plan rather than
            software; carried here so one function answers "what does this
            role get" for every session the sweep runs.
        CONTRACT: Fixes the shape every unit of a cell is then built against.
            Reads no tree at all, and writes a skeleton whose size follows the
            SPECIFICATION rather than any one unit, so it is sized off the
            requirement count instead of sharing the leaf's flat ceiling.
        LEAF: Builds one unit. Reads no sibling's tree, so it takes no fan-in
            scaling; it is scaled instead by how many requirements it CLAIMS,
            because a flat ceiling gives a unit answerable for eighteen of them
            exactly what it gives a unit answerable for two, and 58% of a
            recorded corpus's leaves ran out on it.
        MERGE: Assembles a node's children into one tree. Must read every
            piece before it can write, so its budget grows with how many
            pieces there are.
        REVIEW: Judges one merge attempt. Reads the same pieces the merge
            read, plus what the merge produced, so it scales the same way.
    """

    PLAN = "plan"
    CONTRACT = "contract"
    LEAF = "leaf"
    MERGE = "merge"
    REVIEW = "review"


class Independence(StrEnum):
    """How far apart the reviewer's binding is from the executor's.

    Members:
        CROSS_FAMILY: Different model families, whatever connection reaches
            them. What a headline number requires: decorrelating model family
            is the only lever the blind-spot literature finds effective.
        SAME_FAMILY: A different model of the same family. Permitted, and
            stamped on every artifact, because it biases toward the null: a
            positive result survives it, a null result is not interpretable
            under it.
    """

    CROSS_FAMILY = "cross_family"
    SAME_FAMILY = "same_family"


class ModelPair(BaseModel):
    """An explicit ``(provider, model)`` binding and the rung it runs at.

    The rung is declared rather than looked up. The capability registry grades
    a pair from a catalogue that knows nothing about a placeholder id, and an
    ungraded pair is refused by selection outright, so a roster built from a
    manifest that did not say would leave every review unstaffed and the gated
    arm would record escalations rather than verdicts.

    Attributes:
        provider: The registered connection dispatch goes through.
        model_id: The model that connection is asked for.
        capability: The rung the roster claims for this pair. The catalogue
            still wins where it grades the pair itself.
        family: Who trained the model, which is what self-preference attaches
            to. Declared on a manifest pair and absent on one read back off a
            live identity, which carries no such field; the loader requires it
            wherever a manifest claims decorrelation, so it cannot be missing
            at the point it decides anything.
        temperature: Sampling temperature this pair runs at. Declared PER PAIR
            rather than once for the matrix because it is a property of the
            model: the two pairs a sweep binds are published with different
            values, so one number for both is guaranteed wrong for one of them.
        top_p: Nucleus threshold, which moves with the temperature because a
            vendor publishes the two together. Applying one without the other
            produces a distribution nobody tested.
        reasoning_effort: Reasoning depth to ask this pair for, or ``None`` to
            send none. Which of this and ``temperature`` actually reaches a
            given model is the model's business and they differ sharply: some
            families expose graded effort and ignore sampling while thinking,
            others expose no effort parameter at all. Declaring both per pair
            is what lets one manifest describe both honestly.
        max_tokens: Per-RESPONSE output ceiling, or ``None`` to defer. Declared
            here for the same reason as the rest: a reasoning model spends this
            budget on hidden reasoning BEFORE it can emit content, so the right
            value depends on the depth that model reasons at, which is a
            per-pair fact.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    provider: NotBlankStr
    model_id: NotBlankStr
    capability: CapabilityLevel
    family: NotBlankStr | None = None
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    top_p: float | None = Field(default=None, ge=0.0, le=1.0)
    reasoning_effort: ReasoningEffort | None = None
    max_tokens: int | None = Field(default=None, gt=0)

    @property
    def label(self) -> str:
        """A one-line rendering for logs and artifacts.

        Returns:
            ``provider/model_id``.
        """
        return f"{self.provider}/{self.model_id}"

    @property
    def sampling_summary(self) -> str:
        """Render every dial this pair holds, stated or not.

        An unset dial is named rather than omitted, because omission reads as
        an assertion that nothing applied while each of these resolves
        somewhere further down. What an absence MEANS differs by caller (a
        planned pair resolves through staffing, a recorded one through the
        completion config), so this states only what the pair holds and leaves
        the meaning to the caller's own caption.

        Returns:
            All four dials, comma-separated, each with its value or ``unset``.
        """
        return ", ".join(
            f"{name} {'unset' if value is None else value}"
            for name, value in (
                ("temperature", self.temperature),
                ("top_p", self.top_p),
                ("reasoning_effort", self.reasoning_effort),
                ("max_tokens", self.max_tokens),
            )
        )

    @classmethod
    def of(
        cls, identity: AgentIdentity, declared: Sequence[ModelPair] = ()
    ) -> ModelPair:
        """Read the pair an agent actually dispatches on.

        Read off the identity rather than off the manifest, because the
        manifest says what the roster was ASKED to bind and this says what ran.
        A reviewer that silently came up on the executor's own pair is the one
        failure that would bias the gated arm toward the null while every
        manifest-level field still read correctly.

        ``family`` is the exception, because an ``AgentIdentity`` has no such
        field: it is looked up in *declared* by exact ``provider/model_id``
        key, never derived from the provider, since an aggregating connection
        serves many families through one endpoint. Without this every per-unit
        record wrote ``family: null`` while the manifest claimed
        ``cross_family``, so the one claim a gated result rests on was
        evidenced nowhere in the ledger. A pair matching nothing declared keeps
        ``None``, which is itself the finding: something came up on a pair the
        manifest never named.

        Args:
            identity: The agent whose binding is wanted.
            declared: The manifest's own pairs, which carry the families.

        Returns:
            Its ``(provider, model)`` pair, the rung it is graded at, and the
            family declared for it.

        Raises:
            RecursionDepthCapabilityUnresolvedError: The identity carries no
                rung. Every roster identity is built from one of these pairs,
                where the rung is required, so this is a roster built outside
                that path rather than anything a run can reach.
        """
        if identity.model.capability is None:
            msg = (
                f"agent {identity.name!r} dispatches on "
                f"{identity.model.provider}/{identity.model.model_id} with no "
                f"declared capability rung, so what judged this unit cannot be "
                f"recorded"
            )
            raise RecursionDepthCapabilityUnresolvedError(msg)
        provider = NotBlankStr(identity.model.provider)
        model_id = NotBlankStr(identity.model.model_id)
        families = {
            (pair.provider, pair.model_id): pair.family
            for pair in declared
            if pair.family is not None
        }
        # Sampling is read off the IDENTITY, unlike `family`, because an
        # identity does carry it: this is the binding the roster resolved to,
        # not what the manifest asked for, which is the distinction this whole
        # method exists to preserve. It is the BINDING and not the request: a
        # dial left unset here is one the binding does not state, and per-call
        # resolution can still fill it downstream (an unset reasoning depth
        # falls to the stakes ladder, an unset `top_p` to the completion
        # config's own default), so an absence records what was bound rather
        # than proving what no request carried.
        return cls(
            provider=provider,
            model_id=model_id,
            capability=identity.model.capability,
            family=families.get((provider, model_id)),
            temperature=identity.model.temperature,
            top_p=identity.model.top_p,
            reasoning_effort=identity.model.reasoning_effort,
            max_tokens=identity.model.max_tokens,
        )


class RecursionDepthManifest(BaseModel):
    """The whole recording matrix.

    Attributes:
        spec_dir: The specification the sweep is run against. Declared
            relative to the manifest file and stored absolute by
            :func:`load_manifest`, so no consumer depends on a working
            directory.
        depths: The ``max_depth`` caps swept, ascending.
        repetitions: How many times each cap is recorded, per cap.
        arms: The arms recorded. Both, in every real recording.
        executor: The pair every unit is built on.
        reviewer: The pair every merge review runs on.
        independence: What the manifest claims about those two pairs, checked
            against them at load.
        contract_stage: Whether one CONTRACT session runs between the plan and
            the first leaf, fixing the module layout, the signatures and one
            failing test per requirement that every unit is then recreated
            from. Off reproduces the recorded corpus exactly: each unit seeds
            from the specification's committed README, finds no name to import,
            and invents one. That corpus is why this exists at all, and it is
            also why the flag exists rather than the stage simply always
            running: the measured claim is that a cell WITH a contract diverges
            less than one without, and a treatment nothing can be compared
            against is not a measurement.
        leaf_reasoning_effort: Reasoning depth for the agents that BUILD units,
            or ``None`` to bind them exactly like every other builder, which is
            what every recording before this field was made under.

            The one published harness ablation with numbers behind it says the
            win is in the SCHEDULE rather than the level: holding a model
            fixed, reasoning at the deepest setting throughout scored WORSE
            than reasoning moderately throughout (53.9% against 63.6%), and
            reasoning deeply while planning and verifying but moderately while
            building beat both (66.5%). Implementation is mostly execution of a
            plan already understood.

            Applies to leaves alone, because everything else this executor pair
            runs is a planning or an assembly session and the reviewer already
            carries its own pair. Set BELOW the executor's own depth to buy the
            sandwich; the ordering is a hypothesis to test here rather than a
            result to import, since the same source reports its harness gains
            did not transfer to another model untuned.
        merge_attempts: How many attempts each merge gets, in BOTH arms. Equal
            by construction: repair only in the gated arm would let it win by
            spending more rather than by catching anything.
        planner_max_turns: The turn ceiling one PLANNING session gets. Its own
            field rather than a share of ``unit_max_turns`` because the two
            bound different things and only one of them has a product limit:
            a planner writes a plan and a unit builds software, so what is
            generous for the first is not necessarily enough for the second,
            and one value serving both means raising either raises both until
            the stricter limit refuses the pair.
        unit_max_turns: The base turn budget one unit's session gets, which
            re-earns itself up to ``engine.max_turn_extensions`` times and so
            never stops a runaway on its own. Bounded loosely for that
            reason: a unit is held by ``unit_token_ceiling`` in practice.
        unit_cost_ceiling: What one unit's session may spend before the
            gateway's own hard kill stops it. Money only, so it is half a
            bound: see ``unit_token_ceiling``. Shared unscaled across every
            role under :func:`session_limits_for` (leaf, planner, merge,
            review): unlike the token axis there is no
            ``merge_cost_base`` / ``review_cost_base`` pair, because a
            flat-rate connection attributes 0.0 to every call and a
            fan-in-scaled money ceiling would be sized against a bound that
            never fires there.
        unit_token_ceiling: The same bound counted in tokens, and the only one
            of the two that binds everywhere. A flat-rate connection
            attributes 0.0 to every call, so the cost ceiling cannot fire
            there and a runaway unit would be held by nothing but its turn
            cap. Required rather than optional, because the connection a
            manifest will be recorded against is not knowable here. This is
            also the LEAF's BASE budget under :func:`session_limits_for`: a
            leaf reads no sibling's tree, so it takes no fan-in scaling, and
            what it does scale on is ``unit_token_per_claim``.
        unit_token_per_claim: Tokens added per specification requirement the
            leaf is answerable for. Flat was the shipped shape and it is the
            reason 58% of a recorded corpus's leaves terminated on their
            ceiling rather than on their work: a planner is free to make one
            unit answerable for eighteen requirements and another for two, and
            handing both the same budget prices the plan's own shape at zero.
            Zero restores the flat behaviour exactly, which is what makes the
            comparison against the recorded corpus a fair one.
        unit_token_cap: The most one leaf may be sized to, however many
            requirements it claims. Declared so a planner that puts most of
            the specification behind a single unit cannot mint an unbounded
            session out of its own bad decomposition.
        contract_max_turns: The turn budget the CONTRACT session gets.
        contract_token_ceiling: What the contract session may spend. Its own
            field rather than the leaf's, because what it writes follows the
            SPECIFICATION rather than any one unit: it declares every module
            the plan named and one failing test per requirement, so a leaf's
            budget is the wrong size for it in both directions.
        merge_token_base: What one merge session gets before any child is
            counted, on the same footing as ``unit_token_ceiling``: a merge
            with nothing to read still has to write an assembled tree.
        merge_token_per_piece: Tokens added per child the merge must read
            before it can write. A merge that mounts eight children and gets
            a leaf's flat budget can read them and nothing is left to write
            with, which is exactly what starved the stopped recording: four
            merges made 167 tool calls between them, all of them
            ``shell_command``, and wrote zero files.
        merge_token_cap: The most one merge session may be sized to, however
            wide its fan-in. Declared so a node with an unusually large number
            of children cannot mint an unbounded session.
        merge_max_turns_base: The base turn budget one merge session gets
            before any child is counted, on ``unit_max_turns``'s own footing:
            it re-earns itself up to ``engine.max_turn_extensions`` times and
            so never stops a runaway alone, which is what
            ``merge_token_cap`` is for.
        merge_max_turns_per_piece: Turns added per child the merge must read.
        merge_max_turns_cap: The most turns one merge session may be sized to.
        review_token_base: What one review session gets before any child is
            counted. A review reads the same pieces the merge read, plus what
            the merge produced, so its reading burden is the merge's own: the
            stopped recording's rep 1 review spent 1,058,678 tokens against a
            flat 1,500,000 ceiling without reaching a verdict, which is the
            starved reviewer that let a park stand for the escalation the
            approval-branch bug then treated as a stop.
        review_token_per_piece: Tokens added per child the review must read.
        review_token_cap: The most one review session may be sized to.
        review_max_turns_base: The base turn budget one review session gets
            before any child is counted. Lower than ``merge_max_turns_base``:
            a review writes a verdict and findings, not an assembled tree.
        review_max_turns_per_piece: Turns added per child the review must
            read.
        review_max_turns_cap: The most turns one review session may be sized
            to.
        max_sessions: The whole sweep's session ceiling. A depth sweep's
            session count is a product of branching factors nobody can predict
            from the manifest alone, and the cost of being wrong is spend.
        projected_branching: How many subtasks a planning session is assumed
            to produce, used ONLY to project the bill before a run and never
            by the run itself. Declared rather than inferred, and printed
            beside the figure it produces, because the projection is a model
            and a model whose assumption is hidden reads as a measurement.
            What it produces is the cost of the FULL tree a cap admits at this
            branching, which is neither a floor nor a ceiling: a planner that
            stops short of the cap spends less, and one that branches wider
            than declared spends more. It is the scenario worth sizing
            ``max_sessions`` against, because the run that uses its whole cap
            is the expensive one, and the ceiling is what makes being wrong in
            either direction survivable.
        expected_sessions_per_cell: What ONE cell at each cap is expected to
            cost, which is a different question from the projection above and
            answers it for a different consumer. The projection is the
            worst-case scenario a ceiling is sized against; this is the
            best available estimate of what a cell will actually cost, and it
            is what decides whether the sweep STARTS one. Declared from
            measurement rather than modelled, because the full-tree model
            answers a figure an order of magnitude high at the deep end and
            would refuse the deepest cells of every sweep. A cap with a
            recorded cell is priced from that instead; see
            :func:`evals.recursion_depth.forecast.estimate_sessions`.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    spec_dir: NotBlankStr
    depths: tuple[int, ...] = Field(min_length=1)
    repetitions: dict[int, int]
    arms: tuple[Arm, ...] = Field(min_length=1)
    executor: ModelPair
    reviewer: ModelPair
    independence: Independence
    contract_stage: bool
    leaf_reasoning_effort: ReasoningEffort | None = None
    merge_attempts: int = Field(ge=1, le=10)
    planner_max_turns: int = Field(ge=1, le=_PLANNER_TURN_CAP)
    unit_max_turns: int = Field(ge=1, le=_UNIT_TURN_CAP)
    unit_cost_ceiling: float = Field(gt=0.0)
    unit_token_ceiling: int = Field(gt=0)
    unit_token_per_claim: int = Field(ge=0)
    unit_token_cap: int = Field(gt=0)
    contract_max_turns: int = Field(ge=1, le=_UNIT_TURN_CAP)
    contract_token_ceiling: int = Field(gt=0)
    merge_token_base: int = Field(gt=0)
    merge_token_per_piece: int = Field(ge=0)
    merge_token_cap: int = Field(gt=0)
    merge_max_turns_base: int = Field(ge=1, le=_UNIT_TURN_CAP)
    merge_max_turns_per_piece: int = Field(ge=0)
    merge_max_turns_cap: int = Field(ge=1, le=_UNIT_TURN_CAP)
    review_token_base: int = Field(gt=0)
    review_token_per_piece: int = Field(ge=0)
    review_token_cap: int = Field(gt=0)
    review_max_turns_base: int = Field(ge=1, le=_UNIT_TURN_CAP)
    review_max_turns_per_piece: int = Field(ge=0)
    review_max_turns_cap: int = Field(ge=1, le=_UNIT_TURN_CAP)
    max_sessions: int = Field(ge=1)
    projected_branching: int = Field(ge=2, le=50)
    expected_sessions_per_cell: dict[int, int]

    def expected_sessions(self, depth_cap: int) -> int:
        """What one cell at *depth_cap* is expected to cost, as declared.

        Args:
            depth_cap: The cap of the cell being priced.

        Returns:
            The declared session count.

        Raises:
            KeyError: The cap is not priced. Unreachable for a swept cap,
                which the validator requires an entry for.
        """
        return self.expected_sessions_per_cell[depth_cap]

    def projected_sessions(
        self, depth_cap: int, *, branching: int | None = None
    ) -> int:
        """What a FULL tree at *depth_cap* costs, in sessions.

        The scenario the bill is printed from and ``max_sessions`` is sized
        against, and deliberately NOT what decides whether a cell is started:
        the trees this sweep produces branch wide at the top and narrow below
        (7, then 4.6, then 3.5 per level in a recorded cap-3 tree), so a
        uniform factor raised to the fourth power answers an order of magnitude
        high. Used as a refusal threshold it refuses the deepest cell of every
        sweep. That question is answered by ``expected_sessions_per_cell``.

        A cap of ``d`` admits ``d`` levels of PLANNING (a node plans at
        ``current_depth`` 0 through ``d - 1``, since ``has_room`` asks whether
        ``current_depth + 1 < max_depth``), so at branching ``b`` the tree
        holds ``b ** d`` leaves and ``(b ** d - 1) / (b - 1)`` nodes that
        planned. Every node that planned also assembles what it planned, and an
        assembly costs TWO sessions rather than one: the merge and the review
        that follows it, in both arms by construction.

        The tree is where a depth sweep's sessions come from, so a figure
        scaling only with the number of runs is the one an operator sizes
        ``max_sessions`` from and loses a paid run to. A real cap-3 run cost
        about 158 sessions; a ceiling of 30, chosen against a matrix-shaped
        figure of 42, bought a whole planned tree, six built units and nothing
        measured.

        Args:
            depth_cap: The cap this cell runs at.
            branching: How wide one planning session splits. Defaults to the
                manifest's assumption, which is what the plan projection
                prints before any tree exists. A running sweep passes what it
                has MEASURED instead: the assumption is the one input a
                recording can correct about itself, and it is wrong in the
                direction that compounds with depth.

        Returns:
            The projected session count for one cell.
        """
        branching = self.projected_branching if branching is None else branching
        # Annotated because ``int ** int`` widens to Any: the exponent could be
        # negative, and this one is bounded at one by the caller's own field.
        leaves: int = branching**depth_cap
        planned = (leaves - 1) // (branching - 1)
        # The contract is ONE session per cell however deep the tree, because
        # it fixes the shape of the specification rather than of any level.
        # That is also why it barely moves this figure and must still be in it:
        # a projection that omits a session the run makes is one an operator
        # sizes a ceiling from and loses a cell to.
        contract = 1 if self.contract_stage else 0
        return contract + planned + leaves + _SESSIONS_PER_ASSEMBLY * planned

    @model_validator(mode="after")
    def _validate(self) -> Self:
        """Check the sweep is coherent and the judge is actually independent.

        Returns:
            ``self`` when the matrix is recordable.

        Raises:
            ValueError: A depth is outside the sweep's range, is repeated, or
                has no repetition count or no expected session cost.
            RecursionDepthJudgeNotIndependentError: The reviewer's binding does
                not match the independence the manifest claims.
        """
        if len(set(self.depths)) != len(self.depths):
            msg = f"depths repeat: {self.depths}"
            raise ValueError(msg)
        outside = [d for d in self.depths if not MIN_DEPTH <= d <= MAX_DEPTH]
        if outside:
            msg = f"depths outside {MIN_DEPTH}..{MAX_DEPTH}: {outside}"
            raise ValueError(msg)
        uncounted = [d for d in self.depths if self.repetitions.get(d, 0) < 1]
        if uncounted:
            msg = f"depths with no repetition count: {uncounted}"
            raise ValueError(msg)
        # Only the SWEPT depths are required to be priced, exactly as they are
        # the only ones required to be counted. An entry for a cap the matrix
        # does not sweep is inert, and that is load-bearing rather than lax:
        # `narrow` rewrites `depths` and leaves every mapping beside it alone,
        # so refusing the leftovers would refuse every staged run, which is how
        # a matrix this size is paid for at all.
        unpriced = [
            d for d in self.depths if self.expected_sessions_per_cell.get(d, 0) < 1
        ]
        self._validate_sizing_bounds()
        if unpriced:
            msg = (
                f"depths with no expected session cost: {unpriced}. A cap the "
                f"refusal check cannot price is one the sweep enters without "
                f"knowing whether it can finish it."
            )
            raise ValueError(msg)
        self._validate_independence()
        return self

    def _validate_independence(self) -> None:
        """Hold the declared independence class to the pairs themselves.

        Raises:
            RecursionDepthJudgeNotIndependentError: The pairs are identical, the
                families a decorrelation claim rests on are missing, or the
                declared class does not match them.
        """
        if self.reviewer == self.executor:
            msg = (
                f"the reviewer and the executor are both bound to "
                f"{self.executor.label!r}; a judge reviewing its own binding is "
                "self-preference at its maximum and biases the gated arm toward "
                "the null"
            )
            raise RecursionDepthJudgeNotIndependentError(msg)
        families = (self.executor.family, self.reviewer.family)
        if self.independence is Independence.SAME_FAMILY:
            if None not in families and self.executor.family != self.reviewer.family:
                msg = (
                    f"manifest declares {Independence.SAME_FAMILY.value} but the "
                    f"pairs name families {self.executor.family!r} and "
                    f"{self.reviewer.family!r}; declare "
                    f"{Independence.CROSS_FAMILY.value}, which is stronger, "
                    f"rather than stamping a caveat that is not true of the run"
                )
                raise RecursionDepthJudgeNotIndependentError(msg)
            return
        undeclared = [
            role
            for role, pair in (("executor", self.executor), ("reviewer", self.reviewer))
            if pair.family is None
        ]
        if undeclared:
            msg = (
                f"manifest declares {Independence.CROSS_FAMILY.value} but "
                f"{' and '.join(undeclared)} declares no family, so the claim "
                f"rests on nothing; name who trained each model, or declare "
                f"{Independence.SAME_FAMILY.value}, which claims less"
            )
            raise RecursionDepthJudgeNotIndependentError(msg)
        if self.reviewer.family == self.executor.family:
            msg = (
                f"manifest declares {Independence.CROSS_FAMILY.value} but both "
                f"pairs are family {self.executor.family!r}; a shared family is "
                f"the correlation the claim denies, whatever connection reaches "
                f"it"
            )
            raise RecursionDepthJudgeNotIndependentError(msg)

    def _validate_sizing_bounds(self) -> None:
        """Check every role's cap can hold its own base.

        A cap below its base means :func:`session_limits_for`'s
        ``min(base, cap)`` silently sizes every session of that role to the
        undersized cap instead, which nothing downstream would catch as a
        manifest mistake: the session's own budget enforcer would refuse it
        as an ordinary too-small ceiling at runtime, indistinguishable from
        the matrix legitimately needing less. Catching it here, as a load-time
        refusal of the manifest itself, says so before the sweep starts
        spending rather than at the first merge or review the enforcer stops.

        Raises:
            ValueError: A role's base sizing exceeds its own cap.
        """
        bounds = (
            (
                "merge_token_base",
                self.merge_token_base,
                "merge_token_cap",
                self.merge_token_cap,
            ),
            (
                "merge_max_turns_base",
                self.merge_max_turns_base,
                "merge_max_turns_cap",
                self.merge_max_turns_cap,
            ),
            (
                "review_token_base",
                self.review_token_base,
                "review_token_cap",
                self.review_token_cap,
            ),
            (
                "review_max_turns_base",
                self.review_max_turns_base,
                "review_max_turns_cap",
                self.review_max_turns_cap,
            ),
        )
        for base_name, base, cap_name, cap in bounds:
            if base > cap:
                msg = f"{base_name} ({base}) exceeds {cap_name} ({cap})"
                raise ValueError(msg)

    @property
    def planned_cells(self) -> int:
        """How many ``(depth, arm, repetition)`` cells the sweep records.

        Returns:
            The cell count.
        """
        return sum(self.repetitions[depth] for depth in self.depths) * len(self.arms)

    def caveat(self) -> str | None:
        """The independence caveat every artifact carries, when there is one.

        Returns:
            The caveat text, or ``None`` under cross-family independence.
        """
        if self.independence is Independence.CROSS_FAMILY:
            return None
        return SHARED_FAMILY_CAVEAT


def load_manifest(path: Path) -> RecursionDepthManifest:
    """Load and validate the recording matrix.

    ``spec_dir`` is resolved against the manifest's OWN directory and stored
    absolute, so every consumer reads the same specification wherever it was
    invoked from. Left relative it was a repository-root-relative path passed
    through unchanged, which resolved correctly only for a process whose
    working directory happened to be the repository root.

    Args:
        path: The manifest YAML.

    Returns:
        The validated manifest, with an absolute ``spec_dir``.
    """
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    manifest = RecursionDepthManifest.model_validate(payload)
    resolved = (path.resolve().parent / manifest.spec_dir).resolve()
    return RecursionDepthManifest.model_validate(
        manifest.model_dump() | {"spec_dir": str(resolved)}
    )


__all__ = [
    "MAX_DEPTH",
    "MIN_DEPTH",
    "Arm",
    "Independence",
    "ModelPair",
    "RecursionDepthManifest",
    "Role",
    "load_manifest",
]
