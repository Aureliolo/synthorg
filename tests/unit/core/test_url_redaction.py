"""Unit tests for the canonical URL credential redactor."""

import pytest

from synthorg.core.url_redaction import REDACTED_QUERY, redact_url

pytestmark = pytest.mark.unit


def test_strips_userinfo_by_default() -> None:
    """A URL's embedded credentials are dropped entirely by default."""
    assert (
        redact_url("https://user:token@example.com/path") == "https://example.com/path"
    )


def test_masks_userinfo_when_requested() -> None:
    """``mask_userinfo`` keeps a ``***@`` marker that credentials existed."""
    assert (
        redact_url("nats://user:pass@host:4222", mask_userinfo=True, query="strip")
        == "nats://***@host:4222"
    )


def test_no_credentials_leaves_authority_untouched() -> None:
    """A credential-free URL keeps host and port."""
    assert (
        redact_url("https://host:8080/x", mask_userinfo=True) == "https://host:8080/x"
    )


def test_query_redact_replaces_non_empty_query() -> None:
    """The default ``redact`` policy swaps a present query for the sentinel."""
    assert (
        redact_url("https://example.com/p?api_key=secret")
        == f"https://example.com/p?{REDACTED_QUERY}"
    )


def test_query_redact_drops_empty_query() -> None:
    """An absent query produces no trailing ``?``."""
    assert redact_url("https://example.com/p") == "https://example.com/p"


def test_query_keep_preserves_query() -> None:
    """The ``keep`` policy leaves the query verbatim."""
    assert (
        redact_url("https://user@example.com/p?a=1", query="keep")
        == "https://example.com/p?a=1"
    )


def test_query_strip_removes_query() -> None:
    """The ``strip`` policy removes the query without a sentinel."""
    assert (
        redact_url("https://example.com/p?a=1", query="strip")
        == "https://example.com/p"
    )


def test_ipv6_host_is_bracketed() -> None:
    """An IPv6 literal host is rebuilt with brackets and keeps its port."""
    assert (
        redact_url("https://user:tok@[::1]:8443/p", query="strip")
        == "https://[::1]:8443/p"
    )


def test_malformed_port_treated_as_absent() -> None:
    """A non-numeric port never raises; it is treated as absent."""
    result = redact_url("https://user:tok@host:notaport/p")
    assert result.startswith("https://host")
    assert "tok" not in result


def test_no_hostname_returns_input_unchanged() -> None:
    """A string with no parseable host is returned verbatim."""
    assert redact_url("not-a-url") == "not-a-url"
