"""Proactive OAuth token-refresh behaviour for the background manager.

Exercises the expiry-simulation path for the external-remote backend:
a forge (GitHub) connection authenticated via OAuth2 whose access
token is about to expire is refreshed and the new token persisted, so
the external-remote git backend (which re-resolves credentials per
operation) picks up the fresh token on its next push.
"""

from datetime import UTC, datetime, timedelta

import pytest
from tests._shared import mock_of

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionStatus,
    ConnectionType,
    OAuthToken,
)
from synthorg.integrations.oauth.flows.authorization_code import (
    AuthorizationCodeFlow,
)
from synthorg.integrations.oauth.token_manager import OAuthTokenManager

pytestmark = pytest.mark.unit


def _forge_oauth_connection(expires_at: datetime) -> Connection:
    return Connection(
        name=NotBlankStr("github-oauth"),
        connection_type=ConnectionType.GITHUB,
        auth_method=AuthMethod.OAUTH2,
        base_url=NotBlankStr("https://github.com/acme"),
        metadata={"token_expires_at": expires_at.isoformat()},
    )


class TestOAuthTokenManagerRefresh:
    async def test_forge_token_near_expiry_is_refreshed(self) -> None:
        now = datetime.now(UTC)
        conn = _forge_oauth_connection(now + timedelta(seconds=60))
        catalog = mock_of[ConnectionCatalog]()
        catalog.list_all.return_value = (conn,)
        catalog.get_credentials.return_value = {
            "token_url": "https://github.com/login/oauth/access_token",
            "client_id": "cid",
            "client_secret": "csecret",
            "refresh_token": "old-refresh",
        }
        new_expiry = now + timedelta(hours=1)
        flow = mock_of[AuthorizationCodeFlow]()
        flow.refresh_token.return_value = OAuthToken(
            access_token="new-access",
            refresh_token="new-refresh",
            expires_at=new_expiry,
        )
        manager = OAuthTokenManager(catalog, refresh_threshold_seconds=300)
        manager._flow = flow

        await manager._check_and_refresh()

        flow.refresh_token.assert_awaited_once()
        catalog.store_oauth_tokens.assert_awaited_once()
        kwargs = catalog.store_oauth_tokens.await_args.kwargs
        assert kwargs["access_token"] == "new-access"

    async def test_expired_token_flips_connection_degraded(self) -> None:
        now = datetime.now(UTC)
        conn = _forge_oauth_connection(now - timedelta(seconds=10))
        catalog = mock_of[ConnectionCatalog]()
        catalog.list_all.return_value = (conn,)
        manager = OAuthTokenManager(catalog)

        await manager._check_and_refresh()

        catalog.update_health.assert_awaited_once()
        assert (
            catalog.update_health.await_args.kwargs["status"]
            == ConnectionStatus.DEGRADED
        )

    async def test_fresh_token_is_not_refreshed(self) -> None:
        now = datetime.now(UTC)
        conn = _forge_oauth_connection(now + timedelta(hours=2))
        catalog = mock_of[ConnectionCatalog]()
        catalog.list_all.return_value = (conn,)
        manager = OAuthTokenManager(catalog, refresh_threshold_seconds=300)

        await manager._check_and_refresh()

        catalog.get_credentials.assert_not_called()
