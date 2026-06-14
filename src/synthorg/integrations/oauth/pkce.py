"""PKCE (RFC 7636) utilities for OAuth 2.1 authorization code flows.

Provides code verifier generation, SHA-256 code challenge
computation, and symmetric at-rest encryption for the verifier.

The verifier is ephemeral but persisted in ``oauth_states`` between
the authorization request and the code exchange. Storing it in
plaintext means a database leak gives an attacker the verifier
needed to complete an intercepted authorization code. We wrap the
verifier in Fernet symmetric encryption using the same master key
as the encrypted_sqlite secret backend, so the DB row alone is not
sufficient to recover it.
"""

import base64
import hashlib
import os
import re
import secrets
from threading import Lock
from typing import Final

from cryptography.fernet import Fernet, InvalidToken

from synthorg.integrations.errors import MasterKeyError, PKCEValidationError
from synthorg.observability import get_logger

logger = get_logger(__name__)

_UNRESERVED_RE = re.compile(r"^[A-Za-z0-9\-._~]+$")
_VERIFIER_LENGTH: Final[int] = 128
_MIN_VERIFIER_LENGTH: Final[int] = 43
_MAX_VERIFIER_LENGTH: Final[int] = 128

_MASTER_KEY_ENV = "SYNTHORG_MASTER_KEY"


