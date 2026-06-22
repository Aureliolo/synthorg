# module-kind: controller
"""Provider mutation audit-log endpoint."""

from litestar import Controller, get
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.cursor import InvalidCursorError, decode_keyset_cursor
from synthorg.api.dto import DEFAULT_LIMIT, PaginatedResponse
from synthorg.api.dto_provider_capabilities import ProviderAuditEvent
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    encode_keyset_meta,
)
from synthorg.api.path_params import PathName
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.providers.state import ProvidersStateSlice


class ProviderAuditController(Controller):
    """Keyset-paginated provider mutation audit log."""

    path = "/providers"
    tags = ("providers",)

    @get(
        "/{name:str}/audit",
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy("providers.audit", key="user"),
        ],
    )
    async def list_audit(
        self,
        state: State,
        name: PathName,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[ProviderAuditEvent]:
        """List the mutation audit log for one provider, newest first.

        Keyset-paginated on the integer ``id`` column.  ``cursor`` is
        an opaque keyset cursor returned by the previous page; pass
        ``None`` (omit the param) for the first page.

        The endpoint accepts any provider name and returns whatever
        rows exist for it -- including for providers that have since
        been deleted.  The most important audit row a user ever
        queries is ``provider_deleted``; gating the endpoint on
        live-provider existence would make that row undiscoverable.
        A name with no rows simply yields an empty page.

        Args:
            state: Application state.
            name: Provider name (any value accepted; missing
                providers yield an empty page rather than 404).
            cursor: Opaque keyset cursor from a previous page.
            limit: Page size (default ``DEFAULT_LIMIT``, max ``MAX_LIMIT``).

        Returns:
            Paginated response of ``ProviderAuditEvent`` rows.

        Raises:
            InvalidCursorError: HTTP 400 -- malformed, tampered, or
                signed by a different secret.
        """
        app_state: AppState = state.app_state
        # Audit history is queryable by name forever -- including
        # for providers that have been deleted, since the most
        # important row a user ever queries is the
        # ``provider_deleted`` event itself.  A name with no rows
        # simply yields an empty page.

        after_id_str = (
            decode_keyset_cursor(cursor, secret=cursor_secret_of(app_state))
            if cursor is not None
            else None
        )
        # The keyset cursor encodes the last id as a string for
        # cross-domain consistency; the provider audit log carries
        # integer ids, so coerce here.  A validly-signed but malformed
        # cursor (e.g. tampered payload that survived signature check
        # but no longer parses as int) maps to a 400.
        after_id: int | None = None
        if after_id_str is not None:
            try:
                after_id = int(after_id_str)
            except ValueError as exc:
                msg = "cursor payload is not an integer"
                raise InvalidCursorError(msg) from exc

        audit_service = require_service(
            app_state.slice(ProvidersStateSlice).audit_service,
            "Provider Audit Service",
        )
        events, has_more = await audit_service.list_for_provider(
            provider_name=name,
            after_id=after_id,
            limit=limit,
        )
        next_after_key = (
            str(events[-1].id)
            if has_more and events and events[-1].id is not None
            else None
        )
        meta = encode_keyset_meta(
            next_after_key=next_after_key,
            has_more=has_more,
            limit=limit,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse(data=events, pagination=meta)
