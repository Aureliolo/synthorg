# module-kind: code
"""Domain models for the EVALUATE stage's verdict.

The stage answers one question: does the assembled deliverable meet the
objective's success criteria? The answer is per-criterion and evidenced, not a
single thumbs-up, because "it works" is exactly the claim that has been
unfalsifiable everywhere else in this loop.

Two invariants make the verdict load-bearing rather than decorative. Every
criterion is answered exactly once, so a criterion cannot be quietly dropped to
reach a pass. And ``PARTIAL`` is not a pass: an initiative is delivered when the
objective is met, not mostly met.
"""

import copy
from enum import StrEnum
from typing import Final, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    computed_field,
    model_validator,
)

from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import InitiativeEvaluationParseError
from synthorg.providers.models import ToolDefinition

#: Defence in depth against a runaway or adversarial submission. An objective
#: with more criteria than this is not something a single verdict can settle.
_MAX_VERDICTS: Final[int] = 100


class CriterionOutcome(StrEnum):
    """Whether one success criterion is met by the delivered whole.

    ``MET``: the criterion holds, with evidence. ``PARTIAL``: partly holds, so
    the objective is not delivered. ``UNMET``: does not hold.
    """

    MET = "met"
    PARTIAL = "partial"
    UNMET = "unmet"


class CriterionVerdict(BaseModel):
    """One criterion's verdict with the evidence behind it.

    Attributes:
        criterion: The objective criterion being judged, quoted back so the
            report is readable without cross-referencing the plan.
        outcome: Whether it is met, partly met, or unmet.
        evidence: What was observed that supports the outcome. Required
            regardless of outcome: an unevidenced pass is a guess, and an
            unevidenced failure gives the replan nothing to work from.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    criterion: NotBlankStr = Field(description="The objective criterion judged")
    outcome: CriterionOutcome = Field(description="Met, partial, or unmet")
    evidence: NotBlankStr = Field(description="What was observed")


class EvaluationReport(BaseModel):
    """The evaluate stage's verdict on a delivered initiative.

    Attributes:
        summary: A short narrative of what was evaluated and how.
        verdicts: One verdict per objective criterion.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    summary: NotBlankStr = Field(description="Short evaluation narrative")
    verdicts: tuple[CriterionVerdict, ...] = Field(
        min_length=1,
        max_length=_MAX_VERDICTS,
        description="One verdict per objective criterion",
    )

    @computed_field
    @property
    def objective_met(self) -> bool:
        """Whether the delivered whole meets the objective.

        Returns:
            ``True`` only when every criterion is MET. PARTIAL does not
            deliver: the gap is what the replan is for.
        """
        return all(v.outcome is CriterionOutcome.MET for v in self.verdicts)

    @model_validator(mode="after")
    def _validate_unique_criteria(self) -> Self:
        """Reject a report that judges one criterion twice.

        Returns:
            The validated model.

        Raises:
            ValueError: When a criterion appears more than once.
        """
        seen = {v.criterion for v in self.verdicts}
        if len(seen) != len(self.verdicts):
            msg = "Each criterion must be judged exactly once"
            raise ValueError(msg)
        return self


#: JSON schema for the ``submit_evaluation`` terminal tool, kept as a module
#: constant (deep-copied per build) so the literal does not push the builder
#: past the function-length guideline.
_SUBMIT_EVALUATION_SCHEMA: Final[dict[str, JsonValue]] = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": (
                "A short, honest account of what you ran, read, or observed to "
                "reach these verdicts."
            ),
        },
        "verdicts": {
            "type": "array",
            "description": (
                "One entry per success criterion, quoting the criterion "
                "verbatim. Judge every criterion; do not skip one you could "
                "not check, mark it unmet and say so in the evidence."
            ),
            "items": {
                "type": "object",
                "properties": {
                    "criterion": {"type": "string"},
                    "outcome": {
                        "type": "string",
                        "enum": ["met", "partial", "unmet"],
                    },
                    "evidence": {"type": "string"},
                },
                "required": ["criterion", "outcome", "evidence"],
            },
        },
    },
    "required": ["summary", "verdicts"],
}


