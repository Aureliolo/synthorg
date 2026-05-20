"""Resolved runtime settings for the headless browser tool.

Reads the ``tools.browser_*`` settings via :class:`ConfigResolver` at
boot and packages them in a frozen ``BrowserSettings`` model so
:class:`BrowserTool` can consume DB > env > code-default values rather
than baked-in module constants.

The resolver runs from :func:`synthorg.workers.runtime_builder._build_tool_registry`,
so the audit log fires once at startup and the settings track operator
overrides for the rest of the process lifetime.
"""

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.browser import (
    BROWSER_ARGS_VALIDATION_FAILED,
)
from synthorg.tools.browser._constants import (
    A11Y_MIN_IMPACT_DEFAULT,
    BROWSER_IMAGE_PIN_DEFAULT,
    BROWSER_LAUNCH_TIMEOUT_SECONDS,
    DEFAULT_VIEWPORT_HEIGHT,
    DEFAULT_VIEWPORT_WIDTH,
    DIFF_SSIM_TOLERANCE_DEFAULT,
)

if TYPE_CHECKING:
    from synthorg.settings.resolver import ConfigResolver

logger = get_logger(__name__)

_NS = "tools"
_KEY_LAUNCH_TIMEOUT = "browser_launch_timeout_seconds"
_KEY_VIEWPORT_W = "browser_viewport_width"
_KEY_VIEWPORT_H = "browser_viewport_height"
_KEY_TOLERANCE = "browser_screenshot_ssim_tolerance"
_KEY_MIN_IMPACT = "browser_a11y_min_impact_default"
_KEY_IMAGE_PIN = "browser_image_pin"


class BrowserSettings(BaseModel):
    """Resolved settings consumed by :class:`BrowserTool`.

    Each field's default mirrors the corresponding ``_constants.py``
    value so a deployment without an operator override behaves
    identically to the constants-only build.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

    launch_timeout_seconds: float = Field(
        default=BROWSER_LAUNCH_TIMEOUT_SECONDS,
        gt=0,
    )
    viewport_width: int = Field(default=DEFAULT_VIEWPORT_WIDTH, ge=1)
    viewport_height: int = Field(default=DEFAULT_VIEWPORT_HEIGHT, ge=1)
    diff_ssim_tolerance: float = Field(
        default=DIFF_SSIM_TOLERANCE_DEFAULT,
        ge=0.0,
        le=1.0,
    )
    a11y_min_impact_default: NotBlankStr = Field(
        default=A11Y_MIN_IMPACT_DEFAULT,
    )
    image_pin: NotBlankStr = Field(default=BROWSER_IMAGE_PIN_DEFAULT)


async def resolve_browser_settings(
    resolver: ConfigResolver,
) -> BrowserSettings:
    """Resolve the ``tools.browser_*`` registry into a :class:`BrowserSettings`.

    Boot tolerance: a malformed registry value (e.g. a fake resolver in
    tests returning a sentinel that violates the field constraints)
    must not crash the boot path. On validation failure log a warning
    and return the BrowserSettings defaults so the BrowserTool behaves
    as if no overrides were configured.
    """
    try:
        return BrowserSettings(
            launch_timeout_seconds=await resolver.get_float(
                _NS,
                _KEY_LAUNCH_TIMEOUT,
            ),
            viewport_width=await resolver.get_int(_NS, _KEY_VIEWPORT_W),
            viewport_height=await resolver.get_int(_NS, _KEY_VIEWPORT_H),
            diff_ssim_tolerance=await resolver.get_float(
                _NS,
                _KEY_TOLERANCE,
            ),
            a11y_min_impact_default=await resolver.get_str(
                _NS,
                _KEY_MIN_IMPACT,
            ),
            image_pin=await resolver.get_str(_NS, _KEY_IMAGE_PIN),
        )
    except ValidationError as exc:
        logger.warning(
            BROWSER_ARGS_VALIDATION_FAILED,
            origin="browser_settings_resolution",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return BrowserSettings()
