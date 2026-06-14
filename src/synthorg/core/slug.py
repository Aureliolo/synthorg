"""Shared kebab-case slug reduction.

A single ASCII kebab-case reducer used wherever a human string must
become a filesystem- and URL-safe slug (living-doc slugs, procedural
memory ids). It lives in ``core`` so the docs and memory subsystems
share one compiled pattern and one truncation rule instead of each
redefining ``[^a-z0-9]+``.
"""

import re

_KEBAB_PATTERN = re.compile(r"[^a-z0-9]+")


def kebab_slug(text: str, *, max_length: int, fallback: str = "") -> str:
    """Reduce ``text`` to a bounded kebab-case ASCII slug.

    Lowercases, replaces every run of non-alphanumeric characters with a
    single ``-``, strips leading/trailing dashes, truncates to
    ``max_length``, then strips any dash left dangling by truncation.

    Args:
        text: The source string.
        max_length: Maximum slug length in characters.
        fallback: Value returned when the reduction is empty (and when a
            truncation leaves nothing but dashes). Defaults to ``""``.

    Returns:
        The kebab-cased slug, or ``fallback`` when the reduction is empty.

    Raises:
        ValueError: If ``max_length`` is below 1. A non-positive bound
            would otherwise slice from the tail (Python reverse-tail
            slicing) and silently violate the maximum-length contract.
    """
    if max_length < 1:
        msg = "max_length must be >= 1"
        raise ValueError(msg)
    safe = _KEBAB_PATTERN.sub("-", text.lower()).strip("-")
    if not safe:
        return fallback
    return safe[:max_length].rstrip("-") or fallback
