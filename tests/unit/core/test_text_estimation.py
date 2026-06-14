"""Tests for the shared token-count heuristic."""

import pytest

from synthorg.core.text_estimation import DEFAULT_CHAR_PER_TOKEN, approx_tokens

pytestmark = pytest.mark.unit


def test_empty_text_is_zero_tokens() -> None:
    assert approx_tokens("") == 0


def test_non_empty_text_floors_at_one() -> None:
    # Three characters is below the divisor but still one logical token.
    assert approx_tokens("abc") == 1


def test_uses_default_divisor() -> None:
    text = "x" * 40
    assert approx_tokens(text) == 40 // DEFAULT_CHAR_PER_TOKEN


def test_custom_divisor() -> None:
    text = "x" * 40
    assert approx_tokens(text, chars_per_token=10) == 4


@pytest.mark.parametrize("bad_divisor", [0, -1, -4])
def test_non_positive_divisor_rejected(bad_divisor: int) -> None:
    # A zero divisor would raise ZeroDivisionError, a negative one would
    # produce a meaningless estimate; both are rejected up front.
    with pytest.raises(ValueError, match="chars_per_token must be >= 1"):
        approx_tokens("some text", chars_per_token=bad_divisor)
