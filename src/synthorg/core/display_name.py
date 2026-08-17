# module-kind: code
"""The one rule for what may appear in an operator-facing name field.

A name field is a name or it is absent; it never carries an identifier. The
alternative reads as prose addressed to a person: the chat surface told the
operator that ``d83b8bfd-156f-49c1-b596-850d09170be5`` was asking them a
question, because the display name fell back to the raw requester whenever the
roster lookup missed.

The rule is decidable rather than a guess. An entity primary key is a ``UUID``
by convention, so a value that parses as one is a key and can never be a name.
Every other actor an approval carries is already a word a person reads: a
system actor (``plan_review_gate``, ``coordinator``), a peer gateway label, or
an authenticated username. Those stay, because replacing them with "unknown"
would discard a name to enforce a rule about identifiers.
"""

from typing import Final
from uuid import UUID

#: What a stored name field says when the row it named is gone. Denormalised
#: name columns are ``NOT NULL`` (they are what a surface prints), so the
#: absence needs a word rather than the key it stands in for. Kept here beside
#: the rule it serves, and matched by the backfill in the migration that added
#: ``plans.project_name``.
UNNAMED_PROJECT: Final[str] = "Unknown project"

__all__ = ["UNNAMED_PROJECT", "display_name_or_none"]


def display_name_or_none(value: str | None) -> str | None:
    """Return *value* when it reads as a name, ``None`` when it is a key.

    Args:
        value: A candidate display name, typically an actor reference that
            resolved against no roster.

    Returns:
        The value unchanged when it is not an identifier, otherwise ``None``.
    """
    if value is None or not (trimmed := value.strip()):
        return None
    try:
        # The TRIMMED value: ``UUID`` does not strip, so a key that arrived
        # with whitespace around it would parse as a failure and be handed
        # back as a name, which is the one outcome this rules out.
        UUID(trimmed)
    except ValueError:
        return value
    return None
