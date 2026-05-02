"""OAuth API controller.

Endpoints for initiating OAuth flows, handling callbacks,
and checking token status.
"""

from typing import Annotated, Any

from litestar import Controller, get, post
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.path_params import PathName  # noqa: TC001 -- runtime annotation
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.core.domain_errors import ValidationError
from synthorg.core.types import (
    NotBlankStr,  # noqa: TC001 -- Pydantic field annotation evaluated at runtime
)
from synthorg.integrations.errors import (
    InvalidStateError,
    SecretRetrievalError,
    TokenExchangeFailedError,
)
from synthorg.integrations.oauth.callback_handler import (
    resolve_oauth_http_timeout,
)
from synthorg.integrations.oauth.flows.authorization_code import (
    AuthorizationCodeFlow,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import SECRET_RETRIEVAL_FAILED

logger = get_logger(__name__)

# Length caps on attacker-controllable strings.
_MAX_CONNECTION_NAME_LEN = 128
_MAX_SCOPE_LEN = 256
_MAX_OAUTH_CODE_LEN = 2048
_MAX_OAUTH_STATE_LEN = 512


class InitiateOAuthFlowRequest(BaseModel):
    """Body model for ``POST /oauth/initiate``.

    Replaces the prior ``data: dict[str, Any]`` shape so input
    bounds are enforced by Pydantic at the boundary.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
    )

    connection_name: Annotated[
        NotBlankStr,
        Field(max_length=_MAX_CONNECTION_NAME_LEN),
    ]
    # Scope items are themselves NotBlankStr so an empty / whitespace
    # element cannot be silently forwarded to the OAuth provider call.
    scopes: tuple[Annotated[NotBlankStr, Field(max_length=_MAX_SCOPE_LEN)], ...] = ()


class OAuthController(Controller):
    """OAuth flow management endpoints."""

    path = "/oauth"
    tags = ["Integrations"]  # noqa: RUF012

    @post(
        "/initiate",
        guards=[require_write_access],
        summary="Start an OAuth flow",
    )
    async def initiate_flow(
        self,
        state: State,
        data: InitiateOAuthFlowRequest,
    ) -> ApiResponse[dict[str, str]]:
        """Initiate an OAuth authorization code flow.

        Returns the authorization URL for the user to visit.
        """
        # ``ConnectionNotFoundError`` propagates to the central
        # handler with its class-level 404 + ``CONNECTION_NOT_FOUND``
        # mapping; controller-level translation collapses the type
        # into the generic ``NotFoundError``.
        connection_name = data.connection_name
        catalog = state["app_state"].connection_catalog
        conn = await catalog.get_or_raise(connection_name)

        credentials = await catalog.get_credentials(connection_name)

        app_state = state["app_state"]
        resolver = app_state.config_resolver if app_state.has_config_resolver else None
        timeout = await resolve_oauth_http_timeout(resolver)
        if timeout is not None:
            flow = AuthorizationCodeFlow(http_timeout_seconds=timeout)
        else:
            flow = AuthorizationCodeFlow()
        config = app_state.config.integrations.oauth
        if not config.redirect_uri_base:
            msg = "oauth.redirect_uri_base must be configured to initiate OAuth flows"
            raise ValidationError(msg)

        # Build the callback URL from the configured API prefix so
        # deployments on a non-default prefix do not hand the OAuth
        # provider a URL this app never actually serves.
        api_prefix = state["app_state"].config.api.api_prefix
        redirect_uri = (
            config.redirect_uri_base.rstrip("/")
            + "/"
            + api_prefix.strip("/")
            + "/oauth/callback"
        )

        auth_url, oauth_state = await flow.start_flow(
            auth_url=credentials.get("auth_url", ""),
            token_url=credentials.get("token_url", ""),
            client_id=credentials.get("client_id", ""),
            client_secret=credentials.get("client_secret", ""),
            scopes=data.scopes,
            redirect_uri=redirect_uri,
        )

        # Route the persistence write through ``OAuthStateService`` so
        # the audit-grade ``SECURITY_OAUTH_STATE_PERSISTED`` event
        # accompanies every save. Raises 503 when the service is not
        # yet wired (matches every other persistence-bound facade).
        bound_state = await state["app_state"].oauth_state_service.persist_initiation(
            oauth_state,
            connection_name=conn.name,
        )

        return ApiResponse(
            data={
                "authorization_url": auth_url,
                "state_token": bound_state.state_token,
            },
        )

    @get(
        "/callback",
        summary="OAuth callback",
        guards=[
            per_op_rate_limit_from_policy("oauth.callback", key="ip"),
        ],
    )
    async def callback(
        self,
        state: State,
        code: str = Parameter(
            description="Authorization code",
            max_length=_MAX_OAUTH_CODE_LEN,
        ),
        state_param: str = Parameter(
            query="state",
            description="OAuth state token",
            max_length=_MAX_OAUTH_STATE_LEN,
        ),
    ) -> ApiResponse[dict[str, Any]]:
        """Handle OAuth provider callback.

        The callback URL itself is unauthenticated because the
        external OAuth provider cannot carry a session cookie,
        but the state token is validated inside the handler and
        acts as CSRF protection.
        """
        from synthorg.integrations.oauth.callback_handler import (  # noqa: PLC0415
            handle_oauth_callback,
        )

        app_state = state["app_state"]
        persistence = app_state.persistence
        catalog = app_state.connection_catalog
        resolver = app_state.config_resolver if app_state.has_config_resolver else None

        try:
            connection_name = await handle_oauth_callback(
                state_param=state_param,
                code=code,
                state_repo=persistence.oauth_states,
                catalog=catalog,
                config_resolver=resolver,
            )
        except InvalidStateError as exc:
            raise ValidationError(str(exc)) from exc
        except TokenExchangeFailedError as exc:
            raise ValidationError(str(exc)) from exc
        return ApiResponse(
            data={
                "status": "connected",
                "connection_name": connection_name,
            },
        )

    @get(
        "/status/{connection_name:str}",
        guards=[require_read_access],
        summary="Check OAuth token status",
    )
    async def token_status(
        self,
        state: State,
        connection_name: PathName,
    ) -> ApiResponse[dict[str, Any]]:
        """Check the OAuth token status for a connection."""
        # ``ConnectionNotFoundError`` propagates with its class-level
        # 404 + ``CONNECTION_NOT_FOUND`` envelope.
        catalog = state["app_state"].connection_catalog
        conn = await catalog.get_or_raise(connection_name)

        # ``has_token`` is true only when the OAuth exchange has
        # actually completed -- derive it from the token expiry
        # metadata, not from the presence of any stored secret
        # (which would be true for a connection that only has
        # client_id/client_secret but no user token yet).
        expires_at = conn.metadata.get("token_expires_at")
        # Check the credential blob for a stored access_token as
        # a secondary signal (e.g. non-expiring client credentials).
        # ``has_token=None`` signals a secret-store outage -- distinct
        # from ``False`` (user never connected) so the UI can render a
        # "backend unavailable" state instead of prompting a reconnect.
        has_access_token: bool | None = False
        try:
            credentials = await catalog.get_credentials(connection_name)
            has_access_token = bool(credentials.get("access_token"))
        except SecretRetrievalError as exc:
            # No traceback on a credential-lookup warning path --
            # frame-locals could carry decrypted secret material.
            # Operators get the type + scrubbed message via
            # ``safe_error_description``. Narrow to
            # ``SecretRetrievalError`` so an unexpected bug in
            # ``catalog.get_credentials`` (KeyError, TypeError, etc.)
            # surfaces as a 500 instead of being silently masked as
            # "backend unavailable".
            logger.warning(
                SECRET_RETRIEVAL_FAILED,
                connection_name=connection_name,
                reason="credential lookup failed in /status",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            has_access_token = None
        if has_access_token is None:
            has_token: bool | None = None
        else:
            has_token = bool(expires_at) or has_access_token
        return ApiResponse(
            data={
                "connection_name": connection_name,
                "has_token": has_token,
                "token_expires_at": expires_at,
            },
        )
