"""Stage-1 information stripping for the safety classifier.

``InformationStripper`` removes PII, secrets, UUIDs, emails, and internal
IDs from the reviewer-facing description before the LLM classifier
(:mod:`synthorg.security.safety_classifier`) ever sees it. The original
text is preserved for execution.
"""

import re
from typing import Final

from synthorg.observability import get_logger
from synthorg.observability.events.security import SECURITY_INFO_STRIP_COMPLETE
from synthorg.security._shared_patterns import CONTROL_CHAR_RE
from synthorg.security.rules.credential_detector import CREDENTIAL_PATTERNS
from synthorg.security.rules.data_leak_detector import PII_PATTERNS

logger = get_logger(__name__)

# ── Information stripping patterns ───────────────────────────────

# Reuse existing credential and PII patterns from detectors.
_CREDENTIAL_STRIP_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    CREDENTIAL_PATTERNS
)
_PII_STRIP_PATTERNS: Final[tuple[tuple[str, re.Pattern[str]], ...]] = PII_PATTERNS

# Additional patterns for UUIDs, emails, and internal IDs.
_UUID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"
    r"-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b",
)
_EMAIL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
)
_INTERNAL_ID_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:agent|task)-[A-Za-z0-9][A-Za-z0-9\-]*\b",
)

# Placeholder tokens.
_CREDENTIAL_PLACEHOLDER: Final[str] = "[CREDENTIAL]"
_PII_PLACEHOLDER: Final[str] = "[PII]"
_ID_PLACEHOLDER: Final[str] = "[ID]"
_EMAIL_PLACEHOLDER: Final[str] = "[EMAIL]"


class InformationStripper:
    """Strip PII, secrets, UUIDs, emails, and internal IDs from text.

    Reuses credential patterns from ``credential_detector`` and PII
    patterns from ``data_leak_detector``, plus additional patterns
    for UUIDs, email addresses, and internal ID formats.  Each
    category is replaced with a distinct tagged placeholder.
    """

    def strip(self, text: str) -> str:
        """Replace sensitive data with tagged placeholders.

        Args:
            text: The input text to sanitize.

        Returns:
            The text with sensitive patterns replaced by
            ``[CREDENTIAL]``, ``[PII]``, ``[ID]``, or ``[EMAIL]``.
        """
        if not text:
            return text

        result = text

        # Credentials first (most specific patterns).
        for _label, pattern in _CREDENTIAL_STRIP_PATTERNS:
            result = pattern.sub(_CREDENTIAL_PLACEHOLDER, result)

        # PII patterns.
        for _label, pattern in _PII_STRIP_PATTERNS:
            result = pattern.sub(_PII_PLACEHOLDER, result)

        # UUIDs.
        result = _UUID_PATTERN.sub(_ID_PLACEHOLDER, result)

        # Internal IDs (agent-xxx, task-xxx).
        result = _INTERNAL_ID_PATTERN.sub(_ID_PLACEHOLDER, result)

        # Emails (after credentials to avoid double-matching
        # patterns that look like email-with-token).
        result = _EMAIL_PATTERN.sub(_EMAIL_PLACEHOLDER, result)

        # Strip bidi overrides and zero-width characters that
        # could hide prompt injection payloads.
        result = CONTROL_CHAR_RE.sub(" ", result)

        logger.debug(
            SECURITY_INFO_STRIP_COMPLETE,
            original_length=len(text),
            stripped_length=len(result),
        )
        return result
