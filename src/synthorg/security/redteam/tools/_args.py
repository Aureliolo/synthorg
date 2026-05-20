"""Argument model for the ``submit_red_team_report`` tool.

Lives in a sibling ``_args.py`` so the tool body and the args model
import cleanly (the codebase convention is one args module per tool
domain).
"""

from pydantic import BaseModel, ConfigDict

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.security.redteam.models import RedTeamFinding  # noqa: TC001


class SubmitRedTeamReportArgs(BaseModel):
    """Arguments the red-team agent supplies when filing its report.

    The agent must echo the deliverable's ``execution_id`` and
    ``task_id`` from the system prompt's brief block. This keeps the
    tool registered ONCE at boot on the agent's tool registry without
    per-evaluation re-instantiation; the single-shot invariant comes
    from :class:`RedTeamReportAlreadyExistsError` on the repository,
    not from execution_id concealment.

    Attributes:
        execution_id: Echoed from the deliverable's brief. Used as
            the repository key; a mismatch caused by a misaligned
            agent simply persists into a slot the gate never reads.
        task_id: Echoed from the deliverable's brief. Carried into
            the :class:`RedTeamReport` for the audit record.
        findings: Structured findings the agent identified during
            adversarial review. May be empty for a clean deliverable.
        summary: One-paragraph natural-language summary of the
            assessment. Required even on a clean deliverable so the
            audit record carries the agent's reasoning.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    execution_id: NotBlankStr
    task_id: NotBlankStr
    findings: tuple[RedTeamFinding, ...] = ()
    summary: NotBlankStr
