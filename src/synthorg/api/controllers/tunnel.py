"""Tunnel API controller.

Multi-provider webhook tunnel for development: status + provider
readiness, start/stop of the selected provider, dashboard-managed
token credentials, and the Dev Tunnels device-code login.

``TunnelError`` propagates to the client untouched: every adapter
builds its message from static text or an already-scrubbed error
description (``safe_error_description``), so the text is both safe
and actionable (e.g. "paste your token on the tunnel card").
"""

from typing import Final

from litestar import Controller, delete, get, post, put
from litestar.datastructures import State
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.path_params import PathName
from synthorg.core.types import NotBlankStr
from synthorg.integrations.state import tunnel_manager_of
from synthorg.integrations.tunnel.protocol import (
    DeviceLoginPrompt,
    TunnelSnapshot,
)
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    TUNNEL_STARTED,
    TUNNEL_STOPPED,
)

logger = get_logger(__name__)

_MAX_TOKEN_LEN: Final[int] = 512
_MAX_PROVIDER_LEN: Final[int] = 64


class TunnelStartResponse(BaseModel):
    """Response body for ``POST /integrations/tunnel/start``.

    Attributes:
        public_url: The started tunnel's public URL.
        provider: The provider that served the start.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    public_url: NotBlankStr
    provider: NotBlankStr


class TunnelCredentialRequest(BaseModel):
    """Request body for ``PUT /integrations/tunnel/credential``.

    ``extra="forbid"`` rejects unknown keys at the boundary. The token
    rides as ``SecretStr`` so accidental logging of the parsed body
    cannot echo it.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr = Field(max_length=_MAX_PROVIDER_LEN)
    token: SecretStr = Field(min_length=1, max_length=_MAX_TOKEN_LEN)


class TunnelDeviceLoginRequest(BaseModel):
    """Request body for ``POST /integrations/tunnel/device-login``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    provider: NotBlankStr = Field(max_length=_MAX_PROVIDER_LEN)


class TunnelController(Controller):
    """Multi-provider webhook tunnel for local development."""

    path = "/integrations/tunnel"
    tags = ["Integrations"]  # noqa: RUF012

    @get(
        "/status",
        guards=[require_read_access],
        summary="Get tunnel status and provider readiness",
    )
    async def get_status(
        self,
        state: State,
    ) -> ApiResponse[TunnelSnapshot]:
        """Snapshot the tunnel: URL, selection, per-provider readiness.

        Returns:
            ``ApiResponse[TunnelSnapshot]`` instance.
        """
        manager = tunnel_manager_of(state["app_state"])
        return ApiResponse(data=await manager.snapshot())

    @post(
        "/start",
        guards=[require_write_access],
        summary="Start the selected provider's tunnel",
    )
    async def start_tunnel(
        self,
        state: State,
    ) -> ApiResponse[TunnelStartResponse]:
        """Start the tunnel on the currently selected provider.

        Returns:
            ``ApiResponse[TunnelStartResponse]`` instance.

        Raises:
            TunnelError: When the selected provider cannot start
                (missing binary, missing credential, upstream refusal).
        """
        manager = tunnel_manager_of(state["app_state"])
        url = await manager.start()
        snapshot = await manager.snapshot()
        provider = snapshot.active_provider or snapshot.selected_provider
        logger.info(TUNNEL_STARTED, public_url=url, provider=provider)
        return ApiResponse(
            data=TunnelStartResponse(public_url=url, provider=provider),
        )

    @post(
        "/stop",
        guards=[require_write_access],
        summary="Stop the running tunnel",
    )
    async def stop_tunnel(
        self,
        state: State,
    ) -> ApiResponse[None]:
        """Stop the active tunnel (no-op when stopped).

        Returns:
            ``ApiResponse[None]`` instance.
        """
        manager = tunnel_manager_of(state["app_state"])
        await manager.stop()
        logger.info(TUNNEL_STOPPED)
        return ApiResponse(data=None)

    @put(
        "/credential",
        guards=[require_write_access],
        summary="Store a tunnel provider's auth token",
    )
    async def put_credential(
        self,
        state: State,
        data: TunnelCredentialRequest,
    ) -> ApiResponse[None]:
        """Store (or rotate) a token-kind provider's auth token.

        The token is written to the encrypted connection catalog as a
        ``tunnel-<provider>`` connection; adapters resolve it fresh at
        every start.

        Returns:
            ``ApiResponse[None]`` instance.

        Raises:
            TunnelError: For an unknown provider, a provider that does
                not take a token, or when no catalog is available.
        """
        manager = tunnel_manager_of(state["app_state"])
        await manager.store_token(data.provider, data.token.get_secret_value())
        return ApiResponse(data=None)

    @delete(
        "/credential/{provider:str}",
        guards=[require_write_access],
        status_code=200,
        summary="Delete a tunnel provider's stored auth token",
    )
    async def delete_credential(
        self,
        state: State,
        provider: PathName,
    ) -> ApiResponse[None]:
        """Delete a provider's stored token (idempotent).

        Returns:
            ``ApiResponse[None]`` instance.

        Raises:
            TunnelError: For an unknown provider or one that does not
                take a token.
        """
        manager = tunnel_manager_of(state["app_state"])
        await manager.clear_token(provider)
        return ApiResponse(data=None)

    @post(
        "/device-login",
        guards=[require_write_access],
        summary="Begin a provider's device-code login",
    )
    async def device_login(
        self,
        state: State,
        data: TunnelDeviceLoginRequest,
    ) -> ApiResponse[DeviceLoginPrompt]:
        """Start a device-code login (GitHub Dev Tunnels).

        The response carries the verification URL and one-time code;
        the provider CLI completes the login in the background once the
        operator finishes in the browser, and the status endpoint's
        ``credential_configured`` flips on the next poll.

        Returns:
            ``ApiResponse[DeviceLoginPrompt]`` instance.

        Raises:
            TunnelError: For an unknown provider, one without a device
                login, or a CLI failure.
        """
        manager = tunnel_manager_of(state["app_state"])
        return ApiResponse(data=await manager.begin_device_login(data.provider))
