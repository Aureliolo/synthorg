# module-kind: code
"""Describe a pydantic validation failure without echoing what it validated.

Separate from :mod:`synthorg.observability.redaction`, which scrubs secret
material out of an arbitrary exception string. This answers a different
question with a different technique: a validation failure over a model that
carries credentials must not be scrubbed, it must never receive the value at
all. Pydantic quotes the offending input in its message and truncates the
middle of a large one, which removes exactly the framing a pattern scrubber
matches on; and a scrubber has to recognise a secret to redact it, while this
product privileges no vendor, so a self-hosted gateway key looks like nothing
in particular.
"""

from collections.abc import Mapping

from pydantic import ValidationError

MAX_DESCRIPTION_LENGTH: int = 512
"""Hard cap on the length of a description.

A blob with hundreds of bad fields would otherwise amplify one log record.
"""

_TRUNCATION_MARKER: str = "...[truncated]"

_EXTRA_KEY_ERROR_TYPE: str = "extra_forbidden"
"""The one error type whose ``loc`` ends in a name the input chose."""

_REDACTED_KEY: str = "<unexpected-key>"
"""Stands in for that name, so the clause still says what went wrong."""


def _safe_reason(error: Mapping[str, object]) -> str:
    """Return the part of *error* that cannot carry what was validated.

    The type slug, never the message. Pydantic renders ``msg`` when the
    exception is raised, so any message an author wrote already holds
    whatever they interpolated into it, and no later exclusion takes it
    back out. Three constructs reach here carrying author-written text
    (``ValueError``, ``AssertionError`` and ``PydanticCustomError``, the
    last with both an author-chosen code and an author-written template),
    so naming them individually is a deny-list, and a deny-list is wrong
    by one the first time pydantic grows a fourth.

    The slug is pydantic's own machine-readable classification, no input
    reaches it, and it is stable enough to alert on. What is given up is
    the constraint-derived prose of pydantic's own errors ("Field
    required" rather than ``missing``), which is a small loss at a
    credential boundary and none at all in the log: the validator's
    message is still recorded where it is raised.

    Deliberately not typed ``ErrorDetails``: that ``TypedDict`` requires
    an ``input`` key, and the whole point of the caller is to ask for the
    errors without one, so the mapping it hands over does not have it.

    Returns:
        The error's type slug.
    """
    return str(error["type"])


def _safe_location(error: Mapping[str, object]) -> str:
    """Return *error*'s location with any input-supplied name removed.

    ``loc`` is mostly schema-derived (field names) or names an entity the
    operator created (a provider key), and it is the most useful half of
    the clause: it says WHICH entry failed. One component is neither.
    ``extra_forbidden`` fires on a key the input supplied and the schema
    has never heard of, so its final component is free text from the blob
    being validated, and a blob is exactly where a credential could end up
    used as a key. That one is masked; the path above it still names the
    entry, which is the part an operator acts on.

    Returns:
        The dotted location, ``<root>`` when there is none.
    """
    parts = list(error["loc"]) if isinstance(error["loc"], tuple | list) else []
    if str(error["type"]) == _EXTRA_KEY_ERROR_TYPE and parts:
        parts[-1] = _REDACTED_KEY
    return ".".join(str(part) for part in parts) or "<root>"


def describe_without_input(exc: ValidationError) -> str:
    """Describe a ``ValidationError`` without echoing what it validated.

    Pydantic is asked for the structured errors with the input, the
    context and the docs URL all excluded, leaving the field location and
    the reason, which is what an operator needs and all they need: they
    know what they typed. Excluding those three fields is not on its own
    enough: ``msg`` can hold an author-interpolated value, which
    :func:`_safe_reason` closes, and ``loc`` can hold an input-supplied
    key, which :func:`_safe_location` closes.

    Args:
        exc: The validation failure to describe.

    Returns:
        One ``location: reason`` clause per error, bounded in length.
        The type name alone when pydantic reports no structured errors.
    """
    clauses = [
        f"{_safe_location(error)}: {_safe_reason(error)}"
        for error in exc.errors(
            include_url=False,
            include_input=False,
            include_context=False,
        )
    ]
    if not clauses:
        return type(exc).__name__
    candidate = "; ".join(clauses)
    if len(candidate) <= MAX_DESCRIPTION_LENGTH:
        return candidate
    keep = MAX_DESCRIPTION_LENGTH - len(_TRUNCATION_MARKER)
    return candidate[:keep] + _TRUNCATION_MARKER
