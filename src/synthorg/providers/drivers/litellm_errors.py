# module-kind: code
"""Map a LiteLLM exception onto the provider error hierarchy.

Two conditions are decided before the type table is consulted, because
neither is expressed as a LiteLLM exception class and both would otherwise
be filed as something retryable:

- **An empty extra-usage balance** (402) arrives wearing whichever type the
  status happened to map to, so matching on the type would file it as a bad
  request.
- **A spent plan allowance** arrives as a 429 like any other throttle, and
  is separated from one by its message body.

The mapping is a module function taking the provider name rather than a
driver method, so the classification can be exercised without constructing
a driver.
"""

from litellm.exceptions import (
    APIConnectionError as LiteLLMConnectionError,
)
from litellm.exceptions import (
    AuthenticationError as LiteLLMAuthError,
)
from litellm.exceptions import (
    BadRequestError as LiteLLMBadRequest,
)
from litellm.exceptions import (
    ContentPolicyViolationError as LiteLLMContentPolicy,
)
from litellm.exceptions import (
    ContextWindowExceededError as LiteLLMContextWindow,
)
from litellm.exceptions import (
    InternalServerError as LiteLLMInternalError,
)
from litellm.exceptions import (
    NotFoundError as LiteLLMNotFound,
)
from litellm.exceptions import (
    RateLimitError as LiteLLMRateLimit,
)
from litellm.exceptions import (
    ServiceUnavailableError as LiteLLMUnavailable,
)
from litellm.exceptions import (
    Timeout as LiteLLMTimeout,
)
from pydantic import JsonValue

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import (
    PROVIDER_AUTH_ERROR,
    PROVIDER_CONNECTION_ERROR,
    PROVIDER_OVERLOADED,
    PROVIDER_PAYMENT_REQUIRED,
    PROVIDER_QUOTA_EXCEEDED,
    PROVIDER_RATE_LIMITED,
)
from synthorg.providers import errors
from synthorg.providers.drivers.litellm_billing import (
    is_payment_required,
    is_quota_exhaustion,
)

from .mappers import extract_retry_after

logger = get_logger(__name__)

_EXCEPTION_TABLE: tuple[tuple[type[Exception], type[errors.ProviderError]], ...] = (
    (LiteLLMAuthError, errors.AuthenticationError),
    (LiteLLMRateLimit, errors.RateLimitError),
    (LiteLLMNotFound, errors.ModelNotFoundError),
    (LiteLLMContextWindow, errors.InvalidRequestError),
    (LiteLLMContentPolicy, errors.ContentFilterError),
    (LiteLLMBadRequest, errors.InvalidRequestError),
    (LiteLLMTimeout, errors.ProviderTimeoutError),
    # 503 is a queueing model, not a broken endpoint. LiteLLM raises a
    # distinct type for it, so collapsing both into ProviderInternalError
    # discarded a distinction the wire already carried.
    (LiteLLMUnavailable, errors.ProviderOverloadedError),
    (LiteLLMInternalError, errors.ProviderInternalError),
    (LiteLLMConnectionError, errors.ProviderConnectionError),
)


def _map_rate_limit(
    exc: Exception,
    *,
    provider_name: str,
    model: str,
    ctx: dict[str, JsonValue],
) -> errors.ProviderError:
    """Separate a spent allowance from an ordinary throttle.

    Returns:
        A quota error when the message says the allowance is gone, else a
        retryable rate-limit error carrying any ``Retry-After``.
    """
    if is_quota_exhaustion(exc):
        logger.warning(PROVIDER_QUOTA_EXCEEDED, provider=provider_name, model=model)
        return errors.ProviderQuotaExceededError(
            safe_error_description(exc),
            context=ctx,
        )
    logger.warning(PROVIDER_RATE_LIMITED, provider=provider_name, model=model)
    return errors.RateLimitError(
        safe_error_description(exc),
        retry_after=extract_retry_after(exc),
        context=ctx,
    )


def map_litellm_exception(
    exc: Exception,
    *,
    provider_name: str,
    model: str,
) -> errors.ProviderError:
    """Map a LiteLLM exception to the provider error hierarchy.

    Args:
        exc: The exception LiteLLM raised.
        provider_name: The registry name of the provider that dispatched.
        model: The model the call named.

    Returns:
        The matching ``ProviderError`` subclass for the exception, or a
        generic ``ProviderInternalError`` for unmapped types.
    """
    ctx: dict[str, JsonValue] = {"provider": provider_name, "model": model}

    if is_payment_required(exc):
        logger.warning(PROVIDER_PAYMENT_REQUIRED, provider=provider_name, model=model)
        return errors.ProviderPaymentRequiredError(
            safe_error_description(exc),
            context=ctx,
        )

    for litellm_type, our_type in _EXCEPTION_TABLE:
        if not isinstance(exc, litellm_type):
            continue
        if our_type is errors.RateLimitError:
            return _map_rate_limit(
                exc, provider_name=provider_name, model=model, ctx=ctx
            )
        if our_type is errors.AuthenticationError:
            logger.error(PROVIDER_AUTH_ERROR, provider=provider_name, model=model)
        elif our_type is errors.ProviderConnectionError:
            logger.warning(
                PROVIDER_CONNECTION_ERROR, provider=provider_name, model=model
            )
        elif our_type is errors.ProviderOverloadedError:
            # The distinction the generic 5xx bucket loses, and the one the
            # serviceability window is read for: a queueing model is worth
            # waiting on, a broken endpoint is not.
            logger.warning(PROVIDER_OVERLOADED, provider=provider_name, model=model)
        return our_type(
            f"Provider {provider_name} error",
            context={**ctx, "detail": safe_error_description(exc)},
        )

    if isinstance(exc, errors.ProviderError):
        return exc

    return errors.ProviderInternalError(
        f"Unexpected error from provider {provider_name}",
        context={**ctx, "detail": safe_error_description(exc)},
    )


__all__ = ["map_litellm_exception"]
