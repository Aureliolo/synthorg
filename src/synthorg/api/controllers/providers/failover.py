# module-kind: controller
"""Reading the operator's declared failover, and every time it engaged.

Two reads, because they answer two questions an operator asks in sequence:
what did I declare, and did it ever have to be used. The second is the one
the event log alone cannot answer after a restart, which is why the rows are
persisted rather than only logged.
"""

from typing import Annotated

from litestar import Controller, get
from litestar.datastructures import State
from litestar.params import QueryParameter
from pydantic import BaseModel, ConfigDict, Field

from synthorg._core.features import require_service
from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import DEFAULT_LIMIT, ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    encode_countless_seek_meta,
)
from synthorg.api.state import AppState
from synthorg.core.types import NotBlankStr
from synthorg.persistence.provider_failover_event_protocol import (
    ProviderFailoverEventFilterSpec,
)
from synthorg.persistence.state import PersistenceStateSlice
from synthorg.providers.failover import parse_failover_routes
from synthorg.providers.failover_event import ProviderFailoverEvent
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.state import SettingsStateSlice

_FeatureFilter = Annotated[
    str | None,
    QueryParameter(
        max_length=200,
        description="Restrict to one system feature's dispatches",
    ),
]
_ProviderFilter = Annotated[
    str | None,
    QueryParameter(
        max_length=200,
        description="Restrict to one declared connection",
    ),
]


class DeclaredFailoverRoute(BaseModel):
    """One ``declared -> alternate`` route, split into its four halves.

    Split rather than returned as the stored blob so the dashboard renders
    what an operator wrote without re-implementing the key format, which is
    the sort of duplication that lets a display drift from the resolution.

    Attributes:
        declared_provider: Connection the feature is bound to.
        declared_model: Model on that connection.
        alternate_provider: Connection that serves when it cannot.
        alternate_model: Model on that connection.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    declared_provider: NotBlankStr = Field(description="Bound connection")
    declared_model: NotBlankStr = Field(description="Bound model")
    alternate_provider: NotBlankStr = Field(description="Alternate connection")
    alternate_model: NotBlankStr = Field(description="Alternate model")


class FailoverDeclaration(BaseModel):
    """What the operator has declared, as the resolver reads it.

    Attributes:
        enabled: Whether the mechanism is on. A route declared while this is
            off is inert, so both halves are reported together rather than
            leaving the dashboard to imply one from the other.
        routes: The declared routes, in the order their keys sort.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = Field(description="Whether declared failover may engage")
    routes: tuple[DeclaredFailoverRoute, ...] = Field(
        default=(), description="Declared routes"
    )


class ProviderFailoverController(Controller):
    """The declared failover and its engagement log."""

    path = "/providers"
    tags = ("providers",)

    @get("/failover", guards=[require_read_access])
    async def get_declaration(self, state: State) -> ApiResponse[FailoverDeclaration]:
        """Report the mechanism toggle and every declared route.

        Returns:
            ``ApiResponse`` carrying the declaration as the resolver reads
            it, so a route the resolver refuses (a provider-less alternate,
            a route to itself) is absent here too rather than displayed as
            active.
        """
        app_state: AppState = state.app_state
        resolver = require_service(
            app_state.slice(SettingsStateSlice).config_resolver, "Config Resolver"
        )
        namespace = SettingNamespace.PROVIDERS.value
        enabled = await resolver.get_bool(namespace, "failover_enabled")
        routes = parse_failover_routes(
            await resolver.get_str(namespace, "failover_routes")
        )
        return ApiResponse(
            data=FailoverDeclaration(
                enabled=enabled,
                routes=tuple(
                    DeclaredFailoverRoute(
                        declared_provider=NotBlankStr(declared.provider),
                        declared_model=NotBlankStr(declared.model_id),
                        alternate_provider=NotBlankStr(alternate.provider),
                        alternate_model=NotBlankStr(alternate.model_id),
                    )
                    for declared, alternate in routes.declared_pairs()
                ),
            )
        )

    @get("/failover-events", guards=[require_read_access])
    async def list_events(
        self,
        state: State,
        feature: _FeatureFilter = None,
        declared_provider: _ProviderFilter = None,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[ProviderFailoverEvent]:
        """List engagements newest-first.

        Args:
            state: Application state.
            feature: Restrict to one system feature's dispatches.
            declared_provider: Restrict to one declared connection.
            cursor: Opaque cursor from a previous page.
            limit: Page size.

        Returns:
            Paginated response of engagements.

        Raises:
            InvalidCursorError: HTTP 400 when the cursor is malformed,
                tampered, or signed by a different secret.
        """
        app_state: AppState = state.app_state
        secret = cursor_secret_of(app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        backend = require_service(
            app_state.slice(PersistenceStateSlice).backend, "Persistence Backend"
        )
        connection = declared_provider
        spec = ProviderFailoverEventFilterSpec(
            feature=None if feature is None else NotBlankStr(feature),
            declared_provider=None if connection is None else NotBlankStr(connection),
        )
        rows = await backend.provider_failover_events.query(
            spec, limit=limit + 1, offset=offset
        )
        meta = encode_countless_seek_meta(
            offset=offset, fetched_rows=len(rows), limit=limit, secret=secret
        )
        return PaginatedResponse(data=rows[:limit], pagination=meta)


__all__ = [
    "DeclaredFailoverRoute",
    "FailoverDeclaration",
    "ProviderFailoverController",
]
