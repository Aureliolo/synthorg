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
        limit: Maximum character count to retain.

    Returns:
        ``text`` truncated to ``limit`` characters.
    """
    return text[:limit]
