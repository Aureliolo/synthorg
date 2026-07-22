"""Domain error hierarchy for the governed deploy tools.

Every failure path raises a ``Deploy<Condition>Error`` subclass of
:class:`synthorg.tools.errors.ToolError` so the
``check_domain_error_hierarchy.py`` gate stays clean and callers can
discriminate failures by ``error_code`` / class. The tool maps the
lower-level ``DeployApi*`` client errors onto these.

:class:`DeploySetupRequiredError` is deliberately distinct from
:class:`DeployConnectionNotFoundError`: a target that was never set up is
a request for a human to act, not a defect, and an agent can only raise
that with a human if the two are distinguishable.
"""

from typing import ClassVar

from pydantic import JsonValue

from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.tools.errors import ToolError


class DeployToolError(ToolError):
    """Base for all governed deploy-tool domain errors."""

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Deploy tool failure"


class DeployToolArgumentError(DeployToolError):
    """Arguments were structurally valid but semantically unusable."""

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_PARAMETER_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Deploy tool arguments invalid"


class DeployTargetNotAllowedError(
    DeployToolError
):  # lint-allow: error-code-uniqueness -- twin of ForbiddenError
    """The named target is not on the operator's deploy allowlist.

    Raised before any credential is brokered: an agent naming a target
    nobody approved must not cause a secret to be read, let alone a call
    to be made.
    """

    status_code: ClassVar[int] = 403
    error_code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    default_message: ClassVar[str] = "Deploy target is not allowed"


class DeployConnectionNotFoundError(
    DeployToolError
):  # lint-allow: error-code-uniqueness -- twin of ConnectionNotFoundError
    """The named deploy connection is absent from the catalog."""

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.CONNECTION_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Deploy connection not found"


class DeploySetupRequiredError(DeployToolError):
    """The target exists but is not usable until a human finishes setup.

    Carries what is missing so an agent can ask a person for exactly
    that, rather than reporting an opaque failure.
    """

    status_code: ClassVar[int] = 424
    error_code: ClassVar[ErrorCode] = ErrorCode.DEPLOY_TARGET_NOT_CONFIGURED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = "Deploy target needs setup"


class DeployUnsupportedError(
    DeployToolError
):  # lint-allow: error-code-uniqueness -- twin of FeatureNotImplementedError
    """The declared platform has no wired deploy client."""

    status_code: ClassVar[int] = 501
    error_code: ClassVar[ErrorCode] = ErrorCode.FEATURE_NOT_IMPLEMENTED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Deploy platform not supported"


class DeployCredentialError(DeployToolError):
    """Credentials could not be brokered for the deploy connection."""

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Deploy credential brokering failed"


class DeployRateLimitedError(DeployToolError):
    """The deploy platform rate-limited the request.

    Carries the platform-advertised cooldown so the agent (the outer
    retry loop) can honour it rather than re-hammering the platform.
    """

    status_code: ClassVar[int] = 429
    error_code: ClassVar[ErrorCode] = ErrorCode.RATE_LIMITED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.RATE_LIMIT
    default_message: ClassVar[str] = "Deploy rate limit exceeded"

    retry_after_seconds: float | None

    def __init__(
        self,
        message: str,
        *,
        retry_after_seconds: float | None = None,
        context: dict[str, JsonValue] | None = None,
    ) -> None:
        """Initialise with an optional platform-advertised cooldown.

        Args:
            message: Human-readable error description.
            retry_after_seconds: Seconds to wait before retrying, when
                the platform advertised a ``Retry-After``.
            context: Arbitrary error metadata.
        """
        super().__init__(message, context=context)
        self.retry_after_seconds = retry_after_seconds


class DeployUpstreamError(DeployToolError):
    """The deploy request failed (auth, transport, or non-2xx)."""

    status_code: ClassVar[int] = 502
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Deploy upstream request failed"


__all__ = [
    "DeployConnectionNotFoundError",
    "DeployCredentialError",
    "DeployRateLimitedError",
    "DeploySetupRequiredError",
    "DeployTargetNotAllowedError",
    "DeployToolArgumentError",
    "DeployToolError",
    "DeployUnsupportedError",
    "DeployUpstreamError",
]
