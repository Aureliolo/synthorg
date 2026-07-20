"""Redaction of text entering agent memory.

Memory persists for the life of a deployment and re-injects into later
prompts, so a credential captured once leaks into every future context it
is recalled into. These guard that credentials and emails are masked
before storage, and, the sharper invariant, that the finding report never
quotes the secret it removed.
"""

import pytest

from synthorg.memory.redaction import RedactionResult, redact_for_memory

pytestmark = pytest.mark.unit

# A synthetic PAT-shaped token the credential detector recognises,
# assembled at runtime so no contiguous token literal is committed (the
# secret scanner would flag one, even in a test fixture).
_TOKEN = "ghp_" + "x" * 36
_EMAIL = "alice@example.com"


class TestCredentialRedaction:
    def test_credential_is_masked(self) -> None:
        result = redact_for_memory(f"the key is {_TOKEN} keep it safe")
        assert _TOKEN not in result.content
        assert result.redacted

    def test_finding_never_quotes_the_secret(self) -> None:
        # The SEV9 invariant: a report that echoes the secret defeats the
        # redaction it reports.
        result = redact_for_memory(f"token {_TOKEN}")
        for finding in result.findings:
            assert _TOKEN not in finding
            assert "ghp_" not in finding


class TestEmailRedaction:
    def test_email_is_masked(self) -> None:
        result = redact_for_memory(f"contact {_EMAIL} for access")
        assert _EMAIL not in result.content
        assert "[REDACTED_EMAIL]" in result.content
        assert "Email address" in result.findings

    def test_every_email_is_masked(self) -> None:
        result = redact_for_memory("a@x.com and b@y.org both leaked")
        assert "@x.com" not in result.content
        assert "@y.org" not in result.content

    def test_finding_never_quotes_the_email(self) -> None:
        result = redact_for_memory(f"contact {_EMAIL}")
        assert all(_EMAIL not in finding for finding in result.findings)

    def test_version_string_is_not_mistaken_for_an_email(self) -> None:
        # The pattern deliberately does not swallow identifiers that merely
        # contain an @, or ordinary prose, which it would otherwise destroy.
        result = redact_for_memory("upgraded to node@20.11.0 for the build")
        assert result.content == "upgraded to node@20.11.0 for the build"
        assert not result.redacted


class TestCleanAndCombined:
    def test_clean_text_passes_through_unchanged(self) -> None:
        text = "a perfectly ordinary lesson about retry backoff"
        result = redact_for_memory(text)
        assert result.content == text
        assert result.findings == ()
        assert not result.redacted

    def test_findings_are_sorted_and_deduplicated(self) -> None:
        result = redact_for_memory(f"{_EMAIL} then {_TOKEN} then bob@example.com")
        assert list(result.findings) == sorted(set(result.findings))

    def test_both_classes_masked_together(self) -> None:
        result = redact_for_memory(f"login {_EMAIL} with {_TOKEN}")
        assert _EMAIL not in result.content
        assert _TOKEN not in result.content
        assert "Email address" in result.findings
        assert len(result.findings) >= 2


class TestRedactionResult:
    def test_redacted_is_false_without_findings(self) -> None:
        assert not RedactionResult(content="clean", findings=()).redacted

    def test_redacted_is_true_with_findings(self) -> None:
        assert RedactionResult(content="x", findings=("Email address",)).redacted
