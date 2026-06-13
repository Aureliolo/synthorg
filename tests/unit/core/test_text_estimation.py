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
