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

from synthorg.core.domain_errors import ConflictError, DomainError, NotFoundError
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


class ConnectionNotFoundError(IntegrationError, NotFoundError):
    """A connection with the given name does not exist.

    Multi-inherits :class:`IntegrationError` (so the integrations
    catch-all family covers it) and :class:`NotFoundError` (so
    :func:`synthorg.api.responses.require_resource_or_404` accepts it
    as a typed ``error_class``).  Mirrors the pattern used by
    :class:`CatalogEntryNotFoundError`.
    """

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
    """Caller-supplied connection auth configuration is invalid.

    Raised by each connection type's ``validate_credentials`` when the
    submitted credential payload is missing a field or malformed. That is
    a request-validation failure (the caller controls the input), so the
    wire contract is a 422 ``VALIDATION_ERROR`` rather than the
    integration family's upstream-failure 502: a controller can let it
    propagate untouched instead of catching and re-mapping it.
    """

    status_code: ClassVar[int] = 422
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Connection authentication is invalid"


class ConnectionHealthError(IntegrationError):
    """A health check operation failed."""

    is_retryable = True
    retryable: ClassVar[bool] = True


# -- Secret errors -------------------------------------------------------


class SecretRetrievalError(IntegrationError):
    """A secret could not be retrieved from the backend."""

    is_retryable = True
    retryable: ClassVar[bool] = True


class SecretRetrievalNotFoundError(NotFoundError):
    """A secret reveal is reported as a 404, hiding the real cause.

    Deliberate security override for ``reveal_secret``: whether the
    connection is absent OR the secret backend is unreachable, the client
    sees one uniform 404 (``RESOURCE_NOT_FOUND``, non-retryable, generic
    message) so a secret-backend error code cannot be used to enumerate
    which connections exist. The typed class makes the intentional 502 ->
    404 / retryable -> non-retryable override explicit so a future
    maintainer does not "fix" it back to 502; operators still get the
    true cause from the ERROR-level redacted log. See
    docs/reference/errors.md.
    """


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


class SecretCaptureHandleInvalidError(IntegrationError):
    """An out-of-band secret-capture handle is invalid.

    Uniform failure for a missing, expired, already-consumed, or
    wrong-binding handle: the single generic message (400
    ``VALIDATION_ERROR``) deliberately does not distinguish the cases so a
    caller cannot probe which handles exist or replay a consumed one.
    Single-use is enforced at consume time, so a legitimate re-attempt
    after a downstream failure re-captures rather than reusing.
    """

    status_code: ClassVar[int] = 400
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "Secret capture handle is invalid or expired"


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
    unavailable, rate-limited, or returned a non-JSON body. For
    deterministic failures (SSRF-rejected ``token_url``, a missing or
    undecryptable PKCE verifier) raise :class:`OAuthConfigurationError`,
    which is non-retryable.
    """

    is_retryable = True
    retryable: ClassVar[bool] = True


class OAuthConfigurationError(TokenExchangeFailedError):
    """An OAuth exchange failed for a deterministic, non-retryable reason.

    Covers an SSRF-rejected ``token_url`` and a missing or undecryptable
    PKCE verifier: retrying cannot change the outcome, so a retry layer
    must treat it as terminal. Subclasses :class:`TokenExchangeFailedError`
    (keeping the ``OAUTH_ERROR`` inheritance-alias code) so existing
    ``except TokenExchangeFailedError`` handlers still catch it, while
    ``is_retryable`` flips to ``False``.
    """

    is_retryable = False
    retryable: ClassVar[bool] = False


class TokenRefreshFailedError(OAuthError):
    """A token refresh attempt failed.

    Transient -- the refresh endpoint call failed or returned
    an unusable response. Callers should back off and retry.
    """

    is_retryable = True
    retryable: ClassVar[bool] = True


class OAuthRateLimitedError(OAuthError):
    """The token endpoint rate-limited the request (HTTP 429).

    Transient -- the caller should back off and retry. Carries the
    provider's advertised ``Retry-After`` cool-off (seconds) when the
    response supplied a parseable one, so a retry layer can honour the
    hint instead of guessing a backoff. Keeps the ancestor
    ``OAUTH_ERROR`` code (inheritance alias) since clients branch on the
    OAuth family, not a dedicated 429 code.
    """

    is_retryable = True
    retryable: ClassVar[bool] = True
    retry_after_seconds: float | None

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class InvalidStateError(OAuthError):
    """The OAuth state parameter is invalid, expired, or already used.

    This is a callback-validation failure (a malformed, expired, or
    replayed ``state`` query param), not a transient upstream token-endpoint
    fault, so the wire contract is a 400 ``VALIDATION_ERROR`` rather than the
    OAuth family's 502. The controller lets it propagate untouched.
    """

    status_code: ClassVar[int] = 400
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "OAuth state parameter is invalid"


class DeviceFlowTimeoutError(OAuthError):
    """The device flow polling timed out before user authorization."""


class PKCEValidationError(OAuthError):
    """PKCE code verifier or challenge validation failed."""


class OIDCVerificationError(OAuthError):
    """An OIDC id_token failed signature or claim verification.

    A failed signature / issuer / audience / expiry check is a callback
    security rejection, not a transient fault, so the wire contract is a
    400 ``VALIDATION_ERROR`` rather than the OAuth family's 502. The
    controller lets it propagate untouched.
    """

    status_code: ClassVar[int] = 400
    error_code: ClassVar[ErrorCode] = ErrorCode.VALIDATION_ERROR
    error_category: ClassVar[ErrorCategory] = ErrorCategory.VALIDATION
    default_message: ClassVar[str] = "OAuth id_token verification failed"


class OIDCNonceMismatchError(OIDCVerificationError):
    """The id_token ``nonce`` claim did not match the stored nonce.

    Signals a replayed or injected id_token: the authorization-code
    flow bound a single-use nonce that the returned token does not
    carry, so the token is rejected.
    """


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


class WebhookVerifierUnavailableError(WebhookError):
    """No signature verifier is registered for the connection type.

    Fail-closed: the connection exists but its type has no webhook
    signature verifier in this deployment, so the request cannot be
    authenticated and is rejected rather than processed unverified.

    Status is 501 Not Implemented, not the family's 502: the server did
    not contact an upstream and get a bad response (502); it simply does
    not implement verification for this connection type. 501 tells the
    sender this is a permanent capability gap (an operator must deploy the
    verifier), not a transient upstream fault to retry. Keeps the
    ``WEBHOOK_ERROR`` family code as an inheritance alias; only the HTTP
    status is narrowed.
    """

    status_code: ClassVar[int] = 501
    default_message: ClassVar[str] = "Webhook signature verification unavailable"


# -- Rate limiting errors ------------------------------------------------


class ConnectionRateLimitError(IntegrationError):
    """The connection's rate limit has been exceeded."""

    is_retryable = True
    retryable: ClassVar[bool] = True


