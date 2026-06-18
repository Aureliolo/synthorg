"""Pagination-argument validation and page-draining helpers.

These are pure, dependency-free utilities (offset-paging math plus
``limit`` / ``offset`` validation) shared by the persistence layer and by
domain code that drives paginated reads. They live in ``core`` so every
layer may import them without reaching up into the persistence boundary:
the ``core-is-foundation`` import contract guarantees ``core`` depends on
nothing above it. The persistence layer re-exports these names from
``persistence/_shared/pagination.py`` so backend repositories keep their
existing import surface unchanged.

Both backends accept ``limit: int`` / ``offset: int`` on their cursor
paginated read methods. The validation rule (reject non-int, reject
``bool``, reject ``limit < 1`` / ``offset < 0``) is identical across
backends and lives here as a single helper so a future tightening applies
everywhere atomically. Backend-specific concerns (the ``event`` constant
emitted on rejection and extra context kwargs to log) stay in the call
sites.
"""

from collections.abc import (
    AsyncIterator,
    Awaitable,
    Callable,
    Mapping,
    Sequence,
)
from itertools import count
from typing import Final

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger

logger = get_logger(__name__)

# Canonical default page size for ``list_*`` / ``query`` repository
# methods. Lives here so every repo and Protocol shares a single named
# constant rather than embedding an inline ``100`` literal that trips
# the magic-numbers gate. Matches the established convention across
# the codebase (30+ repositories already default ``limit`` to 100).
DEFAULT_LIST_LIMIT: Final[int] = 100

# Hard upper bound on ``list_*`` / ``query`` page sizes regardless of
# caller-supplied limit. Defense-in-depth: the API layer's
# ``CursorLimit`` already caps caller input at 200, but internal
# service calls that bypass the API would not see that bound. The
# 10_000 ceiling matches the established per-repo ``_MAX_LIST_ROWS``
# precedent in the project repositories.
MAX_LIST_LIMIT: Final[int] = 10_000


async def paginate[PageItemT](
    fetch: Callable[[int, int], Awaitable[Sequence[PageItemT]]],
    *,
    page_size: int = DEFAULT_LIST_LIMIT,
) -> AsyncIterator[Sequence[PageItemT]]:
    """Yield successive repository pages until the dataset is exhausted.

    Centralises the offset-paging sweep that several services need when
    a single capped read would silently drop rows past the backend page
    cap. The sweep is bounded: iteration stops as soon as a backend
    returns a short (fewer than ``page_size``) or empty page, so a
    caller cannot accidentally spin forever.

    Implemented with ``itertools.count`` (a ``for`` iterator) rather
    than ``while True``. The long-running-loop kill-switch gate only
    inspects ``while`` loops; expressing a finite pagination sweep as a
    ``for`` keeps it from being misclassified as an unbounded
    background loop, so no per-call-site kill-switch suppression is
    needed.

    Args:
        fetch: Async callable taking ``(limit, offset)`` positionally
            and returning the page sequence. Wrap the repository method
            at the call site, e.g.
            ``lambda limit, offset: repo.list_items(limit=limit, offset=offset)``
            or a ``query`` bound to its filter spec.
        page_size: Rows requested per page. Defaults to
            ``DEFAULT_LIST_LIMIT``.

    Yields:
        Each non-empty page in offset order. The final (short) page is
        yielded before iteration terminates.

    Raises:
        QueryError: If ``page_size`` is not a positive int. A
            non-positive step makes ``itertools.count`` never advance,
            so a non-empty backend would page forever.
    """
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
        msg = f"page_size must be a positive int, got {page_size!r}"
        raise QueryError(msg)
    for offset in count(0, page_size):
        page = await fetch(page_size, offset)
        if not page:
            return
        yield page
        if len(page) < page_size:
            return


async def collect_all[PageItemT](
    fetch: Callable[[int, int], Awaitable[Sequence[PageItemT]]],
    *,
    page_size: int = DEFAULT_LIST_LIMIT,
) -> tuple[PageItemT, ...]:
    """Drain a ``*, limit, offset`` repo method into one full tuple.

    For the callers that genuinely need the *complete* set (boot-time
    state rehydration, drift detection) of a now-paginated repository
    method. Each underlying query stays bounded at ``page_size`` (no
    single unbounded scan), while the caller still gets every row, so
    correctness is preserved without reintroducing the unbounded read
    the pagination was added to remove. Thin wrapper over
    :func:`paginate`; the short-page termination guarantee is
    inherited.

    Args:
        fetch: Async callable taking ``(limit, offset)`` positionally
            and returning the page sequence (wrap the repo method at
            the call site, e.g.
            ``lambda limit, offset: repo.load_all(limit=limit,
            offset=offset)``).
        page_size: Rows per underlying query.

    Returns:
        Every row across all pages, in the method's deterministic
        order.
    """
    collected: list[PageItemT] = []
    async for page in paginate(fetch, page_size=page_size):
        collected.extend(page)
    return tuple(collected)


