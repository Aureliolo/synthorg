"""Shared helpers for memory domain models."""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable


def deduplicate_tags[T](tags: Iterable[T]) -> tuple[T, ...]:
    """Return ``tags`` with duplicates removed, preserving insertion order."""
    return tuple(dict.fromkeys(tags))
