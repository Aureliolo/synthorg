"""Xvfb + x11vnc virtual-desktop driver (live observation).

Same headless Xvfb base as :class:`XvfbDesktopDriver`, plus an x11vnc
channel so a human operator can attach and watch the agent drive the
session. Heavier image; opt-in via the ``vnc`` driver discriminator.
"""

from synthorg.tools.desktop._constants import DEFAULT_DISPLAY, DEFAULT_VNC_PORT
from synthorg.tools.desktop.driver.config import (
    DesktopDriverKind,
    DesktopSessionConfig,
)


class VncDesktopDriver:
    """Bring up a headless Xvfb session exposed over x11vnc."""

    def __init__(
        self,
        *,
        screen_width: int,
        screen_height: int,
        vnc_port: int = DEFAULT_VNC_PORT,
        display: str = DEFAULT_DISPLAY,
    ) -> None:
        """Store the geometry and VNC port threaded into the session.

        Args:
            screen_width: Virtual screen width in pixels.
            screen_height: Virtual screen height in pixels.
            vnc_port: TCP port x11vnc binds to.
            display: X display identifier.
        """
        self._screen_width = screen_width
        self._screen_height = screen_height
        self._vnc_port = vnc_port
        self._display = display

    @property
    def kind(self) -> DesktopDriverKind:
        """Return the ``vnc`` discriminator."""
        return DesktopDriverKind.VNC

    def session_config(self) -> DesktopSessionConfig:
        """Return a VNC-enabled session at the configured geometry.

        Returns:
            Result of type ``DesktopSessionConfig``.
        """
        return DesktopSessionConfig(
            display=self._display,
            screen_width=self._screen_width,
            screen_height=self._screen_height,
            enable_vnc=True,
            vnc_port=self._vnc_port,
        )
