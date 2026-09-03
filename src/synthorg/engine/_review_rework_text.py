# module-kind: code
"""The text a review gate sends back with work it did not approve.

A verdict carries a one-paragraph ``summary`` for the operator and a list of
``findings`` for the assignee, and the assignee reads only what the rework
hop's reason carries: it becomes the task's transition reason, then the
rework metadata, then the correction turn's prompt. Every gate used to hand
that hop the summary alone, so a reviewer following its own tool's
instructions (findings first, summary short) sent the work back naming
nothing, which is the failure the findings requirement exists to prevent.
"""

from collections.abc import Sequence
from enum import StrEnum
from typing import Protocol


class ReviewFinding(Protocol):
    """What every gate's finding carries, whatever else it adds."""

    @property
    def severity(self) -> StrEnum: ...

    @property
    def description(self) -> str: ...

    @property
    def evidence(self) -> tuple[str, ...]: ...

    @property
    def suggested_fix(self) -> str | None: ...


def rework_brief(
    lead: str,
    summary: str,
    findings: Sequence[ReviewFinding],
) -> str:
    """Compose the reason a rework hop carries.

    Args:
        lead: What judged the work, e.g. ``"Completion review (reject)"``.
        summary: The reviewer's paragraph.
        findings: Every finding, in the order the reviewer filed them.

    Returns:
        The lead and summary on the first line, then one numbered line per
        finding naming its severity, description, evidence and fix.
    """
    lines = [f"{lead}: {summary}"]
    for index, finding in enumerate(findings, start=1):
        line = f"{index}. [{finding.severity.value}] {finding.description}"
        if finding.evidence:
            line += f" Evidence: {'; '.join(finding.evidence)}"
        if finding.suggested_fix:
            line += f" Fix: {finding.suggested_fix}"
        lines.append(line)
    return "\n".join(lines)


__all__ = ["ReviewFinding", "rework_brief"]
