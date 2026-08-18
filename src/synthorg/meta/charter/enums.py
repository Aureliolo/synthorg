"""Charter domain enumerations."""

from enum import StrEnum


class CharterStatus(StrEnum):
    """Lifecycle state of a project charter produced by a deep interview.

    Attributes:
        DRAFTED: The interview produced a charter draft; the user may
            review and edit it in place. The only non-terminal state.
        APPROVED: The charter was approved and dispatched into the work
            pipeline spine as a real project run. Terminal.
        CANCELLED: The charter was discarded before approval. Terminal.
    """

    DRAFTED = "drafted"
    APPROVED = "approved"
    CANCELLED = "cancelled"


class CharterFacet(StrEnum):
    """A part of a charter the interview has to settle before drafting.

    The interview declares, per facet, whether the human's own words settled
    it or the draft supplies the org's judgement instead. That declaration is
    what lets the interview be held to asking rather than assuming, and what
    lets the operator see which lines they actually agreed.

    Attributes:
        GOALS: What success looks like in concrete terms.
        CONSTRAINTS: Hard limits the work must respect.
        SUCCESS_CRITERIA: How completion is judged. The initiative's whole
            tail scores against these, so a charter that invents them
            decides the run.
        SCOPE: What is explicitly in and out.
        ENVELOPE: The budget ceiling and the time horizon.
        PROJECT: Which project the work is filed under.
    """

    GOALS = "goals"
    CONSTRAINTS = "constraints"
    SUCCESS_CRITERIA = "success_criteria"
    SCOPE = "scope"
    ENVELOPE = "envelope"
    PROJECT = "project"
