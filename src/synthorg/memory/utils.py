"""Shared helpers for memory domain models."""

from typing import TYPE_CHECKING, TypeVar

from pydantic import BaseModel

from synthorg.core.collections import dedupe_preserving_order

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

_M = TypeVar("_M", bound=BaseModel)


def deduplicate_tags[T](tags: Iterable[T]) -> tuple[T, ...]:
    """Memory-domain alias for :func:`dedupe_preserving_order`.

    Kept as a named entry point so memory models read as
    ``deduplicate_tags(self.tags)`` (the meaningful domain phrase)
    instead of leaking the generic helper name into the model docs.
    """
    return dedupe_preserving_order(tags)


# Factory implements dedup-and-keep-first semantics. The
# reject-on-duplicate validator in synthorg.core.role uses a different
# invariant (raises on duplicates rather than silently collapsing
# them); do NOT unify the two -- the choice between "fix it for me"
# and "fail loudly" is a per-domain policy decision.
def make_dedupe_tags_model_validator(
    field_name: str = "tags",
    *,
    max_items: int | None = None,
) -> Callable[[_M], _M]:
    """Build a ``@model_validator(mode='after')`` callable that dedupes ``field_name``.

    When ``max_items`` is set, the deduped tuple is also truncated.
    Mutation uses ``object.__setattr__`` because the model is frozen
    (Pydantic blocks ordinary attribute assignment on
    ``ConfigDict(frozen=True)`` instances).

    Args:
        field_name: Field on the model whose value is deduplicated.
        max_items: Optional maximum length after deduplication.

    Returns:
        A callable suitable for use as
        ``model_validator(mode="after")(make_dedupe_tags_model_validator(...))``
        on a Pydantic v2 model.
    """

    def _validator(self: _M) -> _M:
        current = getattr(self, field_name)
        unique = deduplicate_tags(current)
        if max_items is not None and len(unique) > max_items:
            unique = unique[:max_items]
        if len(unique) != len(current):
            object.__setattr__(self, field_name, unique)
        return self

    return _validator
