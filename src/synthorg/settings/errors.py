"""Error hierarchy for the settings persistence layer."""


class SettingsError(Exception):
    """Base exception for all settings-related errors."""


class SettingNotFoundError(SettingsError):
    """Raised when a setting key is not found in the registry."""


class SettingValidationError(SettingsError):
    """Raised when a setting value fails type, range, or pattern validation."""


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
