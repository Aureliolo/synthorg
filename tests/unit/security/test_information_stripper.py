"""Tests for the InformationStripper."""

import pytest

from synthorg.security.information_stripper import InformationStripper

# ── Helpers ───────────────────────────────────────────────────────


def _stripper() -> InformationStripper:
    return InformationStripper()


# ── Tests: clean text ────────────────────────────────────────────


@pytest.mark.unit
class TestCleanText:
    """Clean text passes through unchanged."""

    def test_clean_text_unchanged(self) -> None:
        text = "Agent requested to write a file to /src/main.py"
        assert _stripper().strip(text) == text

    def test_empty_string_unchanged(self) -> None:
        assert _stripper().strip("") == ""

    def test_normal_log_output_unchanged(self) -> None:
        text = "Task completed successfully with 3 retries."
        assert _stripper().strip(text) == text


# ── Tests: credential stripping ──────────────────────────────────


@pytest.mark.unit
class TestCredentialStripping:
    """Credentials are replaced with [CREDENTIAL]."""

    def test_aws_access_key(self) -> None:
        text = "Using key AKIAIOSFODNN7EXAMPLE for S3 access"
        result = _stripper().strip(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in result
        assert "[CREDENTIAL]" in result

    def test_ssh_private_key(self) -> None:
        text = "Key data: -----BEGIN RSA PRIVATE KEY-----\nMIIEow..."
        result = _stripper().strip(text)
        assert "BEGIN RSA PRIVATE KEY" not in result
        assert "[CREDENTIAL]" in result

    def test_bearer_token(self) -> None:
        text = "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.abc"
        result = _stripper().strip(text)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in result
        assert "[CREDENTIAL]" in result

    def test_generic_api_key(self) -> None:
        text = "api_key = sk-1234567890abcdefghij"
        result = _stripper().strip(text)
        assert "sk-1234567890abcdefghij" not in result
        assert "[CREDENTIAL]" in result

    def test_github_pat(self) -> None:
        text = "Token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
        result = _stripper().strip(text)
        assert "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in result
        assert "[CREDENTIAL]" in result


# ── Tests: PII stripping ─────────────────────────────────────────


@pytest.mark.unit
class TestPiiStripping:
    """PII patterns are replaced with [PII]."""

    def test_social_security_number(self) -> None:
        text = "User SSN is 123-45-6789 on record"
        result = _stripper().strip(text)
        assert "123-45-6789" not in result
        assert "[PII]" in result

    def test_credit_card_visa(self) -> None:
        text = "Card number: 4111111111111111"
        result = _stripper().strip(text)
        assert "4111111111111111" not in result
        assert "[PII]" in result

    def test_credit_card_mastercard(self) -> None:
        text = "Payment with 5105105105105100"
        result = _stripper().strip(text)
        assert "5105105105105100" not in result
        assert "[PII]" in result


# ── Tests: UUID stripping ────────────────────────────────────────


@pytest.mark.unit
class TestUuidStripping:
    """UUIDs are replaced with [ID]."""

    def test_uuid_v4(self) -> None:
        text = "Task ID: 550e8400-e29b-41d4-a716-446655440000"
        result = _stripper().strip(text)
        assert "550e8400-e29b-41d4-a716-446655440000" not in result
        assert "[ID]" in result

    def test_uuid_lowercase(self) -> None:
        text = "Record a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        result = _stripper().strip(text)
        assert "a1b2c3d4-e5f6-7890-abcd-ef1234567890" not in result
        assert "[ID]" in result

    def test_uuid_uppercase(self) -> None:
        text = "ID=A1B2C3D4-E5F6-7890-ABCD-EF1234567890"
        result = _stripper().strip(text)
        assert "A1B2C3D4-E5F6-7890-ABCD-EF1234567890" not in result
        assert "[ID]" in result


# ── Tests: email stripping ───────────────────────────────────────


@pytest.mark.unit
class TestEmailStripping:
    """Email addresses are replaced with [EMAIL]."""

    def test_simple_email(self) -> None:
        text = "Contact admin@example.com for details"
        result = _stripper().strip(text)
        assert "admin@example.com" not in result
        assert "[EMAIL]" in result

    def test_complex_email(self) -> None:
        text = "Sent to john.doe+tag@sub.domain.org"
        result = _stripper().strip(text)
        assert "john.doe+tag@sub.domain.org" not in result
        assert "[EMAIL]" in result


# ── Tests: internal ID stripping ─────────────────────────────────


@pytest.mark.unit
class TestInternalIdStripping:
    """Internal ID patterns (agent-xxx, task-xxx) are replaced."""

    def test_agent_id(self) -> None:
        text = "Assigned to agent-cto-alpha-001"
        result = _stripper().strip(text)
        assert "agent-cto-alpha-001" not in result
        assert "[ID]" in result

    def test_task_id(self) -> None:
        text = "Working on task-build-feature-xyz"
        result = _stripper().strip(text)
        assert "task-build-feature-xyz" not in result
        assert "[ID]" in result


# ── Tests: mixed content ─────────────────────────────────────────


@pytest.mark.unit
class TestMixedContent:
    """Multiple pattern types in a single text."""

    def test_multiple_patterns(self) -> None:
        text = (
            "Agent agent-sec-007 found SSN 123-45-6789 "
            "in config with api_key = sk-secretkey1234567890"
        )
        result = _stripper().strip(text)
        assert "agent-sec-007" not in result
        assert "123-45-6789" not in result
        assert "sk-secretkey1234567890" not in result
        assert "[ID]" in result
        assert "[PII]" in result
        assert "[CREDENTIAL]" in result

    def test_preserves_non_sensitive_context(self) -> None:
        text = "Agent agent-cto found SSN 123-45-6789 in /src/config.py"
        result = _stripper().strip(text)
        # Structural words should be preserved
        assert "found" in result
        assert "/src/config.py" in result
