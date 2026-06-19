"""The OAuth token endpoints fail closed when SSRF-rejected.

A provider-supplied ``token_url`` that resolves to a blocked host must
abort the token request with a domain error, log the dedicated
failure event (no traceback), and never leak the request credentials.
"""

from unittest.mock import patch

import pytest

from synthorg.integrations.errors import TokenRefreshFailedError
from synthorg.integrations.oauth.flows.authorization_code import (
    AuthorizationCodeFlow,
)

pytestmark = pytest.mark.unit

_SECRET = "super-secret-client-secret"
_REFRESH = "super-secret-refresh-token"


async def _reject_ssrf(*_args: object, **_kwargs: object) -> object:
    """Stand in for resolve_outbound_target, rejecting like an SSRF block."""
    msg = "blocked: resolves to 169.254.169.254"
    raise ValueError(msg)


async def test_refresh_token_ssrf_rejection_fails_closed() -> None:
    flow = AuthorizationCodeFlow()
    with (
        patch(
            "synthorg.integrations.oauth.flows.authorization_code."
            "resolve_outbound_target",
            new=_reject_ssrf,
        ),
        pytest.raises(TokenRefreshFailedError) as exc_info,
    ):
        await flow.refresh_token(
            token_url="http://metadata.internal/token",
            client_id="cid",
            client_secret=_SECRET,
            refresh_token=_REFRESH,
        )
    # The domain error must not echo the credentials or the raw SSRF
    # reason (which could carry resolved internal addresses).
    message = str(exc_info.value)
    assert _SECRET not in message
    assert _REFRESH not in message
    assert "169.254" not in message
