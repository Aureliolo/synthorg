"""Brief data model.

A brief is one item in the exam suite. Briefs are authored as YAML files
under ``evals/briefs/<id>.yaml`` and loaded into frozen Pydantic models
at the file boundary via :func:`evals.loader.briefs.load_brief_suite`.

Two kinds:

* ``executable`` -- has hidden acceptance tests + build + lint commands;
  the run is graded binary-deterministically by command exit codes.
* ``judged`` -- has a weighted rubric and a reference answer; the run
  is graded by a calibrated LLM judge against a hand-scored anchor set
  (see :mod:`evals.scoring.judged`).
"""

from enum import StrEnum
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type

# Shell metacharacters refused at the leading argv token in a hidden
# check command. The grader runs commands with ``shell=False`` so a
# leaked metachar should be inert; this list is a defence-in-depth
# load-time check so a typo in a brief YAML surfaces immediately.
_SHELL_METACHARS: Final[tuple[str, ...]] = (
    ";",
    "|",
    "&",
    ">",
    "<",
    "$",
    "`",
    "\n",
    "\r",
)

# Lower bounds for brief schema validation. Allowlisted module-level
# annotated constants per project conventions; named here so the YAML
# loader's invariant errors quote the same numbers as the schema doc.
MIN_ACCEPTANCE_CRITERIA: Final[int] = 1
MIN_COMPLEXITY: Final[int] = 1
MAX_COMPLEXITY: Final[int] = 5

# Floating-point tolerance for rubric weight-sum validation. The check
# accepts any sum within +/- this margin of 1.0; tighter than this is
# noise from YAML float parsing, looser invites authoring drift where
# weights silently fail to sum to a meaningful total.
RUBRIC_WEIGHT_TOLERANCE: Final[float] = 0.001
RUBRIC_WEIGHT_TARGET: Final[float] = 1.0

# Default wall-clock timeout for a single hidden-check subprocess. Brief
# authors override per-check via the YAML schema; named here so the
# allowlisted no-magic-numbers gate passes and authors have a single
# place to tune the suite-wide default.
DEFAULT_HIDDEN_CHECK_TIMEOUT_SECONDS: Final[int] = 30


class BriefKind(StrEnum):
    """Discriminator between executable and judged briefs."""

    EXECUTABLE = "executable"
    JUDGED = "judged"


class BriefPriority(StrEnum):
    """Operator-facing priority hint propagated onto the TaskRequirement."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ArtifactSpec(BaseModel):
    """An artifact the brief expects the company to produce."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    kind: Literal["file", "dir", "report", "diff"]
    path: NotBlankStr


class LimitsSpec(BaseModel):
    """Per-brief budget and time limits.

    These bound the run; over-budget / over-time events come back as
    process-fact penalties via :mod:`evals.scoring.penalties`. The
    runner also enforces a wall-clock safety stop at
    ``max_wall_clock_seconds * SAFETY_FACTOR`` (see
    :mod:`evals.runner.orchestrator`) so a misbehaving brief cannot
    hang the suite.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    max_total_cost_usd: float = Field(gt=0.0)
    max_wall_clock_seconds: int = Field(gt=0)
    max_turns: int = Field(gt=0)


class HiddenCheckSpec(BaseModel):
    """One subprocess invocation in an executable brief's hidden checks.

    ``cmd`` is a tuple of argv tokens (never a shell string) so the
    grader can run it without ``shell=True``; the first token must be
    a path or known binary, no shell metacharacters allowed. The
    model validator below enforces that the leading token does not
    carry metachars even though ``shell=False`` should already make a
    leaked metachar inert -- defence in depth so a typo in a brief
    YAML fails at load time, not at run time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    cmd: tuple[NotBlankStr, ...] = Field(min_length=1)
    timeout_seconds: int = Field(default=DEFAULT_HIDDEN_CHECK_TIMEOUT_SECONDS, gt=0)

    @model_validator(mode="after")
    def _leading_token_has_no_shell_metachars(self) -> Self:
        """Refuse a leading argv token carrying any shell metacharacter."""
        leading = self.cmd[0]
        for ch in _SHELL_METACHARS:
            if ch in leading:
                msg = (
                    f"HiddenCheckSpec.cmd[0]={leading!r} contains "
                    f"shell metacharacter {ch!r}; declare an argv token, "
                    "not a shell fragment"
                )
                raise ValueError(msg)
        return self


