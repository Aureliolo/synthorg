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
from typing import Final, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    computed_field,
    model_validator,
)

from synthorg.core.evaluation_verdict import (
    MAX_VERDICT_TEXT_LENGTH,
    CriterionOutcome,
    CriterionVerdict,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import InitiativeEvaluationParseError
from synthorg.providers.models import ToolDefinition

#: Defence in depth against a runaway or adversarial submission. An objective
#: with more criteria than this is not something a single verdict can settle.
MAX_VERDICTS: Final[int] = 100


class EvaluationReport(BaseModel):
    """The evaluate stage's verdict on a delivered initiative.

    Attributes:
        summary: A short narrative of what was evaluated and how.
        verdicts: One verdict per objective criterion.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    summary: NotBlankStr = Field(
        max_length=MAX_VERDICT_TEXT_LENGTH,
        description="Short evaluation narrative",
    )
    verdicts: tuple[CriterionVerdict, ...] = Field(
        min_length=1,
        max_length=MAX_VERDICTS,
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

    @classmethod
    def covering(
        cls,
        *,
        summary: NotBlankStr,
        verdicts: tuple[CriterionVerdict, ...],
        criteria: tuple[NotBlankStr, ...],
    ) -> Self:
        """Build a report and check it judges exactly *criteria*.

        The coverage rule is the module's headline invariant, and the model
        alone cannot enforce it: it has no way to know what the objective
        actually asked for. Binding it to a factory is what makes "a criterion
        cannot be quietly dropped to reach a pass" one rule with one entry
        point rather than a check any future call site could forget.

        Returns:
            The validated report.

        Raises:
            InitiativeEvaluationParseError: When a criterion is unjudged or a
                verdict names something the objective does not have.
        """
        report = cls(summary=summary, verdicts=verdicts)
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
        return report

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
        return EvaluationReport.covering(
            summary=NotBlankStr(_require_str(args, "summary")),
            verdicts=tuple(
                CriterionVerdict(
                    criterion=NotBlankStr(_require_str(item, "criterion")),
                    outcome=CriterionOutcome(_require_str(item, "outcome")),
                    evidence=NotBlankStr(_require_str(item, "evidence")),
                )
                for item in _as_items(args.get("verdicts"))
            ),
            criteria=criteria,
        )
    except ValidationError as exc:
        # The rule text, never the rejected input: a ValidationError's own
        # string echoes what was submitted, and this message goes back to the
        # model and into the log.
        reasons = "; ".join(dict.fromkeys(error["msg"] for error in exc.errors()))
        msg = f"Invalid evaluation submission: {reasons}"
        raise InitiativeEvaluationParseError(msg) from exc
    except (ValueError, TypeError, KeyError) as exc:
        # Raised by this module's own field checks, whose messages name the
        # field rather than quoting what was submitted.
        msg = f"Invalid evaluation submission: {exc}"
        raise InitiativeEvaluationParseError(msg) from exc


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
