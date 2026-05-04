"""Custom exception hierarchy for configuration errors."""

from dataclasses import dataclass
from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


@dataclass(frozen=True)
class ConfigLocation:
    """Source location for a configuration error.

    Attributes:
        file_path: Path to the configuration file.
        key_path: Dot-separated path to the key
            (e.g. ``"budget.alerts.warn_at"``).
        line: Line number in the file (1-based).
        column: Column number in the file (1-based).
    """

    file_path: str | None = None
    key_path: str | None = None
    line: int | None = None
    column: int | None = None


class ConfigError(DomainError):
    """Base exception for configuration errors.

    Attributes:
        message: Human-readable error description.
        locations: Source locations associated with this error.
    """

    default_message: ClassVar[str] = "Configuration error"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500

    message: str
    locations: tuple[ConfigLocation, ...]

    def __init__(
        self,
        message: str,
        locations: tuple[ConfigLocation, ...] = (),
    ) -> None:
        self.message = message
        self.locations = locations
        super().__init__(message)

    def __str__(self) -> str:
        """Format error message with source locations."""
        if not self.locations:
            return self.message
        parts = [self.message]
        for loc in self.locations:
            loc_parts: list[str] = []
            if loc.key_path:
                loc_parts.append(f"  {loc.key_path}")
            if loc.file_path:
                if loc.line is not None and loc.column is not None:
                    line_info = f" at line {loc.line}, column {loc.column}"
                elif loc.line is not None:
                    line_info = f" at line {loc.line}"
                else:
                    line_info = ""
                loc_parts.append(f"    in {loc.file_path}{line_info}")
            parts.extend(loc_parts)
        return "\n".join(parts)


class ConfigFileNotFoundError(ConfigError):
    """Raised when a configuration file does not exist."""

    default_message: ClassVar[str] = "Configuration file not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_NOT_FOUND
    status_code: ClassVar[int] = 404


class ConfigParseError(ConfigError):
    """Raised when YAML parsing fails."""

    default_message: ClassVar[str] = "Configuration file parse error"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    status_code: ClassVar[int] = 400


class ConfigValidationError(ConfigError):
    """Raised when Pydantic validation fails.

    Attributes:
        field_errors: Per-field error messages as
            ``(key_path, message)`` pairs.
    """

    default_message: ClassVar[str] = "Configuration validation error"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    status_code: ClassVar[int] = 422

    field_errors: tuple[tuple[str, str], ...]

    def __init__(
        self,
        message: str,
        locations: tuple[ConfigLocation, ...] = (),
        field_errors: tuple[tuple[str, str], ...] = (),
    ) -> None:
        super().__init__(message, locations)
        self.field_errors = field_errors

    def __str__(self) -> str:
        """Format validation error with per-field details."""
        if not self.field_errors:
            return super().__str__()
        parts = [f"{self.message} ({len(self.field_errors)} errors):"]
        loc_by_key: dict[str, ConfigLocation] = {
            loc.key_path: loc for loc in self.locations if loc.key_path
        }
        for key_path, msg in self.field_errors:
            parts.append(f"  {key_path}: {msg}")
            loc = loc_by_key.get(key_path)
            if loc and loc.file_path:
                if loc.line is not None and loc.column is not None:
                    line_info = f" at line {loc.line}, column {loc.column}"
                elif loc.line is not None:
                    line_info = f" at line {loc.line}"
                else:
                    line_info = ""
                parts.append(f"    in {loc.file_path}{line_info}")
        return "\n".join(parts)
