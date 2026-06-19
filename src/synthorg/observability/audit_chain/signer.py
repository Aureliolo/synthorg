# module-kind: adapter
"""Ed25519 audit-chain signer.

Concrete :class:`~synthorg.observability.audit_chain.protocol.AuditChainSigner`
backed by Ed25519 from the ``cryptography`` dependency already shipped for
secret encryption. The classical Ed25519 scheme is the baseline arm of the
audit chain (the ``backend="asqav"`` config slot reserves a future
quantum-safe ML-DSA-65 arm); it gives every signed security event a
tamper-evident signature without pulling in an extra dependency.

The private key is loaded from a PEM file when ``signing_key_path`` is set,
or generated ephemerally otherwise. An ephemeral key signs for the life of
the process only -- a restart cannot verify signatures from a prior run --
so the generation path logs a WARNING directing operators to a persistent
key for durable verification.
"""

from pathlib import Path
from typing import Final

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
)

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.audit_chain.protocol import SignedPayload
from synthorg.observability.events.audit_chain import (
    AUDIT_CHAIN_SIGNER_KEY_GENERATED,
    AUDIT_CHAIN_SIGNER_KEY_LOADED,
)

logger = get_logger(__name__)

_ALGORITHM: Final[str] = "ed25519"
_SIGNER_ID_PREFIX_LEN: Final[int] = 16


class Ed25519AuditChainSigner:
    """Audit-chain signer using an Ed25519 keypair.

    Args:
        private_key: The Ed25519 private key used for signing.
        clock: Clock seam for the signature timestamp.
    """

    __slots__ = ("_clock", "_private_key", "_public_key", "_signer_id")

    def __init__(
        self,
        private_key: Ed25519PrivateKey,
        *,
        clock: Clock | None = None,
    ) -> None:
        self._private_key = private_key
        self._public_key = private_key.public_key()
        self._clock = clock or SystemClock()
        raw = self._public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
        self._signer_id = NotBlankStr(
            f"ed25519:{raw.hex()[:_SIGNER_ID_PREFIX_LEN]}",
        )

    @property
    def algorithm(self) -> str:
        """Signature algorithm name.

        Returns:
            The fixed ``"ed25519"`` identifier.
        """
        return _ALGORITHM

    @property
    def signer_id(self) -> NotBlankStr:
        """Stable signer identity derived from the public key.

        Returns:
            ``ed25519:<public-key-hex-prefix>``.
        """
        return self._signer_id

    async def sign(self, data: bytes) -> SignedPayload:
        """Sign *data* with the Ed25519 private key.

        Args:
            data: Raw bytes to sign.

        Returns:
            The signed payload with signature + metadata.
        """
        signature = self._private_key.sign(data)
        return SignedPayload(
            signature=signature,
            algorithm=NotBlankStr(_ALGORITHM),
            signer_id=self._signer_id,
            signed_at=self._clock.now(),
        )

    async def verify(self, data: bytes, signature: bytes) -> bool:
        """Verify an Ed25519 *signature* over *data*.

        Args:
            data: Original data bytes.
            signature: Signature bytes to verify.

        Returns:
            ``True`` when the signature is valid, ``False`` otherwise.
        """
        from cryptography.exceptions import (  # noqa: PLC0415
            InvalidSignature,
        )

        try:
            self._public_key.verify(signature, data)
        except InvalidSignature:
            return False
        return True


def _load_signing_key(signing_key_path: Path) -> object | None:
    """Read + parse a PEM private key, distinguishing the failure modes.

    Splits the file read from the PEM parse so the WARNING ``reason``
    tells an operator whether the key file could not be read (permission
    / I/O) or was unparseable (corrupt / password-protected), instead of
    collapsing both into one opaque ``key_load_failed``.

    Returns:
        The loaded private-key object, or ``None`` when the key could
        not be read or parsed (the caller then falls back to an
        ephemeral key).
    """
    try:
        raw = signing_key_path.read_bytes()
    except OSError as exc:
        logger.warning(
            AUDIT_CHAIN_SIGNER_KEY_GENERATED,
            reason="key_read_failed",
            path=str(signing_key_path),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None
    try:
        return serialization.load_pem_private_key(raw, password=None)
    except Exception as exc:  # noqa: BLE001 -- fall back to ephemeral
        reraise_critical(exc)
        logger.warning(
            AUDIT_CHAIN_SIGNER_KEY_GENERATED,
            reason="key_parse_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None


def build_ed25519_signer(
    signing_key_path: Path | None,
    *,
    clock: Clock | None = None,
) -> Ed25519AuditChainSigner:
    """Build an Ed25519 signer, loading or generating the keypair.

    Loads a PEM private key from *signing_key_path* when set and
    readable; otherwise generates an ephemeral key and logs a WARNING
    (signatures from an ephemeral key cannot be verified after a
    restart).

    Args:
        signing_key_path: Optional path to a PEM Ed25519 private key.
        clock: Clock seam for signature timestamps.

    Returns:
        A constructed :class:`Ed25519AuditChainSigner`.
    """
    resolved_clock = clock or SystemClock()
    if signing_key_path is not None and signing_key_path.is_file():
        loaded = _load_signing_key(signing_key_path)
        if loaded is not None:
            if isinstance(loaded, Ed25519PrivateKey):
                logger.info(
                    AUDIT_CHAIN_SIGNER_KEY_LOADED,
                    path=str(signing_key_path),
                )
                return Ed25519AuditChainSigner(loaded, clock=resolved_clock)
            logger.warning(
                AUDIT_CHAIN_SIGNER_KEY_GENERATED,
                reason="key_not_ed25519",
                key_type=type(loaded).__name__,
            )

    logger.warning(
        AUDIT_CHAIN_SIGNER_KEY_GENERATED,
        reason="no_persistent_key",
        note=(
            "generated an ephemeral Ed25519 key; audit signatures from this "
            "process cannot be verified after a restart -- set "
            "audit_chain.signing_key_path to a persistent PEM key"
        ),
    )
    return Ed25519AuditChainSigner(
        Ed25519PrivateKey.generate(),
        clock=resolved_clock,
    )


__all__ = ["Ed25519AuditChainSigner", "build_ed25519_signer"]
