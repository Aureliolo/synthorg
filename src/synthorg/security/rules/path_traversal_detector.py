"""Path traversal detector rule."""

import re
from datetime import UTC, datetime
from typing import Final

from synthorg.core.enums import ApprovalRiskLevel
from synthorg.observability import get_logger
from synthorg.observability.events.security import SECURITY_PATH_TRAVERSAL_DETECTED
from synthorg.security.models import (
    SecurityContext,
    SecurityVerdict,
    SecurityVerdictType,
)
from synthorg.security.rules._utils import walk_string_values

logger = get_logger(__name__)

_RULE_NAME: Final[str] = "path_traversal_detector"

# Pre-compiled patterns for path traversal attacks.
_TRAVERSAL_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    (
        "directory traversal (../)",
        re.compile(r"(?:^|[/\\])\.\.(?:[/\\]|$)"),
    ),
    (
        "null byte injection",
        re.compile(r"\x00"),
    ),
    (
        "URL-encoded traversal (%2e%2e)",
        re.compile(r"%2e%2e[%/\\]|[/\\]%2e%2e", re.IGNORECASE),
    ),
    (
        "double-encoded traversal",
        re.compile(r"%252e%252e", re.IGNORECASE),
    ),
    (
        "overlong UTF-8 traversal",
        re.compile(r"%c0%ae", re.IGNORECASE),
    ),
    (
        "Windows UNC path",
        re.compile(r"^\\\\[^\\]"),
    ),
)


def _scan_value(value: str) -> str | None:
    """Scan a single string for traversal patterns."""
    for pattern_name, pattern in _TRAVERSAL_PATTERNS:
        if pattern.search(value):
            return pattern_name
    return None


class PathTraversalDetector:
    """Detects path traversal attacks in tool call arguments.

    Looks for ``../`` sequences, null bytes, URL-encoded traversal,
    double-encoded traversal, overlong UTF-8, and Windows UNC paths.
    """

    @property
    def name(self) -> str:
        """Rule name."""
        return _RULE_NAME

    def evaluate(
        self,
        context: SecurityContext,
    ) -> SecurityVerdict | None:
        """Scan arguments for path traversal patterns.

        Returns DENY with CRITICAL risk if traversal is detected.
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
            SECURITY_PATH_TRAVERSAL_DETECTED,
            tool_name=context.tool_name,
            findings=unique,
        )
        return SecurityVerdict(
            verdict=SecurityVerdictType.DENY,
            reason=f"Path traversal detected: {', '.join(unique)}",
            risk_level=ApprovalRiskLevel.CRITICAL,
            matched_rules=(_RULE_NAME,),
            evaluated_at=datetime.now(UTC),
            evaluation_duration_ms=0.0,
        )
