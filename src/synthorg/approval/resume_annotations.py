# module-kind: code
"""How a decided approval must be presented to the agent it resumes.

Two things about a decision change how the resume message has to read, and
neither is carried by the decision itself:

- **Who wrote the reason.** On a free-text approval it is the operator's own
  words. On a decision fork it is the writeup of the option they picked, and
  that writeup was authored by the agent when it offered the fork; the
  operator contributed a choice, not prose. Labelling the second as the
  first would tell the model an agent's text came from a human.
- **Whether a rejection means stop.** A declined question resumes REJECTED,
  which alone reads as "do not proceed", when the agent is in fact meant to
  carry on using its own judgement.

Both are derived from the PERSISTED approval rather than threaded through
the decision write, so a replay from the resume-intent outbox presents the
decision exactly as the original dispatch did.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from synthorg.approval.questions import DECLINED_QUESTION_NOTE, is_question
from synthorg.core.approval import ApprovalItem


class ResumeReasonProvenance(StrEnum):
    """Who authored the decision text carried into the resume.

    Attributes:
        OPERATOR_TEXT: The operator's own free text.
        AGENT_OPTION: The agent's writeup of the option the operator chose.
    """

    OPERATOR_TEXT = "operator_text"
    AGENT_OPTION = "agent_option"


@dataclass(frozen=True, slots=True)
class ResumeAnnotations:
    """What the resume message needs beyond the decision itself.

    Attributes:
        reason_provenance: Who authored the decision reason, selecting the
            fence tag and banner it is rendered under.
        system_note: Server-owned guidance rendered in the trusted region.
            Never request data: it is the one part of the message the agent
            is told to act on.
    """

    reason_provenance: ResumeReasonProvenance = ResumeReasonProvenance.OPERATOR_TEXT
    system_note: str | None = None


#: What an approval with nothing to annotate resumes with: the operator's own
#: words, no extra guidance. The default for every caller that has no item to
#: derive from, so a missing derivation cannot invent a provenance claim.
DEFAULT_RESUME_ANNOTATIONS: Final[ResumeAnnotations] = ResumeAnnotations()


def reason_provenance(item: ApprovalItem) -> ResumeReasonProvenance:
    """Classify where a decided approval's reason text came from.

    Reads the persisted structural pick rather than the derived prose, so
    every consumer (the resume message, the project-brain record) agrees on
    the authorship, and a replay classifies it as the original did.

    Returns:
        :attr:`ResumeReasonProvenance.AGENT_OPTION` when the operator decided
        by picking an offered option, otherwise
        :attr:`ResumeReasonProvenance.OPERATOR_TEXT`.
    """
    evidence = item.evidence_package
    if evidence is not None and evidence.chosen_option_id is not None:
        return ResumeReasonProvenance.AGENT_OPTION
    return ResumeReasonProvenance.OPERATOR_TEXT


def resume_annotations(
    item: ApprovalItem | None,
    *,
    approved: bool,
) -> ResumeAnnotations:
    """Derive how *item*'s decision must be presented to the resumed agent.

    Returns:
        The annotations for this decision, or the defaults when the approval
        could not be re-read (nothing to derive from is not licence to claim
        a provenance).
    """
    if item is None:
        return DEFAULT_RESUME_ANNOTATIONS
    return ResumeAnnotations(
        reason_provenance=reason_provenance(item),
        system_note=(
            DECLINED_QUESTION_NOTE
            if not approved and is_question(item.action_type)
            else None
        ),
    )


__all__ = [
    "DEFAULT_RESUME_ANNOTATIONS",
    "ResumeAnnotations",
    "ResumeReasonProvenance",
    "reason_provenance",
    "resume_annotations",
]
