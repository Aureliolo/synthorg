"""Typed argument model for :class:`BrowserTool`.

One frozen Pydantic v2 model discriminates on ``mode`` and carries
the per-mode fields. Cross-field invariants run in
``model_validator(mode="after")`` so the LLM-facing schema and the
boundary validator stay in sync.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type
from synthorg.tools.browser._constants import (
    A11Y_IMPACT_LEVELS,
    A11Y_MIN_IMPACT_DEFAULT,
    DIFF_SSIM_TOLERANCE_MAX,
    DIFF_SSIM_TOLERANCE_MIN,
    MAX_VIEWPORT_DIMENSION,
    MIN_VIEWPORT_DIMENSION,
    NAVIGATION_TIMEOUT_SECONDS,
    START_COMMAND_TIMEOUT_SECONDS_DEFAULT,
    WAIT_CONDITION_DEFAULT,
)

_ARGS_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)

BrowserMode = Literal[
    "navigate",
    "screenshot",
    "diff",
    "accessibility_scan",
    "spec",
]

A11yImpact = Literal["minor", "moderate", "serious", "critical"]

WaitCondition = Literal["load", "domcontentloaded", "networkidle"]


class BrowserToolArgs(BaseModel):
    """Arguments for the unified browser tool.

    The ``mode`` field selects the operation; other fields are
    optional and are required only by specific modes (enforced by the
    after-validator). All paths are workspace-relative; ``url`` is
    accepted for absolute URLs (e.g. when a dev server is reachable
    on a known port).
    """

    model_config = _ARGS_CONFIG

    mode: BrowserMode = Field(
        description=(
            "Operation: navigate, screenshot, diff, accessibility_scan, or spec."
        ),
    )

    url: NotBlankStr | None = Field(
        default=None,
        description=(
            "Absolute URL to load (e.g. http://host.docker.internal:5173/). "
            "Either url or path is required for modes that open a page."
        ),
    )
    path: NotBlankStr | None = Field(
        default=None,
        description=(
            "Workspace-relative file path to open as file://. "
            "Translated to file:///<container-workspace>/<path>."
        ),
    )

    spec_name: NotBlankStr | None = Field(
        default=None,
        description=(
            "Spec identifier (also the baseline directory name). "
            "Required for diff and spec modes."
        ),
    )
    screenshot_name: NotBlankStr | None = Field(
        default=None,
        description=(
            "Screenshot file name without extension. "
            "Required for screenshot, diff, and spec modes."
        ),
    )

    viewport_width: int | None = Field(
        default=None,
        ge=MIN_VIEWPORT_DIMENSION,
        le=MAX_VIEWPORT_DIMENSION,
        description="Override default viewport width (pixels).",
    )
    viewport_height: int | None = Field(
        default=None,
        ge=MIN_VIEWPORT_DIMENSION,
        le=MAX_VIEWPORT_DIMENSION,
        description="Override default viewport height (pixels).",
    )
    full_page: bool = Field(
        default=False,
        description=("Capture entire scrollable page (True) or viewport only."),
    )

    wait_condition: WaitCondition = Field(
        default="load",
        description=(
            "Playwright wait_until value for navigation completion."
            f" Default constant: {WAIT_CONDITION_DEFAULT!r}."
        ),
    )
    navigation_timeout_seconds: float | None = Field(
        default=None,
        ge=1.0,
        le=NAVIGATION_TIMEOUT_SECONDS * 10,
        description="Override navigation timeout (seconds).",
    )

    tolerance: float | None = Field(
        default=None,
        ge=DIFF_SSIM_TOLERANCE_MIN,
        le=DIFF_SSIM_TOLERANCE_MAX,
        description=("SSIM pass threshold (0.5 to 1.0); default 0.98 when omitted."),
    )
    create_baseline_if_missing: bool = Field(
        default=False,
        description=(
            "If True and baseline is absent, save current capture as "
            "baseline and mark as new (no failure). If False, missing "
            "baseline raises BrowserBaselineNotFoundError."
        ),
    )

    min_impact: A11yImpact = Field(
        default="serious",
        description=(
            "Minimum axe-core impact level treated as a violation. "
            "Lower impacts are surfaced in metadata as warnings."
            f" Default constant: {A11Y_MIN_IMPACT_DEFAULT!r}."
        ),
    )

    start_command: str | None = Field(
        default=None,
        description=(
            "Optional shell command executed in the sandbox before "
            "navigation (e.g. 'npm run dev'). The tool blocks until "
            "the command completes or its timeout elapses."
        ),
    )
    start_command_timeout_seconds: float = Field(
        default=START_COMMAND_TIMEOUT_SECONDS_DEFAULT,
        ge=1.0,
        le=START_COMMAND_TIMEOUT_SECONDS_DEFAULT * 20,
        description="Timeout for start_command in seconds.",
    )

    @model_validator(mode="after")
    def _validate_per_mode_fields(self) -> Self:
        """Enforce the per-mode required-field invariants.

        Returns:
            Result of type ``Self``.

        Raises:
            ValueError: If an argument fails domain validation.
        """
        _ = A11Y_IMPACT_LEVELS  # taxonomy reference for future widening
        if (
            self.mode in {"navigate", "screenshot", "accessibility_scan", "spec"}
            and self.url is None
            and self.path is None
        ):
            msg = f"{self.mode!r} mode requires url or path"
            raise ValueError(msg)
        if self.mode in {"screenshot", "diff", "spec"} and self.screenshot_name is None:
            msg = f"{self.mode!r} mode requires screenshot_name"
            raise ValueError(msg)
        if self.mode in {"diff", "spec"} and self.spec_name is None:
            msg = f"{self.mode!r} mode requires spec_name"
            raise ValueError(msg)
        if (self.viewport_width is None) != (self.viewport_height is None):
            msg = "viewport_width and viewport_height must be set together"
            raise ValueError(msg)
        return self
