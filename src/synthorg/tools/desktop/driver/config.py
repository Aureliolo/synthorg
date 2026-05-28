"""Configuration models for the virtual-desktop driver subsystem.

``DesktopSessionConfig`` is the frozen contract the driver hands to the
in-container executor: which DISPLAY to use, the screen geometry, and
whether a VNC observation channel should be started. ``DesktopDriverConfig``
carries the ``kind`` discriminator the factory dispatches on.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.tools.desktop._constants import (
    DEFAULT_COLOR_DEPTH,
    DEFAULT_DISPLAY,
    DEFAULT_SCREEN_HEIGHT,
    DEFAULT_SCREEN_WIDTH,
    DEFAULT_VNC_PORT,
    MAX_SCREEN_DIMENSION,
    MAX_VNC_PORT,
    MIN_SCREEN_DIMENSION,
    MIN_VNC_PORT,
)

_CONFIG = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")


class DesktopDriverKind(StrEnum):
    """Discriminator selecting a concrete :class:`DesktopDriver`."""

    XVFB = "xvfb"
    VNC = "vnc"


class DesktopSessionConfig(BaseModel):
    """How the headless X session is configured inside the sandbox.

    Threaded into the executor payload so the in-container bootstrap
    starts Xvfb (and, when ``enable_vnc``, x11vnc) on the named display
    at the requested geometry before any input or capture runs.
    """

    model_config = _CONFIG

    display: NotBlankStr = Field(
        default=DEFAULT_DISPLAY,
        description="X display identifier (e.g. ':99').",
    )
    screen_width: int = Field(
        default=DEFAULT_SCREEN_WIDTH,
        ge=MIN_SCREEN_DIMENSION,
        le=MAX_SCREEN_DIMENSION,
        description="Virtual screen width in pixels.",
    )
    screen_height: int = Field(
        default=DEFAULT_SCREEN_HEIGHT,
        ge=MIN_SCREEN_DIMENSION,
        le=MAX_SCREEN_DIMENSION,
        description="Virtual screen height in pixels.",
    )
    color_depth: int = Field(
        default=DEFAULT_COLOR_DEPTH,
        ge=1,
        description="Xvfb colour depth in bits.",
    )
    enable_vnc: bool = Field(
        default=False,
        description="Start an x11vnc observation channel for the session.",
    )
    vnc_port: int = Field(
        default=DEFAULT_VNC_PORT,
        ge=MIN_VNC_PORT,
        le=MAX_VNC_PORT,
        description="TCP port x11vnc binds to when enable_vnc is True.",
    )


class DesktopDriverConfig(BaseModel):
    """Frozen selector for the active desktop driver strategy."""

    model_config = _CONFIG

    kind: DesktopDriverKind = Field(
        default=DesktopDriverKind.XVFB,
        description="Driver strategy discriminator.",
    )
    screen_width: int = Field(
        default=DEFAULT_SCREEN_WIDTH,
        ge=MIN_SCREEN_DIMENSION,
        le=MAX_SCREEN_DIMENSION,
        description="Virtual screen width passed to the session.",
    )
    screen_height: int = Field(
        default=DEFAULT_SCREEN_HEIGHT,
        ge=MIN_SCREEN_DIMENSION,
        le=MAX_SCREEN_DIMENSION,
        description="Virtual screen height passed to the session.",
    )
    vnc_port: int = Field(
        default=DEFAULT_VNC_PORT,
        ge=MIN_VNC_PORT,
        le=MAX_VNC_PORT,
        description="VNC port used when kind is 'vnc'.",
    )
