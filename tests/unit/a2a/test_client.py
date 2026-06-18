"""Tests for the outbound A2A client."""

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import respx

from synthorg.a2a.client import A2AClient, A2AClientError
from synthorg.a2a.models import (
    A2AMessage,
    A2AMessageRole,
    A2ATaskState,
    A2ATextPart,
)

# Share the registered default so the tests never diverge from the
# precedence contract if the registry default is ever re-tuned.
_A2A_DEFAULT_TIMEOUT = 30.0


def _make_message(text: str = "hi") -> A2AMessage:
    """Build a minimal valid A2A message for send_message tests."""
    return A2AMessage(
        role=A2AMessageRole.USER,
        parts=(A2ATextPart(text=text),),
    )


def _mock_catalog(
    *,
    base_url: str = "https://peer.example.com",
    credentials: dict[str, str] | None = None,
    conn_exists: bool = True,
) -> AsyncMock:
    """Create a mock connection catalog."""
    catalog = AsyncMock()
    if conn_exists:
        conn = MagicMock()
        conn.base_url = base_url
        catalog.get = AsyncMock(return_value=conn)
    else:
        catalog.get = AsyncMock(return_value=None)
    catalog.get_credentials = AsyncMock(
        return_value=credentials or {"api_key": "test-key"},
    )
    return catalog


def _make_client(
    catalog: AsyncMock | None = None,
) -> A2AClient:
    """Create a client with an injected httpx client (no real I/O)."""
    return A2AClient(
        catalog or _mock_catalog(),
        timeout_seconds=_A2A_DEFAULT_TIMEOUT,
        http_client=httpx.AsyncClient(),
    )


