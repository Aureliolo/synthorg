"""Canonical agent-pair key utility for undirected pair tracking.

Normalises keys so that ``(a, b)`` and ``(b, a)`` map to the same
entry.  Used by stateful loop prevention mechanisms that track
per-pair state without direction sensitivity.
"""


def pair_key(a: str, b: str) -> tuple[str, str]:
    """Create a canonical sorted key for an agent pair.

    The key is direction-agnostic: ``pair_key("x", "y")`` equals
    ``pair_key("y", "x")``.

    Args:
        a: First agent ID.
        b: Second agent ID.

    Returns:
        Lexicographically sorted ``(min, max)`` tuple.

    Raises:
        ValueError: If either agent ID is blank.
    """
    if not a or not a.strip():
        msg = f"pair_key received blank first agent ID: {a!r}"
        raise ValueError(msg)
    if not b or not b.strip():
        msg = f"pair_key received blank second agent ID: {b!r}"
        raise ValueError(msg)
    return (min(a, b), max(a, b))
