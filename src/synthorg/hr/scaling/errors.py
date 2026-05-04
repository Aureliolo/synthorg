"""Scaling domain exceptions."""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class ScalingError(DomainError):
    """Base exception for scaling operations."""

    default_message: ClassVar[str] = "Scaling operation failed"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    error_code: ClassVar[ErrorCode] = ErrorCode.INTERNAL_ERROR
    status_code: ClassVar[int] = 500


class ScalingStrategyError(ScalingError):
    """A strategy evaluation failed."""


class ScalingGuardError(ScalingError):
    """A guard evaluation failed."""


class ScalingExecutionError(ScalingError):
    """Executing a scaling decision (hire/prune) failed."""


class ScalingCooldownActiveError(ScalingError):
    """Action blocked by an active cooldown window."""

    default_message: ClassVar[str] = "Scaling blocked by active cooldown"
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_CONFLICT
    status_code: ClassVar[int] = 409
