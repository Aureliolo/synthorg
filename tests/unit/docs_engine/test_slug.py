"""Unit tests for :func:`synthorg.docs_engine.slug.derive_slug`."""

import pytest

from synthorg.docs_engine.constants import DOCS_SLUG_MAX_LENGTH
from synthorg.docs_engine.slug import derive_slug

pytestmark = pytest.mark.unit


class TestDeriveSlug:
    @pytest.mark.parametrize(
        ("title", "existing", "expected"),
        [
            ("Q2 Status Report", set(), "q2-status-report"),
            ("Q2 Status", {"q2-status"}, "q2-status-2"),
            ("Q2", {"q2", "q2-2", "q2-3"}, "q2-4"),
            ("日本語", set(), "doc"),
            ("Foo / Bar - Baz", set(), "foo-bar-baz"),
            ("---", set(), "doc"),
        ],
        ids=[
            "kebab_case_default",
            "collision_appends_suffix",
            "repeated_collisions_increment",
            "unicode_falls_back",
            "punctuation_collapses",
            "pure_punctuation_falls_back",
        ],
    )
    def test_derives_expected_slug(
        self, title: str, existing: set[str], expected: str
    ) -> None:
        assert derive_slug(title, existing_slugs=existing) == expected

    def test_long_title_truncates(self) -> None:
        title = "a" * (DOCS_SLUG_MAX_LENGTH + 50)
        slug = derive_slug(title, existing_slugs=set())
        assert len(slug) <= DOCS_SLUG_MAX_LENGTH
