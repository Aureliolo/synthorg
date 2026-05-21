"""Virtual desktop tool package (Xvfb + xdotool + scrot).

Exposes :class:`DesktopTool` and the args / result models so callers
that import :mod:`synthorg.tools.desktop` get the stable surface.
"""

from synthorg.tools.desktop._args import DesktopToolArgs
from synthorg.tools.desktop._models import (
    InputResult,
    LaunchResult,
    ScreenshotResult,
)
from synthorg.tools.desktop._settings import DesktopSettings, resolve_desktop_settings
from synthorg.tools.desktop.desktop_tool import DesktopTool
from synthorg.tools.desktop.driver import (
    DesktopDriver,
    DesktopDriverConfig,
    DesktopDriverConfigError,
    DesktopDriverKind,
    DesktopSessionConfig,
    build_desktop_driver,
)
from synthorg.tools.desktop.errors import (
    DesktopAppNotRunningError,
    DesktopArgumentError,
    DesktopDomainError,
    DesktopDriverError,
    DesktopInputError,
    DesktopLaunchError,
    DesktopScreenshotError,
    DesktopSessionError,
)

__all__ = (
    "DesktopAppNotRunningError",
    "DesktopArgumentError",
    "DesktopDomainError",
    "DesktopDriver",
    "DesktopDriverConfig",
    "DesktopDriverConfigError",
    "DesktopDriverError",
    "DesktopDriverKind",
    "DesktopInputError",
    "DesktopLaunchError",
    "DesktopScreenshotError",
    "DesktopSessionConfig",
    "DesktopSessionError",
    "DesktopSettings",
    "DesktopTool",
    "DesktopToolArgs",
    "InputResult",
    "LaunchResult",
    "ScreenshotResult",
    "build_desktop_driver",
    "resolve_desktop_settings",
)
