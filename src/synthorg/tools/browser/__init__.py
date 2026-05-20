"""Headless browser tool package (Playwright + Chromium).

Exposes :class:`BrowserTool` and the args / result models so callers
that import :mod:`synthorg.tools.browser` get the stable surface.
"""

from synthorg.tools.browser._args import BrowserToolArgs
from synthorg.tools.browser._models import (
    A11yScanResult,
    A11yViolation,
    NavigationResult,
    ScreenshotDiffResult,
    ScreenshotMetadata,
    SpecResult,
)
from synthorg.tools.browser._protocols import ScreenshotDiffer
from synthorg.tools.browser._settings import BrowserSettings
from synthorg.tools.browser.browser_tool import BrowserTool
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

__all__ = (
    "A11yScanResult",
    "A11yViolation",
    "BrowserAccessibilityError",
    "BrowserArgumentError",
    "BrowserBaselineNotFoundError",
    "BrowserDiffError",
    "BrowserDomainError",
    "BrowserLaunchError",
    "BrowserNavigationError",
    "BrowserScreenshotError",
    "BrowserSettings",
    "BrowserStartCommandError",
    "BrowserTool",
    "BrowserToolArgs",
    "NavigationResult",
    "ScreenshotDiffResult",
    "ScreenshotDiffer",
    "ScreenshotMetadata",
    "SpecResult",
)
