# module-kind: code
"""Result envelopes + executor-error mapping for the browser tool.

Turns a result model into a :class:`ToolExecutionResult`, wraps a failed
operation into the error envelope, and maps an out-of-process executor's
error-type string back onto the browser domain-error hierarchy. Kept
beside :class:`BrowserTool` so the tool module stays focused on the
per-operation dispatch.
"""

import json
from typing import Final

from pydantic import BaseModel

from synthorg.observability import safe_error_description
from synthorg.tools.base import ToolExecutionResult
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


def ok_result(model: BaseModel) -> ToolExecutionResult:
    """Wrap a successful result model in a tool execution result.

    Returns:
        Result of type ``ToolExecutionResult``.
    """
    payload = model.model_dump(mode="json")
    return ToolExecutionResult(
        content=json.dumps(payload),
        is_error=False,
        metadata=payload,
    )


def error_result(
    error_cls: type[BrowserDomainError],
    exc: Exception,
) -> ToolExecutionResult:
    """Wrap a failed operation in an error tool execution result.

    Returns:
        Result of type ``ToolExecutionResult``.
    """
    msg = safe_error_description(exc)
    return ToolExecutionResult(
        content=msg,
        is_error=True,
        metadata={"error_type": error_cls.__name__},
    )


_EXECUTOR_ERROR_MAP: Final[dict[str, type[BrowserDomainError]]] = {
    "BrowserNavigationError": BrowserNavigationError,
    "BrowserLaunchError": BrowserLaunchError,
    "BrowserScreenshotError": BrowserScreenshotError,
    "BrowserAccessibilityError": BrowserAccessibilityError,
    "BrowserDiffError": BrowserDiffError,
    "BrowserBaselineNotFoundError": BrowserBaselineNotFoundError,
    "BrowserStartCommandError": BrowserStartCommandError,
    "BrowserArgumentError": BrowserArgumentError,
    # ``asyncio.wait_for`` raises TimeoutError when the executor's
    # launch budget is exceeded; navigation timeouts come back as
    # PlaywrightTimeoutError from page.goto.
    "TimeoutError": BrowserLaunchError,
    "PlaywrightTimeoutError": BrowserNavigationError,
    "FileNotFoundError": BrowserAccessibilityError,
}


def map_executor_error(
    err_type: str,
    message: str,
    operation: str,
) -> BrowserDomainError:
    """Map an executor error-type string onto a browser domain error.

    Returns:
        Result of type ``BrowserDomainError``.
    """
    cls = _EXECUTOR_ERROR_MAP.get(err_type, BrowserDomainError)
    return cls(
        message,
        context={"operation": operation, "executor_error_type": err_type},
    )
