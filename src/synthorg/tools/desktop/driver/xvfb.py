"""Plain Xvfb virtual-desktop driver (the deterministic default).

Runs a headless X server with no remote-observation channel. Smallest
image, fully deterministic screenshots: the default strategy used by
the acceptance test and cassette replay.
"""

from synthorg.tools.desktop._constants import DEFAULT_DISPLAY
from synthorg.tools.desktop.driver.config import (
    DesktopDriverKind,
    DesktopSessionConfig,
)


class XvfbDesktopDriver:
    """Bring up a headless Xvfb session with no VNC channel."""

    def __init__(
        self,
        *,
        screen_width: int,
        screen_height: int,
        display: str = DEFAULT_DISPLAY,
    ) -> None:
        """Store the geometry threaded into the session config.

        Args:
            screen_width: Virtual screen width in pixels.
            screen_height: Virtual screen height in pixels.
            display: X display identifier.
        """
        self._screen_width = screen_width
        self._screen_height = screen_height
        self._display = display

    @property
    def kind(self) -> DesktopDriverKind:
        """Return the ``xvfb`` discriminator."""
        return DesktopDriverKind.XVFB

    def session_config(self) -> DesktopSessionConfig:
        """Return a VNC-free session at the configured geometry."""
        return DesktopSessionConfig(
            display=self._display,
            screen_width=self._screen_width,
            screen_height=self._screen_height,
            enable_vnc=False,
        )
