"""Tests for ``synthorg.core.error_taxonomy``."""

from collections.abc import Generator

import pytest

from synthorg.core.error_taxonomy import (
    _ERROR_DOCS_BASE_DEFAULT,
    CATEGORY_TITLES,
    CODE_CATEGORY_PREFIX,
    NOT_FOUND_BAND,
    ErrorCategory,
    ErrorCode,
    category_title,
    category_type_uri,
    set_error_docs_base_url,
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


@pytest.mark.unit
class TestDocsBaseUrlOverride:
    """``set_error_docs_base_url`` retargets RFC 9457 ``type`` URIs."""

    @pytest.fixture(autouse=True)
    def _reset_base_url(self) -> Generator[None]:
        # Reset both before and after so the first test in this class
        # cannot inherit a value mutated by an earlier file on the same
        # xdist worker.
        set_error_docs_base_url(_ERROR_DOCS_BASE_DEFAULT)
        yield
        set_error_docs_base_url(_ERROR_DOCS_BASE_DEFAULT)

    def test_default_base_url_appears_in_uri(self) -> None:
        uri = category_type_uri(ErrorCategory.AUTH)
        assert uri.startswith(_ERROR_DOCS_BASE_DEFAULT)

    def test_override_changes_uri_prefix(self) -> None:
        set_error_docs_base_url("https://docs.example.com/errors")
        uri = category_type_uri(ErrorCategory.VALIDATION)
        assert uri == "https://docs.example.com/errors#validation"

    def test_override_strips_trailing_slash(self) -> None:
        set_error_docs_base_url("https://docs.example.com/errors/")
        uri = category_type_uri(ErrorCategory.AUTH)
        assert uri == "https://docs.example.com/errors#auth"

    @pytest.mark.parametrize(
        "bad_value",
        [
            "",
            "   ",
            "http://docs.example.com/errors",
            "ftp://docs.example.com/errors",
            "javascript:alert(1)",
            "https:///errors",
            "https://user:pw@docs.example.com/errors",
            "https://docs.example.com/errors?q=1",
            "https://docs.example.com/errors#frag",
        ],
        ids=[
            "empty",
            "whitespace_only",
            "http_insecure",
            "ftp_scheme",
            "javascript_scheme",
            "no_host",
            "with_userinfo",
            "with_query",
            "with_fragment",
        ],
    )
    def test_setter_rejects_malformed_input(self, bad_value: str) -> None:
        with pytest.raises(ValueError, match="error_docs_base_url"):
            set_error_docs_base_url(bad_value)
