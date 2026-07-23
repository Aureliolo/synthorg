"""Domain error hierarchy for the governed publish tools.

Every failure path raises a ``Publish<Condition>Error`` subclass of
:class:`synthorg.tools.errors.ToolError` so the
``check_domain_error_hierarchy.py`` gate stays clean and callers can
discriminate failures by ``error_code`` / class. The tool maps the
lower-level ``RegistryApi*`` client errors onto these.

:class:`PublishSetupRequiredError` is deliberately distinct from
:class:`PublishConnectionNotFoundError`: a target that was never set up is a
request for a human to act, not a defect, and an agent can only raise that
with a human if the two are distinguishable.
"""

from typing import ClassVar

from pydantic import JsonValue

from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.tools.errors import ToolError


class PublishToolError(ToolError):
    """Base for all governed publish-tool domain errors."""

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Publish tool failure"


class PublishToolArgumentError(PublishToolError):
    """Arguments were structurally valid but semantically unusable."""

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_PARAMETER_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Publish tool arguments invalid"


class PublishSourceError(PublishToolError):
    """The image source a workspace push names is missing or malformed.

    Distinct from a plain argument error: the arguments parsed, but the OCI
    layout the agent pointed at is absent, oversized, or does not match the
    declared digests.
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_PARAMETER_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Publish image source invalid"


class PublishTargetNotAllowedError(
    PublishToolError
):  # lint-allow: error-code-uniqueness -- twin of ForbiddenError
    """The named target is not on the operator's publish allowlist.

    Raised before any credential is brokered: an agent naming a target
    nobody approved must not cause a secret to be read, let alone a call
    to be made.
    """

    status_code: ClassVar[int] = 403
    error_code: ClassVar[ErrorCode] = ErrorCode.FORBIDDEN
    error_category: ClassVar[ErrorCategory] = ErrorCategory.AUTH
    default_message: ClassVar[str] = "Publish target is not allowed"


class PublishConnectionNotFoundError(
    PublishToolError
):  # lint-allow: error-code-uniqueness -- twin of ConnectionNotFoundError
    """The named registry connection is absent from the catalog."""

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.CONNECTION_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Publish connection not found"


class PublishSetupRequiredError(PublishToolError):
    """The target exists but is not usable until a human finishes setup.

    Carries what is missing so an agent can ask a person for exactly that,
    rather than reporting an opaque failure.
    """

    status_code: ClassVar[int] = 424
    error_code: ClassVar[ErrorCode] = ErrorCode.PUBLISH_TARGET_NOT_CONFIGURED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = "Publish target needs setup"


class PublishUnsupportedError(
    PublishToolError
):  # lint-allow: error-code-uniqueness -- twin of FeatureNotImplementedError
    """The declared registry provider has no wired client."""

    status_code: ClassVar[int] = 501
    error_code: ClassVar[ErrorCode] = ErrorCode.FEATURE_NOT_IMPLEMENTED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Publish provider not supported"


class PublishCredentialError(PublishToolError):
    """Credentials could not be brokered for the registry connection."""

    status_code: ClassVar[int] = 500
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Publish credential brokering failed"


class PublishRateLimitedError(PublishToolError):
    """The registry rate-limited the request.

    Carries the platform-advertised cooldown so the agent (the outer retry
    loop) can honour it rather than re-hammering the registry.
    """

    status_code: ClassVar[int] = 429
    error_code: ClassVar[ErrorCode] = ErrorCode.RATE_LIMITED
    error_category: ClassVar[ErrorCategory] = ErrorCategory.RATE_LIMIT
    default_message: ClassVar[str] = "Publish rate limit exceeded"

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
            retry_after_seconds: Seconds to wait before retrying, when the
                registry advertised a ``Retry-After``.
            context: Arbitrary error metadata.
        """
        super().__init__(message, context=context)
        self.retry_after_seconds = retry_after_seconds


class PublishUpstreamError(PublishToolError):
    """The publish request failed (auth, transport, or non-2xx)."""

    status_code: ClassVar[int] = 502
    error_code: ClassVar[ErrorCode] = ErrorCode.TOOL_EXECUTION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.INTERNAL
    default_message: ClassVar[str] = "Publish upstream request failed"


__all__ = [
    "PublishConnectionNotFoundError",
    "PublishCredentialError",
    "PublishRateLimitedError",
    "PublishSetupRequiredError",
    "PublishSourceError",
    "PublishTargetNotAllowedError",
    "PublishToolArgumentError",
    "PublishToolError",
    "PublishUnsupportedError",
    "PublishUpstreamError",
]