class TestA2AClient:
    """Outbound A2A client tests."""

    @pytest.mark.unit
    async def test_connection_not_found(self) -> None:
        """Raises when peer connection doesn't exist."""
        catalog = _mock_catalog(conn_exists=False)
        client = _make_client(catalog)
        with pytest.raises(A2AClientError, match="not found"):
            await client.get_task("missing-peer", "task-1")

    @pytest.mark.unit
    async def test_no_base_url(self) -> None:
        """Raises when peer has no base_url."""
        catalog = _mock_catalog(base_url="")
        client = _make_client(catalog)
        with pytest.raises(A2AClientError, match="no base_url"):
            await client.send_message("peer-a", _make_message())

    @pytest.mark.unit
    @respx.mock
    async def test_send_message_success(self) -> None:
        """send_message returns a task on success."""
        respx.post("https://peer.example.com/api/v1/a2a").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": "1",
                    "result": {"id": "task-99", "state": "submitted"},
                },
            ),
        )
        catalog = _mock_catalog()
        client = _make_client(catalog)
        task = await client.send_message("peer-a", _make_message())

        assert task.id == "task-99"
        assert task.state == A2ATaskState.SUBMITTED
        catalog.get.assert_called_once_with("peer-a")

    @pytest.mark.unit
    @respx.mock
    async def test_get_task_success(self) -> None:
        """get_task returns the remote task state."""
        respx.post("https://peer.example.com/api/v1/a2a").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": "1",
                    "result": {"id": "t-1", "state": "working"},
                },
            ),
        )
        catalog = _mock_catalog()
        client = _make_client(catalog)
        task = await client.get_task("peer-a", "t-1")

        assert task.id == "t-1"
        assert task.state == A2ATaskState.WORKING

    @pytest.mark.unit
    @respx.mock
    async def test_cancel_task_success(self) -> None:
        """cancel_task returns the cancelled task."""
        respx.post("https://peer.example.com/api/v1/a2a").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": "1",
                    "result": {"id": "t-1", "state": "canceled"},
                },
            ),
        )
        catalog = _mock_catalog()
        client = _make_client(catalog)
        task = await client.cancel_task("peer-a", "t-1")
        assert task.state == A2ATaskState.CANCELED

    @pytest.mark.unit
    @respx.mock
    async def test_remote_error_raises(self) -> None:
        """Remote JSON-RPC error raises A2AClientError."""
        respx.post("https://peer.example.com/api/v1/a2a").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": "1",
                    "error": {"code": -32001, "message": "Not found"},
                },
            ),
        )
        catalog = _mock_catalog()
        client = _make_client(catalog)
        with pytest.raises(A2AClientError, match="Not found"):
            await client.get_task("peer-a", "t-1")

    @pytest.mark.unit
    @respx.mock
    async def test_http_error_raises(self) -> None:
        """HTTP 500 raises A2AClientError."""
        respx.post("https://peer.example.com/api/v1/a2a").mock(
            return_value=httpx.Response(
                500,
                text="Internal Server Error",
            ),
        )
        catalog = _mock_catalog()
        client = _make_client(catalog)
        with pytest.raises(A2AClientError, match="returned 500"):
            await client.send_message("peer-a", _make_message())

    @pytest.mark.unit
    @respx.mock
    async def test_credentials_injected_as_api_key(self) -> None:
        """API key from catalog is injected as X-API-Key header."""
        route = respx.post(
            "https://peer.example.com/api/v1/a2a",
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": "1",
                    "result": {"id": "t-1", "state": "submitted"},
                },
            ),
        )
        catalog = _mock_catalog(
            credentials={"api_key": "secret-abc", "auth_scheme": "api_key"},
        )
        client = _make_client(catalog)
        await client.send_message("peer-a", _make_message())

        assert route.called
        req = route.calls[0].request
        assert req.headers["x-api-key"] == "secret-abc"

    @pytest.mark.unit
    async def test_client_error_carries_peer_name(self) -> None:
        """A2AClientError carries the peer name."""
        catalog = _mock_catalog(conn_exists=False)
        client = _make_client(catalog)
        with pytest.raises(A2AClientError) as exc_info:
            await client.get_task("my-peer", "task-1")
        assert exc_info.value.peer_name == "my-peer"

    @pytest.mark.unit
    def test_client_error_str(self) -> None:
        """A2AClientError has a human-readable string."""
        err = A2AClientError("test error", peer_name="p1")
        assert str(err) == "test error"
        assert err.peer_name == "p1"

    @pytest.mark.unit
    @respx.mock
    async def test_bearer_auth_scheme(self) -> None:
        """Bearer auth scheme injects Authorization header."""
        route = respx.post(
            "https://peer.example.com/api/v1/a2a",
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": "1",
                    "result": {"id": "t-1", "state": "submitted"},
                },
            ),
        )
        catalog = _mock_catalog(
            credentials={
                "auth_scheme": "bearer",
                "access_token": "tok-123",
            },
        )
        client = _make_client(catalog)
        await client.send_message("peer-a", _make_message())
        assert route.called
        req = route.calls[0].request
        assert req.headers["authorization"] == "Bearer tok-123"

    @pytest.mark.unit
    @respx.mock
    async def test_oauth2_auth_scheme(self) -> None:
        """OAuth2 auth scheme injects Bearer token."""
        route = respx.post(
            "https://peer.example.com/api/v1/a2a",
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": "1",
                    "result": {"id": "t-1", "state": "submitted"},
                },
            ),
        )
        catalog = _mock_catalog(
            credentials={
                "auth_scheme": "oauth2",
                "access_token": "oauth-tok",
            },
        )
        client = _make_client(catalog)
        await client.send_message("peer-a", _make_message())
        assert route.called
        req = route.calls[0].request
        assert req.headers["authorization"] == "Bearer oauth-tok"

    @pytest.mark.unit
    @respx.mock
    async def test_mtls_auth_scheme_no_header(self) -> None:
        """mTLS auth scheme does not inject an auth header."""
        route = respx.post(
            "https://peer.example.com/api/v1/a2a",
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": "1",
                    "result": {"id": "t-1", "state": "submitted"},
                },
            ),
        )
        catalog = _mock_catalog(
            credentials={"auth_scheme": "mtls"},
        )
        client = _make_client(catalog)
        await client.send_message("peer-a", _make_message())
        assert route.called
        req = route.calls[0].request
        assert "authorization" not in req.headers
        assert "x-api-key" not in req.headers

    @pytest.mark.unit
    @respx.mock
    async def test_malformed_response_missing_id(self) -> None:
        """Peer result without 'id' raises A2AClientError."""
        respx.post("https://peer.example.com/api/v1/a2a").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": "1",
                    "result": {"state": "submitted"},
                },
            ),
        )
        catalog = _mock_catalog()
        client = _make_client(catalog)
        with pytest.raises(A2AClientError, match="malformed response"):
            await client.send_message("peer-a", _make_message())

    @pytest.mark.unit
    @respx.mock
    async def test_null_result_raises(self) -> None:
        """Peer result that is null raises A2AClientError."""
        respx.post("https://peer.example.com/api/v1/a2a").mock(
            return_value=httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0",
                    "id": "1",
                    "result": {},
                },
            ),
        )
        catalog = _mock_catalog()
        client = _make_client(catalog)
        with pytest.raises(A2AClientError, match="malformed response"):
            await client.send_message("peer-a", _make_message())

    @pytest.mark.unit
    @respx.mock
    async def test_invalid_json_response(self) -> None:
        """Non-JSON response raises A2AClientError."""
        respx.post("https://peer.example.com/api/v1/a2a").mock(
            return_value=httpx.Response(
                200,
                text="not json",
                headers={"content-type": "text/plain"},
            ),
        )
        catalog = _mock_catalog()
        client = _make_client(catalog)
        with pytest.raises(A2AClientError, match="invalid JSON"):
            await client.send_message("peer-a", _make_message())

    @pytest.mark.unit
    @respx.mock
    async def test_connection_timeout_raises(self) -> None:
        """Connection timeout raises A2AClientError."""
        respx.post("https://peer.example.com/api/v1/a2a").mock(
            side_effect=httpx.ConnectError("Connection refused"),
        )
        catalog = _mock_catalog()
        client = _make_client(catalog)
        with pytest.raises(A2AClientError, match=r"Connection.*failed"):
            await client.send_message("peer-a", _make_message())

    @pytest.mark.unit
    async def test_aclose_closes_http_client(self) -> None:
        """aclose() closes the injected HTTP client."""
        http_client = AsyncMock(spec=httpx.AsyncClient)
        client = A2AClient(
            _mock_catalog(),
            timeout_seconds=_A2A_DEFAULT_TIMEOUT,
            http_client=http_client,
        )
        await client.aclose()
        http_client.aclose.assert_called_once()

    @pytest.mark.unit
    async def test_aclose_none_client(self) -> None:
        """aclose() is a no-op when no HTTP client."""
        client = A2AClient(
            _mock_catalog(),
            timeout_seconds=_A2A_DEFAULT_TIMEOUT,
        )
        await client.aclose()  # should not raise

    @pytest.mark.unit
    async def test_ssrf_blocks_private_url(self) -> None:
        """SSRF validator blocks private/internal URLs."""
        from unittest.mock import patch

        catalog = _mock_catalog(base_url="http://169.254.169.254")
        validator = MagicMock()
        client = A2AClient(
            catalog,
            timeout_seconds=_A2A_DEFAULT_TIMEOUT,
            network_validator=validator,
        )

        with (
            patch(
                "synthorg.tools.network_validator.validate_url_host",
                side_effect=ValueError("SSRF blocked"),
            ),
            patch(
                "synthorg.tools.network_validator.extract_hostname",
                return_value="169.254.169.254",
            ),
            pytest.raises(A2AClientError, match="SSRF"),
        ):
            await client.send_message("peer-a", _make_message())

    @pytest.mark.unit
    async def test_ssrf_unparseable_url(self) -> None:
        """Unparseable URL raises SSRF error."""
        from unittest.mock import patch

        catalog = _mock_catalog(base_url="not-a-url://???")
        validator = MagicMock()
        client = A2AClient(
            catalog,
            timeout_seconds=_A2A_DEFAULT_TIMEOUT,
            network_validator=validator,
        )

        with (
            patch(
                "synthorg.tools.network_validator.extract_hostname",
                return_value=None,
            ),
            pytest.raises(A2AClientError, match=r"SSRF.*cannot parse"),
        ):
            await client.send_message("peer-a", _make_message())


class TestA2AClientTimeoutContract:
    """The ``timeout_seconds`` kwarg is required -- no silent default."""

    @pytest.mark.unit
    def test_construct_without_timeout_raises(self) -> None:
        # Pre-alpha contract: callers must thread the value from the
        # registered ``a2a.client_timeout_seconds`` setting so the
        # constructor cannot drift from the precedence default.
        with pytest.raises(TypeError, match=r"timeout_seconds"):
            A2AClient(_mock_catalog())  # type: ignore[call-arg]

    @pytest.mark.unit
    def test_construct_with_explicit_timeout_succeeds(self) -> None:
        client = A2AClient(
            _mock_catalog(),
            timeout_seconds=_A2A_DEFAULT_TIMEOUT,
        )
        assert client is not None  # constructed without error

    @pytest.mark.unit
    def test_construct_with_positional_timeout_raises(self) -> None:
        # The `*` kw-only marker on __init__ prevents callers from
        # passing timeout positionally, which would reintroduce the
        # silent-default drift this contract is meant to prevent.
        with pytest.raises(TypeError):
            A2AClient(_mock_catalog(), _A2A_DEFAULT_TIMEOUT)  # type: ignore[misc]
