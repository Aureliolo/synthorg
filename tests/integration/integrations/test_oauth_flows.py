"""Integration tests for all three OAuth 2.1 flows.

- Authorization code + PKCE (with callback handler storing tokens)
- Device flow (RFC 8628) including ``authorization_pending`` polling
- Client credentials (M2M)

These tests mock ``httpx.AsyncClient`` to avoid real network calls.
They exercise the actual flow classes end-to-end and verify that
raw tokens are returned (not placeholder ``pending-*`` refs).
"""

from collections.abc import Mapping, Sequence
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import structlog.testing
from typeguard import suppress_type_checks

from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionType,
    OAuthState,
    OAuthToken,
)
from synthorg.integrations.errors import (
    InvalidStateError,
    OIDCNonceMismatchError,
    OIDCVerificationError,
    TokenExchangeFailedError,
)
from synthorg.integrations.oauth.callback_handler import handle_oauth_callback
from synthorg.integrations.oauth.flows.authorization_code import (
    AuthorizationCodeFlow,
)
from synthorg.integrations.oauth.flows.client_credentials import (
    ClientCredentialsFlow,
)
from synthorg.integrations.oauth.flows.device_flow import DeviceFlow
from synthorg.integrations.oauth.pkce import (
    encrypt_pkce_verifier,
    generate_code_verifier,
)
from synthorg.integrations.oauth.state_service import OAuthStateService
from synthorg.tools.network_validator import DnsValidationOk
from tests._shared import JsonDict
from tests._shared.fake_clock import FakeClock


