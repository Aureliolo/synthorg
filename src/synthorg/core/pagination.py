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

#: Canonical page size for ``list_items`` / ``query`` across every
#: repository protocol and concrete repo. Lives in ``core`` (not
#: ``persistence/_generics``) so domain and observability callers that
#: page through repository reads import it without reaching up into a
#: persistence internal; ``persistence/_generics`` re-exports it so the
#: backend repositories keep their existing import surface. Same value as
#: :data:`DEFAULT_LIST_LIMIT`; the two names mark the two surfaces (the
#: pure-helper limit here vs. the repository-protocol page size).
DEFAULT_PAGE_SIZE: Final[int] = 100

#: Largest batch a caller may fold into ONE filtered read: the id set a
#: page of conversations turns into a single turn query. Lives in ``core``
#: so that caller can size its batch without reaching into a persistence
#: internal, and so it cannot read a private copy whose bound disagrees
#: with the one actually applied.
#:
#: Deliberately NOT a second page ceiling. A repository that silently
#: clamped its own ``limit`` below :data:`MAX_LIST_LIMIT` would make a
#: short page ambiguous to :func:`paginate`, which reads one as the end of
#: the data, and a drain asking above that private clamp would stop at the
#: first page with rows still to read. One ceiling on a page, and it is
#: the one :func:`validate_pagination_args` applies.
MAX_PAGE_SIZE: Final[int] = 1_000

# Hard upper bound on ``list_*`` / ``query`` page sizes regardless of
# caller-supplied limit. Defense-in-depth: the API layer's
# ``CursorLimit`` already caps caller input at 200, but internal
# service calls that bypass the API would not see that bound. The
# 10_000 ceiling matches the established per-repo ``_MAX_LIST_ROWS``
# precedent in the project repositories.
MAX_LIST_LIMIT: Final[int] = 10_000

#: Hardest stop on one drain, counted in FULL pages. The short-page rule ends
#: a sweep the moment a backend answers with fewer rows than it was asked for,
#: which is every backend that runs out of data and most that are broken. The
#: one shape it cannot see is a ``fetch`` that ignores ``offset`` and answers a
#: full page every time: each page is indistinguishable from a healthy one, so
#: only a count catches it. A drain reaching this many full pages has read ten
#: million rows at the largest page a repository serves, which is a stuck fetch
#: rather than a large dataset, so it is refused rather than left to grow until
#: the process dies.
MAX_DRAIN_PAGES: Final[int] = 1_000


def _refuse_stuck_drain(pages: int, page_size: int) -> None:
    """Refuse a drain that has read *pages* full pages without ending.

    Args:
        pages: Full pages yielded so far.
        page_size: Rows each of them carried.

    Raises:
        QueryError: When the page cap is reached, naming both numbers so the
            operator can tell a stuck fetch from a genuinely enormous read.
    """
    if pages < MAX_DRAIN_PAGES:
        return
    msg = (
        f"drain read {pages} full pages of {page_size} without a short or "
        "empty page; the fetch is not honouring offset"
    )
    raise QueryError(msg)


async def paginate[PageItemT](
    fetch: Callable[[int, int], Awaitable[Sequence[PageItemT]]],
    *,
    page_size: int = DEFAULT_LIST_LIMIT,
) -> AsyncIterator[Sequence[PageItemT]]:
    """Yield successive repository pages until the dataset is exhausted.

    Centralises the offset-paging sweep that several services need when
    a single capped read would silently drop rows past the backend page
    cap. The sweep is bounded two ways, and it needs both: iteration
    stops as soon as a backend returns a short or empty page, and it
    refuses outright past :data:`MAX_DRAIN_PAGES`.

    The short page is the ordinary terminator and covers a backend that
    ran out of rows. It also covers most of the broken ones, which is
    why it terminates rather than the empty page: a ``fetch`` ignoring
    ``offset`` answers rows for ever, and a sweep keyed on emptiness
    alone would accumulate them until the process died.

    What it does NOT cover is a ``fetch`` that ignores ``offset`` and
    answers a FULL page every time. Every page then looks exactly like a
    healthy one, so no per-page test can tell them apart and only a
    count can. That is the page cap: a broken fetch rather than a large
    dataset, so the sweep fails loudly instead of growing until the
    process dies.

    Reading a short page as the end of the data is sound only while the
    backend honours the size it was asked for, which is why there is
    exactly ONE ceiling on a page and ``validate_pagination_args``
    applies it. A repository that clamped its own ``limit`` lower would
    answer a larger request with a full-but-short page, this sweep would
    read that as the end, and the drain would stop with rows still to
    come. That is a repository defect rather than something to
    compensate for here: compensating means naming the lowest private
    clamp in this module, which taxes every other drain and is one
    repository away from being wrong again.

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
        QueryError: If ``page_size`` is not a positive int (a
            non-positive step makes ``itertools.count`` never advance,
            so a non-empty backend would page forever), or if the drain
            reaches :data:`MAX_DRAIN_PAGES` full pages.
    """
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
        msg = f"page_size must be a positive int, got {page_size!r}"
        raise QueryError(msg)
    effective_page_size = min(page_size, MAX_LIST_LIMIT)
    for pages, offset in enumerate(count(0, effective_page_size), start=1):
        page = await fetch(effective_page_size, offset)
        if not page:
            return
        yield page
        if len(page) < effective_page_size:
            return
        _refuse_stuck_drain(pages, effective_page_size)


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
    :func:`paginate`; both of its termination guarantees, the short
    page and the page cap, are inherited. The cap matters most here,
    because this is the drain that accumulates: a fetch stuck on a full
    page grows this list until the process dies.

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

    Raises:
        QueryError: Whatever :func:`paginate` raises, including the
            refusal past :data:`MAX_DRAIN_PAGES`.
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
    pages over a stable key order and honour ``offset``. Overlapping
    keys across pages are silently last-write-wins (``dict.update``);
    this helper does not detect page overlap. An empty first page
    legitimately yields an empty dict (a valid result, not an error).

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
        QueryError: If ``page_size`` is not a positive int, or if the
            drain reaches :data:`MAX_DRAIN_PAGES` full pages.
    """
    # ``bool`` is a subclass of ``int``; without the explicit
    # ``isinstance(page_size, bool)`` guard ``True`` / ``False`` would
    # slip through as page sizes 1 / 0 and corrupt the drain loop.
    if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
        msg = f"page_size must be a positive int, got {page_size!r}"
        raise QueryError(msg)
    # Both terminators, for the reasons :func:`paginate` gives: one
    # ceiling applies to a page, the short page ends the ordinary sweep,
    # and the page cap catches the one broken shape a per-page test
    # cannot see, a fetch answering a full page at every offset.
    effective_page_size = min(page_size, MAX_LIST_LIMIT)
    merged: dict[KeyT, ValT] = {}
    for pages, offset in enumerate(count(0, effective_page_size), start=1):
        page = await fetch(effective_page_size, offset)
        if not page:
            return merged
        merged.update(page)
        if len(page) < effective_page_size:
            return merged
        _refuse_stuck_drain(pages, effective_page_size)
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
