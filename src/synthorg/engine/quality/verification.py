"""Verification stage models: rubric, criteria, grading results.

Provides the data models for calibrated rubric grading with atomic
criteria decomposition.  Evaluator agents use these to produce
structured verdicts (pass/fail/refer) over handoff artifacts.
"""

import copy
import math
from collections.abc import Mapping
from datetime import datetime
from enum import StrEnum
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from synthorg.core.types import NotBlankStr


class VerificationVerdict(StrEnum):
    """Outcome of a verification stage evaluation."""

    PASS = "pass"  # noqa: S105
    FAIL = "fail"
    REFER = "refer"


class GradeType(StrEnum):
    """Grading scale for a rubric criterion."""

    BINARY = "binary"
    TERNARY = "ternary"
    SCORE = "score"


class RubricCriterion(BaseModel):
    """A single grading criterion within a verification rubric.

    Attributes:
        name: Machine-readable criterion identifier.
        description: Human-readable description of what is evaluated.
        weight: Relative weight in [0, 1] (all weights in a rubric
            must sum to 1.0).
        grade_type: Grading scale for this criterion.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Criterion identifier")
    description: NotBlankStr = Field(description="What is evaluated")
    weight: float = Field(ge=0.0, le=1.0, description="Relative weight")
    grade_type: GradeType = Field(description="Grading scale")


class CalibrationExample(BaseModel):
    """A few-shot calibration example for rubric grading.

    Attributes:
        artifact_summary: Condensed representation of the artifact.
        expected_verdict: The correct verdict for this example.
        rationale: Explanation of why this verdict is correct.
        expected_grades: Optional per-criterion expected grades.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    artifact_summary: NotBlankStr = Field(
        description="Condensed artifact representation",
    )
    expected_verdict: VerificationVerdict = Field(
        description="Correct verdict for calibration",
    )
    rationale: NotBlankStr = Field(
        description="Why this verdict is correct",
    )
    expected_grades: Mapping[NotBlankStr, float] | None = Field(
        default=None,
        description="Optional per-criterion expected grades",
    )

    @field_validator("expected_grades", mode="before")
    @classmethod
    def _deepcopy_expected_grades(cls, v: object) -> object:
        """Deep-copy to prevent external mutation.

        Returns:
            A deep copy of ``v`` when it is a mapping; otherwise ``v``
            unchanged for downstream validation to reject.
        """
        if isinstance(v, Mapping):
            return copy.deepcopy(v)
        return v

    @model_validator(mode="after")
    def _validate_expected_grades(self) -> Self:
        """Validate expected grade values are finite and in [0, 1].

        Returns:
            ``self`` unchanged when every expected grade is finite
            and within [0, 1].

        Raises:
            ValueError: When any expected grade is non-finite or
                outside the [0, 1] range.
        """
        if self.expected_grades is None:
            return self
        for name, grade in self.expected_grades.items():
            if math.isnan(grade) or math.isinf(grade):
                msg = f"Expected grade for {name!r} must be finite"
                raise ValueError(msg)
            if not (0.0 <= grade <= 1.0):
                msg = f"Expected grade for {name!r} must be in [0, 1], got {grade}"
                raise ValueError(msg)
        return self


class VerificationRubric(BaseModel):
    """A calibrated rubric for verification stage grading.

    Criteria weights must sum to 1.0 (within tolerance).

    Attributes:
        name: Rubric identifier used in blueprint config.
        criteria: Grading criteria (non-empty, weights sum to 1.0).
        calibration_examples: Few-shot examples for LLM grader.
        min_confidence: Minimum confidence threshold; below this
            the verdict is overridden to REFER.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Rubric identifier")
    criteria: tuple[RubricCriterion, ...] = Field(
        description="Grading criteria",
    )
    calibration_examples: tuple[CalibrationExample, ...] = Field(
        default=(),
        description="Few-shot calibration examples",
    )
    min_confidence: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Minimum confidence for non-REFER verdict",
    )
    grading_style: Literal["absolute", "relative", "pairwise"] = Field(
        default="absolute",
        description="Grading style (absolute/relative/pairwise)",
    )

    @model_validator(mode="after")
    def _validate_grading_style(self) -> Self:
        """Reject non-absolute grading styles until implemented.

        Returns:
            ``self`` unchanged when ``grading_style`` is ``"absolute"``.

        Raises:
            ValueError: When ``grading_style`` is any value other than
                ``"absolute"``.
        """
        if self.grading_style != "absolute":
            msg = (
                f"Grading style {self.grading_style!r} is not yet "
                f"implemented; only 'absolute' is supported"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_criteria(self) -> Self:
        """Require non-empty criteria whose weights sum to 1.0.

        Returns:
            ``self`` unchanged when the criteria collection is valid.

        Raises:
            ValueError: When the criteria tuple is empty, weights do
                not sum to 1.0, criterion names duplicate, or a
                calibration example references an unknown criterion.
        """
        if not self.criteria:
            msg = "Rubric must have at least one criterion"
            raise ValueError(msg)
        total = sum(c.weight for c in self.criteria)
        if not math.isclose(total, 1.0, abs_tol=1e-6):
            msg = f"Criteria weights must sum to 1.0, got {total}"
            raise ValueError(msg)
        names = tuple(c.name for c in self.criteria)
        if len(names) != len(set(names)):
            msg = "Duplicate criterion names"
            raise ValueError(msg)
        allowed = set(names)
        for ex in self.calibration_examples:
            if ex.expected_grades is not None:
                unknown = set(ex.expected_grades.keys()) - allowed
                if unknown:
                    msg = (
                        f"Calibration example references unknown "
                        f"criteria: {sorted(unknown)}"
                    )
                    raise ValueError(msg)
        return self


class AtomicProbe(BaseModel):
    """An atomic binary probe decomposed from acceptance criteria.

    Each probe asks a single yes/no question derived from a
    higher-level acceptance criterion.

    Attributes:
        id: Unique probe identifier.
        probe_text: Binary yes/no question text.
        source_criterion: The original acceptance criterion text
            this probe was derived from.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Probe identifier")
    probe_text: NotBlankStr = Field(description="Binary yes/no question")
    source_criterion: NotBlankStr = Field(
        description="Original acceptance criterion text",
    )