# -- Chat platform Web API errors ----------------------------------------


class ChatApiError(IntegrationError):
    """Base for chat-platform Web API client failures.

    Transient by default (an upstream 5xx / network failure is safe to
    retry). The auth and rate-limit leaves below narrow the HTTP status
    while keeping the family ``INTEGRATION_ERROR`` code so callers branch
    on the class, not a dedicated code.
    """

    is_retryable = True
    retryable: ClassVar[bool] = True
    default_message: ClassVar[str] = "Chat API request failed"


class ChatApiAuthError(ChatApiError):
    """The chat platform rejected the bot token (auth / scope failure).

    Non-retryable: a fresh or better-scoped credential is required, not a
    backoff.
    """

    is_retryable = False
    retryable: ClassVar[bool] = False
    status_code: ClassVar[int] = 401
    default_message: ClassVar[str] = "Chat API authentication failed"


class ChatApiRateLimitError(ChatApiError):
    """The chat platform rate-limited the request (HTTP 429).

    Carries the advertised ``Retry-After`` cool-off (seconds) when the
    response supplied a parseable one.
    """

    is_retryable = True
    retryable: ClassVar[bool] = True
    status_code: ClassVar[int] = 429
    retry_after_seconds: float | None

    def __init__(
        self,
        message: str | None = None,
        *,
        retry_after_seconds: float | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


# -- Tunnel errors -------------------------------------------------------


class TunnelError(IntegrationError):
    """An error occurred starting or operating the tunnel.

    Deterministic by default (missing token, unsupported platform,
    unknown provider); the transient leaf subclasses below mark the
    failures a retry can plausibly fix.
    """


class TunnelStartFailedError(TunnelError):
    """The vendor CLI failed to spawn, connect, or yield a public URL."""

    is_retryable = True
    retryable: ClassVar[bool] = True


class TunnelDownloadError(TunnelError):
    """Fetching or unpacking the vendor CLI release asset failed."""

    is_retryable = True
    retryable: ClassVar[bool] = True


# -- Lifecycle errors ----------------------------------------------------


class IntegrationLifecycleConflictError(ConflictError):
    """Raised when an integration service is restarted after a timed-out stop.

    Shared by the rate-limit coordinator and the OAuth token manager:
    a stuck drain leaves the original background loop alive on the
    instance, so the canonical lifecycle pattern marks the service
    unrestartable rather than stacking a second loop on the orphan.
    Mirrors :class:`~synthorg.providers.errors.ProviderLifecycleConflictError`;
    inherits the shareable ``RESOURCE_CONFLICT`` code.
    """

    default_message: ClassVar[str] = (
        "Integration service is unrestartable after a timed-out stop"
    )


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