def _mock_token_response(
    json_body: JsonDict,
    status_code: int = 200,
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    resp.json.return_value = json_body
    resp.is_error = status_code >= 400
    return resp


@pytest.mark.integration
class TestAuthorizationCodeFlow:
    async def test_exchange_code_returns_raw_tokens(self) -> None:
        flow = AuthorizationCodeFlow()
        # OAuthState now stores the PKCE verifier encrypted at rest;
        # build the same ciphertext the real ``start_flow`` would
        # produce so ``exchange_code`` can decrypt it back.
        verifier = generate_code_verifier()
        state = OAuthState(
            state_token=NotBlankStr("state-xyz"),
            connection_name=NotBlankStr("conn-1"),
            pkce_verifier=NotBlankStr(encrypt_pkce_verifier(verifier)),
            scopes_requested="read",
            redirect_uri="https://app.example.com/cb",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        resp = _mock_token_response(
            {
                "access_token": "atk-123",
                "refresh_token": "rtk-456",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "read",
            }
        )
        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.post.return_value = resp

        async def _enter(self: object) -> AsyncMock:
            return client_mock

        async def _exit(self: object, *_: object) -> None:
            return None

        with patch(
            "synthorg.integrations.oauth.flows.authorization_code.httpx.AsyncClient"
        ) as client_cls:
            client_cls.return_value.__aenter__ = _enter
            client_cls.return_value.__aexit__ = _exit
            token = await flow.exchange_code(
                token_url="https://example.com/token",
                client_id="cid",
                client_secret="csec",
                state=state,
                code="auth-code",
                redirect_uri="https://app.example.com/cb",
            )
        assert token.access_token == "atk-123"
        assert token.refresh_token == "rtk-456"
        assert token.token_type == "Bearer"
        assert token.expires_at is not None

    async def test_refresh_wraps_exchange_failure_as_refresh_failure(
        self,
    ) -> None:
        from synthorg.integrations.errors import TokenRefreshFailedError

        flow = AuthorizationCodeFlow()
        resp = _mock_token_response({})  # no access_token

        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.post.return_value = resp

        async def _enter(self: object) -> AsyncMock:
            return client_mock

        async def _exit(self: object, *_: object) -> None:
            return None

        with patch(
            "synthorg.integrations.oauth.flows.authorization_code.httpx.AsyncClient"
        ) as client_cls:
            client_cls.return_value.__aenter__ = _enter
            client_cls.return_value.__aexit__ = _exit
            with pytest.raises(TokenRefreshFailedError):
                await flow.refresh_token(
                    token_url="https://example.com/token",
                    client_id="cid",
                    client_secret="csec",
                    refresh_token="rtk",
                )


@pytest.mark.integration
class TestCallbackHandler:
    async def test_callback_persists_tokens_via_catalog(self) -> None:
        now = datetime.now(UTC)
        state = OAuthState(
            state_token=NotBlankStr("state-1"),
            connection_name=NotBlankStr("conn-1"),
            pkce_verifier=NotBlankStr("verifier"),
            scopes_requested="read",
            redirect_uri="https://app.example.com/cb",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        state_service = MagicMock(spec=OAuthStateService)
        state_service.get.return_value = state
        state_service.mark_consumed.return_value = True

        stored_tokens: dict[str, str] = {}

        async def _store(
            name: str,
            *,
            access_token: str,
            refresh_token: str | None = None,
        ) -> None:
            stored_tokens["access"] = access_token
            if refresh_token:
                stored_tokens["refresh"] = refresh_token

        catalog = MagicMock(spec=ConnectionCatalog)
        catalog.get_or_raise.return_value = Connection(
            name=NotBlankStr("conn-1"),
            connection_type=ConnectionType.OAUTH_APP,
            auth_method=AuthMethod.OAUTH2,
        )
        catalog.get_credentials.return_value = {
            "token_url": "https://example.com/token",
            "client_id": "cid",
            "client_secret": "csec",
        }
        catalog.store_oauth_tokens.side_effect = _store

        fake_flow = MagicMock(spec=AuthorizationCodeFlow)
        fake_flow.exchange_code.return_value = OAuthToken(
            access_token="new-access",
            refresh_token="new-refresh",
            expires_at=now + timedelta(seconds=3600),
        )

        result = await handle_oauth_callback(
            state_param="state-1",
            code="auth-code",
            state_service=state_service,
            catalog=catalog,
            flow=fake_flow,
        )
        assert result == "conn-1"
        assert stored_tokens == {"access": "new-access", "refresh": "new-refresh"}
        catalog.store_oauth_tokens.assert_awaited_once()
        catalog.update.assert_awaited()

    async def test_callback_rejects_expired_state(self) -> None:
        past = datetime.now(UTC) - timedelta(hours=2)
        state = OAuthState(
            state_token=NotBlankStr("state-expired"),
            connection_name=NotBlankStr("conn-1"),
            created_at=past - timedelta(hours=1),
            expires_at=past,
        )
        state_service = MagicMock(spec=OAuthStateService)
        state_service.get.return_value = state
        catalog = MagicMock(spec=ConnectionCatalog)
        with pytest.raises(InvalidStateError):
            await handle_oauth_callback(
                state_param="state-expired",
                code="auth-code",
                state_service=state_service,
                catalog=catalog,
            )

    async def test_callback_rejects_missing_credentials(self) -> None:
        now = datetime.now(UTC)
        state = OAuthState(
            state_token=NotBlankStr("state-missing"),
            connection_name=NotBlankStr("conn-1"),
            pkce_verifier=NotBlankStr("verifier"),
            expires_at=now + timedelta(hours=1),
        )
        state_service = MagicMock(spec=OAuthStateService)
        state_service.get.return_value = state
        catalog = MagicMock(spec=ConnectionCatalog)
        catalog.get_or_raise.return_value = Connection(
            name=NotBlankStr("conn-1"),
            connection_type=ConnectionType.OAUTH_APP,
            auth_method=AuthMethod.OAUTH2,
        )
        catalog.get_credentials.return_value = {}
        with pytest.raises(TokenExchangeFailedError):
            await handle_oauth_callback(
                state_param="state-missing",
                code="auth-code",
                state_service=state_service,
                catalog=catalog,
            )


@pytest.mark.integration
class TestCallbackOidcBinding:
    """OIDC id_token nonce binding fail-closed matrix on the callback.

    Four cells: (id_token present?) x (jwks_uri configured?). Only
    "no jwks_uri AND no id_token" (plain OAuth2) skips verification;
    every other asymmetry fails closed.
    """

    @staticmethod
    def _harness(
        *,
        credentials: dict[str, str],
        id_token: str | None,
        nonce: str | None = "flow-nonce",
    ) -> tuple[MagicMock, MagicMock, MagicMock]:
        now = datetime.now(UTC)
        state = OAuthState(
            state_token=NotBlankStr("state-oidc"),
            connection_name=NotBlankStr("conn-1"),
            pkce_verifier=NotBlankStr("verifier"),
            redirect_uri="https://app.example.com/cb",
            created_at=now,
            expires_at=now + timedelta(hours=1),
            nonce=NotBlankStr(nonce) if nonce else None,
        )
        state_service = MagicMock(spec=OAuthStateService)
        state_service.get.return_value = state
        state_service.mark_consumed.return_value = True

        catalog = MagicMock(spec=ConnectionCatalog)
        catalog.get_or_raise.return_value = Connection(
            name=NotBlankStr("conn-1"),
            connection_type=ConnectionType.OAUTH_APP,
            auth_method=AuthMethod.OAUTH2,
        )
        catalog.get_credentials.return_value = {
            "token_url": "https://example.com/token",
            "client_id": "cid",
            "client_secret": "csec",
            **credentials,
        }

        async def _store(
            name: str,
            *,
            access_token: str,
            refresh_token: str | None = None,
        ) -> None:
            return None

        catalog.store_oauth_tokens.side_effect = _store

        fake_flow = MagicMock(spec=AuthorizationCodeFlow)
        fake_flow.exchange_code.return_value = OAuthToken(
            access_token="atk",
            id_token=id_token,
            expires_at=now + timedelta(seconds=3600),
        )
        return state_service, catalog, fake_flow

    async def test_plain_oauth2_skips_verification(self) -> None:
        state_service, catalog, flow = self._harness(credentials={}, id_token=None)
        with patch(
            "synthorg.integrations.oauth.callback_handler.verify_id_token",
            autospec=True,
        ) as verify:
            result = await handle_oauth_callback(
                state_param="state-oidc",
                code="auth-code",
                state_service=state_service,
                catalog=catalog,
                flow=flow,
            )
        assert result == "conn-1"
        verify.assert_not_called()

    async def test_full_oidc_verification_invoked(self) -> None:
        state_service, catalog, flow = self._harness(
            credentials={
                "jwks_uri": "https://idp.example.com/jwks",
                "oidc_issuer": "https://idp.example.com",
            },
            id_token="h.p.s",
        )
        with patch(
            "synthorg.integrations.oauth.callback_handler.verify_id_token",
            autospec=True,
        ) as verify:
            result = await handle_oauth_callback(
                state_param="state-oidc",
                code="auth-code",
                state_service=state_service,
                catalog=catalog,
                flow=flow,
            )
        assert result == "conn-1"
        verify.assert_awaited_once()
        assert verify.await_args is not None
        kwargs = verify.await_args.kwargs
        assert kwargs["jwks_uri"] == "https://idp.example.com/jwks"
        assert kwargs["issuer"] == "https://idp.example.com"
        assert kwargs["client_id"] == "cid"
        assert kwargs["expected_nonce"] == "flow-nonce"

    async def test_id_token_without_jwks_uri_fails_closed(self) -> None:
        state_service, catalog, flow = self._harness(credentials={}, id_token="h.p.s")
        with pytest.raises(OIDCVerificationError):
            await handle_oauth_callback(
                state_param="state-oidc",
                code="auth-code",
                state_service=state_service,
                catalog=catalog,
                flow=flow,
            )

    async def test_jwks_configured_but_no_id_token_fails_closed(self) -> None:
        state_service, catalog, flow = self._harness(
            credentials={
                "jwks_uri": "https://idp.example.com/jwks",
                "oidc_issuer": "https://idp.example.com",
            },
            id_token=None,
        )
        with pytest.raises(OIDCVerificationError):
            await handle_oauth_callback(
                state_param="state-oidc",
                code="auth-code",
                state_service=state_service,
                catalog=catalog,
                flow=flow,
            )

    async def test_jwks_without_issuer_fails_closed(self) -> None:
        state_service, catalog, flow = self._harness(
            credentials={"jwks_uri": "https://idp.example.com/jwks"},
            id_token="h.p.s",
        )
        with pytest.raises(OIDCVerificationError):
            await handle_oauth_callback(
                state_param="state-oidc",
                code="auth-code",
                state_service=state_service,
                catalog=catalog,
                flow=flow,
            )

    async def test_nonce_mismatch_propagates(self) -> None:
        state_service, catalog, flow = self._harness(
            credentials={
                "jwks_uri": "https://idp.example.com/jwks",
                "oidc_issuer": "https://idp.example.com",
            },
            id_token="h.p.s",
        )
        with patch(
            "synthorg.integrations.oauth.callback_handler.verify_id_token",
            autospec=True,
        ) as verify:
            verify.side_effect = OIDCNonceMismatchError("nope")
            with pytest.raises(OIDCNonceMismatchError):
                await handle_oauth_callback(
                    state_param="state-oidc",
                    code="auth-code",
                    state_service=state_service,
                    catalog=catalog,
                    flow=flow,
                )


@pytest.mark.integration
class TestCallbackReplay:
    """Redelivered callbacks return the original connection name unchanged."""

    async def test_replay_returns_connection_without_re_exchange(self) -> None:
        now = datetime.now(UTC)
        # Already-consumed state row: a successful prior callback
        # stamped these two fields atomically via ``mark_consumed``.
        state = OAuthState(
            state_token=NotBlankStr("state-replay"),
            connection_name=NotBlankStr("conn-1"),
            scopes_requested="read",
            redirect_uri="https://app.example.com/cb",
            created_at=now - timedelta(minutes=5),
            expires_at=now + timedelta(minutes=55),
            consumed_at=now - timedelta(minutes=4),
            connection_name_returned=NotBlankStr("conn-1"),
        )
        state_service = MagicMock(spec=OAuthStateService)
        state_service.get.return_value = state

        catalog = MagicMock(spec=ConnectionCatalog)

        fake_flow = MagicMock(spec=AuthorizationCodeFlow)

        result = await handle_oauth_callback(
            state_param="state-replay",
            code="auth-code",
            state_service=state_service,
            catalog=catalog,
            flow=fake_flow,
        )

        assert result == "conn-1"
        # No re-exchange, no token storage, no metadata update, no
        # mark_consumed on the replay path.
        fake_flow.exchange_code.assert_not_awaited()
        catalog.store_oauth_tokens.assert_not_awaited()
        catalog.update.assert_not_awaited()
        state_service.mark_consumed.assert_not_awaited()
        state_service.expire.assert_not_awaited()

    async def test_fresh_callback_marks_consumed_and_does_not_delete(
        self,
    ) -> None:
        now = datetime.now(UTC)
        state = OAuthState(
            state_token=NotBlankStr("state-fresh"),
            connection_name=NotBlankStr("conn-2"),
            pkce_verifier=NotBlankStr("verifier"),
            scopes_requested="read",
            redirect_uri="https://app.example.com/cb",
            created_at=now,
            expires_at=now + timedelta(hours=1),
        )
        state_service = MagicMock(spec=OAuthStateService)
        state_service.get.return_value = state
        state_service.mark_consumed.return_value = True

        catalog = MagicMock(spec=ConnectionCatalog)
        catalog.get_or_raise.return_value = Connection(
            name=NotBlankStr("conn-2"),
            connection_type=ConnectionType.OAUTH_APP,
            auth_method=AuthMethod.OAUTH2,
        )
        catalog.get_credentials.return_value = {
            "token_url": "https://example.com/token",
            "client_id": "cid",
            "client_secret": "csec",
        }

        fake_flow = MagicMock(spec=AuthorizationCodeFlow)
        fake_flow.exchange_code.return_value = OAuthToken(
            access_token="acc",
            refresh_token="ref",
            expires_at=now + timedelta(seconds=3600),
        )

        result = await handle_oauth_callback(
            state_param="state-fresh",
            code="auth-code",
            state_service=state_service,
            catalog=catalog,
            flow=fake_flow,
        )

        assert result == "conn-2"
        # Success path stamps the consumed marker and never falls
        # back to ``delete`` (delete is now reserved for the expiry
        # branch only).
        state_service.mark_consumed.assert_awaited_once()
        kwargs = state_service.mark_consumed.await_args.kwargs
        assert kwargs.get("connection_name") == "conn-2"
        state_service.expire.assert_not_awaited()


@pytest.mark.integration
class TestClientCredentialsFlow:
    async def test_exchange_returns_raw_access_token(self) -> None:
        flow = ClientCredentialsFlow()
        resp = _mock_token_response(
            {
                "access_token": "m2m-token",
                "token_type": "Bearer",
                "expires_in": 600,
                "scope": "read write",
            }
        )
        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.post.return_value = resp

        async def _enter(self: object) -> AsyncMock:
            return client_mock

        async def _exit(self: object, *_: object) -> None:
            return None

        with patch(
            "synthorg.integrations.oauth.flows.client_credentials.httpx.AsyncClient"
        ) as client_cls:
            client_cls.return_value.__aenter__ = _enter
            client_cls.return_value.__aexit__ = _exit
            token = await flow.exchange(
                token_url="https://example.com/token",
                client_id="cid",
                client_secret="csec",
                scopes=("read", "write"),
            )
        assert token.access_token == "m2m-token"
        assert token.refresh_token is None
        assert token.scope_granted == "read write"


@pytest.mark.integration
class TestDeviceFlow:
    async def test_device_flow_polling_grants_token(self) -> None:
        # FakeClock keeps the polling loop deterministic: each
        # ``sleep`` call advances virtual time and returns immediately,
        # so the three queued ``client_mock.post`` responses fire
        # back-to-back without a real 1 s wait per iteration.
        flow = DeviceFlow(clock=FakeClock())

        start_resp = _mock_token_response(
            {
                "device_code": "dev-code",
                "user_code": "USR-123",
                "verification_uri": "https://example.com/activate",
                "interval": 1,
                "expires_in": 600,
            }
        )
        pending_resp = _mock_token_response({"error": "authorization_pending"})
        granted_resp = _mock_token_response(
            {
                "access_token": "dev-token",
                "refresh_token": "dev-refresh",
                "token_type": "Bearer",
                "expires_in": 1800,
            }
        )
        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.post.side_effect = [
            start_resp,
            pending_resp,
            granted_resp,
        ]

        async def _enter(self: object) -> AsyncMock:
            return client_mock

        async def _exit(self: object, *_: object) -> None:
            return None

        with patch(
            "synthorg.integrations.oauth.flows.device_flow.httpx.AsyncClient"
        ) as client_cls:
            client_cls.return_value.__aenter__ = _enter
            client_cls.return_value.__aexit__ = _exit

            result = await flow.request_device_code(
                device_authorization_url="https://example.com/device",
                client_id="cid",
                scopes=("read",),
            )
            assert result.user_code == "USR-123"

            token = await flow.poll_for_token(
                token_url="https://example.com/token",
                client_id="cid",
                device_code=result.device_code,
                interval=1,
                max_wait_seconds=60,
            )
        assert token.access_token == "dev-token"
        assert token.refresh_token == "dev-refresh"

    @pytest.mark.parametrize("bad_interval", [0, -1, -60])
    async def test_poll_for_token_rejects_non_positive_interval(
        self, bad_interval: int
    ) -> None:
        # A non-positive ``interval`` would short-circuit the
        # ``await self._clock.sleep(...)`` call and spin the polling
        # loop into rapid token-endpoint requests; reject at the
        # boundary instead.
        flow = DeviceFlow(clock=FakeClock())
        with pytest.raises(ValueError, match=r"interval must be a positive int"):
            await flow.poll_for_token(
                token_url="https://example.com/token",
                client_id="cid",
                device_code="dev-code",
                interval=bad_interval,
                max_wait_seconds=60,
            )

    @pytest.mark.parametrize("bad_interval", [True, False, 1.5, 0.0])
    async def test_poll_for_token_rejects_bool_or_float_interval(
        self, bad_interval: bool | float
    ) -> None:
        # ``True == 1`` and ``False == 0`` would silently satisfy a
        # bare ``<= 0`` check; floats would smuggle fractional sleep
        # cadence through the integer-typed parameter.  Reject both
        # at the boundary so the type annotation matches runtime.
        flow = DeviceFlow(clock=FakeClock())
        # Floats are not int instances, so the WARN-level checker would
        # reject the argument before the method's own positive-int guard
        # runs; suppress it so this test exercises that domain validation.
        with (
            pytest.raises(ValueError, match=r"interval must be a positive int"),
            suppress_type_checks(),
        ):
            await flow.poll_for_token(
                token_url="https://example.com/token",
                client_id="cid",
                device_code="dev-code",
                interval=bad_interval,  # type: ignore[arg-type]
                max_wait_seconds=60,
            )

    @pytest.mark.parametrize("bad_max_wait", [0, -1, -60])
    async def test_poll_for_token_rejects_non_positive_max_wait(
        self, bad_max_wait: int
    ) -> None:
        # A non-positive ``max_wait_seconds`` would put the deadline at
        # or before "now" so the loop body never runs and the call
        # always raises ``DeviceFlowTimeoutError`` -- swap that for a
        # clear ``ValueError`` at the boundary.
        flow = DeviceFlow(clock=FakeClock())
        with pytest.raises(
            ValueError, match=r"max_wait_seconds must be a positive int"
        ):
            await flow.poll_for_token(
                token_url="https://example.com/token",
                client_id="cid",
                device_code="dev-code",
                interval=1,
                max_wait_seconds=bad_max_wait,
            )

    @pytest.mark.parametrize("bad_max_wait", [True, False, 60.5, 0.0])
    async def test_poll_for_token_rejects_bool_or_float_max_wait(
        self, bad_max_wait: bool | float
    ) -> None:
        flow = DeviceFlow(clock=FakeClock())
        with (
            pytest.raises(ValueError, match=r"max_wait_seconds must be a positive int"),
            suppress_type_checks(),
        ):
            await flow.poll_for_token(
                token_url="https://example.com/token",
                client_id="cid",
                device_code="dev-code",
                interval=1,
                max_wait_seconds=bad_max_wait,  # type: ignore[arg-type]
            )


# ── Leak-sentinel tests ──────────────────────────────────────────────
# OAuth error logs must not leak `client_secret`, `refresh_token`, or
# `code_verifier` -- not through `str(exc)`, not through traceback
# frame-locals. These tests construct an ``httpx.HTTPStatusError`` whose
# message carries the full POSTed form body (the worst-case shape some
# providers produce) and assert nothing sensitive makes it to the logs.


def _leaky_http_error(body_leak: str) -> httpx.HTTPStatusError:
    """Build an HTTPStatusError whose ``str(exc)`` embeds ``body_leak``."""
    request = httpx.Request(
        "POST",
        "https://idp.example.com/oauth/token",
        content=body_leak.encode(),
    )
    response = httpx.Response(400, request=request, text="bad_request")
    return httpx.HTTPStatusError(
        (
            f"Client error '400 Bad Request' for url "
            f"'https://idp.example.com/oauth/token'. Body: {body_leak}"
        ),
        request=request,
        response=response,
    )


_SENTINEL_CS = "super-secret-value-CS"
_SENTINEL_CV = "super-secret-value-CV"
_SENTINEL_RT = "super-secret-value-RT"
_LEAKY_BODY = (
    f"grant_type=authorization_code&client_secret={_SENTINEL_CS}"
    f"&code_verifier={_SENTINEL_CV}&refresh_token={_SENTINEL_RT}"
)


def _leak_free(events: Sequence[Mapping[str, object]]) -> None:
    """Assert none of the sentinel values or key-prefix combinations
    appear anywhere in the captured log events."""
    blob = repr(events)
    for sentinel in (_SENTINEL_CS, _SENTINEL_CV, _SENTINEL_RT):
        assert sentinel not in blob, f"sentinel {sentinel!r} leaked into logs"
    # Even the key prefixes followed by a real value must be masked.
    for key in ("client_secret=", "refresh_token=", "code_verifier="):
        masked_blob = blob.replace(f"{key}***", "")
        assert key not in masked_blob, (
            f"unmasked {key!r} found in logs (only ``{key}***`` is allowed)"
        )


@pytest.mark.integration
class TestOAuthLogRedaction:
    """Regression guards for OAuth error-path logging.

    All three leak-sentinel cases (authorization-code exchange, refresh,
    client-credentials exchange) share the same sentinel setup and log
    assertions; parametrizing prevents the three bodies from drifting
    and keeps the scenario inventory visible in one place.
    """

    @staticmethod
    def _mock_client() -> AsyncMock:
        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.post.side_effect = _leaky_http_error(_LEAKY_BODY)
        return client_mock

    @pytest.mark.parametrize(
        ("scenario", "mock_path"),
        [
            (
                "authorization_code_exchange",
                "synthorg.integrations.oauth.flows.authorization_code.httpx.AsyncClient",
            ),
            (
                "authorization_code_refresh",
                "synthorg.integrations.oauth.flows.authorization_code.httpx.AsyncClient",
            ),
            (
                "client_credentials_exchange",
                "synthorg.integrations.oauth.flows.client_credentials.httpx.AsyncClient",
            ),
        ],
    )
    async def test_oauth_flow_error_paths_scrub_secrets(
        self,
        scenario: str,
        mock_path: str,
    ) -> None:
        from synthorg.integrations.errors import (
            TokenExchangeFailedError,
            TokenRefreshFailedError,
        )
        from synthorg.integrations.oauth.pkce import (
            encrypt_pkce_verifier,
            generate_code_verifier,
        )

        client_mock = self._mock_client()

        async def _enter(_self: object) -> AsyncMock:
            return client_mock

        async def _exit(_self: object, *_args: object) -> None:
            return None

        raised_message: str | None = None

        # The authorization-code flow SSRF-validates ``token_url`` (real
        # DNS resolution + pinned transport) before the HTTP POST. With
        # the network mocked, ``idp.example.com`` does not resolve, so the
        # flow would fail at the SSRF seam and never reach the mocked
        # leaky HTTP error this test guards. Stub the seam with a real
        # validated result (empty ``resolved_ips`` -> the real
        # ``build_pinned_transport`` returns ``None`` without touching the
        # network) so the request flows into the mocked client.
        async def _ssrf_ok(*_args: object, **_kwargs: object) -> DnsValidationOk:
            return DnsValidationOk(
                hostname=NotBlankStr("idp.example.com"),
                port=443,
                is_https=True,
            )

        ssrf_stubs: list[object] = []
        if scenario.startswith("authorization_code"):
            ssrf_stubs = [
                patch(
                    "synthorg.integrations.oauth.flows."
                    "authorization_code.resolve_outbound_target",
                    new=_ssrf_ok,
                ),
            ]

        with patch(mock_path) as client_cls, ExitStack() as ssrf_stack:
            for stub in ssrf_stubs:
                ssrf_stack.enter_context(stub)  # type: ignore[arg-type]
            client_cls.return_value.__aenter__ = _enter
            client_cls.return_value.__aexit__ = _exit
            if scenario == "authorization_code_exchange":
                flow = AuthorizationCodeFlow()
                verifier = generate_code_verifier()
                state = OAuthState(
                    state_token=NotBlankStr("state-redact"),
                    connection_name=NotBlankStr("conn-1"),
                    pkce_verifier=NotBlankStr(encrypt_pkce_verifier(verifier)),
                    scopes_requested="read",
                    redirect_uri="https://app.example.com/cb",
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
                with (
                    structlog.testing.capture_logs() as events,
                    pytest.raises(
                        TokenExchangeFailedError,
                    ) as exchange_exc,
                ):
                    await flow.exchange_code(
                        token_url="https://idp.example.com/oauth/token",
                        client_id="cid",
                        client_secret=_SENTINEL_CS,
                        state=state,
                        code="auth-code",
                        redirect_uri="https://app.example.com/cb",
                    )
                raised_message = str(exchange_exc.value)
            elif scenario == "authorization_code_refresh":
                flow = AuthorizationCodeFlow()
                with (
                    structlog.testing.capture_logs() as events,
                    pytest.raises(
                        TokenRefreshFailedError,
                    ),
                ):
                    await flow.refresh_token(
                        token_url="https://idp.example.com/oauth/token",
                        client_id="cid",
                        client_secret=_SENTINEL_CS,
                        refresh_token=_SENTINEL_RT,
                    )
            else:  # client_credentials_exchange
                flow_cc = ClientCredentialsFlow()
                with (
                    structlog.testing.capture_logs() as events,
                    pytest.raises(
                        TokenExchangeFailedError,
                    ),
                ):
                    await flow_cc.exchange(
                        token_url="https://idp.example.com/oauth/token",
                        client_id="cid",
                        client_secret=_SENTINEL_CS,
                    )

        _leak_free(events)
        # Taxonomy preserved for operators across every scenario.
        assert any(e.get("error_type") == "HTTPStatusError" for e in events), events
        if raised_message is not None:
            # The exchange path surfaces the raised exception message to
            # callers; make sure it too carries no sentinel material.
            for sentinel in (_SENTINEL_CS, _SENTINEL_CV, _SENTINEL_RT):
                assert sentinel not in raised_message

    async def test_exchange_failure_does_not_emit_traceback_exc_info(
        self,
    ) -> None:
        """``logger.warning`` (not ``exception``) carries no ``exc_info``
        field. Without that field, structlog cannot serialize frame-local
        values from the request payload."""
        from synthorg.integrations.errors import TokenExchangeFailedError
        from synthorg.integrations.oauth.pkce import (
            encrypt_pkce_verifier,
            generate_code_verifier,
        )

        flow = AuthorizationCodeFlow()
        verifier = generate_code_verifier()
        state = OAuthState(
            state_token=NotBlankStr("state-noexc"),
            connection_name=NotBlankStr("conn-1"),
            pkce_verifier=NotBlankStr(encrypt_pkce_verifier(verifier)),
            scopes_requested="read",
            redirect_uri="https://app.example.com/cb",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        client_mock = AsyncMock(spec=httpx.AsyncClient)
        client_mock.post.side_effect = _leaky_http_error(_LEAKY_BODY)

        async def _enter(_self: object) -> AsyncMock:
            return client_mock

        async def _exit(_self: object, *_args: object) -> None:
            return None

        with patch(
            "synthorg.integrations.oauth.flows.authorization_code.httpx.AsyncClient",
        ) as client_cls:
            client_cls.return_value.__aenter__ = _enter
            client_cls.return_value.__aexit__ = _exit
            with (
                structlog.testing.capture_logs() as events,
                pytest.raises(
                    TokenExchangeFailedError,
                ),
            ):
                await flow.exchange_code(
                    token_url="https://idp.example.com/oauth/token",
                    client_id="cid",
                    client_secret=_SENTINEL_CS,
                    state=state,
                    code="auth-code",
                    redirect_uri="https://app.example.com/cb",
                )
        # No event may carry ``exc_info`` -- traceback frame-locals are
        # the primary leak vector we demoted ``logger.exception`` to
        # close.
        for event in events:
            assert "exc_info" not in event, event
            assert event.get("log_level") != "error", event