class VerificationResult(BaseModel):
    """Structured result of a verification stage evaluation.

    The ``evaluator_agent_id`` must differ from ``generator_agent_id``
    to enforce the self-evaluation rejection constraint.

    Attributes:
        verdict: Overall pass/fail/refer outcome.
        confidence: Grader's self-reported confidence in [0, 1].
        per_criterion_grades: Criterion name to grade mapping.
        findings: Human-readable feedback items.
        evaluator_agent_id: Agent that performed the evaluation.
        generator_agent_id: Agent that produced the artifact.
        rubric_name: Rubric used for grading.
        timestamp: When the evaluation was performed.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    verdict: VerificationVerdict = Field(description="Overall outcome")
    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Grader confidence",
    )
    per_criterion_grades: Mapping[NotBlankStr, float] = Field(
        description="Criterion name to grade",
    )

    @field_validator("per_criterion_grades", mode="before")
    @classmethod
    def _deepcopy_grades(cls, v: object) -> object:
        """Deep-copy to prevent external mutation.

        Returns:
            A deep copy of ``v`` when it is a mapping; otherwise ``v``
            unchanged for downstream validation.
        """
        if isinstance(v, Mapping):
            return copy.deepcopy(v)
        return v

    findings: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Human-readable feedback",
    )
    evaluator_agent_id: NotBlankStr = Field(
        description="Evaluator agent identifier",
    )
    generator_agent_id: NotBlankStr = Field(
        description="Generator agent identifier",
    )
    rubric_name: NotBlankStr = Field(description="Rubric used")
    timestamp: datetime = Field(description="Evaluation timestamp")

    @computed_field
    @property
    def passed(self) -> bool:
        """Whether the verification passed."""
        return self.verdict == VerificationVerdict.PASS

    @model_validator(mode="after")
    def _validate_grades_and_agents(self) -> Self:
        """Validate grade values and reject self-evaluation.

        Returns:
            ``self`` unchanged when grades are finite, in [0, 1],
            and evaluator and generator differ.

        Raises:
            ValueError: When any grade is non-finite or out of range,
                or when ``evaluator_agent_id == generator_agent_id``.
        """
        for name, grade in self.per_criterion_grades.items():
            if math.isnan(grade) or math.isinf(grade):
                msg = f"Grade for {name!r} must be finite"
                raise ValueError(msg)
            if not (0.0 <= grade <= 1.0):
                msg = f"Grade for {name!r} must be in [0, 1], got {grade}"
                raise ValueError(msg)
        if self.evaluator_agent_id == self.generator_agent_id:
            msg = (
                "Self-evaluation rejected: evaluator_agent_id must "
                "differ from generator_agent_id"
            )
            raise ValueError(msg)
        return self


_DESIGN_CRITERION = RubricCriterion(
    name="design",
    description="Visual design quality, layout, and aesthetics",
    weight=0.25,
    grade_type=GradeType.SCORE,
)
_ORIGINALITY_CRITERION = RubricCriterion(
    name="originality",
    description="Creative originality and avoidance of generic patterns",
    weight=0.25,
    grade_type=GradeType.SCORE,
)
_CRAFT_CRITERION = RubricCriterion(
    name="craft",
    description="Implementation craft, code quality, and attention to detail",
    weight=0.25,
    grade_type=GradeType.SCORE,
)
_FUNCTIONALITY_CRITERION = RubricCriterion(
    name="functionality",
    description="Functional correctness and completeness",
    weight=0.25,
    grade_type=GradeType.SCORE,
)

FRONTEND_DESIGN_RUBRIC = VerificationRubric(
    name="frontend-design",
    criteria=(
        _DESIGN_CRITERION,
        _ORIGINALITY_CRITERION,
        _CRAFT_CRITERION,
        _FUNCTIONALITY_CRITERION,
    ),
    calibration_examples=(),
    min_confidence=0.7,
)
"""Default four-criterion rubric for frontend design verification."""
