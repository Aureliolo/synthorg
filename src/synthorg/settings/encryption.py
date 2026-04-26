"""Fernet encryption for sensitive settings.

Values marked ``sensitive=True`` in the settings registry are encrypted
at rest using Fernet symmetric encryption.  The encryption key is read
from the ``SYNTHORG_SETTINGS_KEY`` environment variable.
"""

import os

from cryptography.fernet import Fernet, InvalidToken

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import SETTINGS_ENCRYPTION_ERROR
from synthorg.settings.errors import SettingsEncryptionError

logger = get_logger(__name__)

_ENV_VAR = "SYNTHORG_SETTINGS_KEY"


class SettingsEncryptor:
    """Fernet encrypt/decrypt wrapper for sensitive setting values.

    Args:
        key: A valid Fernet key (URL-safe base64-encoded 32-byte key).
    """

    def __init__(self, key: bytes) -> None:
        self._fernet = Fernet(key)

    def encrypt(self, plaintext: str) -> str:
        """Encrypt a plaintext string.

        Args:
            plaintext: The value to encrypt.

        Returns:
            Base64-encoded ciphertext string.

        Raises:
            SettingsEncryptionError: If encryption fails.
        """
        try:
            return self._fernet.encrypt(
                plaintext.encode("utf-8"),
            ).decode("ascii")
        except (ValueError, TypeError, UnicodeEncodeError) as exc:
            logger.warning(
                SETTINGS_ENCRYPTION_ERROR,
                operation="encrypt",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to encrypt setting value"
            raise SettingsEncryptionError(msg) from exc

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a ciphertext string.

        Args:
            ciphertext: Base64-encoded ciphertext to decrypt.

        Returns:
            The decrypted plaintext string.

        Raises:
            SettingsEncryptionError: If decryption fails (wrong key,
                corrupted data, etc.).
        """
        try:
            return self._fernet.decrypt(
                ciphertext.encode("ascii"),
            ).decode("utf-8")
        except InvalidToken as exc:
            logger.warning(
                SETTINGS_ENCRYPTION_ERROR,
                operation="decrypt",
                error_type=type(exc).__name__,
                error="invalid token or wrong key",
            )
            msg = "Failed to decrypt setting value -- wrong key or corrupted data"
            raise SettingsEncryptionError(msg) from exc
        except (ValueError, TypeError, UnicodeDecodeError) as exc:
            logger.warning(
                SETTINGS_ENCRYPTION_ERROR,
                operation="decrypt",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "Failed to decrypt setting value"
            raise SettingsEncryptionError(msg) from exc

    @classmethod
    def from_env(cls) -> SettingsEncryptor:
        """Create an encryptor from the ``SYNTHORG_SETTINGS_KEY`` env var.

        The encryption key is required for the application to start.
        It must be set explicitly via the environment variable;
        ``synthorg init`` generates one automatically.

        Returns:
            An encryptor instance.

        Raises:
            SettingsEncryptionError: If the env var is not set, empty,
                or contains an invalid key.
        """
        raw_or_none = os.environ.get(_ENV_VAR)
        if raw_or_none is None:
            msg = (
                f"{_ENV_VAR} is not set -- the encryption key is "
                f"required. Run 'synthorg init' to generate one, or set "
                f"it manually (URL-safe base64 encoding of 32 raw bytes, "
                f"i.e. a 44-character string)."
            )
            logger.error(
                SETTINGS_ENCRYPTION_ERROR,
                operation="from_env",
                error=msg,
            )
            raise SettingsEncryptionError(msg)
        raw = raw_or_none.strip()
        if not raw:
            msg = (
                f"{_ENV_VAR} is set but empty -- provide a valid Fernet "
                f"key (URL-safe base64 encoding of 32 raw bytes)"
            )
            logger.error(
                SETTINGS_ENCRYPTION_ERROR,
                operation="from_env",
                error=msg,
            )
            raise SettingsEncryptionError(msg)
        try:
            return cls(raw.encode("ascii"))
        except (ValueError, TypeError, UnicodeEncodeError) as exc:
            logger.warning(
                SETTINGS_ENCRYPTION_ERROR,
                operation="from_env",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Invalid Fernet key in {_ENV_VAR}"
            raise SettingsEncryptionError(msg) from exc
