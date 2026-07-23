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

from synthorg.core.normalization import reject_unsafe_url_segment
from synthorg.core.types import NotBlankStr

_DEFAULT_LIST_LIMIT: Final[int] = 20
_MAX_LIST_LIMIT: Final[int] = 100
_DEFAULT_LOG_LINES: Final[int] = 200
_MAX_LOG_LINES: Final[int] = 1000


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
        description=(
            "Acknowledges that a release replaces what is running; a true "
            "value is enforced by the admin guardrail, not this model."
        ),
    )
    reason: str = Field(
        default="",
        description=(
            "Why this release is being made; a non-blank value is enforced "
            "by the admin guardrail and recorded in the audit."
        ),
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
        reject_unsafe_url_segment(str(self.target), field="target")
        return self


class DeployRunArgs(BaseModel):
    """Arguments for observing deployments (read-only actions)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    action: Literal["get", "list", "logs"]
    target: NotBlankStr = Field(
        description="Name of an operator-allowlisted deploy target."
    )
    deployment_id: NotBlankStr | None = Field(
        default=None, description="Deployment to read. Required for get and logs."
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
        reject_unsafe_url_segment(str(self.target), field="target")
        if self.action in ("get", "logs"):
            if self.deployment_id is None:
                msg = f"deployment_id is required for action {self.action!r}"
                raise ValueError(msg)
            reject_unsafe_url_segment(str(self.deployment_id), field="deployment_id")
        return self


__all__ = ["DeployReleaseArgs", "DeployRunArgs"]
