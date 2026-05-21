"""Domain error hierarchy for the virtual desktop tool.

Every failure path raises a ``<Desktop><Condition>Error`` subclass of
:class:`synthorg.tools.errors.ToolError` so the
``check_domain_error_hierarchy.py`` gate stays clean and callers can
discriminate failures by ``error_code`` / class.
"""

from typing import ClassVar

from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.tools.errors import ToolError


class DesktopDomainError(ToolError):
    """Base for all virtual-desktop-tool domain errors."""

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Desktop tool failure"


class DesktopSessionError(DesktopDomainError):
    """Failed to bring up the X server / window manager session."""

    default_message: ClassVar[str] = "Failed to start virtual desktop session"


class DesktopLaunchError(DesktopDomainError):
    """Failed to launch the GUI application on the virtual desktop."""

    default_message: ClassVar[str] = "Failed to launch GUI application"


class DesktopInputError(DesktopDomainError):
    """Pointer / keyboard input injection failed."""

    default_message: ClassVar[str] = "Desktop input injection failed"


class DesktopScreenshotError(DesktopDomainError):
    """Screenshot capture failed."""

    default_message: ClassVar[str] = "Desktop screenshot capture failed"


class DesktopAppNotRunningError(DesktopDomainError):
    """An action was requested but no GUI application is running.

    Distinct from a generic input error because the usual remediation
    is to ``launch`` the app first (often after a ``per-call`` sandbox
    lifecycle reset the session).
    """

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_CONFLICT
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = "No GUI application is running on the desktop"


class DesktopDriverError(DesktopDomainError):
    """Desktop driver configuration or dispatch failed."""

    default_message: ClassVar[str] = "Desktop driver failure"


class DesktopArgumentError(DesktopDomainError):
    """Desktop-tool arguments violated a per-mode invariant.

    Raised when the args model alone cannot express the cross-field
    requirement (e.g. ``click`` mode needs both ``x`` and ``y``).
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_PARAMETER_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Desktop tool arguments invalid"
