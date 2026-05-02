"""Ontology subsystem error hierarchy."""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class OntologyError(DomainError):
    """Base exception for all ontology errors.

    Class Attributes:
        status_code: Default HTTP 500 for generic ontology failures.
        error_code: Default RFC 9457 error code; subclasses override.
        error_category: ``INTERNAL``.
        retryable: ``False``.
        default_message: Generic 5xx-safe message.
    """

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.ONTOLOGY_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    retryable: ClassVar[bool] = False
    default_message: ClassVar[str] = "Ontology error"


class OntologyConnectionError(OntologyError):
    """Backend connection management failed."""

    status_code: ClassVar[int] = 503
    error_code: ClassVar[ErrorCode] = ErrorCode.SERVICE_UNAVAILABLE
    retryable: ClassVar[bool] = True
    default_message: ClassVar[str] = "Ontology backend unavailable"


class OntologyNotFoundError(OntologyError):
    """Requested entity definition does not exist."""

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.ONTOLOGY_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Ontology entity not found"


class OntologyDuplicateError(OntologyError):
    """Entity definition with duplicate name."""

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.ONTOLOGY_DUPLICATE
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = "Ontology entity already exists"


class OntologyConfigError(OntologyError):
    """Ontology configuration is invalid."""

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Ontology configuration is invalid"
