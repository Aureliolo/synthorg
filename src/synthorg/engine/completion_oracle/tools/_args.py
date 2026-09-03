# module-kind: declarative
"""Argument model for the ``submit_completion_oracle_verdict`` tool.

The reviewer echoes the deliverable's ``execution_id`` / ``task_id`` from
the brief and supplies its verdict; the reviewer and executor identities
are stamped by the tool from the trusted runtime context, NOT taken from
these args, so the reviewer cannot spoof who reviewed whom.
"""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.core.types import NotBlankStr
from synthorg.engine.completion_oracle.review_models import (
    CompletionOracleFinding,
    CompletionOracleVerdict,
)

#: What a model writes when it means "no command" and cannot send JSON null:
#: a live reviewer sent the text ``null`` and was refused twice for naming a
#: test command called null, one turn each, before it found the spelling
#: the schema wanted. These are not commands anybody runs.
_ABSENT_COMMAND_SPELLINGS: Final[frozenset[str]] = frozenset({"", "null", "none"})


class SubmitCompletionOracleVerdictArgs(BaseModel):
    """Arguments the reviewer supplies when filing its verdict.

    Attributes:
        execution_id: Echoed from the deliverable's brief; must match the
            gate's trusted runtime context.
        task_id: Echoed from the deliverable's brief.
        verdict: The aggregate verdict (approve / approve_with_notes /
            reject / escalate).
        findings: Structured observations. May be empty on a clean approval.
        summary: One-paragraph natural-language summary of the review.
        build_evidence_cited: Whether the verdict rests on a recorded build
            run the reviewer read in the brief's verification runs.
        test_evidence_cited: Whether it rests on a recorded test run.
        test_command: The recorded test command the reviewer cited, when it
            cited one.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    execution_id: NotBlankStr
    task_id: NotBlankStr
    verdict: CompletionOracleVerdict
    findings: tuple[CompletionOracleFinding, ...] = Field(
        default=(),
        description=(
            "What the assignee has to fix, one entry per defect. The rework "
            "brief renders every entry, and a reject with none is refused: a "
            "defect described only in the summary is one line of prose the "
            "assignee has to find for itself."
        ),
    )
    summary: NotBlankStr = Field(
        description=(
            "One paragraph the rework brief opens with and the operator "
            "reads; the findings carry the defects themselves."
        ),
    )
    build_evidence_cited: bool = False
    test_evidence_cited: bool = False
    test_command: NotBlankStr | None = None

    @field_validator("test_command", mode="before")
    @classmethod
    def _absent_command_is_none(cls, value: object) -> object:
        """Read the spellings of "no command" as no command.

        Returns:
            ``None`` for an empty or null-spelled string, else *value*.
        """
        if not isinstance(value, str):
            return value
        absent = normalize_ascii_lowercase(value) in _ABSENT_COMMAND_SPELLINGS
        return None if absent else value
