"""In-memory pagination helper.

Applies offset/limit slicing to tuples and produces
``PaginationMeta`` for the response envelope.
"""

from typing import Annotated

from litestar.params import Parameter

from synthorg.api.dto import MAX_LIMIT, PaginationMeta

PaginationOffset = Annotated[
    int,
    Parameter(ge=0, description="Pagination offset"),
]
"""Query parameter type for pagination offset (>= 0)."""

PaginationLimit = Annotated[
    int,
    Parameter(ge=1, le=MAX_LIMIT, description="Page size"),
]
"""Query parameter type for pagination limit (1-200)."""


def paginate[T](
    items: tuple[T, ...],
    *,
    offset: int,
    limit: int,
    total: int | None = None,
) -> tuple[tuple[T, ...], PaginationMeta]:
    """Slice a tuple and produce pagination metadata.

    Clamps ``offset`` to ``[0, len(items)]`` and ``limit`` to
    ``[1, MAX_LIMIT]`` as a safety net.

    Args:
        items: Full collection to paginate.
        offset: Zero-based starting index.
        limit: Maximum items to return.
        total: True total count when *items* has been truncated
            upstream (e.g. by a safety cap).  Defaults to
            ``len(items)``.

    Returns:
        A tuple of (page_items, pagination_meta).
    """
    effective_total = total if total is not None else len(items)
    offset = max(0, min(offset, len(items)))
    limit = max(1, min(limit, MAX_LIMIT))
    page = items[offset : offset + limit]
    meta = PaginationMeta(
        total=effective_total,
        offset=offset,
        limit=limit,
    )
    return page, meta
