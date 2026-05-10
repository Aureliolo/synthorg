"""GitHub HMAC-SHA256 webhook signature verifier."""

import hashlib
import hmac

from synthorg.core.normalization import compare_ci
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    WEBHOOK_SIGNATURE_INVALID,
    WEBHOOK_SIGNATURE_VERIFIED,
)

logger = get_logger(__name__)


def _lookup_header_case_insensitive(
    headers: dict[str, str],
    name: str,
) -> str:
    """Return the first header value matching ``name`` regardless of case.

    Returns ``""`` when no match is found so callers can keep the
    existing empty-string fallback behaviour.
    """
    for key, value in headers.items():
        if compare_ci(key, name):
            return value
    return ""


class GitHubHmacVerifier:
    """Verifies GitHub webhook signatures.

    GitHub signs payloads with HMAC-SHA256 using the webhook secret.
    The signature is sent in the ``X-Hub-Signature-256`` header with
    the format ``sha256={hex_digest}``.
    """

    @property
    def signature_header(self) -> str:
        """HTTP header name containing the signature."""
        return "x-hub-signature-256"

    async def verify(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        secret: str,
    ) -> bool:
        """Verify a GitHub webhook signature."""
        signature = _lookup_header_case_insensitive(
            headers,
            self.signature_header,
        )
        if not signature.startswith("sha256="):
            logger.warning(
                WEBHOOK_SIGNATURE_INVALID,
                reason="missing sha256= prefix",
            )
            return False

        expected = hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        received = signature[7:]  # strip "sha256="

        valid = hmac.compare_digest(expected, received)
        if valid:
            logger.debug(WEBHOOK_SIGNATURE_VERIFIED, provider="github")
        else:
            logger.warning(
                WEBHOOK_SIGNATURE_INVALID,
                provider="github",
                reason="digest mismatch",
            )
        return valid
