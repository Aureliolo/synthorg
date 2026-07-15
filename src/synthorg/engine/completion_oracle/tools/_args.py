# module-kind: declarative
"""Argument model for the ``submit_completion_oracle_verdict`` tool.

The reviewer echoes the deliverable's ``execution_id`` / ``task_id`` from
the brief and supplies its verdict; the reviewer and executor identities
are stamped by the tool from the trusted runtime context, NOT taken from
these args, so the reviewer cannot spoof who reviewed whom.
"""

from pydantic import BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr
from synthorg.engine.completion_oracle.review_models import (
    CompletionOracleFinding,
    CompletionOracleVerdict,
)


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
        ran_build: Whether the reviewer built the deliverable.
        ran_tests: Whether the reviewer ran the deliverable's tests.
        test_command: The test command the reviewer ran, when it ran tests.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    execution_id: NotBlankStr
    task_id: NotBlankStr
    verdict: CompletionOracleVerdict
    findings: tuple[CompletionOracleFinding, ...] = ()
    summary: NotBlankStr
    ran_build: bool = False
    ran_tests: bool = False
    test_command: NotBlankStr | None = None