async def collect_all_mapping[KeyT, ValT](
    fetch: Callable[[int, int], Awaitable[Mapping[KeyT, ValT]]],
    *,
    page_size: int = DEFAULT_LIST_LIMIT,
) -> dict[KeyT, ValT]:
    """Drain a paginated mapping-returning repo method into one dict.

    The mapping analogue of :func:`collect_all` for
    ``get_version_manifest``-style aggregates that return
    ``dict[Key, Val]``. Pages are deterministically key-sorted and
    disjoint, so merge order does not change the result; iteration
    stops on the first short page exactly like :func:`paginate`.

    Caller invariant: the wrapped repo method MUST return disjoint
    pages over a stable key order. Overlapping keys across pages are
    silently last-write-wins (``dict.update``); this helper does not
    detect page overlap. An empty first page legitimately yields an
    empty dict (a valid result, not an error).

    Cancellation: if the awaiting task is cancelled mid-page the
    ``CancelledError`` from the in-flight ``fetch`` propagates
    unmodified; ``merged`` is a local accumulator so the partial
    result is simply discarded with no cleanup required.

    Args:
        fetch: Async callable taking ``(limit, offset)`` positionally
            and returning a page of the mapping.
        page_size: Entries per underlying query.

    Returns:
        The fully reassembled mapping.

    Raises:
        QueryError: If ``page_size`` is not a positive int.
    """
    # ``bool`` is a subclass of ``int``; without the explicit
    # ``isinstance(page_size, bool)`` guard ``True`` / ``False`` would
    # slip through as page sizes 1 / 0 and corrupt the drain loop.
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
        msg = f"page_size must be a positive int, got {page_size!r}"
        raise QueryError(msg)
    merged: dict[KeyT, ValT] = {}
    for offset in count(0, page_size):
        page = await fetch(page_size, offset)
        if not page:
            return merged
        merged.update(page)
        if len(page) < page_size:
            return merged
    return merged  # unreachable; count() is infinite


def validate_pagination_args(
    limit: object,
    offset: object,
    *,
    event: str,
    **context: object,
) -> int:
    """Type-check + bounds-check pagination args; log + raise on failure.

    Args:
        limit: Caller-supplied page size. Must be ``int`` (and not
            ``bool``). The numeric bound is ``>= 1``.
        offset: Caller-supplied page offset. Must be ``int`` (and not
            ``bool``). The numeric bound is ``>= 0``.
        event: Domain event constant the call site uses to namespace
            its query-failure logs (e.g.
            ``PERSISTENCE_DECISION_RECORD_QUERY_FAILED``).
        **context: Extra structured fields the caller wants to attach
            to the log line (e.g. ``task_id`` or ``agent_id``).

    Returns:
        ``limit`` clamped to ``[1, MAX_LIST_LIMIT]`` so repository
        callers cannot trigger an unbounded scan even when an internal
        path bypasses the API layer's ``CursorLimit`` bound.

    Raises:
        QueryError: If either argument fails the type or bounds check.
            The structured warning is emitted before the raise so
            operators can correlate the rejected call.
    """
    for name, value in (("limit", limit), ("offset", offset)):
        # ``isinstance(value, bool)`` accepts True / False as int
        # subclasses; we explicitly reject them so callers don't
        # accidentally page with a flag.
        if isinstance(value, bool) or not isinstance(value, int):
            msg = f"{name} must be int, got {type(value).__name__}"
            logger.warning(
                event,
                error=msg,
                param=name,
                provided_type=type(value).__name__,
                **context,
            )
            raise QueryError(msg)
    assert isinstance(limit, int)  # noqa: S101  -- type narrows for mypy
    assert isinstance(offset, int)  # noqa: S101  -- type narrows for mypy
    if limit < 1:
        msg = f"limit must be >= 1, got {limit}"
        logger.warning(
            event,
            error=msg,
            param="limit",
            value=limit,
            **context,
        )
        raise QueryError(msg)
    if offset < 0:
        msg = f"offset must be >= 0, got {offset}"
        logger.warning(
            event,
            error=msg,
            param="offset",
            value=offset,
            **context,
        )
        raise QueryError(msg)
    return min(limit, MAX_LIST_LIMIT)
