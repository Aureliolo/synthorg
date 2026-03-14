"""Destructive operation detector rule."""

import re
from datetime import UTC, datetime
from typing import Final

from synthorg.core.enums import ApprovalRiskLevel
from synthorg.observability import get_logger
from synthorg.observability.events.security import SECURITY_DESTRUCTIVE_OP_DETECTED
from synthorg.security.models import (
    SecurityContext,
    SecurityVerdict,
    SecurityVerdictType,
)
from synthorg.security.rules._utils import walk_string_values

logger = get_logger(__name__)

_RULE_NAME: Final[str] = "destructive_op_detector"

# Patterns that indicate destructive operations.
_DESTRUCTIVE_PATTERNS: Final[
    tuple[tuple[str, re.Pattern[str], SecurityVerdictType], ...]
] = (
    (
        "rm -rf",
        re.compile(r"\brm\s+-[a-zA-Z]*r[a-zA-Z]*f|rm\s+-[a-zA-Z]*f[a-zA-Z]*r"),
        SecurityVerdictType.DENY,
    ),
    (
        "DROP TABLE",
        re.compile(r"\bDROP\s+TABLE\b", re.IGNORECASE),
        SecurityVerdictType.ESCALATE,
    ),
    (
        "DROP DATABASE",
        re.compile(r"\bDROP\s+DATABASE\b", re.IGNORECASE),
        SecurityVerdictType.DENY,
    ),
    (
        "DELETE without WHERE",
        re.compile(
            r"\bDELETE\s+FROM\s+\w+(?:\s*;|\s*$)(?!\s*WHERE)",
            re.IGNORECASE,
        ),
        SecurityVerdictType.ESCALATE,
    ),
    (
        "TRUNCATE TABLE",
        re.compile(r"\bTRUNCATE\s+TABLE\b", re.IGNORECASE),
        SecurityVerdictType.ESCALATE,
    ),
    (
        "git push --force",
        re.compile(r"\bgit\s+push\s+.*(?:--force|-f)\b"),
        SecurityVerdictType.ESCALATE,
    ),
    (
        "git reset --hard",
        re.compile(r"\bgit\s+reset\s+--hard\b"),
        SecurityVerdictType.ESCALATE,
    ),
    (
        "format/mkfs",
        re.compile(r"\b(?:mkfs\b|format\s+[A-Za-z]:)", re.IGNORECASE),
        SecurityVerdictType.DENY,
    ),
)


def _scan_value(
    value: str,
) -> list[tuple[str, SecurityVerdictType]]:
    """Scan a single string for all destructive patterns.

    Returns list of (pattern_name, verdict) for every match.
    """
    matches: list[tuple[str, SecurityVerdictType]] = []
    for pattern_name, pattern, verdict in _DESTRUCTIVE_PATTERNS:
        if pattern.search(value):
            matches.append((pattern_name, verdict))
    return matches


class DestructiveOpDetector:
    """Detects destructive operations in tool call arguments.

    Scans for dangerous commands like ``rm -rf``, ``DROP TABLE``,
    ``git push --force``, etc.  Returns DENY for the most dangerous
    operations and ESCALATE for recoverable ones.
    """

    @property
    def name(self) -> str:
        """Rule name."""
        return _RULE_NAME

    def evaluate(
        self,
        context: SecurityContext,
    ) -> SecurityVerdict | None:
        """Scan arguments for destructive operations.

        Returns the most severe verdict found (DENY > ESCALATE).
        """
        findings: list[tuple[str, SecurityVerdictType]] = []
        for value in walk_string_values(context.arguments):
            findings.extend(_scan_value(value))

        if not findings:
            return None

        names = sorted({f[0] for f in findings})
        has_deny = any(f[1] == SecurityVerdictType.DENY for f in findings)
        verdict = SecurityVerdictType.DENY if has_deny else SecurityVerdictType.ESCALATE
        risk = ApprovalRiskLevel.CRITICAL if has_deny else ApprovalRiskLevel.HIGH

        logger.warning(
            SECURITY_DESTRUCTIVE_OP_DETECTED,
            tool_name=context.tool_name,
            findings=names,
            verdict=verdict.value,
        )
        return SecurityVerdict(
            verdict=verdict,
            reason=f"Destructive operation detected: {', '.join(names)}",
            risk_level=risk,
            matched_rules=(_RULE_NAME,),
            evaluated_at=datetime.now(UTC),
            evaluation_duration_ms=0.0,
        )
