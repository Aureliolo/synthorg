"""Tests for the credential detector security rule."""

import pytest

from synthorg.core.enums import ApprovalRiskLevel, ToolCategory
from synthorg.security.models import SecurityContext, SecurityVerdictType
from synthorg.security.rules.credential_detector import CredentialDetector

pytestmark = pytest.mark.timeout(30)


def _ctx(
    arguments: dict[str, object] | None = None,
    *,
    action_type: str = "code:read",
) -> SecurityContext:
    """Build a SecurityContext with sensible defaults."""
    return SecurityContext(
        tool_name="test-tool",
        tool_category=ToolCategory.FILE_SYSTEM,
        action_type=action_type,
        arguments=arguments or {},
    )


# ── Detection of known bad patterns ──────────────────────────────────


@pytest.mark.unit
class TestCredentialDetectorDetectsSecrets:
    """Credential detector catches known credential patterns."""

    @pytest.mark.parametrize(
        ("label", "value"),
        [
            ("AWS access key", "config AKIAIOSFODNN7EXAMPLE stored"),
            (
                "AWS secret key",
                "aws_secret_access_key=wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            ),
            (
                "AWS secret key (colon separator)",
                "secret_key: wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
            ),
            (
                "Generic API key",
                "api_key=xk_test_1234567890abcdef1234567890abcdef",
            ),
            (
                "Generic auth token",
                "auth_token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9",
            ),
            (
                "SSH private key",
                "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIB...",
            ),
            (
                "SSH OPENSSH key",
                "-----BEGIN OPENSSH PRIVATE KEY-----",
            ),
            (
                "SSH EC key",
                "-----BEGIN EC PRIVATE KEY-----",
            ),
            (
                "Bearer token",
                "Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.xxxxx",
            ),
            (
                "GitHub PAT",
                "token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij",
            ),
            (
                "Generic secret assignment",
                "SECRET=my-super-secret-value-here",
            ),
            (
                "Generic TOKEN assignment",
                "TOKEN: a1b2c3d4e5f6g7h8",
            ),
            (
                "Generic PASSWORD assignment",
                "PASSWORD=longpassword123",
            ),
        ],
        ids=lambda x: x if isinstance(x, str) and len(x) < 30 else None,
    )
    def test_detects_credential_pattern(
        self,
        label: str,
        value: str,
    ) -> None:
        """Each known credential pattern triggers a DENY verdict."""
        detector = CredentialDetector()
        ctx = _ctx({"content": value})
        verdict = detector.evaluate(ctx)

        assert verdict is not None, f"Expected detection of: {label}"
        assert verdict.verdict == SecurityVerdictType.DENY
        assert verdict.risk_level == ApprovalRiskLevel.CRITICAL
        assert "credential_detector" in verdict.matched_rules

    def test_detects_credential_in_nested_dict(self) -> None:
        """Credentials inside nested dicts are detected."""
        detector = CredentialDetector()
        ctx = _ctx(
            {
                "outer": {
                    "inner": "-----BEGIN PRIVATE KEY-----\nMIIE...",
                },
            },
        )
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.DENY

    def test_detects_credential_in_list(self) -> None:
        """Credentials inside list values are detected."""
        detector = CredentialDetector()
        ctx = _ctx(
            {
                "files": [
                    "safe content",
                    "api_key=xk_test_ABCDEFGHIJKLMNOP1234",
                ],
            },
        )
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.DENY

    def test_detects_credential_in_list_of_dicts(self) -> None:
        """Credentials in dicts nested inside lists are detected."""
        detector = CredentialDetector()
        ctx = _ctx(
            {
                "entries": [
                    {"value": "SECRET=do_not_leak_this"},
                ],
            },
        )
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.DENY

    def test_multiple_findings_deduped_and_sorted(self) -> None:
        """Multiple credential types produce sorted, unique findings."""
        detector = CredentialDetector()
        ctx = _ctx(
            {
                "a": "AKIAIOSFODNN7EXAMPLE is a key",
                "b": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9012345",
                "c": "Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9012345",
            },
        )
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        # Reason should contain both pattern names.
        assert "AWS access key" in verdict.reason
        assert "Bearer token" in verdict.reason


# ── Clean input (no detection) ───────────────────────────────────────


@pytest.mark.unit
class TestCredentialDetectorPassThrough:
    """Clean inputs return None (no verdict)."""

    @pytest.mark.parametrize(
        "arguments",
        [
            {},
            {"code": "print('hello world')"},
            {"path": "/usr/local/bin/python"},
            {"content": "Just a regular document with no secrets"},
            {"key": "short"},
            {"nested": {"safe": "value"}},
            {"items": ["one", "two", "three"]},
        ],
        ids=[
            "empty",
            "normal_code",
            "normal_path",
            "normal_text",
            "short_value",
            "nested_safe",
            "list_safe",
        ],
    )
    def test_returns_none_for_clean_input(
        self,
        arguments: dict[str, object],
    ) -> None:
        """Clean arguments produce no verdict."""
        detector = CredentialDetector()
        ctx = _ctx(arguments)
        assert detector.evaluate(ctx) is None


# ── Name property ────────────────────────────────────────────────────


@pytest.mark.unit
class TestCredentialDetectorName:
    """Verify the rule name property."""

    def test_name_is_credential_detector(self) -> None:
        detector = CredentialDetector()
        assert detector.name == "credential_detector"
