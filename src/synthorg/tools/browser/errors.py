"""Domain error hierarchy for the headless browser tool.

Every failure path raises a ``<Browser><Condition>Error`` subclass of
:class:`synthorg.tools.errors.ToolError` so the
``check_domain_error_hierarchy.py`` gate stays clean and callers can
discriminate failures by ``error_code`` / class.
"""

from typing import ClassVar

from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.tools.errors import ToolError


class BrowserDomainError(ToolError):
    """Base for all browser-tool domain errors."""

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Browser tool failure"


class BrowserLaunchError(BrowserDomainError):
    """Failed to launch Chromium or attach to the sandbox."""

    default_message: ClassVar[str] = "Failed to launch browser"


class BrowserNavigationError(BrowserDomainError):
    """Navigation failed (timeout, invalid URL, network)."""

    default_message: ClassVar[str] = "Browser navigation failed"


class BrowserScreenshotError(BrowserDomainError):
    """Screenshot capture failed."""

    default_message: ClassVar[str] = "Browser screenshot capture failed"


class BrowserAccessibilityError(BrowserDomainError):
    """Accessibility scan failed before producing a verdict."""

    default_message: ClassVar[str] = "Accessibility scan failed"


class BrowserBaselineNotFoundError(BrowserDomainError):
    """Reference screenshot baseline is missing.

    Distinct from a generic diff error because a missing baseline is
    usually a setup signal (create the baseline) rather than a defect.
    """

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Baseline screenshot not found"


class BrowserDiffError(BrowserDomainError):
    """Screenshot diff computation failed (e.g. size mismatch)."""

    default_message: ClassVar[str] = "Screenshot diff comparison failed"


class BrowserStartCommandError(BrowserDomainError):
    """Dev-server start command failed inside the sandbox."""

    default_message: ClassVar[str] = "Browser start_command failed"


class BrowserArgumentError(BrowserDomainError):
    """Browser-tool arguments violated a per-mode invariant.

    Raised when the args model alone cannot express the cross-field
    requirement (e.g. ``diff`` mode needs ``spec_name`` plus
    ``screenshot_name``).
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_PARAMETER_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Browser tool arguments invalid"
