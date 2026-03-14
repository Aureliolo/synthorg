"""Tests for the output scanner."""

import pytest

from synthorg.security.models import ScanOutcome
from synthorg.security.output_scanner import OutputScanner

pytestmark = pytest.mark.timeout(30)


# ── Helpers ───────────────────────────────────────────────────────


def _scanner() -> OutputScanner:
    return OutputScanner()


# ── Tests ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestOutputScannerClean:
    """Clean output produces no findings."""

    def test_clean_text_no_findings(self) -> None:
        result = _scanner().scan("Hello, this is a normal log output.")

        assert result.has_sensitive_data is False
        assert result.findings == ()
        assert result.redacted_content is None
        assert result.outcome == ScanOutcome.CLEAN

    def test_empty_string_no_findings(self) -> None:
        result = _scanner().scan("")

        assert result.has_sensitive_data is False
        assert result.findings == ()


@pytest.mark.unit
class TestOutputScannerCredentials:
    """Detecting credential patterns in output."""

    @pytest.mark.parametrize(
        ("label", "text"),
        [
            (
                "AWS access key",
                "Found key: AKIAIOSFODNN7EXAMPLE in config",
            ),
            (
                "SSH private key",
                "-----BEGIN RSA PRIVATE KEY-----\nMIIEow...",
            ),
            (
                "SSH private key (OPENSSH)",
                "-----BEGIN OPENSSH PRIVATE KEY-----\nb3Blbn...",
            ),
            (
                "Bearer token",
                "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.long",
            ),
            (
                "GitHub PAT",
                "Token: ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij",
            ),
            (
                "Generic secret value",
                "SECRET=my_super_secret_value_1234",
            ),
        ],
    )
    def test_credential_detected(self, label: str, text: str) -> None:
        result = _scanner().scan(text)

        assert result.has_sensitive_data is True
        assert len(result.findings) >= 1
        assert result.outcome == ScanOutcome.REDACTED

    def test_aws_key_in_findings(self) -> None:
        result = _scanner().scan("AKIAIOSFODNN7EXAMPLE is the key")

        assert "AWS access key" in result.findings

    def test_bearer_token_in_findings(self) -> None:
        result = _scanner().scan(
            "Header: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig",
        )

        assert "Bearer token" in result.findings

    def test_generic_secret_case_insensitive(self) -> None:
        result = _scanner().scan("password = hunter2_secret_key")

        assert result.has_sensitive_data is True
        assert "Generic API key/token/secret" in result.findings


@pytest.mark.unit
class TestOutputScannerPII:
    """Detecting PII patterns in output."""

    def test_ssn_detected(self) -> None:
        result = _scanner().scan("SSN: 123-45-6789")

        assert result.has_sensitive_data is True
        assert "Social Security Number" in result.findings

    @pytest.mark.parametrize(
        ("label", "text"),
        [
            ("Visa", "Card: 4111111111111111"),
            ("Mastercard", "Card: 5111111111111111"),
        ],
    )
    def test_credit_card_detected(self, label: str, text: str) -> None:
        result = _scanner().scan(text)

        assert result.has_sensitive_data is True
        assert "Credit card number" in result.findings


@pytest.mark.unit
class TestOutputScannerRedaction:
    """Redacted content replaces sensitive data."""

    def test_redacted_content_replaces_aws_key(self) -> None:
        text = "Key is AKIAIOSFODNN7EXAMPLE here"
        result = _scanner().scan(text)

        assert result.redacted_content is not None
        assert "AKIAIOSFODNN7EXAMPLE" not in result.redacted_content
        assert "[REDACTED]" in result.redacted_content

    def test_redacted_content_replaces_ssn(self) -> None:
        text = "SSN is 123-45-6789 on file"
        result = _scanner().scan(text)

        assert result.redacted_content is not None
        assert "123-45-6789" not in result.redacted_content
        assert "[REDACTED]" in result.redacted_content

    def test_redacted_content_none_for_clean(self) -> None:
        result = _scanner().scan("no secrets here")

        assert result.redacted_content is None

    def test_multiple_findings_all_redacted(self) -> None:
        text = "SSN: 123-45-6789 and key AKIAIOSFODNN7EXAMPLE"
        result = _scanner().scan(text)

        assert result.has_sensitive_data is True
        assert len(result.findings) >= 2
        assert result.redacted_content is not None
        assert "123-45-6789" not in result.redacted_content
        assert "AKIAIOSFODNN7EXAMPLE" not in result.redacted_content


@pytest.mark.unit
class TestOutputScannerFindingsDeduplicated:
    """Findings are deduplicated and sorted."""

    def test_duplicate_patterns_yield_single_finding(self) -> None:
        text = "SSN: 123-45-6789 and also 987-65-4321"
        result = _scanner().scan(text)

        ssn_count = result.findings.count("Social Security Number")
        assert ssn_count == 1

    def test_findings_sorted(self) -> None:
        text = (
            "SSN: 123-45-6789 and AKIAIOSFODNN7EXAMPLE "
            "and Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.payload.sig"
        )
        result = _scanner().scan(text)

        assert result.findings == tuple(sorted(result.findings))
