"""Signature verifier protocol for webhook payloads."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class SignatureVerifier(Protocol):
    """Verifies the cryptographic signature of an incoming webhook.

    Each external service uses a different signing scheme.
    Implementations handle the specifics (HMAC-SHA256, Slack's
    v0 timestamp scheme, etc.).
    """

    @property
    def signature_header(self) -> str:
        """HTTP header name containing the signature."""
        ...

    @property
    def delivery_id_header(self) -> str | None:
        """Header carrying the provider's own single-use delivery id.

        Read as the replay nonce. Each provider names this differently
        (``X-GitHub-Delivery``, ``X-Gitea-Delivery``, ...), and none of them
        sends the generic ``X-Nonce`` / ``X-Request-Id`` the ingest path used to
        look for on its own, so without this a genuine delivery arrives with no
        nonce and no timestamp and the replay gate refuses it.

        A provider delivery id is minted once per delivery and repeated on the
        provider's own retries, which is exactly what nonce dedup needs: a retry
        collapses, a replay with a fresh id does not get a free pass.

        ``None`` for a scheme that binds freshness into the signature itself
        (Slack signs its timestamp), where there is no separate id to read.
        """
        ...

    async def verify(
        self,
        *,
        body: bytes,
        headers: dict[str, str],
        secret: str,
    ) -> bool:
        """Verify the webhook signature.

        Args:
            body: Raw request body bytes.
            headers: Request headers (lowercased keys).
            secret: Signing secret from the connection catalog.

        Returns:
            ``True`` if the signature is valid.
        """
        ...
