"""Response parsers for the LLM rubric grader.

Pure validators for the grader tool-call arguments: per-criterion
grades, verdict, confidence, and findings. Each returns the parsed
value on success or a human-readable reason string on failure so the
grader can fail closed to ``REFER``.
"""

import math
from collections.abc import Mapping

from synthorg.engine.quality.verification import (
    VerificationRubric,
    VerificationVerdict,
)


def parse_grades(
    raw: object,
    *,
    rubric: VerificationRubric,
) -> dict[str, float] | str:
    """Validate the per-criterion grades mapping.

    Returns:
        A ``{criterion_name: grade}`` dict on success, or a reason
        string describing the malformed entry on failure.
    """
    if not isinstance(raw, Mapping):
        return "per_criterion_grades is not an object"
    expected = {c.name for c in rubric.criteria}
    grades: dict[str, float] = {}
    for name, value in raw.items():
        if name not in expected:
            return f"unknown criterion {name!r}"
        parsed = _parse_unit_interval(value)
        if isinstance(parsed, str):
            return f"grade for {name!r}: {parsed}"
        grades[name] = parsed
    missing = expected - set(grades)
    if missing:
        return f"missing grades for criteria: {sorted(missing)}"
    return grades


def parse_verdict(raw: object) -> VerificationVerdict | str:
    """Coerce the verdict string into a ``VerificationVerdict``.

    Returns:
        The matching :class:`VerificationVerdict` enum member, or a
        reason string if ``raw`` is not a known verdict name.
    """
    if not isinstance(raw, str):
        return "verdict is not a string"
    try:
        return VerificationVerdict(raw)
    except ValueError:
        return f"unknown verdict {raw!r}"


def parse_confidence(raw: object) -> float | str:
    """Validate confidence is a finite float in [0, 1].

    Returns:
        The validated float, or a reason string when ``raw`` is
        non-numeric, non-finite, or out of range.
    """
    return _parse_unit_interval(raw, label="confidence")


def parse_findings(raw: object) -> tuple[str, ...] | str:
    """Validate findings is a list of non-blank strings.

    Fails closed: any non-string entry or blank string surfaces a
    descriptive error so callers route the whole response to ``REFER``
    rather than silently discarding malformed items and acting on the
    residual.

    Returns:
        A tuple of the stripped findings on success, or a reason string
        when the input is not a list of non-blank strings.
    """
    if not isinstance(raw, list):
        return "findings is not a list"
    findings: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str):
            return f"findings[{index}] is not a string"
        if not item.strip():
            return f"findings[{index}] is blank"
        findings.append(item.strip())
    return tuple(findings)


def _parse_unit_interval(value: object, *, label: str = "value") -> float | str:
    """Return *value* as a finite float in [0, 1] or a reason string."""
    if not isinstance(value, int | float) or isinstance(value, bool):
        return f"{label} is not numeric"
    parsed = float(value)
    if math.isnan(parsed) or math.isinf(parsed):
        return f"{label} is not finite"
    if not (0.0 <= parsed <= 1.0):
        return f"{label} out of [0, 1]"
    return parsed
