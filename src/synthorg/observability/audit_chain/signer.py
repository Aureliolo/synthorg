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

from synthorg.config.errors import ConfigError
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.audit_chain.protocol import SignedPayload
from synthorg.observability.events.audit_chain import (
    AUDIT_CHAIN_SIGNER_KEY_GENERATED,
    AUDIT_CHAIN_SIGNER_KEY_LOAD_FAILED,
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


def _load_ed25519_key(signing_key_path: Path) -> Ed25519PrivateKey:
    """Read + parse a configured PEM Ed25519 private key, fail-closed.

    Splits the file read from the PEM parse and the key-type check so the
    raised :class:`ConfigError` ``reason`` tells an operator whether the
    key file could not be read (permission / I/O), was unparseable
    (corrupt / password-protected), or was the wrong key type. A
    configured key path is a hard requirement: any failure raises rather
    than silently degrading to an unverifiable ephemeral key.

    Returns:
        The loaded Ed25519 private key.

    Raises:
        ConfigError: When the key cannot be read, parsed, or is not an
            Ed25519 private key.
    """
    try:
        raw = signing_key_path.read_bytes()
    except OSError as exc:
        reason = "key_read_failed"
        logger.error(
            AUDIT_CHAIN_SIGNER_KEY_LOAD_FAILED,
            reason=reason,
            path=str(signing_key_path),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = (
            f"audit_chain.signing_key_path {str(signing_key_path)!r} could "
            f"not be read ({reason})"
        )
        raise ConfigError(msg) from exc
    try:
        loaded = serialization.load_pem_private_key(raw, password=None)
    except Exception as exc:
        reraise_critical(exc)
        reason = "key_parse_failed"
        logger.error(
            AUDIT_CHAIN_SIGNER_KEY_LOAD_FAILED,
            reason=reason,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = (
            f"audit_chain.signing_key_path {str(signing_key_path)!r} could "
            f"not be parsed as a PEM private key ({reason})"
        )
        raise ConfigError(msg) from exc
    if not isinstance(loaded, Ed25519PrivateKey):
        reason = "key_not_ed25519"
        logger.error(
            AUDIT_CHAIN_SIGNER_KEY_LOAD_FAILED,
            reason=reason,
            key_type=type(loaded).__name__,
        )
        msg = (
            f"audit_chain.signing_key_path {str(signing_key_path)!r} is a "
            f"{type(loaded).__name__}, not an Ed25519 private key ({reason})"
        )
        raise ConfigError(msg)
    return loaded


def build_ed25519_signer(
    signing_key_path: Path | None,
    *,
    clock: Clock | None = None,
) -> Ed25519AuditChainSigner:
    """Build an Ed25519 signer, loading or generating the keypair.

    When *signing_key_path* is set it is treated as a hard requirement:
    the key is loaded from that PEM file and any failure (missing,
    unreadable, unparseable, or wrong key type) raises a
    :class:`ConfigError` so boot stops loudly rather than silently
    degrading to an unverifiable ephemeral key. When no path is
    configured an ephemeral key is generated and a WARNING is logged
    (signatures from an ephemeral key cannot be verified after a
    restart).

    Args:
        signing_key_path: Optional path to a PEM Ed25519 private key.
        clock: Clock seam for signature timestamps.

    Returns:
        A constructed :class:`Ed25519AuditChainSigner`.

    Raises:
        ConfigError: When *signing_key_path* is set but does not yield a
            usable Ed25519 private key.
    """
    resolved_clock = clock or SystemClock()
    if signing_key_path is not None:
        if not signing_key_path.is_file():
            reason = "key_path_missing"
            logger.error(
                AUDIT_CHAIN_SIGNER_KEY_LOAD_FAILED,
                reason=reason,
                path=str(signing_key_path),
            )
            msg = (
                f"audit_chain.signing_key_path {str(signing_key_path)!r} does "
                f"not exist or is not a file ({reason})"
            )
            raise ConfigError(msg)
        loaded = _load_ed25519_key(signing_key_path)
        logger.info(AUDIT_CHAIN_SIGNER_KEY_LOADED, path=str(signing_key_path))
        return Ed25519AuditChainSigner(loaded, clock=resolved_clock)

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
