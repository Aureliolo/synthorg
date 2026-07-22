"""Frozen argument models for the governed deploy tools.

Each tool dispatches on an ``action`` field. ``target`` names an
operator-allowlisted deploy connection and ``deployment_id`` becomes a
REST URL path segment, so both are validated here to reject traversal,
separators, and control characters before they can reach the client.

Note what is deliberately *absent*: there is no environment argument. The
environment decides how hard the call is gated, so it is read from the
connection record rather than accepted from the caller.
"""

from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr

_DEFAULT_LIST_LIMIT: Final[int] = 20
_MAX_LIST_LIMIT: Final[int] = 100
_DEFAULT_LOG_LINES: Final[int] = 200
_MAX_LOG_LINES: Final[int] = 1000

_CONTROL_CHAR_THRESHOLD: Final[int] = 0x20
# Characters that must never appear in a value destined for a deploy REST
# URL path segment: the separators plus the URL-structure characters
# (query / fragment / userinfo) that could smuggle a different request
# shape onto the pinned host.
_SEGMENT_FORBIDDEN: Final[frozenset[str]] = frozenset({"\\", "/", "?", "#", "@", "%"})


def _reject_unsafe_segment(value: str, *, field: str) -> str:
    """Reject traversal / separator / URL-structure / control chars.

    Args:
        value: The candidate value.
        field: The field name, for the error message.

    Returns:
        The validated value.

    Raises:
        ValueError: When ``value`` contains a ``..`` segment, a leading
            slash, a disallowed character, or a control char.
    """
    if ".." in value:
        msg = f"{field} must not contain '..'"
        raise ValueError(msg)
    if value.startswith("/"):
        msg = f"{field} must not start with '/'"
        raise ValueError(msg)
    if any(ch in value for ch in _SEGMENT_FORBIDDEN):
        msg = f"{field} contains a disallowed character"
        raise ValueError(msg)
    if any(ord(ch) < _CONTROL_CHAR_THRESHOLD for ch in value):
        msg = f"{field} contains a control character"
        raise ValueError(msg)
    return value


class DeployReleaseArgs(BaseModel):
    """Arguments for triggering a release (the destructive action)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    action: Literal["trigger"]
    target: NotBlankStr = Field(
        description="Name of an operator-allowlisted deploy target."
    )
    git_ref: str = Field(
        default="", description="Git ref to deploy. Empty uses the target default."
    )
    confirm: bool = Field(
        default=False,
        description="Must be true. Triggering a release replaces what is running.",
    )
    reason: str = Field(
        default="", description="Why this release is being made. Recorded in the audit."
    )

    @property
    def is_write(self) -> bool:
        """Whether this action mutates the deploy target.

        Returns:
            Always ``True``: a release is the destructive action.
        """
        return True

    @model_validator(mode="after")
    def _validate_segments(self) -> Self:
        """Validate URL-bound and allowlist-bound values.

        Returns:
            ``self`` when every value is safe.
        """
        _reject_unsafe_segment(str(self.target), field="target")
        return self


class DeployRunArgs(BaseModel):
    """Arguments for observing deployments (read-only actions)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    action: Literal["get", "list", "logs"]
    target: NotBlankStr = Field(
        description="Name of an operator-allowlisted deploy target."
    )
    deployment_id: str = Field(
        default="", description="Deployment to read. Required for get and logs."
    )
    limit: int = Field(
        default=_DEFAULT_LIST_LIMIT, ge=1, le=_MAX_LIST_LIMIT, description="Page size."
    )
    log_lines: int = Field(
        default=_DEFAULT_LOG_LINES,
        ge=1,
        le=_MAX_LOG_LINES,
        description="Maximum log lines to return.",
    )

    @property
    def is_write(self) -> bool:
        """Whether this action mutates the deploy target.

        Returns:
            Always ``False``: reading a deployment's state or logs
            observes an outcome, it does not cause one.
        """
        return False

    @model_validator(mode="after")
    def _validate_segments(self) -> Self:
        """Validate URL-bound values and per-action requirements.

        Returns:
            ``self`` when every value is safe and present.

        Raises:
            ValueError: When an action's required field is missing.
        """
        _reject_unsafe_segment(str(self.target), field="target")
        if self.action in ("get", "logs"):
            if not self.deployment_id.strip():
                msg = f"deployment_id is required for action {self.action!r}"
                raise ValueError(msg)
            _reject_unsafe_segment(self.deployment_id, field="deployment_id")
        return self


__all__ = ["DeployReleaseArgs", "DeployRunArgs"]
