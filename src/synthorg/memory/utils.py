"""Shared helpers for memory domain models."""

from typing import TYPE_CHECKING

from synthorg.core.collections import dedupe_preserving_order

if TYPE_CHECKING:
    from collections.abc import Iterable


# Implements dedup-and-keep-first semantics. The reject-on-duplicate
# validator in synthorg.core.role uses a different invariant (raises
# on duplicates rather than silently collapsing them); do NOT unify
# the two -- the choice between "fix it for me" and "fail loudly" is
# a per-domain policy decision.
def deduplicate_tags[T](tags: Iterable[T]) -> tuple[T, ...]:
    """Memory-domain alias for :func:`dedupe_preserving_order`.

    Kept as a named entry point so memory field validators read as
    ``deduplicate_tags(value)`` (the meaningful domain phrase)
    instead of leaking the generic helper name into the model docs.

    Wired via ``@field_validator("tags", mode="after")`` so that:

    * the model stays frozen (no ``object.__setattr__`` bypass),
    * Pydantic does not emit the "validator returning a value other
      than ``self``" warning that ``@model_validator(mode="after")``
      raises when called from ``__init__``,
    * truncation can be composed in the validator itself
      (``deduplicate_tags(value)[:max_items]``) without a separate
      helper.

    Returns:
        Tuple of ``T``.
    """
    return dedupe_preserving_order(tags)
