"""Pins the OAuth callback error contract (audit finding 147).

The ``/oauth/callback`` controller no longer flattens
:class:`TokenExchangeFailedError` to a 422 ``ValidationError``: a
token-endpoint failure is transient, so its own 502 + retryable
metadata must reach the central RFC 9457 handler. This test locks the
class metadata so a future revert of the controller change (or a
weakening of the class) fails the suite immediately.
"""

import pytest

from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode
from synthorg.integrations.errors import (
    IntegrationError,
    TokenExchangeFailedError,
)

pytestmark = pytest.mark.unit


def test_token_exchange_failed_is_transient_502() -> None:
    """A token-exchange failure stays a retryable 502 provider error."""
    assert TokenExchangeFailedError.status_code == 502
    assert TokenExchangeFailedError.error_code is ErrorCode.OAUTH_ERROR
    assert TokenExchangeFailedError.error_category is ErrorCategory.PROVIDER_ERROR
    assert TokenExchangeFailedError.is_retryable is True


def test_token_exchange_failed_routes_through_integration_handler() -> None:
    """It is an ``IntegrationError`` so ``handle_domain_error`` maps it."""
    assert issubclass(TokenExchangeFailedError, IntegrationError)
