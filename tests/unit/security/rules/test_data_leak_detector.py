"""Tests for the data leak detector security rule."""

import pytest

from synthorg.core.enums import ApprovalRiskLevel, ToolCategory
from synthorg.security.models import SecurityContext, SecurityVerdictType
from synthorg.security.rules.data_leak_detector import DataLeakDetector

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


# ── Sensitive path detection ─────────────────────────────────────────


@pytest.mark.unit
class TestDataLeakDetectorSensitivePaths:
    """Data leak detector catches sensitive file path patterns."""

    @pytest.mark.parametrize(
        ("label", "path"),
        [
            (".env", "/app/.env"),
            (".env.local", "/app/.env.local"),
            (".env.production", "/app/.env.production"),
            ("id_rsa", "/home/user/.ssh/id_rsa"),
            ("id_ed25519", "/home/user/.ssh/id_ed25519"),
            ("id_ecdsa", "/home/user/.ssh/id_ecdsa"),
            ("id_dsa", "/home/user/.ssh/id_dsa"),
            (".pem file", "/certs/server.pem"),
            (".p12 file", "/certs/keystore.p12"),
            (".pfx file", "/certs/certificate.pfx"),
            (".key file", "/ssl/private.key"),
            (".aws/credentials", "/home/user/.aws/credentials"),
            (".ssh/config", "/home/user/.ssh/config"),
            (".netrc", "/home/user/.netrc"),
            (".pgpass", "/home/user/.pgpass"),
            ("credentials.json", "/app/credentials.json"),
            ("secrets.yml", "/config/secrets.yml"),
            ("secrets.yaml", "/config/secrets.yaml"),
            (".kube/config", "/home/user/.kube/config"),
        ],
    )
    def test_detects_sensitive_path(self, label: str, path: str) -> None:
        """Each sensitive file path pattern triggers detection."""
        detector = DataLeakDetector()
        ctx = _ctx({"path": path})
        verdict = detector.evaluate(ctx)

        assert verdict is not None, f"Expected detection for: {label}"
        assert verdict.verdict == SecurityVerdictType.DENY
        assert verdict.risk_level == ApprovalRiskLevel.HIGH
        assert "data_leak_detector" in verdict.matched_rules
        assert "sensitive path" in verdict.reason

    def test_detects_sensitive_path_in_list(self) -> None:
        """Sensitive paths inside list values are detected."""
        detector = DataLeakDetector()
        ctx = _ctx({"files": ["README.md", "/app/.env"]})
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.DENY

    def test_sensitive_path_windows_separator(self) -> None:
        """AWS credentials with backslash separators are detected."""
        detector = DataLeakDetector()
        ctx = _ctx({"path": r"C:\Users\user\.aws\credentials"})
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert "sensitive path" in verdict.reason


# ── PII detection ────────────────────────────────────────────────────


@pytest.mark.unit
class TestDataLeakDetectorPII:
    """Data leak detector catches PII patterns."""

    @pytest.mark.parametrize(
        ("label", "value"),
        [
            ("SSN", "My SSN is 123-45-6789"),
            ("Visa credit card", "Card: 4111111111111111"),
            ("Mastercard", "Card: 5111111111111111"),
        ],
    )
    def test_detects_pii_pattern(self, label: str, value: str) -> None:
        """PII patterns in arguments trigger detection."""
        detector = DataLeakDetector()
        ctx = _ctx({"content": value})
        verdict = detector.evaluate(ctx)

        assert verdict is not None, f"Expected PII detection for: {label}"
        assert verdict.verdict == SecurityVerdictType.DENY
        assert verdict.risk_level == ApprovalRiskLevel.HIGH

    def test_detects_pii_in_list(self) -> None:
        """PII inside list values is detected."""
        detector = DataLeakDetector()
        ctx = _ctx({"data": ["safe text", "SSN: 987-65-4321"]})
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert verdict.verdict == SecurityVerdictType.DENY

    def test_combined_path_and_pii_findings(self) -> None:
        """Both sensitive path and PII are reported together."""
        detector = DataLeakDetector()
        ctx = _ctx(
            {
                "path": "/app/.env",
                "content": "SSN: 123-45-6789",
            },
        )
        verdict = detector.evaluate(ctx)

        assert verdict is not None
        assert "sensitive path" in verdict.reason
        assert "Social Security Number" in verdict.reason


# ── Clean input (no detection) ───────────────────────────────────────


@pytest.mark.unit
class TestDataLeakDetectorPassThrough:
    """Clean inputs return None (no verdict)."""

    @pytest.mark.parametrize(
        "arguments",
        [
            {},
            {"path": "/app/src/main.py"},
            {"content": "Just a regular document"},
            {"path": "/home/user/project/config.toml"},
            {"data": ["item1", "item2"]},
            {"content": "phone: 555-1234"},
            {"path": "/home/user/.ssh/id_rsa.pub"},
            {"path": "/home/user/.ssh/id_ed25519.pub"},
        ],
        ids=[
            "empty",
            "normal_source_file",
            "normal_text",
            "safe_config_path",
            "safe_list",
            "non_ssn_digits",
            "public_rsa_key",
            "public_ed25519_key",
        ],
    )
    def test_returns_none_for_clean_input(
        self,
        arguments: dict[str, object],
    ) -> None:
        """Clean arguments produce no verdict."""
        detector = DataLeakDetector()
        ctx = _ctx(arguments)
        assert detector.evaluate(ctx) is None


# ── Name property ────────────────────────────────────────────────────


@pytest.mark.unit
class TestDataLeakDetectorName:
    """Verify the rule name property."""

    def test_name_is_data_leak_detector(self) -> None:
        detector = DataLeakDetector()
        assert detector.name == "data_leak_detector"
