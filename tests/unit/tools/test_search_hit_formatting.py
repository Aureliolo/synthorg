"""Tests for the shared scored-hit formatter."""

import pytest

from synthorg.tools._search_hit_formatting import format_scored_hits


@pytest.mark.unit
class TestFormatScoredHits:
    def test_empty_returns_message(self) -> None:
        assert format_scored_hits([], empty_msg="none") == "none"

    def test_renders_header_and_body(self) -> None:
        out = format_scored_hits(
            [("doc", "slug-1", 0.5, "body text")],
            empty_msg="none",
        )
        assert out == "[doc] slug-1 (score=0.50):\nbody text"

    def test_blank_line_between_hits(self) -> None:
        out = format_scored_hits(
            [("a", "1", 0.1, "x"), ("b", "2", 0.9, "y")],
            empty_msg="none",
        )
        assert out == "[a] 1 (score=0.10):\nx\n\n[b] 2 (score=0.90):\ny"

    def test_wrap_tag_fences_body(self) -> None:
        out = format_scored_hits(
            [("k", "id", 0.3, "danger")],
            empty_msg="none",
            wrap_tag="brain-state",
        )
        assert "danger" in out
        assert "brain-state" in out
