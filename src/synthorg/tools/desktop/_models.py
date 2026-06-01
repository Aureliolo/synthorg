"""Response models for the virtual desktop tool.

Every model is frozen Pydantic v2 with ``extra='forbid'`` and
``allow_inf_nan=False``. Mode handlers ``model_dump()`` these into the
``ToolExecutionResult.metadata`` mapping; the JSON string of the dump
is also placed into ``content`` so the LLM-facing surface stays plain
text.
"""

from typing import TypedDict

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from synthorg.core.types import NotBlankStr
from synthorg.tools.desktop._constants import SHA256_HEX_PATTERN


class ExecutorEnvelope(TypedDict, total=False):
    """Top-level JSON envelope returned by the in-sandbox desktop executor."""

    status: str
    result: dict[str, JsonValue]
    error_type: str
    message: str


_RESPONSE_CONFIG = ConfigDict(
    frozen=True,
    allow_inf_nan=False,
    extra="forbid",
)


class LaunchResult(BaseModel):
    """Outcome of launching a GUI application on the virtual desktop."""

    model_config = _RESPONSE_CONFIG

    display: NotBlankStr = Field(description="X display the app was launched on.")
    pid: int = Field(ge=1, description="Process id of the launched application.")
    screen_width: int = Field(ge=1, description="Virtual screen width in pixels.")
    screen_height: int = Field(ge=1, description="Virtual screen height in pixels.")


class InputResult(BaseModel):
    """Outcome of a pointer / keyboard input action."""

    model_config = _RESPONSE_CONFIG

    action: NotBlankStr = Field(
        description="Input action performed (click, type, key, scroll).",
    )
    detail: str = Field(
        default="",
        description="Bounded human-readable summary of the action target.",
    )


class ScreenshotResult(BaseModel):
    """Metadata for a captured desktop screenshot."""

    model_config = _RESPONSE_CONFIG

    saved_path: NotBlankStr = Field(description="Path relative to the workspace root.")
    width: int = Field(ge=1, description="Image width in pixels.")
    height: int = Field(ge=1, description="Image height in pixels.")
    file_size_bytes: int = Field(ge=0, description="On-disk size in bytes.")
    captured_at_iso: str = Field(description="UTC ISO 8601 capture timestamp.")
    sha256: str = Field(
        pattern=SHA256_HEX_PATTERN,
        description="Lowercase hex SHA-256 (64 chars) of the captured PNG bytes.",
    )


class ExecutorScreenshotPayload(BaseModel):
    """Raw screenshot metadata returned by the in-sandbox executor.

    Validated at the process boundary (JSON over stdout) before the host
    builds the public :class:`ScreenshotResult`. The host re-derives the
    workspace-relative ``saved_path`` and stamps ``captured_at_iso`` from
    its own clock, so only the executor-produced fields live here.
    """

    model_config = _RESPONSE_CONFIG

    saved_path: NotBlankStr = Field(
        description="Container-absolute path the executor wrote the PNG to.",
    )
    width: int = Field(ge=1, description="Image width in pixels.")
    height: int = Field(ge=1, description="Image height in pixels.")
    file_size_bytes: int = Field(ge=0, description="On-disk size in bytes.")
    sha256: str = Field(
        pattern=SHA256_HEX_PATTERN,
        description="Lowercase hex SHA-256 (64 chars) of the captured PNG bytes.",
    )
