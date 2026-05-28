"""AuditChainSigner protocol and SignedPayload model."""

from datetime import datetime
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr


class SignedPayload(BaseModel):
    """Result of signing a data payload.

    Attributes:
        signature: Raw signature bytes.
        algorithm: Signature algorithm identifier.
        signer_id: Identity of the signer.
        signed_at: Timestamp of signature creation.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    signature: bytes = Field(description="Raw signature bytes")
    algorithm: NotBlankStr = Field(description="Signature algorithm")
    signer_id: NotBlankStr = Field(description="Signer identity")
    signed_at: datetime = Field(description="Signature timestamp")


@runtime_checkable
class AuditChainSigner(Protocol):
    """Protocol for audit chain signing backends.

    Implementations provide quantum-safe (ML-DSA-65) or classical
    (Ed25519) signing and verification.
    """

    @property
    def algorithm(self) -> str:
        """Signature algorithm name (e.g. ``"ml-dsa-65"``)."""
        ...

    async def sign(self, data: bytes) -> SignedPayload:
        """Sign data and return a signed payload.

        Args:
            data: Raw bytes to sign.

        Returns:
            Signed payload with signature and metadata.
        """
        ...

    async def verify(self, data: bytes, signature: bytes) -> bool:
        """Verify a signature over data.

        Args:
            data: Original data bytes.
            signature: Signature bytes to verify.

        Returns:
            ``True`` if signature is valid.
        """
        ...
