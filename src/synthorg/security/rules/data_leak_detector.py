"""Data leak detector rule — finds sensitive file paths and PII."""

import re
from datetime import UTC, datetime
from typing import Final

from synthorg.core.enums import ApprovalRiskLevel
from synthorg.observability import get_logger
from synthorg.observability.events.security import SECURITY_DATA_LEAK_DETECTED
from synthorg.security.models import (
    SecurityContext,
    SecurityVerdict,
    SecurityVerdictType,
)
from synthorg.security.rules._utils import walk_string_values

logger = get_logger(__name__)

_RULE_NAME: Final[str] = "data_leak_detector"

# Sensitive file path patterns.
_SENSITIVE_PATHS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("environment file", re.compile(r"\.env(?:\.[a-z]+)?$", re.IGNORECASE)),
    ("RSA private key file", re.compile(r"id_rsa$")),
    ("Ed25519 private key file", re.compile(r"id_ed25519$")),
    ("ECDSA private key file", re.compile(r"id_ecdsa$")),
    ("DSA private key file", re.compile(r"id_dsa$")),
    ("PEM certificate", re.compile(r"\.pem$", re.IGNORECASE)),
    ("PKCS#12 file", re.compile(r"\.p12$", re.IGNORECASE)),
    ("PFX file", re.compile(r"\.pfx$", re.IGNORECASE)),
    ("key file", re.compile(r"\.key$", re.IGNORECASE)),
    ("cloud credentials file", re.compile(r"\.aws[/\\]credentials$")),
    ("SSH config", re.compile(r"\.ssh[/\\]config$")),
    ("netrc file", re.compile(r"\.netrc$")),
    ("pgpass file", re.compile(r"\.pgpass$")),
    ("credentials JSON", re.compile(r"credentials\.json$", re.IGNORECASE)),
    ("secrets YAML", re.compile(r"secrets\.ya?ml$", re.IGNORECASE)),
    ("kubeconfig", re.compile(r"\.kube[/\\]config$")),
)

# PII patterns.
PII_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "Social Security Number",
        re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    ),
    (
        "Credit card number",
        re.compile(r"\b(?:4\d{3}|5[1-5]\d{2}|6011)\d{12}\b"),
    ),
)


def _check_sensitive_paths(arguments: dict[str, object]) -> list[str]:
    """Find sensitive file paths in argument values (recursive)."""
    findings: list[str] = []
    for value in walk_string_values(arguments):
        for label, pattern in _SENSITIVE_PATHS:
            if pattern.search(value):
                findings.append(f"sensitive path detected ({label})")
                break
    return findings


def _check_pii(arguments: dict[str, object]) -> list[str]:
    """Find PII patterns in string argument values (recursive)."""
    findings: list[str] = []
    for value in walk_string_values(arguments):
        for name, pattern in PII_PATTERNS:
            if pattern.search(value):
                findings.append(name)
    return findings


class DataLeakDetector:
    """Detects access to sensitive file paths and PII in arguments."""

    @property
    def name(self) -> str:
        """Rule name."""
        return _RULE_NAME

    def evaluate(
        self,
        context: SecurityContext,
    ) -> SecurityVerdict | None:
        """Scan arguments for sensitive paths and PII.

        Returns DENY with HIGH risk if any sensitive data is found.
        """
        findings = _check_sensitive_paths(context.arguments)
        findings.extend(_check_pii(context.arguments))
        if not findings:
            return None
        unique = sorted(set(findings))
        logger.warning(
            SECURITY_DATA_LEAK_DETECTED,
            tool_name=context.tool_name,
            finding_count=len(unique),
        )
        return SecurityVerdict(
            verdict=SecurityVerdictType.DENY,
            reason=f"Data leak risk: {', '.join(unique)}",
            risk_level=ApprovalRiskLevel.HIGH,
            matched_rules=(_RULE_NAME,),
            evaluated_at=datetime.now(UTC),
            evaluation_duration_ms=0.0,
        )
