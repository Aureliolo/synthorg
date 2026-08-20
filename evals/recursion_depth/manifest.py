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

And the repetitions are per depth rather than uniform. Depths 1 and 2 are
expected flat and are cheap; the transition ARIES reports sits at 3 to 4, which
is where samples are worth paying for.
"""

from enum import StrEnum
from pathlib import Path
from typing import Final, Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from evals.errors import RecursionDepthJudgeNotIndependentError
from synthorg.core.types import NotBlankStr

#: The shallowest cap worth recording: one level of planning, every unit
#: dispatched whole, which is what the product did before recursion existed.
MIN_DEPTH: Final[int] = 1

#: The deepest cap the sweep records.
MAX_DEPTH: Final[int] = 6


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
        CROSS_FAMILY: Different providers. What a headline number requires:
            decorrelating model family is the only lever the blind-spot
            literature finds effective.
        SAME_PROVIDER: Different model on the same provider. Permitted, and
            stamped on every artifact, because it biases toward the null: a
            positive result survives it, a null result is not interpretable
            under it.
    """

    CROSS_FAMILY = "cross_family"
    SAME_PROVIDER = "same_provider"


class ModelPair(BaseModel):
    """An explicit ``(provider, model)`` binding.

    Attributes:
        provider: The registered connection dispatch goes through.
        model_id: The model that connection is asked for.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    provider: NotBlankStr
    model_id: NotBlankStr

    @property
    def label(self) -> str:
        """A one-line rendering for logs and artifacts.

        Returns:
            ``provider/model_id``.
        """
        return f"{self.provider}/{self.model_id}"


class RecursionDepthManifest(BaseModel):
    """The whole recording matrix.

    Attributes:
        spec_dir: The specification the sweep is run against.
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
            gateway's own hard kill stops it.
        max_sessions: The whole sweep's session ceiling. A depth sweep's
            session count is a product of branching factors nobody can predict
            from the manifest alone, and the cost of being wrong is spend.
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
    max_sessions: int = Field(ge=1)

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
            RecursionDepthJudgeNotIndependentError: The pairs are identical, or
                they do not match the declared class.
        """
        if self.reviewer == self.executor:
            msg = (
                f"the reviewer and the executor are both bound to "
                f"{self.executor.label!r}; a judge reviewing its own binding is "
                "self-preference at its maximum and biases the gated arm toward "
                "the null"
            )
            raise RecursionDepthJudgeNotIndependentError(msg)
        same_provider = self.reviewer.provider == self.executor.provider
        if self.independence is Independence.CROSS_FAMILY and same_provider:
            msg = (
                f"manifest declares {Independence.CROSS_FAMILY.value} but both "
                f"pairs use provider {self.executor.provider!r}"
            )
            raise RecursionDepthJudgeNotIndependentError(msg)
        if self.independence is Independence.SAME_PROVIDER and not same_provider:
            msg = (
                f"manifest declares {Independence.SAME_PROVIDER.value} but the "
                f"pairs use different providers ({self.executor.provider!r} and "
                f"{self.reviewer.provider!r}); declare "
                f"{Independence.CROSS_FAMILY.value}, which is stronger"
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
            "The reviewer and the executor share a provider, so judge "
            "independence here is by model rather than by family. Self-preference "
            "runs 75-84% toward a model's own family, which biases the gated arm "
            "toward the null: a gap in its favour survives this, a null result is "
            "not interpretable under it."
        )


def load_manifest(path: Path) -> RecursionDepthManifest:
    """Load and validate the recording matrix.

    Args:
        path: The manifest YAML.

    Returns:
        The validated manifest.
    """
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    return RecursionDepthManifest.model_validate(payload)


__all__ = [
    "MAX_DEPTH",
    "MIN_DEPTH",
    "Arm",
    "Independence",
    "ModelPair",
    "RecursionDepthManifest",
    "load_manifest",
]
