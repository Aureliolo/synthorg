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
    needs_canonicalisation,
)

# ``ex<a-diaeresis>mple.com`` as a single precomposed U+00E4.
PRECOMPOSED_HOST = "exämple.com"

# The same name with U+0061 followed by combining diaeresis U+0308.
DECOMPOSED_HOST = "exämple.com"

# ``example.com`` with a Cyrillic U+0435 in place of the leading ASCII ``e``.
# Being indistinguishable from ASCII ``e`` is the property under test, so the
# ambiguity ruff reports on this line is the point of it.
CONFUSABLE_HOST = "еxample.com"  # noqa: RUF001

EXPECTED_A_LABEL = "xn--exmple-cua.com"

# An internal service label that IDNA rejects, beside an A-label sibling that
# IDNA must still validate.
MIXED_HOST = "my_service.xn--mnchen-3ya.de"

# ── needs_canonicalisation ─────────────────────────────────────


class TestNeedsCanonicalisation:
    """Tests for the gate deciding whether IDNA has a say."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "hostname",
        [
            "example.com",
            "sub.example.co.uk",
            "my_service.internal",
            "127.0.0.1",
            "::1",
            "localhost",
            "host-with-dashes.example",
            "example.com.",
        ],
    )
    def test_plain_ascii_is_left_alone(self, hostname: str) -> None:
        assert needs_canonicalisation(hostname) is False

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
            MIXED_HOST,
        ],
    )
    def test_non_ascii_or_a_label_is_processed(self, hostname: str) -> None:
        assert needs_canonicalisation(hostname) is True

    @pytest.mark.unit
    @pytest.mark.parametrize("hostname", ["XN--EXMPLE-CUA.com", "sub.Xn--Bogus-.com"])
    def test_a_label_prefix_match_is_case_insensitive(self, hostname: str) -> None:
        """An uppercase prefix is the same claim and must not skip validation."""
        assert needs_canonicalisation(hostname) is True


# ── canonical_hostname ─────────────────────────────────────────


class TestCanonicalHostname:
    """Tests for the canonicalisation itself."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "hostname",
        ["example.com", "my_service.internal", "127.0.0.1", "::1", "example.com."],
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
    def test_uppercase_a_label_is_folded(self) -> None:
        assert canonical_hostname("XN--EXMPLE-CUA.com") == EXPECTED_A_LABEL

    @pytest.mark.unit
    def test_sibling_label_does_not_veto_its_neighbours(self) -> None:
        """An underscore label beside an A-label must survive both intact.

        Encoding the joined hostname would refuse the whole name for a
        character in a label that needed no canonicalising at all.
        """
        assert canonical_hostname(MIXED_HOST) == MIXED_HOST

    @pytest.mark.unit
    def test_only_the_labels_that_need_it_are_encoded(self) -> None:
        assert canonical_hostname("my_service.münchen.de") == MIXED_HOST

    @pytest.mark.unit
    def test_trailing_dot_is_preserved(self) -> None:
        assert canonical_hostname("münchen.de.") == "xn--mnchen-3ya.de."

    @pytest.mark.unit
    def test_non_canonical_a_label_rejected(self) -> None:
        with pytest.raises(idna.IDNAError) as excinfo:
            canonical_hostname("xn--bogus-.com")
        assert excinfo.value.code == "invalid_alabel"

    @pytest.mark.unit
    def test_uppercase_non_canonical_a_label_rejected(self) -> None:
        """The case-insensitive prefix test is what makes this reachable."""
        with pytest.raises(idna.IDNAError) as excinfo:
            canonical_hostname("XN--BOGUS-.COM")
        assert excinfo.value.code == "invalid_alabel"

    @pytest.mark.unit
    def test_interior_empty_label_rejected(self) -> None:
        with pytest.raises(idna.IDNAError) as excinfo:
            canonical_hostname("münchen..de")
        assert excinfo.value.code == "empty_label"


# ── describe_idna_failure ──────────────────────────────────────


class TestDescribeIdnaFailure:
    """Tests for the refusal reason rendered into the block log."""

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("code", "position", "expected"),
        [
            pytest.param(
                "disallowed_codepoint",
                3,
                "disallowed_codepoint at position 3",
                id="code-and-position",
            ),
            pytest.param("invalid_alabel", None, "invalid_alabel", id="code-only"),
            pytest.param(None, None, "invalid_hostname", id="library-names-no-rule"),
            pytest.param(
                None, 2, "invalid_hostname at position 2", id="position-without-code"
            ),
        ],
    )
    def test_renders_the_failed_rule(
        self,
        code: str | None,
        position: int | None,
        expected: str,
    ) -> None:
        exc = idna.IDNAError("unused", code=code, position=position)  # type: ignore[arg-type]
        assert describe_idna_failure(exc) == expected

    @pytest.mark.unit
    def test_offending_text_is_never_included(self) -> None:
        exc = idna.IDNAError(
            "unused",
            code="disallowed_codepoint",
            text="secret-internal-host",
            position=2,
        )
        assert "secret-internal-host" not in describe_idna_failure(exc)
