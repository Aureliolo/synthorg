"""Tests for ETag/If-Match concurrency utilities."""

import pytest

from synthorg.api.concurrency import check_if_match, compute_etag
from synthorg.core.domain_errors import ValidationError, VersionConflictError

pytestmark = pytest.mark.unit


class TestComputeEtag:
    def test_deterministic(self) -> None:
        etag1 = compute_etag("hello", "2026-01-01T00:00:00Z")
        etag2 = compute_etag("hello", "2026-01-01T00:00:00Z")
        assert etag1 == etag2

    def test_different_value_different_etag(self) -> None:
        etag1 = compute_etag("hello", "2026-01-01T00:00:00Z")
        etag2 = compute_etag("world", "2026-01-01T00:00:00Z")
        assert etag1 != etag2

    def test_different_timestamp_different_etag(self) -> None:
        etag1 = compute_etag("hello", "2026-01-01T00:00:00Z")
        etag2 = compute_etag("hello", "2026-01-02T00:00:00Z")
        assert etag1 != etag2

    def test_strong_etag_format(self) -> None:
        etag = compute_etag("test", "2026-01-01T00:00:00Z")
        assert etag.startswith('"')
        assert etag.endswith('"')
        assert not etag.startswith('W/"')

    def test_etag_length(self) -> None:
        etag = compute_etag("test", "2026-01-01T00:00:00Z")
        # " + 16 hex chars + "
        assert len(etag) == 18


class TestCheckIfMatch:
    def test_matching_etag_passes(self) -> None:
        etag = compute_etag("val", "ts")
        check_if_match(etag, etag, "test-resource")

    def test_mismatched_etag_raises(self) -> None:
        current = compute_etag("val", "ts1")
        stale = compute_etag("val", "ts2")
        with pytest.raises(VersionConflictError, match="test-resource"):
            check_if_match(stale, current, "test-resource")

    def test_none_etag_skips_check(self) -> None:
        current = compute_etag("val", "ts")
        check_if_match(None, current, "test-resource")

    def test_empty_string_etag_skips_check(self) -> None:
        current = compute_etag("val", "ts")
        check_if_match("", current, "test-resource")

    def test_oversized_if_match_rejected(self) -> None:
        # A pathological If-Match header is rejected with a 400-class
        # ValidationError (DoS guard) before any ETag parsing.
        current = compute_etag("val", "ts")
        with pytest.raises(ValidationError, match="too long"):
            check_if_match("x" * 513, current, "test-resource")
