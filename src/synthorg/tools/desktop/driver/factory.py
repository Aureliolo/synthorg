"""Virtual-desktop driver factory.

Maps :class:`DesktopDriverKind` to a concrete :class:`DesktopDriver`
via the ``StrEnum``-keyed
:class:`~synthorg.core.registry.StrategyRegistry`. An unknown kind
raises :class:`DesktopDriverConfigError` at construction (fail fast),
mirroring the git-backend and autonomy-strategy factories.
"""

from typing import TYPE_CHECKING

from synthorg.core.registry import StrategyRegistry
from synthorg.tools.desktop.driver.config import DesktopDriverKind
from synthorg.tools.desktop.driver.vnc import VncDesktopDriver
from synthorg.tools.desktop.driver.xvfb import XvfbDesktopDriver
from synthorg.tools.desktop.errors import DesktopDriverError

if TYPE_CHECKING:
    from synthorg.tools.desktop.driver.config import DesktopDriverConfig
    from synthorg.tools.desktop.driver.protocol import DesktopDriver


class DesktopDriverConfigError(DesktopDriverError):
    """A desktop driver could not be built from its configuration."""


def _build_xvfb(config: DesktopDriverConfig) -> DesktopDriver:
    return XvfbDesktopDriver(
        screen_width=config.screen_width,
        screen_height=config.screen_height,
    )


def _build_vnc(config: DesktopDriverConfig) -> DesktopDriver:
    return VncDesktopDriver(
        screen_width=config.screen_width,
        screen_height=config.screen_height,
        vnc_port=config.vnc_port,
    )


_REGISTRY: StrategyRegistry[DesktopDriver] = StrategyRegistry(
    {
        DesktopDriverKind.XVFB: _build_xvfb,
        DesktopDriverKind.VNC: _build_vnc,
    },
    kind="desktop_driver",
)


def build_desktop_driver(config: DesktopDriverConfig) -> DesktopDriver:
    """Build the configured :class:`DesktopDriver`.

    Args:
        config: The driver discriminator plus geometry / VNC tuning.

    Returns:
        A strategy satisfying the :class:`DesktopDriver` protocol.

    Raises:
        StrategyFactoryNotFoundError: Unknown ``config.kind``.
    """
    return _REGISTRY.build(config.kind, config)
