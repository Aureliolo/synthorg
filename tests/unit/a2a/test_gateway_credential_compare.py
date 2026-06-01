"""Tests for the constant-time credential comparison in the A2A gateway.

A naive ``!=`` short-circuits on the first differing byte and leaks
credential length plus the position of any matching prefix via
wall-clock timing. ``_verify_peer_credentials`` uses
``hmac.compare_digest`` via the module-private
``_credentials_match`` helper.

These tests assert behavioural equivalence (matching vs mismatching
strings, length mismatch handling, encoding stability) and act as
the gate against future regressions back to ``!=``.
"""

import ast
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
            "for constant-time credential comparison via hmac.compare_digest",
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

        AST-based check rather than string heuristics, which can
        false-positive on docstrings or false-negative on equivalent
        rewrites. Asserts that a real ``Call`` node invokes
        ``compare_digest`` (either ``hmac.compare_digest`` or the
        bare ``compare_digest`` name), AND that no
        equality/inequality ``Compare`` nodes exist on the helper's
        body -- so a future regression replacing the call with
        ``stored == presented`` is caught even if the docstring
        still mentions ``compare_digest``.
        """
        tree = ast.parse(inspect.getsource(credentials_match))

        compare_digest_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "compare_digest"
                )
                or (
                    isinstance(node.func, ast.Name) and node.func.id == "compare_digest"
                )
            )
        ]
        assert compare_digest_calls, (
            "_credentials_match must call hmac.compare_digest; "
            "do not regress to `==` or `!=` for credential equality"
        )

        plain_equality = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Compare)
            and any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops)
        ]
        assert not plain_equality, (
            "_credentials_match must not use plain `==` / `!=` for "
            "credential bytes; route through hmac.compare_digest"
        )


@pytest.mark.unit
class TestGatewayUsesConstantTimeCompare:
    """Gateway-level smoke checks: no direct credential equality.

    A regression that re-introduces ``request_key != stored_key``
    (or its swapped order, or an ``==``) would silently reopen the
    timing side-channel. AST-based detection catches every direct
    comparison between the credential name pairs, not just the two
    literal spellings the original commit replaced.
    """

    def test_no_direct_inequality_on_credentials(self) -> None:
        """Source has no Eq/NotEq between ``request_*`` and ``stored_*``."""
        tree = ast.parse(inspect.getsource(gateway))
        forbidden_pairs: frozenset[frozenset[str]] = frozenset(
            {
                frozenset(("request_key", "stored_key")),
                frozenset(("request_token", "stored_token")),
            }
        )
        direct_compares = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Compare)
            and any(isinstance(op, (ast.Eq, ast.NotEq)) for op in node.ops)
            and isinstance(node.left, ast.Name)
            and len(node.comparators) == 1
            and isinstance(node.comparators[0], ast.Name)
            and frozenset((node.left.id, node.comparators[0].id)) in forbidden_pairs
        ]
        assert not direct_compares, (
            "Found direct credential equality/inequality in gateway.py; "
            "use _credentials_match (hmac.compare_digest)."
        )

    def test_helper_is_used_at_least_twice(self) -> None:
        """Both API-key and OAuth-token paths route through the helper.

        AST-based check rather than a substring count, which would
        pass after a partial conversion if the symbol appears only
        in docstrings. Walks the gateway module's ``ast.Call`` nodes
        and counts every ``_credentials_match(...)`` invocation. Two
        call sites (API-key + OAuth-token) is the floor; a
        regression to a single call site is caught.
        """
        tree = ast.parse(inspect.getsource(gateway))
        helper_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_credentials_match"
        ]
        assert len(helper_calls) >= 2, (
            "expected at least two _credentials_match call sites in "
            "gateway.py (API-key path + OAuth/bearer path); a partial "
            "conversion was detected"
        )
