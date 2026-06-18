"""Webhook signature verifiers for self-hosted Git forges.

Covers the three forges SynthOrg integrates beyond GitHub:

* **Gitea** signs with HMAC-SHA256 and sends the raw hex digest in
  ``X-Gitea-Signature`` (no ``sha256=`` prefix).
* **Forgejo** (a Gitea fork) sends the same digest in
  ``X-Forgejo-Signature`` and also still emits the legacy
  ``X-Gitea-Signature``; the verifier accepts either header.
* **GitLab** does NOT use HMAC -- it echoes the configured shared
  secret verbatim in ``X-Gitlab-Token``, so verification is a
  constant-time equality check against the secret.
"""

import hashlib
import hmac

from synthorg.core.normalization import compare_ci
from synthorg.integrations.webhooks.verifiers.generic_hmac import (
    GenericHmacVerifier,
)
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    WEBHOOK_SIGNATURE_INVALID,
    WEBHOOK_SIGNATURE_VERIFIED,
)

logger = get_logger(__name__)


def _header(headers: dict[str, str], name: str) -> str:
    """Return the case-insensitive header value, or ``""`` when absent.

    Returns:
        The matching header value, or an empty string.
    """
    return next(
        (value for key, value in headers.items() if compare_ci(key, name)),
        "",
    )


class GiteaHmacVerifier(GenericHmacVerifier):
    """Verifies Gitea webhook signatures (raw-hex HMAC-SHA256)."""

    def __init__(self) -> None:
        super().__init__(header_name="x-gitea-signature")


class ForgejoHmacVerifier:
    """Verifies Forgejo webhook signatures (raw-hex HMAC-SHA256).

    Accepts the digest in ``X-Forgejo-Signature`` (preferred) or the
    legacy ``X-Gitea-Signature`` Forgejo still emits.
    """

    _PRIMARY_HEADER = "x-forgejo-signature"
    _LEGACY_HEADER = "x-gitea-signature"

    @property
    def signature_header(self) -> str:
        """HTTP header name containing the signature.

        Returns:
            The preferred Forgejo signature header.
        """
        return self._PRIMARY_HEADER

    async def verify(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        secret: str,
    ) -> bool:
        """Verify a Forgejo webhook signature against either header.

        Returns:
            ``True`` when an HMAC-SHA256 hex digest in either the Forgejo
            or legacy Gitea header matches the expected digest.
        """
        received = _header(headers, self._PRIMARY_HEADER) or _header(
            headers,
            self._LEGACY_HEADER,
        )
        if not received:
            logger.warning(
                WEBHOOK_SIGNATURE_INVALID,
                provider="forgejo",
                reason="empty signature",
            )
            return False
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        valid = hmac.compare_digest(expected, received)
        if valid:
            logger.debug(WEBHOOK_SIGNATURE_VERIFIED, provider="forgejo")
        else:
            logger.warning(
                WEBHOOK_SIGNATURE_INVALID,
                provider="forgejo",
                reason="digest mismatch",
            )
        return valid


class GitLabTokenVerifier:
    """Verifies GitLab webhooks via the shared-secret token.

    GitLab echoes the configured webhook secret verbatim in the
    ``X-Gitlab-Token`` header rather than signing the body, so
    verification is a constant-time comparison against the secret.
    """

    @property
    def signature_header(self) -> str:
        """HTTP header name containing the shared-secret token.

        Returns:
            The GitLab token header name.
        """
        return "x-gitlab-token"

    async def verify(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        secret: str,
    ) -> bool:
        """Verify the GitLab shared-secret token.

        Args:
            body: Raw request body (unused; GitLab does not sign it).
            headers: Request headers.
            secret: Configured webhook secret.

        Returns:
            ``True`` when ``X-Gitlab-Token`` equals the configured secret.
        """
        del body
        received = _header(headers, self.signature_header)
        if not received:
            logger.warning(
                WEBHOOK_SIGNATURE_INVALID,
                provider="gitlab",
                reason="empty token",
            )
            return False
        valid = hmac.compare_digest(received, secret)
        if valid:
            logger.debug(WEBHOOK_SIGNATURE_VERIFIED, provider="gitlab")
        else:
            logger.warning(
                WEBHOOK_SIGNATURE_INVALID,
                provider="gitlab",
                reason="token mismatch",
            )
        return valid


__all__ = [
    "ForgejoHmacVerifier",
    "GitLabTokenVerifier",
    "GiteaHmacVerifier",
]
