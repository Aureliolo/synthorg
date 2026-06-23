"""Frozen registry of built-in verification rubrics."""

from types import MappingProxyType
from typing import Final

from synthorg.core.types import NotBlankStr
from synthorg.engine.quality.verification import (
    FRONTEND_DESIGN_RUBRIC,
    GradeType,
    RubricCriterion,
    VerificationRubric,
)
from synthorg.observability import get_logger
from synthorg.observability.events.verification import (
    VERIFICATION_RUBRIC_NOT_FOUND,
)

logger = get_logger(__name__)

# Default-task criterion weights (sum to 1.0); named so the scoring split
# is explicit and editable in one place.
_DEFAULT_CORRECTNESS_WEIGHT: Final[float] = 0.4
_DEFAULT_COMPLETENESS_WEIGHT: Final[float] = 0.35
_DEFAULT_PROBE_ADHERENCE_WEIGHT: Final[float] = 0.25
_DEFAULT_MIN_CONFIDENCE: Final[float] = 0.7

_DEFAULT_TASK_RUBRIC = VerificationRubric(
    name="default-task",
    criteria=(
        RubricCriterion(
            name="correctness",
            description="Output is factually and logically correct",
            weight=_DEFAULT_CORRECTNESS_WEIGHT,
            grade_type=GradeType.SCORE,
        ),
        RubricCriterion(
            name="completeness",
            description="All acceptance criteria are addressed",
            weight=_DEFAULT_COMPLETENESS_WEIGHT,
            grade_type=GradeType.SCORE,
        ),
        RubricCriterion(
            name="probe-adherence",
            description="Adherence to atomic acceptance probes",
            weight=_DEFAULT_PROBE_ADHERENCE_WEIGHT,
            grade_type=GradeType.BINARY,
        ),
    ),
    calibration_examples=(),
    min_confidence=_DEFAULT_MIN_CONFIDENCE,
)

BUILTIN_RUBRICS: MappingProxyType[str, VerificationRubric] = MappingProxyType(
    {
        FRONTEND_DESIGN_RUBRIC.name: FRONTEND_DESIGN_RUBRIC,
        _DEFAULT_TASK_RUBRIC.name: _DEFAULT_TASK_RUBRIC,
    }
)
"""Immutable registry of built-in rubrics keyed by name."""


def get_rubric(name: NotBlankStr) -> VerificationRubric:
    """Look up a rubric by name.

    Args:
        name: Rubric identifier.

    Returns:
        The matching rubric.

    Raises:
        KeyError: If no rubric with that name exists.
    """
    try:
        return BUILTIN_RUBRICS[name]
    except KeyError:
        available = sorted(BUILTIN_RUBRICS.keys())
        logger.warning(
            VERIFICATION_RUBRIC_NOT_FOUND,
            rubric_name=name,
            available=available,
        )
        msg = f"Unknown rubric {name!r}, available: {available}"
        raise KeyError(msg) from None
