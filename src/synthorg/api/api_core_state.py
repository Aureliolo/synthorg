"""Api-core feature state slice.

Holds the cross-cutting API services that belong to no single domain
feature: the opaque-pagination cursor secret, the auth service, the
session / lockout / refresh-token stores, the WebSocket ticket store,
the user-presence tracker, the org-mutation service, the workflow
rollback service, and the idempotency service. The cursor secret and
auth service are wired in ``create_app``; the persistence-backed
auth stores and services are wired once persistence is connected. All
fields are ``None`` until wired; readers pass them through
``require_service`` to surface a clean 503 before that.

The mutable coordination primitives that ``AppState`` still owns
directly (request locks, bridge-config snapshots, shutdown event,
background-task sets) are not slice fields: a frozen slice cannot host
in-place-mutated state.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.api.auth.api_key_service import ApiKeyService
from synthorg.api.auth.presence import UserPresence
from synthorg.api.auth.service import AuthService
from synthorg.api.auth.ticket_store import WsTicketStore
from synthorg.api.cursor import CursorSecret
from synthorg.api.services.analytics_read_service import AnalyticsReadService
from synthorg.api.services.org_mutations import OrgMutationService
from synthorg.api.services.workflow_rollback_service import (
    WorkflowRollbackService,
)
from synthorg.api.state_slices import AppStateSliceMixin
from synthorg.idempotency import IdempotencyService
from synthorg.persistence.auth_protocol import (
    LockoutRepository as LockoutStore,
)
from synthorg.persistence.auth_protocol import (
    RefreshTokenRepository as RefreshStore,
)
from synthorg.persistence.auth_protocol import (
    SessionRepository as SessionStore,
)


class ApiCoreStateSlice(BaseFeatureStateSlice):
    """Application-state slice for cross-cutting API-core services."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    cursor_secret: CursorSecret | None = None
    auth_service: AuthService | None = None
    api_key_service: ApiKeyService | None = None
    session_store: SessionStore | None = None
    lockout_store: LockoutStore | None = None
    refresh_store: RefreshStore | None = None
    ticket_store: WsTicketStore | None = None
    user_presence: UserPresence | None = None
    org_mutation_service: OrgMutationService | None = None
    workflow_rollback_service: WorkflowRollbackService | None = None
    idempotency_service: IdempotencyService | None = None
    analytics_read_service: AnalyticsReadService | None = None


def auth_service_of(app_state: AppStateSliceMixin) -> AuthService:
    """Resolve the auth service from its slice, or raise 503.

    Returns:
        The wired auth service.
    """
    return require_service(
        app_state.slice(ApiCoreStateSlice).auth_service, "Auth Service"
    )


def org_mutation_service_of(app_state: AppStateSliceMixin) -> OrgMutationService:
    """Resolve the org-mutation service from its slice, or raise 503.

    Returns:
        The wired org-mutation service.
    """
    return require_service(
        app_state.slice(ApiCoreStateSlice).org_mutation_service,
        "Org Mutation Service",
    )


def api_key_service_of(app_state: AppStateSliceMixin) -> ApiKeyService:
    """Resolve the API-key service, lazily building it on first use.

    Requires connected persistence (for ``api_keys``) and a wired auth
    service (for key generation / hashing); raises a 503 via
    :func:`persistence_of` / :func:`auth_service_of` when either is
    absent. The lazy install is made atomic via ``wire_if_field_absent``
    so concurrent first-readers cannot overwrite each other.

    Returns:
        The wired or lazily-composed API-key service.
    """
    existing = app_state.slice(ApiCoreStateSlice).api_key_service
    if existing is not None:
        return existing
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    candidate = ApiKeyService(
        api_keys=persistence_of(app_state).api_keys,
        auth_service=auth_service_of(app_state),
    )
    app_state.wire_if_field_absent(ApiCoreStateSlice, "api_key_service", candidate)
    return app_state.slice(ApiCoreStateSlice).api_key_service or candidate


def session_store_of(app_state: AppStateSliceMixin) -> SessionStore:
    """Resolve the session store from its slice, or raise 503.

    Returns:
        The wired session store.
    """
    return require_service(
        app_state.slice(ApiCoreStateSlice).session_store, "Session Store"
    )


def lockout_store_of(app_state: AppStateSliceMixin) -> LockoutStore:
    """Resolve the lockout store from its slice, or raise 503.

    Returns:
        The wired lockout store.
    """
    return require_service(
        app_state.slice(ApiCoreStateSlice).lockout_store, "Lockout Store"
    )


def refresh_store_of(app_state: AppStateSliceMixin) -> RefreshStore:
    """Resolve the refresh-token store from its slice, or raise 503.

    Returns:
        The wired refresh-token store.
    """
    return require_service(
        app_state.slice(ApiCoreStateSlice).refresh_store, "Refresh Token Store"
    )


def ticket_store_of(app_state: AppStateSliceMixin) -> WsTicketStore:
    """Resolve the WebSocket ticket store from its slice, or raise 503.

    Returns:
        The wired WebSocket ticket store.
    """
    return require_service(
        app_state.slice(ApiCoreStateSlice).ticket_store, "WS Ticket Store"
    )


def workflow_rollback_service_of(
    app_state: AppStateSliceMixin,
) -> WorkflowRollbackService:
    """Resolve the workflow rollback service from its slice, or raise 503.

    Returns:
        The wired workflow rollback service.
    """
    return require_service(
        app_state.slice(ApiCoreStateSlice).workflow_rollback_service,
        "Workflow Rollback Service",
    )


def idempotency_service_of(app_state: AppStateSliceMixin) -> IdempotencyService:
    """Resolve the idempotency service, lazily wrapping ``idempotency_keys``.

    Raises a 503 (via :func:`persistence_of`) when persistence is not
    configured: idempotency must survive restart by definition, so the
    service has no in-memory fallback.

    Returns:
        The wired or lazily-composed idempotency service.
    """
    existing = app_state.slice(ApiCoreStateSlice).idempotency_service
    if existing is not None:
        return existing
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    # Concurrent first-readers race here; ``wire_if_field_absent`` makes
    # the check + install atomic so two simultaneous requests cannot both
    # construct a service and overwrite each other's wiring.
    candidate = IdempotencyService(persistence_of(app_state).idempotency_keys)
    app_state.wire_if_field_absent(ApiCoreStateSlice, "idempotency_service", candidate)
    return app_state.slice(ApiCoreStateSlice).idempotency_service or candidate


def analytics_read_service_of(
    app_state: AppStateSliceMixin,
) -> AnalyticsReadService:
    """Resolve the analytics read service, lazily wrapping ``tasks``.

    Raises a 503 (via :func:`persistence_of`) when persistence is not
    connected. ``wire_if_field_absent`` makes the check + install atomic
    so concurrent first-readers cannot overwrite each other.

    Returns:
        The wired or lazily-composed analytics read service.
    """
    existing = app_state.slice(ApiCoreStateSlice).analytics_read_service
    if existing is not None:
        return existing
    from synthorg.persistence.state import persistence_of  # noqa: PLC0415

    candidate = AnalyticsReadService(task_repo=persistence_of(app_state).tasks)
    app_state.wire_if_field_absent(
        ApiCoreStateSlice, "analytics_read_service", candidate
    )
    return app_state.slice(ApiCoreStateSlice).analytics_read_service or candidate
