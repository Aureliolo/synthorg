"""Error hierarchy for the integrations subsystem.

All integration errors inherit from ``IntegrationError`` so callers
can catch the entire family with a single except clause.

Errors carry an ``is_retryable`` class attribute that mirrors the
provider resilience layer's convention: ``True`` means transient
(network, timeout, rate-limit) and safe to retry, ``False`` means
deterministic (bad config, invalid state, missing credentials) and
should propagate.
"""

from typing import ClassVar

from synthorg.core.domain_errors import DomainError, NotFoundError
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


class IntegrationError(DomainError):
    """Base exception for all integration operations.

    Class Attributes:
        status_code: HTTP 502 default (upstream/integration failure).
        error_code: ``INTEGRATION_ERROR``.
        error_category: ``PROVIDER_ERROR``.
        retryable: Mirrors ``is_retryable``; subclasses override both.
        default_message: Generic 5xx-safe message.
    """

    # Default: deterministic failure -- do NOT retry. Subclasses
    # representing transient failures override this.
    is_retryable: bool = False
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 502
    error_code: ClassVar[ErrorCode] = ErrorCode.INTEGRATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.PROVIDER_ERROR
    default_message: ClassVar[str] = "Integration error"


# -- Connection errors ---------------------------------------------------


class ConnectionNotFoundError(IntegrationError):
    """A connection with the given name does not exist."""

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.CONNECTION_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Connection not found"


class DuplicateConnectionError(IntegrationError):
    """A connection with the given name already exists."""

    status_code: ClassVar[int] = 409
    error_code: ClassVar[ErrorCode] = ErrorCode.RESOURCE_CONFLICT
    error_category: ClassVar[ErrorCategory] = ErrorCategory.CONFLICT
    default_message: ClassVar[str] = "Connection already exists"


class InvalidConnectionAuthError(IntegrationError):
    """Connection authentication configuration is invalid."""


class ConnectionHealthError(IntegrationError):
    """A health check operation failed."""

    is_retryable = True
    retryable: ClassVar[bool] = True


# -- Secret errors -------------------------------------------------------


class SecretRetrievalError(IntegrationError):
    """A secret could not be retrieved from the backend."""

    is_retryable = True
    retryable: ClassVar[bool] = True


class SecretStorageError(IntegrationError):
    """A secret could not be stored in the backend."""

    is_retryable = True
    retryable: ClassVar[bool] = True


class SecretRotationError(IntegrationError):
    """A secret rotation operation failed."""

    is_retryable = True
    retryable: ClassVar[bool] = True


class MasterKeyError(IntegrationError):
    """The master encryption key is missing or invalid."""


# -- OAuth errors --------------------------------------------------------


class OAuthError(IntegrationError):
    """Base exception for OAuth flow failures."""

    status_code: ClassVar[int] = 502
    error_code: ClassVar[ErrorCode] = ErrorCode.OAUTH_ERROR
    default_message: ClassVar[str] = "OAuth flow failed"


class OAuthFlowError(OAuthError):
    """An OAuth flow could not be initiated or completed."""


class TokenExchangeFailedError(OAuthError):
    """The authorization code could not be exchanged for tokens.

    Transient -- the token endpoint may have been temporarily
    unavailable, rate-limited, or returned a non-JSON body.
    """

    is_retryable = True
    retryable: ClassVar[bool] = True


class TokenRefreshFailedError(OAuthError):
    """A token refresh attempt failed.

    Transient -- the refresh endpoint call failed or returned
    an unusable response. Callers should back off and retry.
    """

    is_retryable = True
    retryable: ClassVar[bool] = True


class InvalidStateError(OAuthError):
    """The OAuth state parameter is invalid, expired, or already used."""


class DeviceFlowTimeoutError(OAuthError):
    """The device flow polling timed out before user authorization."""


class PKCEValidationError(OAuthError):
    """PKCE code verifier or challenge validation failed."""


# -- Webhook errors ------------------------------------------------------


class WebhookError(IntegrationError):
    """Base exception for webhook operations."""

    status_code: ClassVar[int] = 502
    error_code: ClassVar[ErrorCode] = ErrorCode.WEBHOOK_ERROR
    default_message: ClassVar[str] = "Webhook processing failed"


class SignatureVerificationFailedError(WebhookError):
    """The webhook signature did not match."""


class ReplayAttackDetectedError(WebhookError):
    """A replayed webhook request was detected (nonce or timestamp)."""


class InvalidWebhookPayloadError(WebhookError):
    """The webhook payload could not be parsed."""


class WebhookProcessingError(WebhookError):
    """An error occurred while processing a verified webhook event."""


# -- Rate limiting errors ------------------------------------------------


class ConnectionRateLimitError(IntegrationError):
    """The connection's rate limit has been exceeded."""

    is_retryable = True
    retryable: ClassVar[bool] = True


# -- Tunnel errors -------------------------------------------------------


class TunnelError(IntegrationError):
    """An error occurred starting or operating the tunnel."""

    is_retryable = True
    retryable: ClassVar[bool] = True


# -- MCP catalog errors --------------------------------------------------


class CatalogEntryNotFoundError(IntegrationError, NotFoundError):
    """A catalog entry with the given ID does not exist.

    Inherits the integration-error family for catch-all integration
    handlers AND :class:`NotFoundError` so callers using
    ``except NotFoundError`` (and the ``EXCEPTION_HANDLERS`` not-found
    routing) catch this without per-controller catch + re-raise.
    """

    status_code: ClassVar[int] = 404
    error_code: ClassVar[ErrorCode] = ErrorCode.RECORD_NOT_FOUND
    error_category: ClassVar[ErrorCategory] = ErrorCategory.NOT_FOUND
    default_message: ClassVar[str] = "Catalog entry not found"


class MCPInstallError(IntegrationError):
    """An MCP server installation failed."""
