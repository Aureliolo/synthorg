"""Pluggable virtual-desktop driver protocol.

A driver translates the operator-selected ``DesktopDriverConfig`` into
a concrete :class:`DesktopSessionConfig` describing how the headless X
session runs inside the sandbox. Implementations are stateless and
safe for concurrent reuse across tasks.
"""

from typing import Protocol, runtime_checkable

from synthorg.tools.desktop.driver.config import (
    DesktopDriverKind,  # noqa: TC001 -- Protocol return annotation
    DesktopSessionConfig,  # noqa: TC001 -- Protocol return annotation
)


@runtime_checkable
class DesktopDriver(Protocol):
    """Strategy that shapes the in-sandbox X session for the tool."""

    @property
    def kind(self) -> DesktopDriverKind:
        """Discriminator identifying the concrete strategy."""
        ...

    def session_config(self) -> DesktopSessionConfig:
        """Return the session contract threaded into the executor.

        Returns:
            A frozen :class:`DesktopSessionConfig` the in-container
            executor consumes to bring up Xvfb (and optionally x11vnc).
        """
        ...