class ExecutableChecks(BaseModel):
    """Hidden acceptance tests, build, and lint commands for an executable brief."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    hidden_tests: tuple[HiddenCheckSpec, ...] = Field(default=())
    build: tuple[HiddenCheckSpec, ...] = Field(default=())
    lint: tuple[HiddenCheckSpec, ...] = Field(default=())


class RubricGradeType(StrEnum):
    """Per-dimension grading scale.

    ``binary`` accepts {0.0, 1.0}; ``ternary`` accepts {0.0, 0.5, 1.0};
    ``score`` accepts any value in the closed interval [0.0, 1.0]. The
    scale is enforced at scoring time, not load time, so an authoring
    typo (e.g. binary with a 0.5 anchor) is caught when the judged
    grader resolves anchors rather than only at the boundary.
    """

    BINARY = "binary"
    TERNARY = "ternary"
    SCORE = "score"


class RubricDimension(BaseModel):
    """One weighted dimension within a judged rubric."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: NotBlankStr
    weight: float = Field(gt=0.0, le=1.0)
    grade_type: RubricGradeType


class JudgedRubric(BaseModel):
    """Calibrated-judge rubric for a non-executable brief."""

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    rubric_id: NotBlankStr
    dimensions: tuple[RubricDimension, ...] = Field(min_length=1)
    reference_answer_path: NotBlankStr

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> Self:
        """Reject rubrics whose dimension weights do not sum to 1.0."""
        total = sum(d.weight for d in self.dimensions)
        if abs(total - RUBRIC_WEIGHT_TARGET) > RUBRIC_WEIGHT_TOLERANCE:
            msg = (
                f"Rubric {self.rubric_id!r}: dimension weights must sum to "
                f"{RUBRIC_WEIGHT_TARGET} (got {total:.4f}, tolerance "
                f"{RUBRIC_WEIGHT_TOLERANCE})"
            )
            raise ValueError(msg)
        return self


class Brief(BaseModel):
    """One exam item.

    The ``kind`` discriminator selects which of ``checks`` / ``rubric``
    is populated; the model validator enforces the XOR invariant so a
    brief cannot ship with both lanes filled or neither.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    brief_id: NotBlankStr
    schema_version: Literal[1]
    kind: BriefKind
    title: NotBlankStr
    description: NotBlankStr
    priority: BriefPriority = BriefPriority.MEDIUM
    estimated_complexity: int = Field(
        ge=MIN_COMPLEXITY,
        le=MAX_COMPLEXITY,
    )
    expected_artifacts: tuple[ArtifactSpec, ...] = Field(default=())
    acceptance_criteria: tuple[NotBlankStr, ...] = Field(
        min_length=MIN_ACCEPTANCE_CRITERIA,
    )
    limits: LimitsSpec
    checks: ExecutableChecks | None = None
    rubric: JudgedRubric | None = None

    @model_validator(mode="after")
    def _kind_matches_payload(self) -> Self:
        """Enforce kind / (checks XOR rubric) consistency."""
        if self.kind is BriefKind.EXECUTABLE:
            if self.checks is None:
                msg = (
                    f"Brief {self.brief_id!r}: kind={self.kind.value} "
                    "requires a 'checks' block"
                )
                raise ValueError(msg)
            if self.rubric is not None:
                msg = (
                    f"Brief {self.brief_id!r}: kind={self.kind.value} "
                    "must not carry a 'rubric' block"
                )
                raise ValueError(msg)
        else:
            if self.rubric is None:
                msg = (
                    f"Brief {self.brief_id!r}: kind={self.kind.value} "
                    "requires a 'rubric' block"
                )
                raise ValueError(msg)
            if self.checks is not None:
                msg = (
                    f"Brief {self.brief_id!r}: kind={self.kind.value} "
                    "must not carry a 'checks' block"
                )
                raise ValueError(msg)
        return self


__all__ = [
    "DEFAULT_HIDDEN_CHECK_TIMEOUT_SECONDS",
    "MAX_COMPLEXITY",
    "MIN_ACCEPTANCE_CRITERIA",
    "MIN_COMPLEXITY",
    "RUBRIC_WEIGHT_TARGET",
    "RUBRIC_WEIGHT_TOLERANCE",
    "ArtifactSpec",
    "Brief",
    "BriefKind",
    "BriefPriority",
    "ExecutableChecks",
    "HiddenCheckSpec",
    "JudgedRubric",
    "LimitsSpec",
    "RubricDimension",
    "RubricGradeType",
]
