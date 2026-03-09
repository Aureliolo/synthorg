"""In-memory pagination helper.

Applies offset/limit slicing to tuples and produces
``PaginationMeta`` for the response envelope.
"""

from ai_company.api.dto import PaginationMeta


def paginate[T](
    items: tuple[T, ...],
    *,
    offset: int,
    limit: int,
) -> tuple[tuple[T, ...], PaginationMeta]:
    """Slice a tuple and produce pagination metadata.

    Args:
        items: Full collection to paginate.
        offset: Zero-based starting index.
        limit: Maximum items to return.

    Returns:
        A tuple of (page_items, pagination_meta).
    """
    page = items[offset : offset + limit]
    meta = PaginationMeta(
        total=len(items),
        offset=offset,
        limit=limit,
    )
    return page, meta
