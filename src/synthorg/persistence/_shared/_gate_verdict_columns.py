"""Reading the nullable reviewer / adversary columns off a verdict row.

Both verdict archives record which agent judged a deliverable and on what
``(provider, model)`` pair, so verdict quality is comparable per agent AND
per model. All of those columns are nullable: rows written before they
existed genuinely do not know, and a NULL is the honest value there rather
than a fabricated attribution.

Shared by four repositories (two tables, two backends) so the NULL handling
cannot drift between them.
"""

from synthorg.core.types import CAPABILITY_LADDER, CapabilityLevel, NotBlankStr


def optional_text(value: object) -> NotBlankStr | None:
    """Return a non-blank string column, or ``None`` when it has no value.

    Args:
        value: The raw column value.

    Returns:
        The text, or ``None`` for NULL and for a blank the model would
        reject anyway.
    """
    if value is None:
        return None
    text = str(value).strip()
    return NotBlankStr(text) if text else None


def optional_capability(value: object) -> CapabilityLevel | None:
    """Return a capability column, or ``None`` when it names no known tier.

    An unrecognised tier reads as ``None`` rather than raising: the column
    records what a model was graded at when the verdict was reached, and a
    tier that has since been renamed must not make the whole audit row
    unreadable.

    Args:
        value: The raw column value.

    Returns:
        The capability level, or ``None``.
    """
    text = optional_text(value)
    if text is None:
        return None
    for level in CAPABILITY_LADDER:
        if level == text:
            return level
    return None


def archive_key(value: object) -> int:
    """Return the archive's surrogate key off a verdict row.

    Both stores assign it, so it is present on every row read back and the
    keyset cursor closes on it; a row without one is a malformed read rather
    than a missing attribution, which is why this raises where its siblings
    return ``None``.

    Args:
        value: The raw ``report_id`` column value.

    Returns:
        The key as an int.

    Raises:
        ValueError: If the column is NULL or not an integer.
    """
    if value is None:
        msg = "verdict archive row has no report_id"
        raise ValueError(msg)
    return int(str(value))


__all__ = ["archive_key", "optional_capability", "optional_text"]
