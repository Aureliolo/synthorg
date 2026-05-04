"""Blueprint-specific error types."""

from typing import ClassVar

from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class BlueprintNotFoundError(NotFoundError):
    """Raised when a workflow blueprint cannot be found."""

    default_message: ClassVar[str] = "Workflow blueprint not found"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_NOT_FOUND
    status_code: ClassVar[int] = 404


class BlueprintValidationError(ValidationError):
    """Raised when a blueprint YAML fails schema validation."""

    default_message: ClassVar[str] = "Workflow blueprint validation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    status_code: ClassVar[int] = 422
