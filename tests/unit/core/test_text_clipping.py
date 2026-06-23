"""Tests for the shared :func:`clip_text` helper."""

import pytest

from synthorg.core.text_clipping import clip_text

pytestmark = pytest.mark.unit


class TestClipText:
    def test_truncates_to_limit(self) -> None:
        assert clip_text("hello world", 5) == "hello"

    def test_limit_exceeds_length_returns_whole_string(self) -> None:
        assert clip_text("hello", 10) == "hello"

    def test_limit_equals_length_returns_whole_string(self) -> None:
        assert clip_text("hello", 5) == "hello"

    def test_zero_limit_returns_empty(self) -> None:
        assert clip_text("hello", 0) == ""

    def test_empty_input_returns_empty(self) -> None:
        assert clip_text("", 5) == ""

    def test_negative_limit_raises(self) -> None:
        # A bare slice would read -1 as an offset-from-the-end and drop the
        # last character silently; the guard surfaces the wrong-sign caller.
        with pytest.raises(ValueError, match="non-negative"):
            clip_text("hello", -1)
