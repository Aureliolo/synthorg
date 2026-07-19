"""Credential detector rule -- finds secrets in tool arguments."""

import re
from typing import Final

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.observability import get_logger
from synthorg.observability.events.security import SECURITY_CREDENTIAL_DETECTED
from synthorg.security.models import (
    SecurityContext,
    SecurityVerdict,
)
from synthorg.security.rules._utils import build_deny_verdict, walk_string_values

logger = get_logger(__name__)

_RULE_NAME: Final[str] = "credential_detector"

# Pre-compiled patterns for credential detection.
CREDENTIAL_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "AWS access key",
        re.compile(r"(?:^|[^A-Za-z0-9])(AKIA[0-9A-Z]{16})(?:[^A-Za-z0-9]|$)"),
    ),
    (
        "AWS secret key",
        re.compile(
            r"(?:aws_secret_access_key|secret_key)\s*[=:]\s*"
            r"[A-Za-z0-9/+=]{40}",
            re.IGNORECASE,
        ),
    ),
    (
        "Generic API key/token/secret",
        re.compile(
            r"(?:api[_-]?key|api[_-]?secret|auth[_-]?token|access[_-]?token"
            r"|secret[_-]?key|private[_-]?key|password)\s*[=:]\s*"
            r"""['\"]?[A-Za-z0-9_\-/.+=]{16,}['\"]?""",
            re.IGNORECASE,
        ),
    ),
    (
        "SSH private key",
        re.compile(r"-----BEGIN\s+(RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    ),
    (
        "Bearer token",
        re.compile(
            r"[Bb]earer\s+[A-Za-z0-9_\-/.+=]{20,}",
        ),
    ),
    (
        "GitHub personal access token",
        re.compile(r"(?:^|[^A-Za-z0-9])(ghp_[A-Za-z0-9]{36,})"),
    ),
    (
        "Generic secret assignment",
        re.compile(
            r"(?:SECRET|TOKEN|PASSWORD|CREDENTIAL)\s*[=:]\s*"
            r"""['\"]?[^\s'\"]{8,}['\"]?""",
            re.IGNORECASE,
        ),
    ),
)


_CREDENTIAL_REDACTED: Final[str] = "[REDACTED]"
_QUOTE_CHARS: Final[str] = "'\""


def _redact_match(match: re.Match[str]) -> str:
    """Replace a credential match, preserving any quote it swallowed.

    Some patterns match an optional surrounding quote, so a naive replace of
    the whole span strips the quotes off a value. When the credential sits
    inside a serialised JSON string that turns ``"secret"`` into ``[REDACTED]``
    and breaks the document, so a parked context can no longer be resumed.
    Re-attaching a leading/trailing quote keeps the boundary intact.

    Returns:
        ``[REDACTED]`` with any leading/trailing quote of the match preserved.
    """
    matched = match.group(0)
    prefix = matched[:1] if matched[:1] in _QUOTE_CHARS else ""
    suffix = matched[-1:] if matched[-1:] in _QUOTE_CHARS else ""
    return f"{prefix}{_CREDENTIAL_REDACTED}{suffix}"


def _scan_value(value: str) -> str | None:
    """Scan a single string for credential patterns.

    Returns:
        The matching pattern name, or ``None`` if no pattern matched.
    """
    for pattern_name, pattern in CREDENTIAL_PATTERNS:
        if pattern.search(value):
            return pattern_name
    return None


def redact_credentials(text: str) -> tuple[str, tuple[str, ...]]:
    """Redact credential-shaped substrings from free text.

    Credential patterns only (no PII), so a chat transcript keeps
    legitimate operator content while any secret-shaped value is masked.
    This is a defence-in-depth backstop for the conversation-persistence
    boundary: with out-of-band capture in place it should never fire.

    Returns:
        A ``(redacted_text, findings)`` tuple; ``findings`` names each
        credential pattern that matched (empty when the text is clean).
    """
    findings: list[str] = []
    redacted = text
    for pattern_name, pattern in CREDENTIAL_PATTERNS:
        if pattern.search(redacted):
            findings.append(pattern_name)
            redacted = pattern.sub(_redact_match, redacted)
    return redacted, tuple(sorted(set(findings)))


class CredentialDetector:
    """Detects credentials and secrets in tool call arguments.

    Scans all string values in the arguments dict for patterns
    matching AWS keys, API tokens, SSH keys, and other secrets.
    """

    @property
    def name(self) -> str:
        """Rule name."""
        return _RULE_NAME

    def evaluate(
        self,
        context: SecurityContext,
    ) -> SecurityVerdict | None:
        """Scan arguments for credential patterns.

        Returns:
            A DENY verdict with CRITICAL risk when a credential is
            found, or ``None`` when the arguments are clean.
        """
        findings = [
            match
            for value in walk_string_values(context.arguments)
            if (match := _scan_value(value))
        ]

        if not findings:
            return None

        unique = sorted(set(findings))
        logger.warning(
            SECURITY_CREDENTIAL_DETECTED,
            tool_name=context.tool_name,
            findings=unique,
        )
        return build_deny_verdict(
            reason=f"Credential detected in arguments: {', '.join(unique)}",
            risk_level=ApprovalRiskLevel.CRITICAL,
            rule_name=_RULE_NAME,
        )
