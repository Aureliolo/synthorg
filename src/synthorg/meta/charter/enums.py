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
