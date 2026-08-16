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

from uuid import UUID

__all__ = ["display_name_or_none"]


def display_name_or_none(value: str | None) -> str | None:
    """Return *value* when it reads as a name, ``None`` when it is a key.

    Args:
        value: A candidate display name, typically an actor reference that
            resolved against no roster.

    Returns:
        The value unchanged when it is not an identifier, otherwise ``None``.
    """
    if value is None or not value.strip():
        return None
    try:
        UUID(value)
    except ValueError:
        return value
    return None