class OAuthPKCECipher:
    """Fernet cipher for at-rest encryption of OAuth PKCE verifiers.

    Validates the Fernet key at construction so a missing or corrupt
    key fails loudly at the wiring edge rather than mid-flight on the
    first OAuth operation.

    Args:
        master_key: URL-safe base64 Fernet key (ASCII).

    Raises:
        MasterKeyError: If *master_key* is blank or not a valid Fernet
            key.
    """

    __slots__ = ("_fernet",)

    def __init__(self, master_key: str) -> None:
        raw = master_key.strip()
        if not raw:
            msg = (
                f"{_MASTER_KEY_ENV} must be set to a valid Fernet key "
                f"to encrypt OAuth PKCE verifiers at rest"
            )
            raise MasterKeyError(msg)
        try:
            self._fernet = Fernet(raw.encode("ascii"))
        except (ValueError, TypeError) as exc:
            msg = f"Invalid Fernet key in {_MASTER_KEY_ENV}"
            raise MasterKeyError(msg) from exc

    @classmethod
    def from_env(cls, *, env_var: str = _MASTER_KEY_ENV) -> OAuthPKCECipher:
        """Construct from the master-key environment variable.

        Cat-3 bootstrap secret: the Fernet key is a pure-env boot secret
        read at construction time, mirroring the encrypted secret
        backends.

        Args:
            env_var: Environment variable holding the Fernet key.

        Returns:
            A validated ``OAuthPKCECipher``.

        Raises:
            MasterKeyError: If the env var is unset or holds an invalid
                key.
        """
        return cls(os.environ.get(env_var, ""))

    def encrypt(self, verifier: str) -> str:
        """Encrypt *verifier* and return URL-safe Fernet ciphertext.

        Returns:
            URL-safe Fernet ciphertext (ASCII).
        """
        return self._fernet.encrypt(verifier.encode("ascii")).decode("ascii")

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt Fernet *ciphertext* back to the verifier plaintext.

        Returns:
            The decrypted verifier plaintext.

        Raises:
            InvalidToken: If the ciphertext cannot be decrypted.
        """
        return self._fernet.decrypt(ciphertext.encode("ascii")).decode("ascii")


_cipher_lock = Lock()
_cipher_holder: list[OAuthPKCECipher | None] = [None]


def init_pkce_cipher() -> None:
    """Eagerly construct + validate the PKCE cipher at startup.

    Best-effort boot hook: when ``SYNTHORG_MASTER_KEY`` is present it is
    validated now so a corrupt or rotated key fails loudly at integration
    wiring instead of mid-flight on the first OAuth exchange. When the
    key is absent this is a no-op (OAuth is optional; a deployment that
    never runs an OAuth flow needs no master key, and an OAuth op without
    a key still raises ``MasterKeyError`` at use).

    Raises:
        MasterKeyError: If the key is present but not a valid Fernet key.
    """
    with _cipher_lock:
        if _cipher_holder[0] is not None:
            return
        if not os.environ.get(_MASTER_KEY_ENV, "").strip():
            return
        _cipher_holder[0] = OAuthPKCECipher.from_env()


def _get_cipher() -> OAuthPKCECipher:
    """Return the PKCE cipher, constructing it on first use if needed.

    Raises:
        MasterKeyError: If ``SYNTHORG_MASTER_KEY`` is unset or invalid.
    """
    with _cipher_lock:
        cached = _cipher_holder[0]
        if cached is not None:
            return cached
        cipher = OAuthPKCECipher.from_env()
        _cipher_holder[0] = cipher
        return cipher


def _reset_cipher_for_tests() -> None:
    """Reset the cached cipher. Test-only hook for env-var changes."""
    with _cipher_lock:
        _cipher_holder[0] = None


def encrypt_pkce_verifier(verifier: str) -> str:
    """Encrypt a PKCE verifier for at-rest storage.

    Args:
        verifier: Raw verifier string.

    Returns:
        URL-safe Fernet ciphertext (ASCII).

    Raises:
        MasterKeyError: If the master key is not configured.
    """
    validate_code_verifier(verifier)
    cipher = _get_cipher()
    return cipher.encrypt(verifier)


def decrypt_pkce_verifier(ciphertext: str) -> str:
    """Decrypt a stored PKCE verifier.

    Args:
        ciphertext: Fernet ciphertext produced by
            :func:`encrypt_pkce_verifier`.

    Returns:
        The original verifier plaintext.

    Raises:
        PKCEValidationError: If the ciphertext cannot be decrypted
            (tamper, wrong key, or plaintext stored instead of
            ciphertext).
    """
    cipher = _get_cipher()
    try:
        # ``decrypt`` ascii-encodes the ciphertext and ascii-decodes the
        # plaintext internally, so a ``UnicodeDecodeError`` (non-ASCII
        # bytes in a corrupted row) is caught here alongside the
        # decrypt-side failures and translated into a structured
        # ``PKCEValidationError`` rather than leaking as a 500.
        return cipher.decrypt(ciphertext)
    except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError) as exc:
        # Corrupted persisted data can fail in multiple ways:
        # ``InvalidToken`` (tamper / wrong key) or a Unicode error
        # (non-ASCII bytes in the pkce_verifier column or its
        # decoded plaintext). Translate all of them into a structured
        # ``PKCEValidationError`` so the controller layer returns
        # a 400 instead of leaking as an unhandled 500.
        from synthorg.observability.events.integrations import (  # noqa: PLC0415
            OAUTH_PKCE_VALIDATION_FAILED,
        )

        logger.warning(
            OAUTH_PKCE_VALIDATION_FAILED,
            error=f"verifier decrypt failed: {type(exc).__name__}",
        )
        msg = "Failed to decrypt stored PKCE verifier"
        raise PKCEValidationError(msg) from exc


def generate_code_verifier() -> str:
    """Generate a PKCE code verifier.

    Returns a 128-character string using only unreserved characters
    (``[A-Za-z0-9-._~]``) as required by RFC 7636 section 4.1.

    Returns:
        A random code verifier string.
    """
    raw = secrets.token_urlsafe(_VERIFIER_LENGTH)
    return raw[:_VERIFIER_LENGTH]


def generate_code_challenge(verifier: str) -> str:
    """Compute a PKCE S256 code challenge from a verifier.

    Args:
        verifier: A valid PKCE code verifier.

    Returns:
        Base64url-encoded SHA-256 digest (no padding).

    Raises:
        PKCEValidationError: If the verifier is invalid.
    """
    validate_code_verifier(verifier)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def validate_code_verifier(verifier: str) -> None:
    """Validate a PKCE code verifier.

    Args:
        verifier: The verifier to validate.

    Raises:
        PKCEValidationError: If the verifier does not meet RFC 7636
            requirements.
    """
    from synthorg.observability.events.integrations import (  # noqa: PLC0415
        OAUTH_PKCE_VALIDATION_FAILED,
    )

    length = len(verifier)
    if length < _MIN_VERIFIER_LENGTH or length > _MAX_VERIFIER_LENGTH:
        logger.warning(
            OAUTH_PKCE_VALIDATION_FAILED,
            error="length out of range",
            length=length,
        )
        msg = (
            f"Code verifier must be {_MIN_VERIFIER_LENGTH}-"
            f"{_MAX_VERIFIER_LENGTH} characters, got {length}"
        )
        raise PKCEValidationError(msg)
    if not _UNRESERVED_RE.match(verifier):
        logger.warning(
            OAUTH_PKCE_VALIDATION_FAILED,
            error="invalid characters",
        )
        msg = "Code verifier contains invalid characters"
        raise PKCEValidationError(msg)


def validate_code_challenge(verifier: str, challenge: str) -> None:
    """Validate a PKCE code challenge against its verifier.

    Args:
        verifier: The original code verifier.
        challenge: The challenge to validate.

    Raises:
        PKCEValidationError: If the challenge does not match.
    """
    from synthorg.observability.events.integrations import (  # noqa: PLC0415
        OAUTH_PKCE_VALIDATION_FAILED,
    )

    expected = generate_code_challenge(verifier)
    if not secrets.compare_digest(expected, challenge):
        logger.warning(
            OAUTH_PKCE_VALIDATION_FAILED,
            error="challenge mismatch",
        )
        msg = "Code challenge does not match verifier"
        raise PKCEValidationError(msg)
