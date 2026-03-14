"""Output scanner — post-tool output scanning for sensitive data.

Reuses credential patterns from ``credential_detector`` and PII
patterns from ``data_leak_detector`` to scan tool output for
sensitive data.  Always logs findings at WARNING.
"""

import re  # noqa: TC003
from typing import Final

from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_OUTPUT_SCAN_FINDING,
    SECURITY_OUTPUT_SCAN_START,
)
from synthorg.security.models import OutputScanResult, ScanOutcome
from synthorg.security.rules.credential_detector import CREDENTIAL_PATTERNS
from synthorg.security.rules.data_leak_detector import PII_PATTERNS

logger = get_logger(__name__)

# Combine credential and PII patterns for output scanning.
_OUTPUT_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    *CREDENTIAL_PATTERNS,
    *PII_PATTERNS,
)

_REDACTED: Final[str] = "[REDACTED]"


class OutputScanner:
    """Scans tool output for sensitive data and optionally redacts it."""

    def scan(self, output: str) -> OutputScanResult:
        """Scan output text for sensitive patterns.

        Detection runs on the original output.  Redaction builds
        a separate redacted copy by applying substitutions in order.

        Args:
            output: The tool's output string.

        Returns:
            An ``OutputScanResult`` with findings and optional
            redacted content.
        """
        logger.debug(
            SECURITY_OUTPUT_SCAN_START,
            output_length=len(output),
        )
        findings: list[str] = []
        redacted = output

        for pattern_name, pattern in _OUTPUT_PATTERNS:
            if pattern.search(output):
                findings.append(pattern_name)
                logger.warning(
                    SECURITY_OUTPUT_SCAN_FINDING,
                    finding=pattern_name,
                )
                redacted = pattern.sub(_REDACTED, redacted)

        if not findings:
            return OutputScanResult()

        return OutputScanResult(
            has_sensitive_data=True,
            findings=tuple(sorted(set(findings))),
            redacted_content=redacted,
            outcome=ScanOutcome.REDACTED,
        )
