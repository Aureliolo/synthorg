"""Tests for the A3 additions to ``synthorg.core.normalization``."""

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from synthorg.core.normalization import (
    collapse_whitespace_lowercase,
    extract_bearer_token,
    extract_media_type,
    normalize_ascii_lowercase,
    normalize_ascii_lowercase_or_default,
    normalize_identifier,
)


@pytest.mark.unit
class TestNormalizeAsciiLowercase:
    """``normalize_ascii_lowercase`` strips and ASCII-lowercases."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("APPLICATION/JSON", "application/json"),
            ("  Application/JSON  ", "application/json"),
            ("True", "true"),
            ("\tFALSE\n", "false"),
            ("", ""),
            ("   ", ""),
            ("text/plain", "text/plain"),
            ("128M", "128m"),
        ],
    )
    def test_basic(self, value: str, expected: str) -> None:
        assert normalize_ascii_lowercase(value) == expected

    def test_diverges_from_normalize_identifier_on_sharp_s(self) -> None:
        """``.lower()`` keeps ß; ``casefold()`` (normalize_identifier) expands it."""
        value = "Straße"
        assert normalize_ascii_lowercase(value) == "straße"
        assert normalize_identifier(value) == "strasse"
        assert normalize_ascii_lowercase(value) != normalize_identifier(value)

    @example(value="APPLICATION/JSON")
    @example(value="  Mixed  ")
    @example(value="ALL UPPER")
    @given(
        value=st.text(
            alphabet=st.characters(min_codepoint=0x20, max_codepoint=0x7E),
        ),
    )
    def test_matches_strip_lower_contract(self, value: str) -> None:
        """Pin the contract: ``value.strip().lower()`` for ASCII inputs."""
        assert normalize_ascii_lowercase(value) == value.strip().lower()

    @given(value=st.text())
    def test_idempotent(self, value: str) -> None:
        once = normalize_ascii_lowercase(value)
        assert normalize_ascii_lowercase(once) == once


@pytest.mark.unit
class TestNormalizeAsciiLowercaseOrDefault:
    """NULL-coalescing companion to ``normalize_ascii_lowercase``."""

    @pytest.mark.parametrize(
        ("value", "default", "expected"),
        [
            ("Bearer", "", "bearer"),
            ("", "TRUE", "true"),
            (None, "TRUE", "true"),
            (None, "", ""),
            ("  Mixed  ", "fallback", "mixed"),
            (" ", "fallback", "fallback"),
        ],
    )
    def test_basic(self, value: str | None, default: str, expected: str) -> None:
        assert normalize_ascii_lowercase_or_default(value, default=default) == expected

    def test_default_default_is_empty(self) -> None:
        assert normalize_ascii_lowercase_or_default(None) == ""
        assert normalize_ascii_lowercase_or_default("") == ""


@pytest.mark.unit
class TestExtractMediaType:
    """``extract_media_type`` parses ``Content-Type`` headers."""

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("application/json", "application/json"),
            ("application/json; charset=utf-8", "application/json"),
            ("APPLICATION/JSON", "application/json"),
            ("  application/json  ; charset=utf-8", "application/json"),
            ("text/plain;boundary=foo", "text/plain"),
            ("application/vnd.api+json", "application/vnd.api+json"),
            ("", ""),
            (";charset=utf-8", ""),
        ],
    )
    def test_basic(self, header: str, expected: str) -> None:
        assert extract_media_type(header) == expected

    def test_tsa_response_content_type(self) -> None:
        """TSA response Content-Type round-trips."""
        assert (
            extract_media_type("application/timestamp-reply")
            == "application/timestamp-reply"
        )
        assert (
            extract_media_type("Application/Timestamp-Reply; charset=us-ascii")
            == "application/timestamp-reply"
        )


@pytest.mark.unit
class TestExtractBearerToken:
    """``extract_bearer_token`` enforces ``Bearer <token>`` shape."""

    @pytest.mark.parametrize(
        ("header", "expected"),
        [
            ("Bearer abc123", "abc123"),
            ("bearer abc123", "abc123"),
            ("BEARER abc123", "abc123"),
            ("Bearer\tabc123", "abc123"),
            ("Bearer    abc123   ", "abc123"),
            ("Bearer abc def", "abc def"),
        ],
    )
    def test_valid_headers(self, header: str, expected: str) -> None:
        assert extract_bearer_token(header) == expected

    @pytest.mark.parametrize(
        "header",
        [
            "",
            "abc123",
            "Basic dXNlcjpwYXNz",
            "Bearer",
            "Bearer ",
            "Bearer  \t  ",
        ],
    )
    def test_malformed_headers_return_none(self, header: str) -> None:
        assert extract_bearer_token(header) is None


@pytest.mark.unit
class TestCollapseWhitespaceLowercase:
    """``collapse_whitespace_lowercase`` normalises whitespace and case."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("hello world", "hello world"),
            ("  Hello   World  ", "hello world"),
            ("Hello\tWorld\nThere", "hello world there"),
            ("ALL UPPER", "all upper"),
            ("", ""),
            ("\t\n  ", ""),
            ("rm -rf /tmp/foo", "rm -rf /tmp/foo"),
            ("RM   -RF\t/TMP/FOO", "rm -rf /tmp/foo"),
        ],
    )
    def test_basic(self, value: str, expected: str) -> None:
        assert collapse_whitespace_lowercase(value) == expected

    @given(value=st.text())
    def test_idempotent(self, value: str) -> None:
        once = collapse_whitespace_lowercase(value)
        assert collapse_whitespace_lowercase(once) == once
