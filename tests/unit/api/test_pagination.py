"""Unit tests for the cursor-based pagination helper."""

import pytest
from pydantic import ValidationError

from synthorg.api.cursor import CursorSecret, InvalidCursorError
from synthorg.api.dto import PaginationMeta
from synthorg.api.pagination import (
    encode_countless_seek_meta,
    encode_repo_seek_meta,
    paginate_cursor,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def secret() -> CursorSecret:
    return CursorSecret.from_key("paginate-test-key-32-bytes-pad0000")


class TestHappyPath:
    """Walk through the full collection page by page."""

    def test_empty_collection(self, secret: CursorSecret) -> None:
        empty: tuple[int, ...] = ()
        page, meta = paginate_cursor(empty, limit=10, cursor=None, secret=secret)
        assert page == ()
        assert meta.limit == 10
        assert meta.next_cursor is None
        assert meta.has_more is False

    def test_single_page(self, secret: CursorSecret) -> None:
        items = tuple(range(5))
        page, meta = paginate_cursor(items, limit=10, cursor=None, secret=secret)
        assert page == items
        assert meta.has_more is False
        assert meta.next_cursor is None

    def test_three_page_walk(self, secret: CursorSecret) -> None:
        items = tuple(range(130))
        collected: list[int] = []
        cursor: str | None = None
        pages = 0
        while True:
            page, meta = paginate_cursor(
                items,
                limit=50,
                cursor=cursor,
                secret=secret,
            )
            collected.extend(page)
            pages += 1
            if not meta.has_more:
                assert meta.next_cursor is None
                break
            assert meta.next_cursor is not None
            cursor = meta.next_cursor
            assert pages <= 10, "Walk exceeded expected page budget"
        assert pages == 3
        assert collected == list(items)

    def test_exact_page_boundary_has_no_next_cursor(
        self,
        secret: CursorSecret,
    ) -> None:
        items = tuple(range(50))
        page, meta = paginate_cursor(items, limit=50, cursor=None, secret=secret)
        assert len(page) == 50
        # 50 items, page of 50 -> no more.
        assert meta.has_more is False
        assert meta.next_cursor is None


class TestLimitClamping:
    """Limit must be clamped to [1, MAX_LIMIT]."""

    def test_limit_clamped_to_max(self, secret: CursorSecret) -> None:
        items = tuple(range(500))
        _, meta = paginate_cursor(items, limit=10_000, cursor=None, secret=secret)
        # MAX_LIMIT is 200 today; clamped to it.
        assert meta.limit == 200

    @pytest.mark.parametrize("limit", [0, -1, -1_000])
    def test_limit_clamped_to_min(
        self,
        secret: CursorSecret,
        limit: int,
    ) -> None:
        """Non-positive limits clamp to 1 rather than returning empty.

        Litestar's ``CursorLimit`` annotation rejects limit<1 at the HTTP
        parameter layer, but the internal helper is callable directly
        from tests and future RPC layers.  Clamping to 1 keeps the
        function's postcondition (``len(page) <= meta.limit``) stable
        without leaking the parameter-layer validation rule.
        """
        items = tuple(range(10))
        page, meta = paginate_cursor(
            items,
            limit=limit,
            cursor=None,
            secret=secret,
        )
        assert meta.limit == 1
        assert len(page) == 1
        assert page[0] == 0
        assert meta.has_more is True


class TestInvalidCursor:
    """Tampered / malformed cursors surface as InvalidCursorError."""

    def test_tampered_cursor_rejected(self, secret: CursorSecret) -> None:
        with pytest.raises(InvalidCursorError):
            paginate_cursor(
                (1, 2, 3),
                limit=10,
                cursor="not-a-valid-token",
                secret=secret,
            )

    def test_cursor_signed_by_other_secret_rejected(
        self,
        secret: CursorSecret,
    ) -> None:
        other = CursorSecret.from_key("other-secret-unit-test-key-pad0000")
        _, meta = paginate_cursor(
            tuple(range(100)),
            limit=10,
            cursor=None,
            secret=other,
        )
        assert meta.next_cursor is not None
        with pytest.raises(InvalidCursorError):
            paginate_cursor(
                tuple(range(100)),
                limit=10,
                cursor=meta.next_cursor,
                secret=secret,
            )

    def test_cursor_points_at_shrunk_collection_boundary(
        self,
        secret: CursorSecret,
    ) -> None:
        """A valid cursor pointing *exactly* at the new end is rejected.

        Regression for the ``offset >= len(items)`` guard.  Issue a
        cursor against a 20-item collection that points to offset 10,
        then replay it against a collection that has shrunk to
        exactly 10 items.  An empty page would silently hide the
        truncation; the explicit rejection forces callers to observe
        the state change instead.
        """
        full = tuple(range(20))
        _, meta = paginate_cursor(full, limit=10, cursor=None, secret=secret)
        assert meta.next_cursor is not None
        shrunk = tuple(range(10))  # cursor now points exactly at len(shrunk).
        with pytest.raises(InvalidCursorError):
            paginate_cursor(
                shrunk,
                limit=10,
                cursor=meta.next_cursor,
                secret=secret,
            )

    def test_cursor_points_past_shrunk_collection_end(
        self,
        secret: CursorSecret,
    ) -> None:
        """A cursor pointing past the end is rejected too."""
        full = tuple(range(20))
        _, meta = paginate_cursor(full, limit=10, cursor=None, secret=secret)
        assert meta.next_cursor is not None
        shrunk = tuple(range(5))  # cursor offset > len(shrunk).
        with pytest.raises(InvalidCursorError):
            paginate_cursor(
                shrunk,
                limit=10,
                cursor=meta.next_cursor,
                secret=secret,
            )


class TestCursorStability:
    """Same input -> same cursor (deterministic for a fixed secret)."""

    def test_cursor_is_deterministic(self, secret: CursorSecret) -> None:
        items = tuple(range(20))
        _, meta_a = paginate_cursor(items, limit=5, cursor=None, secret=secret)
        _, meta_b = paginate_cursor(items, limit=5, cursor=None, secret=secret)
        assert meta_a.next_cursor == meta_b.next_cursor


class TestPaginationMetaConsistency:
    """``has_more`` and ``next_cursor`` must agree."""

    def test_has_more_true_without_cursor_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PaginationMeta(limit=50, next_cursor=None, has_more=True)

    def test_has_more_false_with_cursor_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PaginationMeta(limit=50, next_cursor="abc", has_more=False)

    def test_has_more_true_with_cursor_accepted(self) -> None:
        meta = PaginationMeta(limit=50, next_cursor="abc", has_more=True)
        assert meta.has_more is True
        assert meta.next_cursor == "abc"

    def test_has_more_false_without_cursor_accepted(self) -> None:
        meta = PaginationMeta(limit=50, next_cursor=None, has_more=False)
        assert meta.has_more is False
        assert meta.next_cursor is None


class TestEncodeRepoSeekMeta:
    """Controller-facing helper for repo-backed pagination."""

    def test_intermediate_page_emits_cursor(self, secret: CursorSecret) -> None:
        meta = encode_repo_seek_meta(
            offset=0,
            page_len=10,
            total=30,
            limit=10,
            secret=secret,
        )
        assert meta.has_more is True
        assert meta.next_cursor is not None

    def test_terminal_page_clears_cursor(self, secret: CursorSecret) -> None:
        meta = encode_repo_seek_meta(
            offset=20,
            page_len=10,
            total=30,
            limit=10,
            secret=secret,
        )
        assert meta.has_more is False
        assert meta.next_cursor is None

    def test_short_page_without_matching_total_is_terminal(
        self,
        secret: CursorSecret,
    ) -> None:
        """Snapshot drift guard: empty / short pages cannot loop."""
        meta = encode_repo_seek_meta(
            offset=10,
            page_len=0,
            total=20,
            limit=10,
            secret=secret,
        )
        # Even though ``total`` says more rows exist, a zero-length
        # page cannot advance the cursor, so ``has_more`` must be
        # False.
        assert meta.has_more is False
        assert meta.next_cursor is None

    def test_stale_cursor_past_total_rejected(
        self,
        secret: CursorSecret,
    ) -> None:
        """``offset >= total`` (with offset > 0) raises like paginate_cursor."""
        with pytest.raises(InvalidCursorError, match="past the end"):
            encode_repo_seek_meta(
                offset=50,
                page_len=0,
                total=30,
                limit=10,
                secret=secret,
            )

    def test_stale_cursor_at_total_boundary_rejected(
        self,
        secret: CursorSecret,
    ) -> None:
        """``offset == total`` (with offset > 0) is the truncation signal."""
        with pytest.raises(InvalidCursorError):
            encode_repo_seek_meta(
                offset=30,
                page_len=0,
                total=30,
                limit=10,
                secret=secret,
            )

    def test_reject_stale_cursor_false_returns_terminal_page(
        self,
        secret: CursorSecret,
    ) -> None:
        """Opt-out for callers that genuinely tolerate cursor==total."""
        meta = encode_repo_seek_meta(
            offset=30,
            page_len=0,
            total=30,
            limit=10,
            secret=secret,
            reject_stale_cursor=False,
        )
        assert meta.has_more is False
        assert meta.next_cursor is None

    def test_reject_stale_cursor_false_still_rejects_past_end(
        self,
        secret: CursorSecret,
    ) -> None:
        """The opt-out is boundary-only: ``offset > total`` still raises.

        A cursor whose decoded offset is strictly past the repo end
        could not have been issued against the current snapshot (the
        HMAC would have signed the larger count), so it's never a
        legitimate append-only boundary -- honour the rejection even
        when the caller opted out of the boundary-equal check.
        """
        with pytest.raises(InvalidCursorError):
            encode_repo_seek_meta(
                offset=50,
                page_len=0,
                total=30,
                limit=10,
                secret=secret,
                reject_stale_cursor=False,
            )

    def test_zero_offset_empty_repo_is_not_stale(
        self,
        secret: CursorSecret,
    ) -> None:
        """First page against an empty repo is legitimate, not stale."""
        meta = encode_repo_seek_meta(
            offset=0,
            page_len=0,
            total=0,
            limit=10,
            secret=secret,
        )
        assert meta.has_more is False
        assert meta.next_cursor is None


class TestEncodeCountlessSeekMeta:
    """Helper for repos that use the ``fetch limit + 1`` overflow pattern."""

    def test_intermediate_page_emits_cursor(self, secret: CursorSecret) -> None:
        """``fetched_rows > limit`` -> ``has_more`` + signed cursor."""
        meta = encode_countless_seek_meta(
            offset=0,
            fetched_rows=11,  # asked for 10+1, got overflow.
            limit=10,
            secret=secret,
        )
        assert meta.has_more is True
        assert meta.next_cursor is not None
        assert meta.limit == 10

    def test_terminal_page_clears_cursor(self, secret: CursorSecret) -> None:
        """``fetched_rows <= limit`` -> terminal, no cursor."""
        meta = encode_countless_seek_meta(
            offset=20,
            fetched_rows=7,  # asked for 10+1, got only 7 -> no more.
            limit=10,
            secret=secret,
        )
        assert meta.has_more is False
        assert meta.next_cursor is None

    def test_exact_fit_is_terminal(self, secret: CursorSecret) -> None:
        """``fetched_rows == limit`` means no overflow row, so terminal."""
        meta = encode_countless_seek_meta(
            offset=0,
            fetched_rows=10,
            limit=10,
            secret=secret,
        )
        # Caller fetched ``limit+1`` and only got ``limit`` rows, so
        # there can't be a next page even though the page is full.
        assert meta.has_more is False
        assert meta.next_cursor is None

    def test_empty_first_page(self, secret: CursorSecret) -> None:
        """First page of an empty repo has no data and no cursor."""
        meta = encode_countless_seek_meta(
            offset=0,
            fetched_rows=0,
            limit=10,
            secret=secret,
        )
        assert meta.has_more is False
        assert meta.next_cursor is None

    def test_empty_follow_up_page_is_truncation(
        self,
        secret: CursorSecret,
    ) -> None:
        """``offset > 0`` with ``fetched_rows == 0`` raises truncation error.

        Under the limit+1 contract a server-issued cursor always
        points at a row that existed at the time of the previous
        response, so an empty follow-up page means rows disappeared
        between requests.  Silently returning a terminal page would
        hide the truncation from monitoring.
        """
        with pytest.raises(InvalidCursorError, match="past the end"):
            encode_countless_seek_meta(
                offset=10,
                fetched_rows=0,
                limit=10,
                secret=secret,
            )

    def test_next_cursor_advances_by_limit(self, secret: CursorSecret) -> None:
        """Cursor advancement uses ``offset + limit``, independent of fetched_rows.

        The countless path returns ``limit`` rows to the client even
        when the repo served ``limit+1`` (the extra row is an
        overflow sentinel), so the next cursor must address the row
        after the last one in ``data``, which is always
        ``offset + limit``.
        """
        from synthorg.api.cursor import decode_cursor

        meta = encode_countless_seek_meta(
            offset=40,
            fetched_rows=11,
            limit=10,
            secret=secret,
        )
        assert meta.next_cursor is not None
        assert decode_cursor(meta.next_cursor, secret=secret) == 50
