"""Tests for ``synthorg.core.error_taxonomy``."""

import pytest

from synthorg.core.error_taxonomy import (
    CATEGORY_TITLES,
    CODE_CATEGORY_PREFIX,
    NOT_FOUND_BAND,
    ErrorCategory,
    ErrorCode,
    category_title,
    category_type_uri,
)


@pytest.mark.unit
class TestErrorCategory:
    """``ErrorCategory`` is the canonical 8-member enum."""

    def test_has_eight_members(self) -> None:
        assert len(list(ErrorCategory)) == 8

    def test_values_are_lowercase_strings(self) -> None:
        for member in ErrorCategory:
            assert member.value == member.value.lower()
            assert member.value.replace("_", "").isalpha()


@pytest.mark.unit
class TestErrorCode:
    """Every ``ErrorCode`` first digit maps to a known category."""

    def test_first_digit_in_prefix_table(self) -> None:
        for code in ErrorCode:
            prefix = code.value // 1000
            assert prefix in CODE_CATEGORY_PREFIX, (
                f"{code.name} prefix {prefix} missing from CODE_CATEGORY_PREFIX"
            )

    def test_codes_are_four_digits(self) -> None:
        for code in ErrorCode:
            assert 1000 <= code.value <= 9999, (
                f"{code.name} value {code.value} is not 4-digit"
            )

    def test_not_found_band_is_three(self) -> None:
        assert NOT_FOUND_BAND == 3
        assert CODE_CATEGORY_PREFIX[NOT_FOUND_BAND] == ErrorCategory.NOT_FOUND


@pytest.mark.unit
class TestCategoryHelpers:
    """``category_title`` / ``category_type_uri`` cover every category."""

    @pytest.mark.parametrize("cat", list(ErrorCategory))
    def test_category_title_returns_non_empty_string(self, cat: ErrorCategory) -> None:
        title = category_title(cat)
        assert isinstance(title, str)
        assert title

    @pytest.mark.parametrize("cat", list(ErrorCategory))
    def test_category_type_uri_uses_docs_base(self, cat: ErrorCategory) -> None:
        uri = category_type_uri(cat)
        assert uri.startswith("https://synthorg.io/docs/errors#")
        assert uri.endswith(cat.value)

    def test_category_titles_table_covers_all_categories(self) -> None:
        assert set(CATEGORY_TITLES.keys()) == set(ErrorCategory)


@pytest.mark.unit
class TestPrefixTable:
    """``CODE_CATEGORY_PREFIX`` covers prefixes 1..8 exactly."""

    def test_prefix_table_keys(self) -> None:
        assert set(CODE_CATEGORY_PREFIX.keys()) == set(range(1, 9))

    def test_prefix_table_values_are_distinct(self) -> None:
        values = list(CODE_CATEGORY_PREFIX.values())
        assert len(values) == len(set(values))
