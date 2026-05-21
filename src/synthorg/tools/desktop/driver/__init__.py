"""Pluggable virtual-desktop driver subsystem.

A :class:`DesktopDriver` decides how the headless X session is brought
up inside the sandbox (plain Xvfb, or Xvfb plus a VNC channel for live
observation). The :class:`DesktopTool` stays driver-agnostic: it asks
the driver for a :class:`DesktopSessionConfig` and threads that into
the in-container executor.
"""

from synthorg.tools.desktop.driver.config import (
    DesktopDriverConfig,
    DesktopDriverKind,
    DesktopSessionConfig,
)
from synthorg.tools.desktop.driver.factory import (
    DesktopDriverConfigError,
    build_desktop_driver,
)
from synthorg.tools.desktop.driver.protocol import DesktopDriver
from synthorg.tools.desktop.driver.vnc import VncDesktopDriver
from synthorg.tools.desktop.driver.xvfb import XvfbDesktopDriver

__all__ = (
    "DesktopDriver",
    "DesktopDriverConfig",
    "DesktopDriverConfigError",
    "DesktopDriverKind",
    "DesktopSessionConfig",
    "VncDesktopDriver",
    "XvfbDesktopDriver",
    "build_desktop_driver",
)
