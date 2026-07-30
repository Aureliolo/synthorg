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

        Each provider names this differently (``X-GitHub-Delivery``,
        ``X-Gitea-Delivery``, ...) and none of them sends a generic ``X-Nonce``,
        so the name has to come from the scheme that knows it.

        Logged for traceability, and deliberately NOT used for deduplication:
        the id sits outside everything :meth:`verify` covers, so an attacker
        holding one captured signed body could replay it indefinitely by varying
        the id. Dedup keys on the delivery identity instead (connection + body
        digest), which the signature does cover.

        ``None`` for a scheme that sends no id of its own (Slack and A2A bind
        freshness into the signature by signing a timestamp).
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
