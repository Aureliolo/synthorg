"""Tests for the shared kebab-case slug reducer."""

import pytest

from synthorg.core.slug import kebab_slug

pytestmark = pytest.mark.unit


def test_basic_kebab_reduction() -> None:
    assert kebab_slug("Hello, World!", max_length=80) == "hello-world"


def test_collapses_runs_and_strips_edges() -> None:
    assert kebab_slug("  --Foo___Bar--  ", max_length=80) == "foo-bar"


def test_empty_returns_fallback() -> None:
    assert kebab_slug("!!!", max_length=80, fallback="doc") == "doc"
    assert kebab_slug("", max_length=80) == ""


def test_truncation_strips_trailing_dash() -> None:
    # Truncating mid-token must not leave a dangling dash.
    assert kebab_slug("alpha beta gamma", max_length=6) == "alpha"
