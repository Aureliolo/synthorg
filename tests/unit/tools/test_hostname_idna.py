"""Unit tests for IDNA hostname canonicalisation.

The hostnames under test are named as module constants rather than inlined,
because each is by design hard to tell apart from an ASCII look-alike (or,
for the decomposed form, from its precomposed twin) in a source listing. The
constant name carries what the characters cannot.
"""

import idna
import pytest

from synthorg.tools.hostname_idna import (
    canonical_hostname,
    describe_idna_failure,
    needs_canonicalization,
)

# ``ex<a-diaeresis>mple.com`` as a single precomposed U+00E4.
PRECOMPOSED_HOST = "exämple.com"

# The same name with U+0061 followed by combining diaeresis U+0308.
DECOMPOSED_HOST = "exämple.com"

# ``example.com`` with a Cyrillic U+0435 in place of the leading ASCII ``e``.
# Being indistinguishable from ASCII ``e`` is the property under test, so the
# ambiguity ruff reports on this line is the point of it.
CONFUSABLE_HOST = "еxample.com"  # noqa: RUF001

EXPECTED_A_LABEL = "xn--exmple-cua.com"

# ── needs_canonicalization ─────────────────────────────────────


class TestNeedsCanonicalization:
    """Tests for the narrow gate deciding whether IDNA has a say."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "hostname",
        [
            "example.com",
            "sub.example.co.uk",
            "my_host.internal",
            "127.0.0.1",
            "::1",
            "localhost",
            "host-with-dashes.example",
        ],
    )
    def test_plain_ascii_is_left_alone(self, hostname: str) -> None:
        assert needs_canonicalization(hostname) is False

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "hostname",
        [
            PRECOMPOSED_HOST,
            DECOMPOSED_HOST,
            CONFUSABLE_HOST,
            "xn--exmple-cua.com",
            "sub.xn--exmple-cua.com",
            "xn--bogus-.com",
        ],
    )
    def test_non_ascii_or_a_label_is_processed(self, hostname: str) -> None:
        assert needs_canonicalization(hostname) is True


# ── canonical_hostname ─────────────────────────────────────────


class TestCanonicalHostname:
    """Tests for the canonicalisation itself."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "hostname",
        ["example.com", "my_host.internal", "127.0.0.1", "::1"],
    )
    def test_ascii_hostname_returned_unchanged(self, hostname: str) -> None:
        assert canonical_hostname(hostname) == hostname

    @pytest.mark.unit
    def test_unicode_hostname_becomes_a_label(self) -> None:
        assert canonical_hostname(PRECOMPOSED_HOST) == EXPECTED_A_LABEL

    @pytest.mark.unit
    def test_decomposed_and_precomposed_agree(self) -> None:
        """A combining diaeresis must not resolve to a different host."""
        assert canonical_hostname(DECOMPOSED_HOST) == EXPECTED_A_LABEL
        assert canonical_hostname(PRECOMPOSED_HOST) == EXPECTED_A_LABEL

    @pytest.mark.unit
    def test_confusable_does_not_collide_with_ascii_host(self) -> None:
        """A Cyrillic homograph must not canonicalise onto the ASCII name."""
        assert canonical_hostname(CONFUSABLE_HOST) != "example.com"

    @pytest.mark.unit
    def test_existing_a_label_is_idempotent(self) -> None:
        assert canonical_hostname(EXPECTED_A_LABEL) == EXPECTED_A_LABEL

    @pytest.mark.unit
    def test_non_canonical_a_label_rejected(self) -> None:
        with pytest.raises(idna.IDNAError) as excinfo:
            canonical_hostname("xn--bogus-.com")
        assert excinfo.value.code == "invalid_alabel"

    @pytest.mark.unit
    def test_empty_label_rejected(self) -> None:
        with pytest.raises(idna.IDNAError) as excinfo:
            canonical_hostname("xn--exmple-cua..com")
        assert excinfo.value.code == "empty_label"


# ── describe_idna_failure ──────────────────────────────────────


class TestDescribeIdnaFailure:
    """Tests for the refusal reason rendered into the block log."""

    @pytest.mark.unit
    def test_code_and_position_are_both_reported(self) -> None:
        exc = idna.IDNAError("unused", code="disallowed_codepoint", position=3)
        assert describe_idna_failure(exc) == "disallowed_codepoint at position 3"

    @pytest.mark.unit
    def test_code_alone_when_no_position_applies(self) -> None:
        exc = idna.IDNAError("unused", code="invalid_alabel")
        assert describe_idna_failure(exc) == "invalid_alabel"

    @pytest.mark.unit
    def test_falls_back_when_the_library_names_no_rule(self) -> None:
        assert describe_idna_failure(idna.IDNAError("unused")) == "invalid_hostname"

    @pytest.mark.unit
    def test_offending_text_is_never_included(self) -> None:
        exc = idna.IDNAError(
            "unused",
            code="disallowed_codepoint",
            text="secret-internal-host",
            position=2,
        )
        assert "secret-internal-host" not in describe_idna_failure(exc)
