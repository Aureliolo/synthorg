# module-kind: code
"""Redact-before-persist backstop for conversation-turn content.

Out-of-band secret capture keeps a credential out of the conversation
entirely, so this backstop should never actually redact anything. If it
does, the WARNING it emits is an alert that a secret reached the
persistence boundary and the out-of-band path was bypassed.
"""

from synthorg.observability import get_logger
from synthorg.observability.events.security import SECURITY_TURN_SECRET_REDACTED
from synthorg.security.rules.credential_detector import redact_credentials

logger = get_logger(__name__)


def redact_turn_content(content: str) -> str:
    """Mask any credential-shaped substring before a turn is persisted.

    Returns:
        The content with any credential-shaped value masked. Emits a
        WARNING (findings by pattern name only, never the value) when the
        backstop fires, since a clean out-of-band flow never trips it.
    """
    redacted, findings = redact_credentials(content)
    if findings:
        logger.warning(SECURITY_TURN_SECRET_REDACTED, findings=list(findings))
    return redacted