def build_evaluation_tool() -> ToolDefinition:
    """Build the terminal ``submit_evaluation`` tool definition.

    Returns:
        A ``ToolDefinition`` whose schema captures the summary and the
        per-criterion verdicts.
    """
    return ToolDefinition(
        name="submit_evaluation",
        description=(
            "Submit your verdict on the delivered initiative exactly once, "
            "last, after you have checked the deliverable against every "
            "success criterion."
        ),
        parameters_schema=copy.deepcopy(_SUBMIT_EVALUATION_SCHEMA),
    )


def args_to_evaluation(
    args: dict[str, JsonValue],
    *,
    criteria: tuple[NotBlankStr, ...],
) -> EvaluationReport:
    """Parse ``submit_evaluation`` arguments into a validated report.

    The report must cover *criteria* exactly: every one judged, and nothing
    invented. A partial submission is rejected rather than accepted with the
    unanswered criteria silently treated as met.

    Args:
        args: The tool-call arguments.
        criteria: The objective's success criteria the report must cover.

    Returns:
        The parsed :class:`EvaluationReport`.

    Raises:
        InitiativeEvaluationParseError: If the arguments are structurally
            invalid or do not cover the criteria exactly.
    """
    try:
        report = EvaluationReport(
            summary=NotBlankStr(_require_str(args, "summary")),
            verdicts=tuple(
                CriterionVerdict(
                    criterion=NotBlankStr(_require_str(item, "criterion")),
                    outcome=CriterionOutcome(_require_str(item, "outcome")),
                    evidence=NotBlankStr(_require_str(item, "evidence")),
                )
                for item in _as_items(args.get("verdicts"))
            ),
        )
    except (ValueError, TypeError, KeyError) as exc:
        msg = f"Invalid evaluation submission: {exc}"
        raise InitiativeEvaluationParseError(msg) from exc
    _require_full_coverage(report, criteria)
    return report


def _require_full_coverage(
    report: EvaluationReport,
    criteria: tuple[NotBlankStr, ...],
) -> None:
    """Reject a report that does not judge exactly the objective's criteria.

    Raises:
        InitiativeEvaluationParseError: When a criterion is unjudged or a
            verdict names something that is not a criterion.
    """
    judged = {v.criterion for v in report.verdicts}
    expected = set(criteria)
    missing = sorted(expected - judged)
    if missing:
        msg = f"Evaluation does not judge these criteria: {missing}"
        raise InitiativeEvaluationParseError(msg)
    invented = sorted(judged - expected)
    if invented:
        msg = f"Evaluation judges criteria the objective does not have: {invented}"
        raise InitiativeEvaluationParseError(msg)


def _as_items(value: JsonValue | None) -> tuple[dict[str, JsonValue], ...]:
    """Coerce an optional JSON array of objects into a tuple of dicts.

    Returns:
        The array's object elements; empty when *value* is absent.

    Raises:
        TypeError: If *value* is present but not an array of objects.
    """
    if value is None:
        return ()
    if not isinstance(value, list):
        msg = "expected an array"
        raise TypeError(msg)
    items: list[dict[str, JsonValue]] = []
    for element in value:
        if not isinstance(element, dict):
            msg = "expected an array of objects"
            raise TypeError(msg)
        items.append(element)
    return tuple(items)


def _require_str(source: dict[str, JsonValue], key: str) -> str:
    """Return a non-blank string field from *source*.

    Returns:
        The string value.

    Raises:
        KeyError: If the key is absent.
        ValueError: If the value is not a non-blank string.
    """
    value = source[key]
    if not isinstance(value, str) or not value.strip():
        msg = f"{key!r} must be a non-blank string"
        raise ValueError(msg)
    return value
