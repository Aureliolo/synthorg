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
