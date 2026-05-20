"""Unit tests for the browser-tool domain error hierarchy."""

import pytest

from synthorg.core.domain_errors import DomainError
from synthorg.tools.browser.errors import (
    BrowserAccessibilityError,
    BrowserArgumentError,
    BrowserBaselineNotFoundError,
    BrowserDiffError,
    BrowserDomainError,
    BrowserLaunchError,
    BrowserNavigationError,
    BrowserScreenshotError,
    BrowserStartCommandError,
)
from synthorg.tools.errors import ToolError

pytestmark = pytest.mark.unit

_ALL_ERRORS = (
    BrowserDomainError,
    BrowserLaunchError,
    BrowserNavigationError,
    BrowserScreenshotError,
    BrowserAccessibilityError,
    BrowserBaselineNotFoundError,
    BrowserDiffError,
    BrowserStartCommandError,
    BrowserArgumentError,
)


class TestBrowserErrorHierarchy:
    @pytest.mark.parametrize("cls", _ALL_ERRORS)
    def test_inherits_from_tool_error(self, cls: type[BrowserDomainError]) -> None:
        assert issubclass(cls, ToolError)

    @pytest.mark.parametrize("cls", _ALL_ERRORS)
    def test_inherits_from_domain_error(self, cls: type[BrowserDomainError]) -> None:
        assert issubclass(cls, DomainError)

    @pytest.mark.parametrize("cls", _ALL_ERRORS)
    def test_default_message_set(self, cls: type[BrowserDomainError]) -> None:
        assert cls.default_message
        assert cls.default_message != ToolError.default_message

    def test_context_is_immutable(self) -> None:
        err = BrowserNavigationError("boom", context={"url": "x"})
        assert err.context["url"] == "x"
        with pytest.raises(TypeError):
            err.context["url"] = "y"  # type: ignore[index]

    def test_argument_error_uses_validation_code(self) -> None:
        from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode

        assert BrowserArgumentError.error_category is ErrorCategory.VALIDATION
        assert BrowserArgumentError.error_code is ErrorCode.TOOL_PARAMETER_ERROR

    def test_baseline_not_found_is_404(self) -> None:
        from synthorg.core.error_taxonomy import ErrorCategory

        assert BrowserBaselineNotFoundError.status_code == 404
        assert BrowserBaselineNotFoundError.error_category is ErrorCategory.NOT_FOUND
