"""Resolved runtime settings for the virtual desktop tool.

Reads the ``tools.desktop_*`` settings via :class:`ConfigResolver` at
boot and packages them in a frozen ``DesktopSettings`` model plus the
selected :class:`DesktopDriverConfig` so :class:`DesktopTool` consumes
DB > env > code-default values rather than baked-in module constants.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.desktop import DESKTOP_ARGS_VALIDATION_FAILED
from synthorg.tools.desktop._constants import DESKTOP_IMAGE_PIN_DEFAULT
from synthorg.tools.desktop.driver.config import (
    DesktopDriverConfig,
    DesktopDriverKind,
)

if TYPE_CHECKING:
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_NS = "tools"
_KEY_DRIVER = "desktop_driver"
_KEY_SCREEN_W = "desktop_screen_width"
_KEY_SCREEN_H = "desktop_screen_height"
_KEY_IMAGE_PIN = "desktop_image_pin"


class DesktopSettings(BaseModel):
    """Resolved settings consumed by :class:`DesktopTool`.

    Each field's default mirrors the corresponding ``_constants.py``
    value so a deployment without an operator override behaves
    identically to the constants-only build.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    driver: DesktopDriverConfig = Field(default_factory=DesktopDriverConfig)
    image_pin: NotBlankStr = Field(default=DESKTOP_IMAGE_PIN_DEFAULT)


async def resolve_desktop_settings(
    resolver: ConfigResolver,
) -> DesktopSettings:
    """Resolve the ``tools.desktop_*`` registry into a :class:`DesktopSettings`.

    Boot tolerance: a malformed registry value must not crash the boot
    path. On validation failure log a warning and return the
    ``DesktopSettings`` defaults so the ``DesktopTool`` behaves as if no
    overrides were configured.

    Returns:
        Result of type ``DesktopSettings``.
    """
    try:
        kind = DesktopDriverKind(await resolver.get_str(_NS, _KEY_DRIVER))
        return DesktopSettings(
            driver=DesktopDriverConfig(
                kind=kind,
                screen_width=await resolver.get_int(_NS, _KEY_SCREEN_W),
                screen_height=await resolver.get_int(_NS, _KEY_SCREEN_H),
            ),
            image_pin=await resolver.get_str(_NS, _KEY_IMAGE_PIN),
        )
    except (ValidationError, ValueError) as exc:
        logger.warning(
            DESKTOP_ARGS_VALIDATION_FAILED,
            origin="desktop_settings_resolution",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return DesktopSettings()
