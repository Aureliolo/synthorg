"""Tests for the constant-time credential comparison in the A2A gateway.

Issue #1682 / SEC-1: ``_verify_peer_credentials`` previously compared
stored and presented credentials with ``!=``, which short-circuits on
the first differing byte and leaks credential length plus the position
of any matching prefix via wall-clock timing. The replacement uses
``hmac.compare_digest`` via the module-private ``_credentials_match``
helper.

These tests assert behavioural equivalence (matching vs mismatching
strings, length mismatch handling, encoding stability) and act as the
gate against future regressions back to ``!=``.
"""

import inspect
from collections.abc import Callable

import pytest

from synthorg.a2a import gateway


@pytest.fixture
def credentials_match() -> Callable[[str, str], bool]:
    """Return the module-private ``_credentials_match`` helper.

    Importing through the module attribute (rather than ``from ... import``)
    keeps the fixture resilient to a future rename: the test file fails
    with a clear ``AttributeError`` rather than at module-load time.
    """
    helper: Callable[[str, str], bool] | None = getattr(
        gateway,
        "_credentials_match",
        None,
    )
    if helper is None:
        pytest.fail(
            "expected `_credentials_match` helper in synthorg.a2a.gateway "
            "(introduced by #1682 to replace `!=` with `hmac.compare_digest`)",
        )
    return helper


@pytest.mark.unit
class TestCredentialsMatch:
    """Constant-time credential comparison helper."""

    def test_identical_keys_return_true(
        self,
        credentials_match: Callable[[str, str], bool],
    ) -> None:
        """Identical strings compare equal."""
        assert credentials_match("secret-key-abc123", "secret-key-abc123") is True

    def test_distinct_same_length_keys_return_false(
        self,
        credentials_match: Callable[[str, str], bool],
    ) -> None:
        """Same-length but different strings compare unequal."""
        assert credentials_match("secret-key-aaaaaa", "secret-key-bbbbbb") is False

    def test_mismatched_lengths_return_false(
        self,
        credentials_match: Callable[[str, str], bool],
    ) -> None:
        """Length mismatch never raises; comparison returns False."""
        assert credentials_match("short", "much-longer-credential-value") is False
        assert credentials_match("much-longer-credential-value", "short") is False

    def test_empty_strings_equal(
        self,
        credentials_match: Callable[[str, str], bool],
    ) -> None:
        """Two empty strings are equal under the helper.

        Note: callers are responsible for treating empty stored
        credentials as a missing-credentials condition before calling
        this helper. The helper itself is purely byte-wise.
        """
        assert credentials_match("", "") is True

    def test_one_empty_one_nonempty_returns_false(
        self,
        credentials_match: Callable[[str, str], bool],
    ) -> None:
        """Empty vs non-empty returns False."""
        assert credentials_match("", "presented-value") is False
        assert credentials_match("stored-value", "") is False

    def test_unicode_strings_compared_via_utf8_bytes(
        self,
        credentials_match: Callable[[str, str], bool],
    ) -> None:
        """Unicode strings are compared via their UTF-8 bytes.

        Two visually-identical strings with different Unicode
        normalisations (NFC vs NFD) differ in bytes and are reported
        unequal -- the helper does not normalise.
        """
        nfc = "café-key"  # café (NFC, single code point é)
        nfd = "café-key"  # café (NFD, e + combining accent)
        assert credentials_match(nfc, nfc) is True
        assert credentials_match(nfc, nfd) is False

    def test_uses_hmac_compare_digest(
        self,
        credentials_match: Callable[[str, str], bool],
    ) -> None:
        """Source uses `hmac.compare_digest`, not `==` or `!=`.

        Inspect the helper's source so a future regression that swaps
        in plain `==` is caught at unit-test time. We accept either
        ``hmac.compare_digest(...)`` or ``compare_digest(...)`` because
        a future refactor may import the function directly.
        """
        source = inspect.getsource(credentials_match)
        assert "compare_digest" in source, (
            "_credentials_match must call hmac.compare_digest; "
            "do not regress to `==` or `!=` for credential equality"
        )
        assert " == " not in source.replace("compare_digest", ""), (
            "_credentials_match must not use plain `==` for credential bytes"
        )


@pytest.mark.unit
class TestGatewayUsesConstantTimeCompare:
    """Gateway-level smoke checks: source no longer contains plain `!=`.

    A regression that re-introduces ``request_key != stored_key`` (or
    the OAuth-token equivalent) would silently reopen the timing
    side-channel. This test reads the gateway source once and asserts
    that the credential-compare lines are framed in terms of the
    helper, not direct equality. It is intentionally string-based and
    not parsing AST -- the helper name is short and unambiguous.
    """

    def test_no_direct_inequality_on_credentials(self) -> None:
        """Source does not contain `request_key != stored_key`-style lines."""
        source = inspect.getsource(gateway)
        # The two specific patterns that #1682 replaces.
        forbidden = (
            "request_key != stored_key",
            "request_token != stored_token",
        )
        for needle in forbidden:
            assert needle not in source, (
                f"Found forbidden non-constant-time compare {needle!r} in "
                "gateway.py; use _credentials_match (hmac.compare_digest)."
            )

    def test_helper_is_used_at_least_twice(self) -> None:
        """Both API-key and OAuth-token paths route through the helper.

        #1682 lists two sites (lines 525, 536) that must switch to
        constant-time compare. Asserting the helper is referenced at
        least twice catches a partial conversion.
        """
        source = inspect.getsource(gateway)
        # One occurrence is the definition, plus two call sites = 3 total.
        assert source.count("_credentials_match") >= 3, (
            "expected at least 3 references to _credentials_match in gateway.py "
            "(definition + two call sites); a partial conversion was detected"
        )
