"""Error hierarchy for the settings persistence layer."""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class SettingsError(DomainError):
    """Base exception for all settings-related errors."""

    default_message: ClassVar[str] = "Settings operation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500


class SettingNotFoundError(SettingsError):
    """Raised when a setting key is not found in the registry."""

    default_message: ClassVar[str] = "Setting not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_NOT_FOUND
    status_code: ClassVar[int] = 404


class SettingValidationError(SettingsError):
    """Raised when a setting value fails type, range, or pattern validation."""

    default_message: ClassVar[str] = "Setting validation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    status_code: ClassVar[int] = 422


class SettingReadOnlyError(SettingValidationError):
    """Raised when a write is attempted on a ``read_only_post_init`` setting.

    Inherits from :class:`SettingValidationError` so the existing HTTP 422
    error mapping in the API controllers picks it up without changes.
    These settings are sourced from environment variables or YAML at
    process startup and cannot be overridden by ``SettingsService.set()``
    or related mutations -- the registry entry exists for discoverability
    only.
    """


class SettingsEncryptionError(SettingsError):
    """Raised when encryption key is unavailable or decryption fails."""


class SettingsEncryptionFailedError(SettingsError):
    """API-boundary 500 when a sensitive setting cannot be processed.

    Distinct ``error_code`` (``SETTINGS_ENCRYPTION_ERROR``) so a client
    can tell "the server could not encrypt/decrypt this value" apart
    from a generic internal error. The controller raises this after a
    low-level :class:`SettingsEncryptionError`; the scrubbed message
    keeps key/cipher detail out of the response.
    """

    default_message: ClassVar[str] = "Internal error processing sensitive setting"
    error_code: ClassVar[ErrorCode] = ErrorCode.SETTINGS_ENCRYPTION_ERROR


class SinkConfigValidationError(SettingsError):
    """API-boundary 500 when an observability sink config check fails.

    Raised by the settings controller's sink-config test endpoint when
    validation itself errors unexpectedly (not a user-visible invalid
    config, which returns a structured ``valid=False`` body). Distinct
    ``error_code`` (``SINK_CONFIG_VALIDATION_ERROR``) so operators can
    alert on broken sink validation specifically.
    """

    default_message: ClassVar[str] = "Internal error validating sink configuration"
    error_code: ClassVar[ErrorCode] = ErrorCode.SINK_CONFIG_VALIDATION_ERROR


class SettingsRegistryError(SettingsError):
    """Raised when the registry lookup itself fails its own invariants.

    Distinct from :class:`SettingNotFoundError` (404 -- API-surface
    "the requested setting does not exist") because registry lookup
    failures are internal programming errors: the consumer asked for
    a registered default before the namespace module was imported,
    or the registered default is malformed (e.g. a boolean default
    that is not ``"true"`` / ``"false"``).  HTTP 500 / INTERNAL_ERROR
    is the correct status for these.
    """

    default_message: ClassVar[str] = "Settings registry lookup failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500
