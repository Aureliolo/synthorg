"""Generic collection helpers shared across the codebase."""

from collections.abc import Iterable


def dedupe_preserving_order[T](items: Iterable[T]) -> tuple[T, ...]:
    """Return ``items`` with duplicates removed, preserving insertion order.

    ``dict.fromkeys`` is the idiomatic Python idiom for this -- it
    is O(n), uses hash-based deduplication, and (since Python 3.7)
    preserves insertion order of the keys it sees.
    """
    return tuple(dict.fromkeys(items))
