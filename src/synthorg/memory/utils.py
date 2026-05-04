"""Shared helpers for memory domain models."""

from typing import TYPE_CHECKING

from synthorg.core.collections import dedupe_preserving_order

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import BaseModel


def deduplicate_tags[T](tags: Iterable[T]) -> tuple[T, ...]:
    """Memory-domain alias for :func:`dedupe_preserving_order`.

    Kept as a named entry point so memory models read as
    ``deduplicate_tags(self.tags)`` (the meaningful domain phrase)
    instead of leaking the generic helper name into the model docs.
    """
    return dedupe_preserving_order(tags)


# Implements dedup-and-keep-first semantics. The reject-on-duplicate
# validator in synthorg.core.role uses a different invariant (raises
# on duplicates rather than silently collapsing them); do NOT unify
# the two -- the choice between "fix it for me" and "fail loudly" is
# a per-domain policy decision.
def dedupe_tags_in_place(
    model: BaseModel,
    field_name: str = "tags",
    *,
    max_items: int | None = None,
) -> None:
    """Dedupe and (optionally) truncate ``field_name`` on a frozen model.

    Mutation uses ``object.__setattr__`` because the model is frozen
    (Pydantic blocks ordinary attribute assignment on
    ``ConfigDict(frozen=True)`` instances). Caller wraps this in a
    standard ``@model_validator(mode="after")`` method to keep the
    decorator form readable to mkdocstrings / griffe templates -- the
    factory-returns-callable form (``_x = model_validator(...)(factory())``)
    breaks the Pydantic-model rendering template downstream.

    Args:
        model: The Pydantic model instance (must be frozen).
        field_name: Field whose value is deduplicated.
        max_items: Optional maximum length after deduplication.
    """
    current = getattr(model, field_name)
    unique = deduplicate_tags(current)
    if max_items is not None and len(unique) > max_items:
        unique = unique[:max_items]
    if len(unique) != len(current):
        object.__setattr__(model, field_name, unique)
