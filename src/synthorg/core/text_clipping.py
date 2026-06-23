# module-kind: code
"""Bounded text truncation.

A single character-slice helper used wherever a string must be capped to
a block or label bound. It lives in ``core`` so the narrative assembler /
reducer and the deliverable-receipt renderer share one definition instead
of each re-deriving ``text[:limit]``.
"""


def clip_text(text: str, limit: int) -> str:
    """Truncate ``text`` to at most ``limit`` characters.

    Args:
        text: The text to truncate.
        limit: Maximum character count to retain; must be non-negative.

    Returns:
        ``text`` truncated to ``limit`` characters.

    Raises:
        ValueError: When ``limit`` is negative. A bare slice would read a
            negative ``limit`` as an offset-from-the-end and silently drop
            characters from the wrong side, so a wrong-sign caller fails
            loudly here instead of corrupting the output.
    """
    if limit < 0:
        msg = f"limit must be non-negative, got {limit}"
        raise ValueError(msg)
    return text[:limit]
