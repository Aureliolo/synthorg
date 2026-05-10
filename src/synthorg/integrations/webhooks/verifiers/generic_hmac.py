"""Generic HMAC webhook signature verifier."""

import hashlib
import hmac

from synthorg.core.normalization import compare_ci
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    WEBHOOK_SIGNATURE_INVALID,
    WEBHOOK_SIGNATURE_VERIFIED,
)

logger = get_logger(__name__)


class GenericHmacVerifier:
    """Configurable HMAC-SHA256 webhook signature verifier.

    Works with any service that sends an HMAC-SHA256 hex digest
    in a configurable header.

    Args:
        header_name: HTTP header containing the signature.
        prefix: Optional prefix before the hex digest (e.g. ``"sha256="``).
    """

    def __init__(
        self,
        *,
        header_name: str = "x-signature",
        prefix: str = "",
    ) -> None:
        self._header_name = header_name.lower()
        self._prefix = prefix

    @property
    def signature_header(self) -> str:
        """HTTP header name containing the signature."""
        return self._header_name

    async def verify(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        secret: str,
    ) -> bool:
        """Verify a generic HMAC-SHA256 webhook signature."""
        raw_signature = next(
            (
                value
                for key, value in headers.items()
                if compare_ci(key, self._header_name)
            ),
            "",
        )
        if self._prefix and raw_signature.startswith(self._prefix):
            received = raw_signature[len(self._prefix) :]
        else:
            received = raw_signature

        if not received:
            logger.warning(
                WEBHOOK_SIGNATURE_INVALID,
                reason="empty signature",
            )
            return False

        expected = hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()

        valid = hmac.compare_digest(expected, received)
        if valid:
            logger.debug(WEBHOOK_SIGNATURE_VERIFIED, provider="generic")
        else:
            logger.warning(
                WEBHOOK_SIGNATURE_INVALID,
                provider="generic",
                reason="digest mismatch",
            )
        return valid
