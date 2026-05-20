"""Unit tests for :func:`synthorg.docs_engine.slug.derive_slug`."""

import pytest

from synthorg.docs_engine.constants import DOCS_SLUG_MAX_LENGTH
from synthorg.docs_engine.slug import derive_slug

pytestmark = pytest.mark.unit


class TestDeriveSlug:
    def test_kebab_case_default(self) -> None:
        assert (
            derive_slug("Q2 Status Report", existing_slugs=set()) == "q2-status-report"
        )

    def test_collision_appends_suffix(self) -> None:
        existing = {"q2-status"}
        assert derive_slug("Q2 Status", existing_slugs=existing) == "q2-status-2"

    def test_repeated_collisions_increment(self) -> None:
        existing = {"q2", "q2-2", "q2-3"}
        assert derive_slug("Q2", existing_slugs=existing) == "q2-4"

    def test_unicode_falls_back(self) -> None:
        assert derive_slug("日本語", existing_slugs=set()) == "doc"

    def test_long_title_truncates(self) -> None:
        title = "a" * (DOCS_SLUG_MAX_LENGTH + 50)
        slug = derive_slug(title, existing_slugs=set())
        assert len(slug) <= DOCS_SLUG_MAX_LENGTH

    def test_punctuation_collapses_to_single_dash(self) -> None:
        assert derive_slug("Foo / Bar - Baz", existing_slugs=set()) == "foo-bar-baz"

    def test_pure_punctuation_falls_back(self) -> None:
        assert derive_slug("---", existing_slugs=set()) == "doc"
