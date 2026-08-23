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
from synthorg.core.types import CapabilityLevel, NotBlankStr

#: The shallowest cap worth recording: one level of planning, every unit
#: dispatched whole, which is what the product did before recursion existed.
MIN_DEPTH: Final[int] = 1

#: The deepest cap the sweep records.
MAX_DEPTH: Final[int] = 6

#: An assembly costs a merge session and the review that follows it. Two in
#: BOTH arms by construction, since the ungated arm spends the identical budget
#: blindly so repair cannot win by spending more.
_SESSIONS_PER_ASSEMBLY: Final[int] = 2


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
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    provider: NotBlankStr
    model_id: NotBlankStr
    capability: CapabilityLevel
    family: NotBlankStr | None = None

    @property
    def label(self) -> str:
        """A one-line rendering for logs and artifacts.

        Returns:
            ``provider/model_id``.
        """
        return f"{self.provider}/{self.model_id}"

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
        return cls(
            provider=provider,
            model_id=model_id,
            capability=identity.model.capability,
            family=families.get((provider, model_id)),
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
        merge_attempts: How many attempts each merge gets, in BOTH arms. Equal
            by construction: repair only in the gated arm would let it win by
            spending more rather than by catching anything.
        unit_max_turns: The turn ceiling one unit's session gets.
        unit_cost_ceiling: What one unit's session may spend before the
            gateway's own hard kill stops it. Money only, so it is half a
            bound: see ``unit_token_ceiling``.
        unit_token_ceiling: The same bound counted in tokens, and the only one
            of the two that binds everywhere. A flat-rate connection
            attributes 0.0 to every call, so the cost ceiling cannot fire
            there and a runaway unit would be held by nothing but its turn
            cap. Required rather than optional, because the connection a
            manifest will be recorded against is not knowable here.
        max_sessions: The whole sweep's session ceiling. A depth sweep's
            session count is a product of branching factors nobody can predict
            from the manifest alone, and the cost of being wrong is spend.
        projected_branching: How many subtasks a planning session is assumed
            to produce, used ONLY to project the bill before a run and never
            by the run itself. Declared rather than inferred, and printed
            beside the figure it produces, because the projection is a model
            and a model whose assumption is hidden reads as a measurement.
            Deliberately rounded DOWN from what a real tree showed, so the
            number stays a floor: it is the input to choosing
            ``max_sessions``, and a floor that reads too high costs an
            operator nothing while one that reads too low kills a paid run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    spec_dir: NotBlankStr
    depths: tuple[int, ...] = Field(min_length=1)
    repetitions: dict[int, int]
    arms: tuple[Arm, ...] = Field(min_length=1)
    executor: ModelPair
    reviewer: ModelPair
    independence: Independence
    merge_attempts: int = Field(ge=1, le=10)
    unit_max_turns: int = Field(ge=1, le=200)
    unit_cost_ceiling: float = Field(gt=0.0)
    unit_token_ceiling: int = Field(gt=0)
    max_sessions: int = Field(ge=1)
    projected_branching: int = Field(ge=2, le=50)

    def projected_sessions(self, depth_cap: int) -> int:
        """How many sessions one cell at *depth_cap* is expected to cost.

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

        Returns:
            The projected session count for one cell.
        """
        branching = self.projected_branching
        # Annotated because ``int ** int`` widens to Any: the exponent could be
        # negative, and this one is bounded at one by the caller's own field.
        leaves: int = branching**depth_cap
        planned = (leaves - 1) // (branching - 1)
        return planned + leaves + _SESSIONS_PER_ASSEMBLY * planned

    @model_validator(mode="after")
    def _validate(self) -> Self:
        """Check the sweep is coherent and the judge is actually independent.

        Returns:
            ``self`` when the matrix is recordable.

        Raises:
            ValueError: A depth is outside the sweep's range, is repeated, or
                has no repetition count.
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
        return (
            "The reviewer and the executor share a model family, so judge "
            "independence here is by model rather than by family. Self-preference "
            "runs 75-84% toward a model's own family, which biases the gated arm "
            "toward the null: a gap in its favour survives this, a null result is "
            "not interpretable under it."
        )


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
    "load_manifest",
]
