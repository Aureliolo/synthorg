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


__all__ = ["optional_capability", "optional_text"]
