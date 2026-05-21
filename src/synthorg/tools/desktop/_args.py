"""Typed argument model for :class:`DesktopTool`.

One frozen Pydantic v2 model discriminates on ``mode`` and carries the
per-mode fields. Cross-field invariants run in
``model_validator(mode="after")`` so the LLM-facing schema and the
boundary validator stay in sync.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type
from synthorg.tools.desktop._constants import (
    DEFAULT_CLICK_BUTTON,
    DEFAULT_SCROLL_AMOUNT,
    LAUNCH_TIMEOUT_SECONDS,
    MAX_COORDINATE,
    MAX_MOUSE_BUTTON,
    MAX_SCROLL_AMOUNT,
    MAX_SETTLE_DELAY_SECONDS,
    MIN_COORDINATE,
    MIN_MOUSE_BUTTON,
    MIN_SCROLL_AMOUNT,
    SETTLE_DELAY_SECONDS,
)

_ARGS_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)

DesktopMode = Literal[
    "launch",
    "click",
    "type",
    "key",
    "screenshot",
    "scroll",
]

ScrollDirection = Literal["up", "down"]


class DesktopToolArgs(BaseModel):
    """Arguments for the unified virtual desktop tool.

    The ``mode`` field selects the operation; other fields are optional
    and required only by specific modes (enforced by the after-validator).
    All screenshot paths are workspace-relative; ``app_command`` is run
    via ``bash -c`` inside the sandbox boundary.
    """

    model_config = _ARGS_CONFIG

    mode: DesktopMode = Field(
        description="Operation: launch, click, type, key, screenshot, or scroll.",
    )

    app_command: str | None = Field(
        default=None,
        description=(
            "Shell command that launches the GUI application (e.g. "
            "'python3 /workspace/app.py'). Required for launch mode. "
            "SECURITY: run via bash -c inside the sandbox; never pass "
            "untrusted strings."
        ),
    )
    launch_timeout_seconds: float = Field(
        default=LAUNCH_TIMEOUT_SECONDS,
        ge=1.0,
        le=LAUNCH_TIMEOUT_SECONDS * 20,
        description="Time to wait for the app window to appear (launch mode).",
    )

    x: int | None = Field(
        default=None,
        ge=MIN_COORDINATE,
        le=MAX_COORDINATE,
        description="Pointer X coordinate (click mode).",
    )
    y: int | None = Field(
        default=None,
        ge=MIN_COORDINATE,
        le=MAX_COORDINATE,
        description="Pointer Y coordinate (click mode).",
    )
    button: int = Field(
        default=DEFAULT_CLICK_BUTTON,
        ge=MIN_MOUSE_BUTTON,
        le=MAX_MOUSE_BUTTON,
        description="Mouse button (1=left, 2=middle, 3=right).",
    )
    double: bool = Field(
        default=False,
        description="Perform a double-click instead of a single click.",
    )

    text: str | None = Field(
        default=None,
        description="Literal text to type (type mode).",
    )

    keys: NotBlankStr | None = Field(
        default=None,
        description=(
            "xdotool key sequence to press (e.g. 'ctrl+s', 'Return', "
            "'Tab'). Required for key mode."
        ),
    )

    screenshot_name: NotBlankStr | None = Field(
        default=None,
        description=(
            "Screenshot file name without extension. Required for screenshot mode."
        ),
    )

    direction: ScrollDirection | None = Field(
        default=None,
        description="Scroll direction (scroll mode).",
    )
    amount: int = Field(
        default=DEFAULT_SCROLL_AMOUNT,
        ge=MIN_SCROLL_AMOUNT,
        le=MAX_SCROLL_AMOUNT,
        description="Number of scroll steps (scroll mode).",
    )

    settle_delay_seconds: float = Field(
        default=SETTLE_DELAY_SECONDS,
        ge=0.0,
        le=MAX_SETTLE_DELAY_SECONDS,
        description="Delay after the action before the call returns.",
    )

    @model_validator(mode="after")
    def _validate_per_mode_fields(self) -> Self:
        """Enforce the per-mode required-field invariants."""
        if self.mode == "launch" and not self.app_command:
            msg = "'launch' mode requires app_command"
            raise ValueError(msg)
        if self.mode == "click" and (self.x is None or self.y is None):
            msg = "'click' mode requires both x and y"
            raise ValueError(msg)
        if self.mode == "type" and self.text is None:
            msg = "'type' mode requires text"
            raise ValueError(msg)
        if self.mode == "key" and self.keys is None:
            msg = "'key' mode requires keys"
            raise ValueError(msg)
        if self.mode == "screenshot" and self.screenshot_name is None:
            msg = "'screenshot' mode requires screenshot_name"
            raise ValueError(msg)
        if self.mode == "scroll" and self.direction is None:
            msg = "'scroll' mode requires direction"
            raise ValueError(msg)
        return self
